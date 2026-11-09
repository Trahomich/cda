"""Тесты экономики техработ (§7.9, API-62, #161): стоимости план/факт.

Суммы по операциям журнала цикла (план draft/planned/in_progress, факт
done), unit×га vs total, fallback норматива для факта, null-стоимости не
входят в суммы, руб/т при фактической урожайности и null без неё,
детализация по типам, 404. БД — DATABASE_URL_TEST.
"""

import uuid
from datetime import date

import pytest
from app.models.agronomy import CropCycle, FieldOperation, YieldRecord
from app.models.references import Culture, OperationCost
from fastapi.testclient import TestClient
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_inputs import headers as inputs_headers
from tests.test_inputs import make_culture, make_field

API = "/api/v1"
YEAR = 2026


def make_cycle(db: Session, field_id: uuid.UUID, year: int = YEAR) -> CropCycle:
    cycle = CropCycle(
        field_id=field_id,
        year=year,
        culture_id=make_culture(db, name="Пшеница-эконом").id,
        kind="main",
    )
    db.add(cycle)
    db.flush()
    return cycle


def make_op(
    db: Session,
    field_id: uuid.UUID,
    cycle_id: uuid.UUID,
    op_type: str,
    day: str,
    *,
    status: str,
    area: float = 100.0,
    cost_mode: str | None = None,
    cost_value: float | None = None,
) -> FieldOperation:
    op = FieldOperation(
        field_id=field_id,
        cycle_id=cycle_id,
        operation_type=op_type,
        date=date.fromisoformat(day),
        actual_area_ga=area,
        source="manual",
        status=status,
        cost_mode=cost_mode,
        cost_value=cost_value,
    )
    db.add(op)
    db.flush()
    return op


def seed_costs(db: Session) -> None:
    db.add(OperationCost(operation_type="tillage", rub_per_ga=700.0))
    db.add(OperationCost(operation_type="spraying", rub_per_ga=350.0))
    db.commit()


@pytest.fixture()
def world(client, db_session):
    hdr = inputs_headers(client, db_session)
    seed_costs(db_session)
    return client, hdr, db_session


def _economics(client, hdr, field_id, year=YEAR):
    return client.get(f"{API}/fields/{field_id}/economics", params={"year": year}, headers=hdr)


class TestEconomicsSums:
    def test_plan_and_fact_sums(self, world) -> None:
        """unit×га и total считаются с обеих сторон; руб/га = факт/площадь."""
        client, hdr, db = world
        fid = uuid.UUID(make_field(client, hdr, name="Эконом-1")["field_id"])
        cycle = make_cycle(db, fid)
        # план: вспашка 700 руб/га × 100 га + опрыскивание фикс 5000
        make_op(
            db,
            fid,
            cycle.id,
            "tillage",
            "2026-05-02",
            status="planned",
            cost_mode="unit",
            cost_value=700.0,
        )
        make_op(
            db,
            fid,
            cycle.id,
            "spraying",
            "2026-06-01",
            status="draft",
            cost_mode="total",
            cost_value=5000.0,
        )
        # факт: вспашка выполнена дороже (750 руб/га), опрыскивание — норматив 350×100
        make_op(
            db,
            fid,
            cycle.id,
            "tillage",
            "2026-05-03",
            status="done",
            cost_mode="unit",
            cost_value=750.0,
        )
        make_op(db, fid, cycle.id, "spraying", "2026-06-02", status="done")
        db.commit()

        resp = _economics(client, hdr, fid)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plan_cost_rub"] == 70000.0 + 5000.0  # 75000
        assert body["fact_cost_rub"] == 75000.0 + 35000.0  # 110000
        # площадь поля из make_field
        area = body["area_ga"]
        assert body["cost_per_ga_rub"] == round(110000.0 / area, 2)
        assert body["fact_cost_per_ton_rub"] is None  # урожайности нет
        by_type = {row["type"]: row for row in body["operations"]}
        assert by_type["tillage"]["plan"]["cost_rub"] == 70000.0
        assert by_type["tillage"]["fact"]["cost_rub"] == 75000.0
        assert by_type["tillage"]["fact"]["date"] == "2026-05-03"
        assert by_type["spraying"]["plan"]["cost_rub"] == 5000.0
        assert by_type["spraying"]["fact"]["cost_rub"] == 35000.0  # норматив

    def test_cost_per_ton_with_yield(self, world) -> None:
        """руб/т = факт / (ц/га / 10 × площадь); без урожайности — null."""
        client, hdr, db = world
        fid = uuid.UUID(make_field(client, hdr, name="Эконом-2")["field_id"])
        cycle = make_cycle(db, fid)
        make_op(
            db,
            fid,
            cycle.id,
            "harvesting",
            "2026-08-20",
            status="done",
            cost_mode="total",
            cost_value=120000.0,
        )
        db.add(
            YieldRecord(
                cycle_id=cycle.id,
                yield_c_ha=30.0,
                gross_t=None,
                source="scales",
                date=date(2026, 9, 1),
            )
        )
        db.commit()
        body = _economics(client, hdr, fid).json()
        area = body["area_ga"]
        tons = 30.0 / 10 * area
        assert body["fact_cost_per_ton_rub"] == round(120000.0 / tons, 2)
        assert body["yield_c_ha"] == 30.0

    def test_null_costs_excluded(self, world) -> None:
        """Операции без стоимости (и без норматива для плана) — в суммы
        не входят, в детализации со стоимостью 0."""
        client, hdr, db = world
        db.execute(db_text("DELETE FROM ref_operation_cost"))  # нормативов нет
        db.commit()
        fid = uuid.UUID(make_field(client, hdr, name="Эконом-3")["field_id"])
        cycle = make_cycle(db, fid)
        make_op(db, fid, cycle.id, "other", "2026-07-01", status="planned")  # без стоимости
        make_op(db, fid, cycle.id, "tillage", "2026-05-01", status="done")  # факт без норматива
        db.commit()
        body = _economics(client, hdr, fid).json()
        assert body["plan_cost_rub"] == 0.0 and body["fact_cost_rub"] == 0.0
        assert body["operations"][0]["plan"]["cost_rub"] == 0.0

    def test_fact_without_plan_and_vice_versa(self, world) -> None:
        """План без факта → fact=null; факт без плана → plan=null."""
        client, hdr, db = world
        fid = uuid.UUID(make_field(client, hdr, name="Эконом-4")["field_id"])
        cycle = make_cycle(db, fid)
        make_op(
            db,
            fid,
            cycle.id,
            "seeding",
            "2026-04-20",
            status="planned",
            cost_mode="total",
            cost_value=90000.0,
        )  # не выполнено
        make_op(db, fid, cycle.id, "spraying", "2026-06-11", status="done")  # без плана
        db.commit()
        body = _economics(client, hdr, fid).json()
        by_type = {row["type"]: row for row in body["operations"]}
        assert by_type["seeding"]["fact"] is None and by_type["seeding"]["plan"]
        assert by_type["spraying"]["plan"] is None and by_type["spraying"]["fact"]

    def test_cancelled_and_other_cycles_excluded(self, world) -> None:
        client, hdr, db = world
        fid = uuid.UUID(make_field(client, hdr, name="Эконом-5")["field_id"])
        cycle = make_cycle(db, fid)
        other = make_cycle(db, fid, year=2025)
        make_op(
            db,
            fid,
            cycle.id,
            "tillage",
            "2026-05-01",
            status="cancelled",
            cost_mode="total",
            cost_value=99999.0,
        )
        make_op(
            db,
            fid,
            other.id,
            "tillage",
            "2025-05-01",
            status="done",
            cost_mode="total",
            cost_value=88888.0,
        )
        db.commit()
        body = _economics(client, hdr, fid).json()
        assert body["plan_cost_rub"] == 0.0 and body["fact_cost_rub"] == 0.0
        assert body["operations"] == []

    def test_year_without_cycle_empty(self, world) -> None:
        client, hdr, db = world
        fid = uuid.UUID(make_field(client, hdr, name="Эконом-6")["field_id"])
        db.commit()
        resp = _economics(client, hdr, fid, year=2030)
        assert resp.status_code == 200
        body = resp.json()
        assert body["operations"] == [] and body["plan_cost_rub"] == 0.0
        assert body["yield_c_ha"] is None

    def test_unknown_field_404(self, world) -> None:
        client, hdr, _ = world
        resp = client.get(
            f"{API}/fields/{uuid.uuid4()}/economics", params={"year": 2026}, headers=hdr
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "field_not_found"
