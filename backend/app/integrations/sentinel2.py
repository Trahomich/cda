"""Адаптер IF-SATELLITE «Sentinel-2 L2A» (docs/05 §2, REC-17; задача MVP-10).

Источник — Copernicus Data Space Ecosystem:
- каталог: OData API (catalogue.dataspace.copernicus.eu/odata/v1/Products);
- авторизация: OAuth2 client_credentials (identity.dataspace.copernicus.eu);
- растры: продукты L2A (JP2-каналы 10/20 м); загрузка и распаковка — вне MVP,
  адаптер читает распакованные каналы из локального кэша (satellite_raster_cache_dir),
  пользователь канала доставляет их туда (после включения SATELLITE_ENABLED=true).

Облачность — упрощённо по метаданным продукта (атрибут cloudCover), а не по
маске облачных пикселей полезной площади; метод помечается в Scene.cloud_method
(оговорено в задаче #28: упрощение с пометкой метода).

Ключи (COPERNICUS_CLIENT_ID / _SECRET) — в .env, плейсхолдеры в .env.example.
Если ключей нет или сеть недоступна — адаптер не используется: планировщик
выключен флагом SATELLITE_ENABLED=false (по умолчанию), в тестах и демо
работает FakeSentinel2Adapter с той же сигнатурой.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.integrations import (
    DataGapError,
    DataQualityError,
    Scene,
    SourceCapabilities,
    SourceUnavailableError,
)
from app.integrations.raster import (
    INDEX_BANDS,
    compute_index_array,
    contour_mask,
    stats_over_mask,
    validate_index_values,
)

logger = logging.getLogger(__name__)

# Группировка аппаратов Sentinel-2B/2C: пересмотр 2–3 суток (05 §2)
REVISIT_DAYS = "2-3"
SCENES_SINCE = "2017-03"

MAX_SAVED_CLOUD_PCT = 60.0  # >60 % — не сохраняется вовсе (05 §5, правило 2)


GeometryLookup = Callable[[str], dict | None]
"""field_id (UUID строкой) -> GeoJSON Polygon контура (WGS84) или None."""

RasterLoader = Callable[[str, str], tuple[Any, Any, Any]]
"""(scene_ref, band) -> (массив float, transform, crs) растра канала."""


@dataclass(frozen=True, slots=True)
class SceneMeta:
    """Метаданные сцены из каталога Copernicus (до вычисления индексов)."""

    scene_ref: str  # идентификатор продукта (UUID OData)
    captured_at: date  # дата съёмки
    cloud_pct: float  # облачность по метаданным продукта, %
    name: str | None = None
    tile_id: str | None = None
    assets: dict[str, str] = field(default_factory=dict)  # band -> ссылка/путь


def _http_error(status_code: int, url: str) -> Exception:
    if status_code >= 500 or status_code in (400, 401, 403, 429):
        return SourceUnavailableError(f"satellite.sentinel2-l2a: HTTP {status_code} от {url}")
    return DataGapError(f"satellite.sentinel2-l2a: нет данных (HTTP {status_code} от {url})")


def default_raster_loader(cache_dir: str) -> RasterLoader:
    """Чтение распакованных каналов из кэша: <cache>/<scene_ref>/<band>.tif."""

    def load(scene_ref: str, band: str) -> tuple[Any, Any, Any]:
        import rasterio  # локальный импорт: тяжёлая зависимость, нужна не всегда

        path = Path(cache_dir) / scene_ref / f"{band}.tif"
        if not path.exists():
            raise DataGapError(f"Канал {band} сцены {scene_ref} отсутствует в кэше ({path})")
        with rasterio.open(path) as dataset:
            return dataset.read(1).astype("float32"), dataset.transform, dataset.crs

    return load


class Sentinel2Adapter:
    """HTTP-адаптер каталога Copernicus (source_id="satellite.sentinel2-l2a")."""

    source_id = "satellite.sentinel2-l2a"
    capabilities = SourceCapabilities(
        source_id=source_id,
        kind="satellite",
        metrics=("ndvi", "ndre", "ndmi", "savi"),
    )

    def __init__(
        self,
        *,
        geometry_lookup: GeometryLookup,
        http: httpx.Client | None = None,
        auth_url: str = "https://identity.dataspace.copernicus.eu/auth/realms/CDAS"
        "/protocol/openid-connect/token",
        search_url: str = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products",
        client_id: str = "",
        client_secret: str = "",
        request_timeout: float = 30.0,
        raster_loader: RasterLoader | None = None,
        raster_cache_dir: str = "/var/lib/cda/satellite",
    ) -> None:
        self._geometry_lookup = geometry_lookup
        self._http_client = http
        self._auth_url = auth_url
        self._search_url = search_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = request_timeout
        self._raster_loader = raster_loader or default_raster_loader(raster_cache_dir)
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    # -- HTTP ----------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _ensure_token(self) -> str:
        """OAuth2 client_credentials (кэш токена в памяти адаптера)."""
        if self._token and self._token_expires_at and datetime.now(tz=UTC) < self._token_expires_at:
            return self._token
        if not self._client_id or not self._client_secret:
            raise SourceUnavailableError(
                f"{self.source_id}: не заданы ключи Copernicus "
                "(COPERNICUS_CLIENT_ID / COPERNICUS_CLIENT_SECRET)"
            )
        try:
            response = self._http().post(
                self._auth_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"{self.source_id}: сеть недоступна: {exc}") from exc
        if response.status_code != 200:
            raise SourceUnavailableError(
                f"{self.source_id}: авторизация не удалась (HTTP {response.status_code})"
            )
        payload = response.json()
        self._token = str(payload["access_token"])
        ttl = int(payload.get("expires_in", 300))
        self._token_expires_at = datetime.now(tz=UTC) + timedelta(seconds=max(ttl - 30, 30))
        return self._token

    def _geometry(self, field_id: str) -> dict:
        geometry = self._geometry_lookup(field_id)
        if geometry is None:
            raise DataGapError(f"Нет контура у поля {field_id}")
        return geometry

    # -- каталог (поиск сцен) --------------------------------------------------

    def search_scenes(
        self,
        field_id: str,
        date_from: date,
        date_to: date,
        max_cloud_pct: int = 20,
    ) -> list[SceneMeta]:
        """Поиск сцен L2A, пересекающих контур, с облачностью ≤ max_cloud_pct.

        Фильтр OData: коллекция SENTINEL-2, productType S2MSI2A, пересечение
        footprint с полигоном контура, окно ContentDate, атрибут cloudCover.
        """
        import json

        polygon = self._geometry(field_id)
        # WKT полигона для OData.CSC.Intersects (WGS84, CONTRACTS §5)
        ring = polygon["coordinates"][0]
        wkt_ring = ", ".join(f"{lon} {lat}" for lon, lat in ring)
        geometry_wkt = f"POLYGON(({wkt_ring}))"

        odata_filter = (
            "Collection/Name eq 'SENTINEL-2' and "
            "Attributes/OData.CSC.StringAttribute/any("
            "att:att/Name eq 'productType' and att/OData.CSC.StringAttribute eq 'S2MSI2A') and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;{geometry_wkt}') and "
            f"ContentDate/Start gt {date_from.isoformat()}T00:00:00.000Z and "
            f"ContentDate/End lt {date_to.isoformat()}T23:59:59.999Z and "
            "Attributes/OData.CSC.DoubleAttribute/any("
            "att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute le "
            f"{float(max_cloud_pct)})"
        )
        params: dict[str, str | int] = {
            "$filter": odata_filter,
            "$top": 100,
            "$orderby": "ContentDate/Start asc",
        }

        token = self._ensure_token()
        try:
            response = self._http().get(
                self._search_url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"{self.source_id}: сеть недоступна: {exc}") from exc
        if response.status_code != 200:
            raise _http_error(response.status_code, self._search_url)

        metas: list[SceneMeta] = []
        for item in response.json().get("value", []):
            captured_raw = (item.get("ContentDate") or {}).get("Start")
            if not captured_raw:
                continue
            cloud: float | None = None
            for attribute in item.get("Attributes", ()):  # если каталог вернул атрибуты
                if attribute.get("Name") == "cloudCover" and attribute.get("Value") is not None:
                    cloud = float(attribute["Value"])
                    break
            if cloud is None:
                continue  # без облачности сцена бесполезна (05 §2)
            metas.append(
                SceneMeta(
                    scene_ref=str(item.get("Id", "")),
                    name=item.get("Name"),
                    captured_at=datetime.fromisoformat(
                        captured_raw.replace("Z", "+00:00")
                    ).date(),
                    cloud_pct=cloud,
                    assets=json.loads(item.get("Assets", "{}")) if item.get("Assets") else {},
                )
            )
        return metas

    # -- IF-SATELLITE (05 §2) --------------------------------------------------

    def fetch_scenes(
        self,
        field_id: str,
        index: str,
        date_from: date,
        date_to: date,
        max_cloud_pct: int = 20,
    ) -> list[Scene]:
        """Сцены с вычисленной статистикой индекса по контуру поля (05 §2).

        cloud_pct > max_cloud_pct — сцена не возвращается (не участвует в
        таймлайне, 05 §5); > 60 % дополнительно не сохраняется ingest-слоем.
        """
        if date_from > date_to:
            raise ValueError("date_from позже date_to")
        if index not in INDEX_BANDS:
            raise ValueError(f"Неизвестный индекс {index!r}")
        contour = self._geometry(field_id)
        scenes: list[Scene] = []
        for meta in self.search_scenes(field_id, date_from, date_to, max_cloud_pct):
            scenes.append(self._scene_from_rasters(field_id, index, meta, contour))
        return scenes

    def _scene_from_rasters(
        self, field_id: str, index: str, meta: SceneMeta, contour: dict
    ) -> Scene:
        """Вычислить статистику индекса по каналам сцены и пикселям контура."""
        nir_band, other_band = INDEX_BANDS[index]
        nir, nir_transform, nir_crs = self._raster_loader(meta.scene_ref, nir_band)
        other, other_transform, other_crs = self._raster_loader(meta.scene_ref, other_band)
        if nir.shape != other.shape:
            raise DataQualityError(
                f"Сцена {meta.scene_ref}: каналы {nir_band}/{other_band} разной сетки"
            )
        if nir_transform != other_transform or str(nir_crs) != str(other_crs):
            raise DataQualityError(
                f"Сцена {meta.scene_ref}: каналы {nir_band}/{other_band} в разных проекциях"
            )
        # контур WGS84 -> CRS растра (Sentinel-2 UTM)
        from rasterio.warp import transform_geom

        contour_in_raster_crs = transform_geom("EPSG:4326", str(nir_crs), contour)
        mask = contour_mask(contour_in_raster_crs, nir_transform, nir.shape)
        values = compute_index_array(index, nir, other)
        validate_index_values(index, values[mask])
        index_min, index_avg, index_max = stats_over_mask(values, mask)
        cloud_pct = round(float(meta.cloud_pct), 1)
        return Scene(
            field_id=field_id,
            index=index,
            captured_at=meta.captured_at,
            resolution_m=10,
            index_avg=index_avg,
            index_min=index_min,
            index_max=index_max,
            cloud_pct=cloud_pct,
            source_id=self.source_id,
            trust=round(1.0 - cloud_pct / 100.0, 2),
            composite_days=1,
            scene_ref=meta.scene_ref,
            cloud_method="scene_metadata",
        )


class FakeSentinel2Adapter:
    """Детерминированная заглушка IF-SATELLITE (тесты и демо; сеть не нужна).

    Сцены генерируются по хешу (field_id, дата, индекс): проходят каждые
    revisit_days суток; облачность распределена так, чтобы встречались
    сцены ≤20 %, 20–60 % и >60 % (проверка фильтров 05 §5). Значения NDVI —
    сезонная кривая с «шумом» по хешу (правдоподобно для демо-хозяйства).
    """

    source_id = Sentinel2Adapter.source_id
    capabilities = Sentinel2Adapter.capabilities

    def __init__(
        self,
        *,
        revisit_days: int = 3,
        forced_cloud_pct: float | None = None,
    ) -> None:
        self._revisit_days = max(1, revisit_days)
        self._forced_cloud_pct = forced_cloud_pct

    @staticmethod
    def _hash(seed: str) -> int:
        return int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)

    def _cloud_pct(self, field_id: str, day: date) -> float:
        if self._forced_cloud_pct is not None:
            return float(self._forced_cloud_pct)
        bucket = self._hash(f"{field_id}:{day}:cloud") % 100
        if bucket < 55:
            return float(bucket * 20 // 100)  # 0..11 % — основная масса
        if bucket < 90:
            return 20.0 + (bucket - 55) * 0.7  # ~20..44 %
        return 61.0 + (bucket - 90)  # 61..70 % — не сохраняется (05 §5)

    def _index_value(self, field_id: str, index: str, day: date) -> tuple[float, float, float]:
        """(avg, min, max): сезонная кривая + разброс по хешу."""
        import math

        seasonal = 0.45 + 0.35 * math.sin((day.timetuple().tm_yday - 120) / 365 * 2 * math.pi)
        jitter = ((self._hash(f"{field_id}:{day}:{index}") % 1000) / 1000 - 0.5) * 0.12
        avg = min(max(seasonal + jitter, -1.0), 1.0)
        spread = 0.08 + (self._hash(f"{field_id}:{day}:{index}:spread") % 100) / 1000
        return (
            round(avg, 3),
            round(min(max(avg - spread, -1.0), 1.0), 3),
            round(min(max(avg + spread, -1.0), 1.0), 3),
        )

    def fetch_scenes(
        self,
        field_id: str,
        index: str,
        date_from: date,
        date_to: date,
        max_cloud_pct: int = 20,
    ) -> list[Scene]:
        if date_from > date_to:
            raise ValueError("date_from позже date_to")
        if index not in INDEX_BANDS:
            raise ValueError(f"Неизвестный индекс {index!r}")
        scenes: list[Scene] = []
        day = date_from
        while day <= date_to:
            if self._hash(f"{field_id}:{day}:pass") % self._revisit_days == 0:
                cloud_pct = round(self._cloud_pct(field_id, day), 1)
                if cloud_pct <= max_cloud_pct:
                    avg, lo, hi = self._index_value(field_id, index, day)
                    scenes.append(
                        Scene(
                            field_id=field_id,
                            index=index,
                            captured_at=day,
                            resolution_m=10,
                            index_avg=avg,
                            index_min=lo,
                            index_max=hi,
                            cloud_pct=cloud_pct,
                            source_id=self.source_id,
                            trust=round(1.0 - cloud_pct / 100.0, 2),
                            composite_days=1,
                            scene_ref=f"fake-{self._hash(f'{field_id}:{day}'):08x}",
                            cloud_method="synthetic",
                        )
                    )
            day += timedelta(days=1)
        return scenes


__all__ = [
    "FakeSentinel2Adapter",
    "GeometryLookup",
    "REVISIT_DAYS",
    "RasterLoader",
    "SCENES_SINCE",
    "SceneMeta",
    "Sentinel2Adapter",
]
