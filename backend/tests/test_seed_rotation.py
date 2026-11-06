"""Тесты распределения севооборота по полям (v1-10, задача #66) и API-49.

CLI seed-rotation: для каждого активного поля без плана/циклов — план
approved + циклы по годам со статусами по календарю; RULE-AGRO-01 (без
бан-пар и монокультуры) и RULE-AGRO-02 (возврат не раньше repeat_years);
идемпотентность (повторный запуск — 0 изменений) и детерминированность.
API-49 GET /fields/{id}/crop-cycles: порядок по убыванию года, фильтры
year_from/year_to, color_hex, 404/401.
"""

import uuid
from datetime import UTC, datetime

from geoalchemy2 import Geography
from sqlalchemy import select

from app.models.agronomy import CropCycle, Field, RotationPlan
from app.models.references import PredecessorRule
from app.seed import seed_references
from app.seed_rotation import seed_rotation
from tests.test_auth import auth_header, make_user
from tests.test_fields import square

API = "/api/v1/fields"

CURRENT_YEAR = datetime.now(UTC).year


def make_field(db_session, *, name: str, external_id: str | None = None) -> Field:
    """Поле с квадратным контуром (напрямую ORM — как после SHP-импорта)."""
    field = Field(
        name=name,
        contour=Geography.from_geometry(_square_geo(name)),
        external_id=external_id,
    )
    db_session.add(field)
    db_session.commit()
    return field


def _square_geo(name: str):
    """WKT-полигон 0.01° в районе Воронежа (детерминированно от имени)."""
    from shapely.geometry import shape

    n = abs(hash(name)) % 50
    return shape(square(40.0 + n * 0.02, 51.5 + n * 0.01, 0.01, 0.01)).wkt


def hdr(client, db_session, *, login="agro_rot", role=None) -> dict:
    from app.models.auth import UserRole

    make_user(db_session, login=login, role=role or UserRole.AGRONOMIST)
    return auth_header(client, login, "secret123")


# ---------- seed_rotation ----------


def test_seed_rotation_creates_plan_and_cycles(db_session) -> None:
    seed_references(db_session)
    fields = [make_field(db_session, name=f"Поле-{i}", external_id=f"F-{i:03d}") for i in range(6)]
    report = seed_rotation(db_session, years_back=2, years_forward=2)

    assert report.fields_processed == 6
    assert report.plans_created == 6
    assert report.cycles_created == 6 * 5  # 2 назад + текущий + 2 вперёд

    for field in fields:
        plan = db_session.scalar(
            select(RotationPlan).where(
                RotationPlan.field_id == field.id, RotationPlan.status == "approved"
            )
        )
        assert plan is not None
        assert plan.year_from == CURRENT_YEAR - 2
        assert plan.year_to == CURRENT_YEAR + 2
        assert plan.approved_by  # кем согласован

        cycles = list(
            db_session.scalars(
                select(CropCycle)
                .where(CropCycle.field_id == field.id, CropCycle.kind == "main")
                .order_by(CropCycle.year)
            )
        )
        assert [c.year for c in cycles] == list(range(CURRENT_YEAR - 2, CURRENT_YEAR + 3))
        for cycle in cycles:
            expected = (
                "completed" if cycle.year < CURRENT_YEAR
                else "in_progress" if cycle.year == CURRENT_YEAR
                else "planned"
            )
            assert cycle.status == expected
        # цепочка предшественников связана (RULE-AGRO-04)
        for prev_cycle, cycle in zip(cycles, cycles[1:], strict=False):
            assert cycle.predecessor_cycle_id == prev_cycle.id


def test_seed_rotation_respects_agronomy_rules(db_session) -> None:
    seed_references(db_session)
    fields = [make_field(db_session, name=f"Ротация-{i}", external_id=f"R-{i:03d}") for i in range(8)]
    seed_rotation(db_session, years_back=4, years_forward=4)

    matrix = {
        (r.culture_id, r.predecessor_id): r.verdict
        for r in db_session.scalars(select(PredecessorRule))
    }
    cultures = {c.id: c for c in db_session.scalars(select(Culture := __import__("app.models.references", fromlist=["Culture"]).Culture))}

    for field in fields:
        cycles = list(
            db_session.scalars(
                select(CropCycle)
                .where(CropCycle.field_id == field.id, CropCycle.kind == "main")
                .order_by(CropCycle.year)
            )
        )
        last_year: dict[uuid.UUID, int] = {}
        prev_id = None
        fallow_count = 0
        for cycle in cycles:
            culture = cultures[cycle.culture_id]
            if culture.kind == "fallow":
                fallow_count += 1
            if prev_id is not None:
                # монокультура подряд недопустима всегда
                assert cycle.culture_id != prev_id
                # RULE-AGRO-01: бан-пары матрицы
                assert matrix.get((prev_id, cycle.culture_id)) != "ban"
            # RULE-AGRO-02: возврат не раньше repeat_years
            last = last_year.get(cycle.culture_id)
            if last is not None and cycle.culture_id != prev_id:
                assert cycle.year - last >= culture.repeat_years
            last_year[cycle.culture_id] = cycle.year
            prev_id = cycle.culture_id
        # пар — не чаще раза в ротацию
        assert fallow_count <= 1


def test_seed_rotation_idempotent_and_deterministic(db_session) -> None:
    seed_references(db_session)
    fields = [make_field(db_session, name=f"Идем-{i}", external_id=f"I-{i:03d}") for i in range(4)]
    seed_rotation(db_session, years_back=1, years_forward=1)
    first = {
        f.id: [c.culture_id for c in db_session.scalars(
            select(CropCycle)
            .where(CropCycle.field_id == f.id, CropCycle.kind == "main")
            .order_by(CropCycle.year)
        )]
        for f in fields
    }
    report = seed_rotation(db_session, years_back=1, years_forward=1)
    assert report.fields_processed == 0  # ничего не изменилось
    second = {
        f.id: [c.culture_id for c in db_session.scalars(
            select(CropCycle)
            .where(CropCycle.field_id == f.id, CropCycle.kind == "main")
            .order_by(CropCycle.year)
        )]
        for f in fields
    }
    assert first == second  # и те же культуры (пропуск, не перегенерация)


def test_seed_rotation_skips_fields_with_existing_data(db_session) -> None:
    seed_references(db_session)
    empty = make_field(db_session, name="Пустое", external_id="E-1")
    with_cycle = make_field(db_session, name="С-циклом", external_id="E-2")
    db_session.add(CropCycle(
        field_id=with_cycle.id,
        year=CURRENT_YEAR,
        culture_id=db_session.scalar(select(__import__("app.models.references", fromlist=["Culture"]).Culture.id).where(__import__("app.models.references", fromlist=["Culture"]).Culture.name == "Озимая пшеница")),
        kind="main",
        status="in_progress",
    ))
    with_plan = make_field(db_session, name="С-планом", external_id="E-3")
    db_session.add(RotationPlan(
        field_id=with_plan.id, year_from=CURRENT_YEAR, year_to=CURRENT_YEAR + 4, status="draft"
    ))
    archived_plan_field = make_field(db_session, name="Архив", external_id="E-4")
    db_session.add(RotationPlan(
        field_id=archived_plan_field.id,
        year_from=CURRENT_YEAR,
        year_to=CURRENT_YEAR + 4,
        status="archived",
    ))
    db_session.commit()

    report = seed_rotation(db_session, years_back=1, years_forward=1)
    # обработаны только поле без данных и поле с архивным планом
    assert {empty.id, archived_plan_field.id} <= {f.id for f in [empty, archived_plan_field]}
    assert report.fields_processed == 2
    for field in (with_cycle, with_plan):
        cycles = db_session.scalars(
            select(CropCycle).where(CropCycle.field_id == field.id)
        ).all()
        plans = db_session.scalars(
            select(RotationPlan).where(RotationPlan.field_id == field.id)
        ).all()
        assert len(cycles) == (1 if field is with_cycle else 0)
        assert len(plans) == 1


# ---------- API-49 ----------


def test_api49_history_order_filters_and_color(db_session, client) -> None:
    seed_references(db_session)
    field = make_field(db_session, name="API49", external_id="A49")
    seed_rotation(db_session, years_back=2, years_forward=2)
    h = hdr(client, db_session, login="agro_a49")

    resp = client.get(f"{API}/{field.id}/crop-cycles", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    years = [i["year"] for i in body["items"]]
    assert years == sorted(years, reverse=True)
    for item in body["items"]:
        assert item["culture_name"]
        assert item["kind"] == "main"
        assert item["status"] in ("completed", "in_progress", "planned")
        assert item["color_hex"] and item["color_hex"].startswith("#")

    # фильтры
    resp = client.get(
        f"{API}/{field.id}/crop-cycles",
        params={"year_from": CURRENT_YEAR, "year_to": CURRENT_YEAR + 1},
        headers=h,
    )
    body = resp.json()
    assert body["total"] == 2
    assert {i["year"] for i in body["items"]} == {CURRENT_YEAR, CURRENT_YEAR + 1}


def test_api49_empty_404_and_401(db_session, client) -> None:
    seed_references(db_session)
    field = make_field(db_session, name="Пустая-история", external_id="A49e")
    h = hdr(client, db_session, login="agro_a49e")

    assert client.get(f"{API}/{field.id}/crop-cycles").status_code == 401
    unknown = uuid.uuid4()
    assert client.get(f"{API}/{unknown}/crop-cycles", headers=h).status_code == 404
    resp = client.get(f"{API}/{field.id}/crop-cycles", headers=h)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


def test_api49_machine_operator_allowed(db_session, client) -> None:
    """Права как у карточки поля (API-05): механизатор видит историю."""
    from app.models.auth import UserRole

    seed_references(db_session)
    field = make_field(db_session, name="Мех-история", external_id="A49m")
    seed_rotation(db_session, years_back=1, years_forward=1)
    h = hdr(client, db_session, login="mach_a49", role=UserRole.MACHINE_OPERATOR)
    resp = client.get(f"{API}/{field.id}/crop-cycles", headers=h)
    assert resp.status_code == 200
    assert resp.json()["total"] == 3


# ---------- API-01 current_cycle по текущему году ----------


def test_api01_current_cycle_prefers_current_year(db_session, client) -> None:
    seed_references(db_session)
    field = make_field(db_session, name="Текущий-год", external_id="A01c")
    seed_rotation(db_session, years_back=1, years_forward=3)
    h = hdr(client, db_session, login="agro_a01")
    resp = client.get(API, headers=h)
    items = {i["field_id"]: i["current_cycle"] for i in resp.json()["items"]}
    cycle = items[str(field.id)]
    assert cycle is not None
    assert cycle["year"] == CURRENT_YEAR  # не будущий плановый год
