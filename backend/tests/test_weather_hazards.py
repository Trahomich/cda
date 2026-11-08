"""Тесты опасных погодных явлений по прогнозу (#139, docs/06 §7.8).

Юнит day_hazards: пороги frost/high_wind/heavy_rain, сезонность заморозка
(вне вегетационного окна культуры — info). API-07/29: каждый день прогноза
содержит hazards (пустой массив, если явлений нет) — обратная совместимость.
"""

import uuid
from datetime import date

from app.modules.weather.hazards import (
    FROST_WARN_C,
    HEAVY_RAIN_MM,
    HIGH_WIND_MS,
    day_hazards,
)
from app.models.agronomy import CropCycle
from tests.test_inputs import make_culture
from tests.test_weather import api_forecast_rows, api_headers, make_field

API = "/api/v1"
WINTER = "Пшеница озимая"
SUMMER = "Ячмень"


def _codes(hazards: list[dict]) -> list[tuple[str, str]]:
    return [(h["code"], h["severity"]) for h in hazards]


class TestDayHazards:
    """Юнит-правила: пороги и сезонность (#139)."""

    def test_frost_thresholds_in_season(self) -> None:
        """Ноябрь, озимая (окно 9–11): +1 °C — warn; −1 и −6 °C — alert."""
        nov = date(2026, 11, 10)
        assert _codes(day_hazards(nov, 1.0, None, None, WINTER)) == [("frost", "warn")]
        assert _codes(day_hazards(nov, -1.0, None, None, WINTER)) == [("frost", "alert")]
        severe = day_hazards(nov, -6.0, None, None, WINTER)
        assert _codes(severe) == [("frost", "alert")]
        assert "−6" in severe[0]["text"]  # типографский минус

    def test_frost_out_of_season_is_info(self) -> None:
        """Ноябрь вне окна обычной культуры (4–10) и без культуры — info."""
        nov = date(2026, 11, 10)
        assert _codes(day_hazards(nov, -1.0, None, None, SUMMER)) == [("frost", "info")]
        assert _codes(day_hazards(nov, 1.0, None, None, None)) == [("frost", "info")]
        # апрель — окно обычной культуры: тот же мороз уже alert
        assert _codes(day_hazards(date(2026, 4, 10), -1.0, None, None, SUMMER)) == [
            ("frost", "alert")
        ]

    def test_wind_and_rain_thresholds(self) -> None:
        day = date(2026, 5, 20)
        assert day_hazards(day, 10.0, 14.9, 14.9, SUMMER) == []
        assert _codes(day_hazards(day, 10.0, HEAVY_RAIN_MM, HIGH_WIND_MS, SUMMER)) == [
            ("high_wind", "warn"),
            ("heavy_rain", "warn"),
        ]

    def test_null_metrics_no_hazards(self) -> None:
        assert day_hazards(date(2026, 6, 1), None, None, None, SUMMER) == []

    def test_frost_warn_boundary(self) -> None:
        """Границы: tmin = 2.0 — warn; 2.1 — явления нет."""
        day = date(2026, 10, 1)
        assert _codes(day_hazards(day, FROST_WARN_C, None, None, SUMMER)) == [("frost", "warn")]
        assert day_hazards(day, FROST_WARN_C + 0.1, None, None, SUMMER) == []


class TestHazardsApi:
    """API-07/API-29 отдают hazards по каждому дню (#139)."""

    def test_api07_forecast_hazards(self, client, db_session):
        """Озимая (цикл сезона) + прогноз −2 °C → frost alert в API-07."""
        field_id = make_field(db_session)
        culture = make_culture(db_session, name=WINTER)
        # main-цикл года уборки 2027 (сейчас ноябрь → ищем year+1, потом year)
        db_session.add(CropCycle(field_id=field_id, year=2027, culture_id=culture.id, kind="main"))
        db_session.commit()
        api_forecast_rows(db_session, field_id, [(0, -1.0, -2.0, 0.0), (1, 5.0, 1.0, 0.0)])
        resp = client.get(
            f"{API}/fields/{field_id}/weather", headers=api_headers(client, db_session)
        )
        assert resp.status_code == 200, resp.text
        forecast = resp.json()["forecast"]
        by_code = {h["code"]: h["severity"] for h in forecast[0]["hazards"]}
        assert by_code.get("frost") == "alert"  # −2 °C, ноябрь — окно озимых
        assert forecast[1]["hazards"][0]["severity"] == "warn"  # +1 °C

    def test_api29_hazards_empty_list_back_compat(self, client, db_session):
        """Без ОЯ hazards — пустой массив (поле присутствует всегда)."""
        field_id = make_field(db_session)
        api_forecast_rows(db_session, field_id, [(0, 18.0, 10.0, 1.0)])
        resp = client.get(
            f"{API}/weather/forecast",
            params={"field_id": str(field_id)},
            headers=api_headers(client, db_session),
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1 and items[0]["hazards"] == []
