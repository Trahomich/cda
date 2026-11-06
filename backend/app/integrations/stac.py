"""Адаптер каталога STAC earth-search (Sentinel-2 L2A, docs/05 §2, v1-04 #50).

Источник: https://earth-search.aws.element84.com/v1, коллекция sentinel-2-l2a,
анонимный доступ без ключей (решение v1, ADR-09). Активы — COG на S3: сцены
не скачиваются, rasterio читает окно контура по HTTP (задача #51); здесь
хранятся только ссылки (item.assets).

Облачность фильтруется по eo:cloud_cover плашки снимка (метаданные каталога);
честная облачность по полезной площади (маска SCL) считается на этапе NDVI (#51)
и переопределяет каталогизованную при сохранении сцены (05 §2, оговорка
«облачность по полезной площади поля»).

Ошибки (CONTRACTS §7): HTTP 5xx/429/сеть — SourceUnavailableError (ретраи по
планировщику), пустой результат — нормальное состояние (DataGapError не нужен:
возвращаем пустой список).
"""

import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.integrations import SourceUnavailableError

logger = logging.getLogger(__name__)

SATELLITE_SOURCE_ID = "satellite.sentinel2-l2a"
COLLECTION = "sentinel-2-l2a"

#: каналы, нужные для NDVI и маски облаков (05 §2: B04, B08, SCL);
#: ключ актива — каноническое имя, значения — возможные имена в каталоге
#: (earth-search отдаёт ESA-имена red/nir, Copernicus — b04/b08)
CATALOG_ASSETS: dict[str, tuple[str, ...]] = {
    "b04": ("b04", "red"),
    "b08": ("b08", "nir"),
    "scl": ("scl",),
}

#: верхний порог облачности каталогизации: >60 % сцена не сохраняется (05 §5 п.2)
MAX_CATALOG_CLOUD_PCT = 60.0

#: максимум сцен в одном ответе каталога (лимит earth-search: 500)
PAGE_LIMIT = 200

#: предохранитель от бесконечной пагинации
MAX_PAGES = 10


@dataclass(frozen=True, slots=True)
class StacItem:
    """Нормализованный item каталога STAC (до сопоставления с полями)."""

    item_id: str
    captured_at: date
    cloud_pct: float
    tile_id: str  # MGRS, например 37UFT
    geometry: dict  # GeoJSON geometry (footprint плашки, WGS84)
    assets: dict[str, str] = field(default_factory=dict)  # band -> href

    def asset(self, band: str) -> str | None:
        return self.assets.get(band)


def _http_error(status_code: int) -> Exception:
    if status_code >= 500 or status_code in (408, 429):
        return SourceUnavailableError(
            f"{SATELLITE_SOURCE_ID}: STAC недоступен (HTTP {status_code})"
        )
    # 4xx при корректном запросе — ошибка клиента: не ретраить молча
    return SourceUnavailableError(
        f"{SATELLITE_SOURCE_ID}: неожиданный ответ STAC (HTTP {status_code})"
    )


class EarthSearchStacClient:
    """Поиск сцен L2A по bbox хозяйства (source_id="satellite.sentinel2-l2a")."""

    source_id = SATELLITE_SOURCE_ID

    def __init__(
        self,
        *,
        base_url: str = "https://earth-search.aws.element84.com/v1",
        http: httpx.Client | None = None,
        request_timeout: float = 30.0,
        max_cloud_pct: float = MAX_CATALOG_CLOUD_PCT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http_client = http
        self._timeout = request_timeout
        self._max_cloud_pct = max_cloud_pct

    def _http(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def search(
        self,
        bbox: list[float],
        date_from: date,
        date_to: date,
        *,
        max_cloud_pct: float | None = None,
    ) -> list[StacItem]:
        """Сцены коллекции sentinel-2-l2a в bbox за период, облачность ≤ порога.

        GET-пагинация по link rel=next (earth-search отдаёт token в ссылке).
        """
        threshold = self._max_cloud_pct if max_cloud_pct is None else max_cloud_pct
        params: dict[str, object] = {
            "collections": [COLLECTION],
            "bbox": list(bbox),
            "datetime": f"{date_from.isoformat()}T00:00:00Z/{date_to.isoformat()}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lte": threshold}},
            "limit": PAGE_LIMIT,
        }
        items: list[StacItem] = []
        url: str | None = f"{self._base_url}/search"
        pages = 0
        while url and pages < MAX_PAGES:
            try:
                response = self._http().post(url, json=params)
            except httpx.HTTPError as exc:
                raise SourceUnavailableError(
                    f"{SATELLITE_SOURCE_ID}: сеть недоступна: {exc}"
                ) from exc
            if response.status_code != 200:
                raise _http_error(response.status_code)
            payload = response.json()
            items.extend(self._parse(payload))
            pages += 1
            url = next(
                (link["href"] for link in payload.get("links", []) if link.get("rel") == "next"),
                None,
            )
            params = {}  # next-ссылка уже содержит query (GET-форма от каталога)
        return items

    @staticmethod
    def _parse(payload: dict) -> list[StacItem]:
        parsed: list[StacItem] = []
        for feature in payload.get("features", []):
            props = feature.get("properties") or {}
            captured_raw = (props.get("datetime") or "")[:10]
            cloud = props.get("eo:cloud_cover")
            if not captured_raw or cloud is None:
                continue  # без даты/облачности item бесполезен (05 §2)
            year, month, day = (int(part) for part in captured_raw.split("-"))
            tile = (props.get("s2:mgrs_tile") or props.get("grid:code") or "").replace("MGRS-", "")
            parsed.append(
                StacItem(
                    item_id=str(feature.get("id", "")),
                    captured_at=date(year, month, day),
                    cloud_pct=float(cloud),
                    tile_id=tile,
                    geometry=feature.get("geometry") or {},
                    assets=_catalog_assets(feature.get("assets") or {}),
                )
            )
        return parsed


class FakeStacClient:
    """Заглушка каталога для тестов и dev-режима (CONTRACTS §7): без сети.

    Возвращает фиксированные item'ы для любого запроса (геометрию задают
    тесты); повторный вызов — те же данные (идемпотентность каталогизации
    проверяется на одном наборе).
    """

    source_id = SATELLITE_SOURCE_ID

    def __init__(self, items: list[StacItem]) -> None:
        self.items = items
        self.calls = 0

    def search(
        self,
        bbox: list[float],
        date_from: date,
        date_to: date,
        *,
        max_cloud_pct: float | None = None,
    ) -> list[StacItem]:
        self.calls += 1
        threshold = MAX_CATALOG_CLOUD_PCT if max_cloud_pct is None else max_cloud_pct
        return [i for i in self.items if i.cloud_pct <= threshold]


__all__ = [
    "CATALOG_ASSETS",
    "COLLECTION",
    "EarthSearchStacClient",
    "FakeStacClient",
    "MAX_CATALOG_CLOUD_PCT",
    "SATELLITE_SOURCE_ID",
    "StacItem",
]
