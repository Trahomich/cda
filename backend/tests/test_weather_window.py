"""Тесты WEATHER-WINDOW для планов spraying (docs/06 §7.2, задача #74).

POST /treatment-plans (API-15) и POST /treatment-plans/{id}/approve (API-16)
для spraying дополняются объектом weather_window по кэшу dat_forecast:
{ok, detail, suggested_dates} | null. Проверка не блокирующая (422 остаются
только у RULE-AGRO-07/08/10). Тестовый контур — PostgreSQL (DATABASE_URL_TEST).
"""

import uuid
from datetime import UTC, date, datetime

from app.models.integrations import Forecast
from tests.test_inputs import (
    headers,
    make_cycle,
    make_field,
    make_ppp,
    post_plan,
)

API_APPROVE = "/api/v1/treatment-plans/{id}/approve"


def make_forecast(
    db_session,
    *,
    field_id: uuid.UUID,
    day: date,
    t_min: float = 12.0,
    t_max: float = 20.0,
    wind: float = 3.0,
    precip_prob: int | None = 20,
) -> Forecast:
    """Сутки кэша прогноза (dat_forecast); precip_prob=None — провайдер не отдал."""
    fc = Forecast(
        field_id=field_id,
        forecast_for=day,
        issued_at=datetime.now(UTC),
        temperature_min_c=t_min,
        temperature_max_c=t_max,
        wind_max_ms=wind,
        precip_prob_pct=precip_prob,
    )
    db_session.add(fc)
    db_session.commit()
    return fc


PLANNED = date(2027, 5, 20)


def test_post_bad_day_ok_false_with_detail_and_suggestions(client, db_session) -> None:
    """planned_date вне окна: ok=false, detail с причинами и альтернативы."""
    hdr = headers(client, db_session)
    field = make_field(client, hdr)
    cycle = make_cycle(db_session, field=field, year=2027)
    item = make_ppp(db_session)
    fid = uuid.UUID(field["field_id"])

    make_forecast(db_session, field_id=fid, day=PLANNED, t_min=10.0, t_max=42.0, wind=6.2,
                  precip_prob=70)  # все три критерия нарушены
    # горизон planned+1..+6: ветер/осадки ок через день
    for delta, wind in ((1, 5.0), (2, 3.5), (3, 3.0), (4, 4.5), (5, 2.0), (6, 3.0)):
        make_forecast(db_session, field_id=fid, day=PLANNED.replace(day=PLANNED.day + delta),
                      wind=wind)

    resp = post_plan(client, hdr, cycle, item, planned_date=PLANNED.isoformat())
    assert resp.status_code == 201, resp.text  # окно НЕ блокирует
    win = resp.json()["weather_window"]
    assert win is not None
    assert win["ok"] is False
    detail = win["detail"]
    assert detail.startswith("2027-05-20: ")
    assert "Tср" in detail and "(вне 5–25)" in detail
    assert "ветер 6.2 м/с (>4)" in detail
    assert "осадки 70 % (≥50)" in detail
    # до 5 подходящих дат из 7 суток по возрастанию (+2, +3, +5, +6 — ветер ≤4)
    assert win["suggested_dates"] == ["2027-05-22", "2027-05-23", "2027-05-25", "2027-05-26"]


def test_post_good_day_ok_true(client, db_session) -> None:
    """planned_date в окне: ok=true, detail пуст."""
    hdr = headers(client, db_session)
    field = make_field(client, hdr)
    cycle = make_cycle(db_session, field=field, year=2027)
    item = make_ppp(db_session)
    make_forecast(db_session, field_id=uuid.UUID(field["field_id"]), day=PLANNED)

    resp = post_plan(client, hdr, cycle, item, planned_date=PLANNED.isoformat())
    assert resp.status_code == 201, resp.text
    win = resp.json()["weather_window"]
    assert win["ok"] is True
    assert win["detail"] == ""
    assert PLANNED.isoformat() in win["suggested_dates"]


def test_null_metrics_do_not_block(client, db_session) -> None:
    """null-метрики (провайдер не отдал) не нарушают окно (tolerate null, §7.2)."""
    hdr = headers(client, db_session)
    field = make_field(client, hdr)
    cycle = make_cycle(db_session, field=field, year=2027)
    item = make_ppp(db_session)
    make_forecast(db_session, field_id=uuid.UUID(field["field_id"]), day=PLANNED,
                  t_min=10.0, t_max=None, wind=None, precip_prob=None)

    resp = post_plan(client, hdr, cycle, item, planned_date=PLANNED.isoformat())
    assert resp.status_code == 201, resp.text
    win = resp.json()["weather_window"]
    assert win["ok"] is True
    assert win["suggested_dates"] == [PLANNED.isoformat()]


def test_no_forecast_on_date_window_null(client, db_session) -> None:
    """Нет записи прогноза на planned_date → weather_window: null (не ошибка)."""
    hdr = headers(client, db_session)
    field = make_field(client, hdr)
    cycle = make_cycle(db_session, field=field, year=2027)
    item = make_ppp(db_session)
    # прогноз есть, но только на другие сутки
    make_forecast(db_session, field_id=uuid.UUID(field["field_id"]),
                  day=PLANNED.replace(day=PLANNED.day + 2))

    resp = post_plan(client, hdr, cycle, item, planned_date=PLANNED.isoformat())
    assert resp.status_code == 201, resp.text
    assert resp.json()["weather_window"] is None


def test_not_spraying_no_window(client, db_session) -> None:
    """fertilizing — окно не вычисляется (weather_window: null)."""
    hdr = headers(client, db_session)
    field = make_field(client, hdr)
    cycle = make_cycle(db_session, field=field, year=2027)
    item = make_ppp(db_session)
    make_forecast(db_session, field_id=uuid.UUID(field["field_id"]), day=PLANNED)

    resp = post_plan(client, hdr, cycle, item, operation_type="fertilizing")
    assert resp.status_code == 201, resp.text
    assert resp.json()["weather_window"] is None


def test_approve_same_window_as_post(client, db_session) -> None:
    """API-16: approve возвращает то же окно, что POST (единая логика §7.2)."""
    hdr = headers(client, db_session)
    field = make_field(client, hdr)
    cycle = make_cycle(db_session, field=field, year=2027)
    item = make_ppp(db_session)
    make_forecast(db_session, field_id=uuid.UUID(field["field_id"]), day=PLANNED,
                  wind=6.0, precip_prob=60)
    make_forecast(db_session, field_id=uuid.UUID(field["field_id"]),
                  day=PLANNED.replace(day=PLANNED.day + 3), wind=2.0)

    resp = post_plan(client, hdr, cycle, item, planned_date=PLANNED.isoformat())
    assert resp.status_code == 201, resp.text
    posted = resp.json()["weather_window"]

    mgr_hdr = headers(client, db_session, login="mgr_ww2", role=UserRole.MANAGER)
    plan_id = resp.json()["plan_id"]
    approved = client.post(API_APPROVE.format(id=plan_id), headers=mgr_hdr)
    assert approved.status_code == 200, approved.text
    win = approved.json()["weather_window"]
    assert win == posted  # одинаковая логика POST и approve
    assert win["ok"] is False
    assert win["suggested_dates"] == [PLANNED.replace(day=PLANNED.day + 3).isoformat()]
