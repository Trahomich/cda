"""Тесты модуля препаратов (задача MVP-11, API-14..16).

Проверяется: RULE-AGRO-07/08/10 на чистых функциях и через API (422
rule_violation с rule_id/severity/rationale/entity — RFC 7807 + доменные
расширения), справочник препаратов (валидация kind-полей, дубли 409),
согласование планов (API-16) с перепроверкой правил и аудитом, RBAC
(группа inputs; approve — manager/admin). Тестовый контур — PostgreSQL+PostGIS
(DATABASE_URL_TEST, паттерн #20).
"""

import uuid
from datetime import date

from app.models.agronomy import AgroInput, CropCycle, TreatmentPlan
from app.models.auth import AuditRecord, UserRole
from app.models.references import Culture
from app.modules.inputs import rules
from sqlalchemy import select
from tests.test_auth import auth_header, make_user
from tests.test_fields import square

API_INPUTS = "/api/v1/agro-inputs"
API_PLANS = "/api/v1/treatment-plans"


# ---------- утилиты ----------


def headers(client, db_session, *, login="agro_i", role=UserRole.AGRONOMIST) -> dict:
    make_user(db_session, login=login, role=role)
    return auth_header(client, login, "secret123")


def make_culture(db_session, *, name: str = "Пшеница", kind: str = "main") -> Culture:
    culture = Culture(
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        kind=kind,
        forage_flag=False,
        crop_group="test",
    )
    db_session.add(culture)
    db_session.commit()
    return culture


def make_field(client, hdr, *, name: str = "Поле-1") -> dict:
    resp = client.post(
        "/api/v1/fields",
        json={"name": f"{name}-{uuid.uuid4().hex[:6]}", "contour": square(41.0, 0.0, 0.01, 0.01)},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_cycle(
    db_session,
    *,
    field: dict,
    culture: Culture,
    year: int = 2027,
    harvest: date | None = date(2027, 7, 20),
) -> CropCycle:
    cycle = CropCycle(
        field_id=uuid.UUID(field["field_id"]),
        year=year,
        culture_id=culture.id,
        kind="main",
        planned_harvest_date=harvest,
    )
    db_session.add(cycle)
    db_session.commit()
    return cycle


def make_fertilizer(db_session, *, name="Аммиачная селитра", n_pct=34.0, unit="kg_ha") -> AgroInput:
    item = AgroInput(
        kind="fertilizer", name=f"{name}-{uuid.uuid4().hex[:6]}", n_pct=n_pct, unit=unit
    )
    db_session.add(item)
    db_session.commit()
    return item


def make_ppp(
    db_session,
    *,
    name="Фюзилад",
    substance="флуазифоп",
    waiting_days=20,
    approved_crops=None,
    max_treatments=2,
    unit="l_ha",
) -> AgroInput:
    item = AgroInput(
        kind="ppp",
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        active_substance=substance,
        unit=unit,
        waiting_days=waiting_days,
        approved_crops=approved_crops or [],
        max_treatments_per_season=max_treatments,
    )
    db_session.add(item)
    db_session.commit()
    return item


def post_plan(client, hdr, cycle, item, **overrides) -> object:
    body = {
        "cycle_id": str(cycle.id),
        "input_id": str(item.id),
        "dose": 1.0,
        "operation_type": "spraying",
        "planned_date": "2027-05-20",
    }
    body.update(overrides)
    return client.post(API_PLANS, json=body, headers=hdr)


# ---------- чистые функции правил ----------


class TestRulesUnit:
    def test_nitrogen_kg_dv_ha(self):
        assert rules.nitrogen_kg_dv_ha(100, "kg_dv_ha", None) == 100.0
        assert rules.nitrogen_kg_dv_ha(100, "kg_ha", 34.0) == 34.0
        assert rules.nitrogen_kg_dv_ha(50, "l_ha", 28.0) == 14.0
        assert rules.nitrogen_kg_dv_ha(100, "kg_ha", None) == 0.0
        assert rules.nitrogen_kg_dv_ha(-5, "kg_ha", 34.0) == 0.0

    def test_check_n_season(self):
        assert rules.check_n_season(current_n=100, new_n=80, input_name="X") is None  # ровно 180 — граница
        assert rules.check_n_season(current_n=100, new_n=81, input_name="X") is not None
        assert rules.check_n_season(current_n=100, new_n=80, limit=200, input_name="X") is None
        v = rules.check_n_season(current_n=0, new_n=181, input_name="X")
        assert v is not None and v.rule_id == "RULE-AGRO-07" and v.severity == "ban"

    def test_check_waiting_days(self):
        assert (
            rules.check_waiting_days(
                planned_date=date(2027, 6, 30),
                harvest_date=date(2027, 7, 20),
                waiting_days=20,
                input_name="W",
                culture_name="Пшеница",
            )
            is None
        )
        v = rules.check_waiting_days(
            planned_date=date(2027, 7, 11),  # осталось 9 дн. < 20
            harvest_date=date(2027, 7, 20),
            waiting_days=20,
            input_name="W",
            culture_name="Пшеница",
        )
        assert v is not None and v.rule_id == "RULE-AGRO-08" and "9 дн." in v.rationale
        # без срока ожидания / без даты уборки правило не применяется
        assert (
            rules.check_waiting_days(
                planned_date=date(2027, 7, 19),
                harvest_date=date(2027, 7, 20),
                waiting_days=None,
                input_name="W",
                culture_name="П",
            )
            is None
        )

    def test_check_max_treatments(self):
        assert (
            rules.check_max_treatments(count=1, max_treatments=2, substance="S", input_name="W")
            is None
        )
        v = rules.check_max_treatments(count=2, max_treatments=2, substance="S", input_name="W")
        assert v is not None and v.rule_id == "RULE-AGRO-10" and "S" in v.rationale


# ---------- API-14: справочник препаратов ----------


class TestAgroInputs:
    def test_create_fertilizer_201_and_location(self, client, db_session):
        hdr = headers(client, db_session)
        resp = client.post(
            API_INPUTS,
            json={
                "kind": "fertilizer",
                "name": "Карбамид",
                "npk_content": [46.0, 0.0, 0.0],
                "unit": "kg_ha",
            },
            headers=hdr,
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers["location"].startswith("/api/v1/agro-inputs/")
        body = resp.json()
        assert body["kind"] == "fertilizer"
        assert body["npk_content"] == [46.0, 0.0, 0.0]
        assert body["waiting_days"] is None
        assert body["max_treatments_per_season"] == 2  # default (03 §1.4)
        # аудит создания справочника
        rec = db_session.scalar(select(AuditRecord).where(AuditRecord.entity == "agro_input"))
        assert rec is not None and rec.action == "create"

    def test_create_ppp_with_limits(self, client, db_session):
        hdr = headers(client, db_session)
        culture = make_culture(db_session)
        resp = client.post(
            API_INPUTS,
            json={
                "kind": "ppp",
                "name": "Би-58",
                "active_substance": "диметоат",
                "concentration": 40.0,
                "unit": "l_ha",
                "waiting_days": 30,
                "approved_crops": [str(culture.id)],
                "max_treatments_per_season": 1,
            },
            headers=hdr,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["active_substance"] == "диметоат"
        assert body["waiting_days"] == 30
        assert body["approved_crops"] == [str(culture.id)]
        assert body["npk_content"] is None

    def test_fertilizer_with_waiting_days_400(self, client, db_session):
        hdr = headers(client, db_session)
        resp = client.post(
            API_INPUTS,
            json={"kind": "fertilizer", "name": "NPK", "unit": "kg_ha", "waiting_days": 5},
            headers=hdr,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_kind_fields"

    def test_ppp_with_npk_400(self, client, db_session):
        hdr = headers(client, db_session)
        resp = client.post(
            API_INPUTS,
            json={"kind": "ppp", "name": "Глифовит", "unit": "l_ha", "npk_content": [1, 2, 3]},
            headers=hdr,
        )
        assert resp.status_code == 400

    def test_duplicate_name_409(self, client, db_session):
        hdr = headers(client, db_session)
        payload = {"kind": "fertilizer", "name": "Суперфосфат", "unit": "kg_ha"}
        assert client.post(API_INPUTS, json=payload, headers=hdr).status_code == 201
        resp = client.post(API_INPUTS, json=payload, headers=hdr)
        assert resp.status_code == 409
        assert resp.json()["code"] == "duplicate_input"

    def test_list_filters_and_rbac(self, client, db_session):
        hdr = headers(client, db_session)
        client.post(
            API_INPUTS,
            json={"kind": "fertilizer", "name": "Мочевина", "unit": "kg_ha"},
            headers=hdr,
        )
        client.post(
            API_INPUTS,
            json={"kind": "ppp", "name": "Логран", "unit": "kg_ha"},
            headers=hdr,
        )
        resp = client.get(API_INPUTS, params={"kind": "ppp"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1 and body["items"][0]["name"] == "Логран"
        resp = client.get(API_INPUTS, params={"q": "мочев"})
        assert resp.json()["total"] == 1
        # machine_operator не входит в группу inputs (07-ux §5: E-05 — А, Р, Д)
        make_user(db_session, login="op_i", role=UserRole.MACHINE_OPERATOR)
        resp = client.get(API_INPUTS, headers=auth_header(client, "op_i", "secret123"))
        assert resp.status_code == 403


# ---------- API-15: планы внесения, RULE-AGRO-07/08/10 ----------


class TestTreatmentPlansCreate:
    def test_create_draft_201(self, client, db_session):
        hdr = headers(client, db_session)
        field = make_field(client, hdr)
        cycle = make_cycle(db_session, field=field)
        item = make_ppp(db_session, waiting_days=20)
        resp = post_plan(
            client, hdr, cycle, item, planned_date="2027-06-15", operation_type="spraying"
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers["location"].startswith("/api/v1/treatment-plans/")
        body = resp.json()
        assert body["status"] == "draft"
        # развёрнутые имена для E-05 (frontend TreatmentPlan)
        assert body["field_name"].startswith("Поле-1")
        assert body["year"] == 2027
        assert body["culture_name"].startswith("Пшеница")
        assert body["input_name"].startswith("Фюзилад")
        assert body["unit"] == "l_ha"

    def test_negative_dose_422_rule_07(self, client, db_session):
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        item = make_ppp(db_session)
        resp = post_plan(client, hdr, cycle, item, dose=-1.5)
        assert resp.status_code == 422
        problem = resp.json()
        assert problem["code"] == "rule_violation"
        assert problem["rule_id"] == "RULE-AGRO-07"
        assert problem["entity"] == "treatment_plan"

    def test_n_season_limit_422_rule_07(self, client, db_session):
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        fert = make_fertilizer(db_session, n_pct=46.0)  # 200 кг/га → 92 кг д.в. N
        assert (
            post_plan(client, hdr, cycle, fert, dose=200, operation_type="fertilizing").status_code
            == 201
        )
        resp = post_plan(client, hdr, cycle, fert, dose=200, operation_type="fertilizing")
        assert resp.status_code == 422
        problem = resp.json()
        assert problem["rule_id"] == "RULE-AGRO-07"
        assert "= 184.0 кг д.в./га" in problem["detail"]
        # 180 − 92 = 88 кг д.в. → 191 кг препарата проходят
        ok = post_plan(client, hdr, cycle, fert, dose=191, operation_type="fertilizing")
        assert ok.status_code == 201

    def test_waiting_days_422_rule_08_and_pass(self, client, db_session):
        hdr = headers(client, db_session)
        field = make_field(client, hdr)
        cycle = make_cycle(db_session, field=field, harvest=date(2027, 7, 20))
        item = make_ppp(db_session, waiting_days=20)  # ≥ 30.06.2027
        resp = post_plan(client, hdr, cycle, item, planned_date="2027-07-11")  # 9 дн.
        assert resp.status_code == 422
        problem = resp.json()
        assert problem["rule_id"] == "RULE-AGRO-08"
        assert problem["severity"] == "ban"
        # раньше — можно
        ok = post_plan(client, hdr, cycle, item, planned_date="2027-06-30")
        assert ok.status_code == 201
        # тем же препаратом не spraying — правило не применяется (03 §3.3)
        ok2 = post_plan(
            client, hdr, cycle, item, planned_date="2027-07-11", operation_type="fertilizing"
        )
        assert ok2.status_code == 201

    def test_approved_crops_422_rule_10(self, client, db_session):
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        other = make_culture(db_session, name="Горох")
        item = make_ppp(db_session, approved_crops=[other.id])  # пшеница не входит
        resp = post_plan(client, hdr, cycle, item)
        assert resp.status_code == 422
        problem = resp.json()
        assert problem["rule_id"] == "RULE-AGRO-10"
        # пусто = без ограничений (03 §1.4)
        free = make_ppp(db_session, name="Широкого спектра", approved_crops=[])
        assert post_plan(client, hdr, cycle, free).status_code == 201

    def test_max_treatments_same_substance_422_rule_10(self, client, db_session):
        hdr = headers(client, db_session)
        field = make_field(client, hdr)
        cycle = make_cycle(db_session, field=field)
        first = make_ppp(db_session, name="Тарга", substance="флуазифоп", max_treatments=2)
        second = make_ppp(db_session, name="Тарга Супер", substance="флуазифоп", max_treatments=2)
        assert post_plan(client, hdr, cycle, first).status_code == 201
        assert post_plan(client, hdr, cycle, second).status_code == 201  # 2 из 2
        resp = post_plan(client, hdr, cycle, first)  # третья обработка тем же д.в.
        assert resp.status_code == 422
        problem = resp.json()
        assert problem["rule_id"] == "RULE-AGRO-10"
        # другое действующее вещество — не ограничено этим правилом
        other = make_ppp(db_session, name="Зеллек", substance="галоксифоп")
        assert post_plan(client, hdr, cycle, other).status_code == 201

    def test_unknown_operation_type_400(self, client, db_session):
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        item = make_ppp(db_session)
        resp = post_plan(client, hdr, cycle, item, operation_type="watering")
        assert resp.status_code == 400

    def test_unknown_cycle_404(self, client, db_session):
        hdr = headers(client, db_session)
        item = make_ppp(db_session)
        body = {
            "cycle_id": str(uuid.uuid4()),
            "input_id": str(item.id),
            "dose": 1,
            "operation_type": "spraying",
            "planned_date": "2027-05-20",
        }
        resp = client.post(API_PLANS, json=body, headers=hdr)
        assert resp.status_code == 404


class TestTreatmentPlansList:
    def test_list_with_names_and_filters(self, client, db_session):
        hdr = headers(client, db_session)
        field = make_field(client, hdr)
        cycle = make_cycle(db_session, field=field)
        item = make_ppp(db_session)
        post_plan(client, hdr, cycle, item)
        resp = client.get(API_PLANS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        row = body["items"][0]
        assert row["cycle_id"] == str(cycle.id)
        assert row["input_kind"] == "ppp"
        # фильтры
        assert client.get(API_PLANS, params={"status": "draft"}).json()["total"] == 1
        assert client.get(API_PLANS, params={"status": "approved"}).json()["total"] == 0
        assert client.get(API_PLANS, params={"field_id": str(field.id)}).json()["total"] == 1
        assert client.get(API_PLANS, params={"status": "нет"}).status_code == 400


# ---------- API-16: согласование ----------


class TestApprove:
    def test_approve_by_manager_with_audit(self, client, db_session):
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        item = make_ppp(db_session)
        plan_id = post_plan(client, hdr, cycle, item).json()["plan_id"]
        make_user(db_session, login="mgr_i", role=UserRole.MANAGER)
        mgr = auth_header(client, "mgr_i", "secret123")
        resp = client.post(f"{API_PLANS}/{plan_id}/approve", headers=mgr)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"
        # повтор — 409 invalid_state
        resp2 = client.post(f"{API_PLANS}/{plan_id}/approve", headers=mgr)
        assert resp2.status_code == 409
        assert resp2.json()["code"] == "invalid_state"
        rec = db_session.scalar(
            select(AuditRecord).where(
                AuditRecord.entity == "treatment_plan", AuditRecord.action == "status_change"
            )
        )
        assert rec is not None
        assert rec.changed == {"status_from": "draft", "status_to": "approved"}

    def test_approve_by_agronomist_403(self, client, db_session):
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        item = make_ppp(db_session)
        plan_id = post_plan(client, hdr, cycle, item).json()["plan_id"]
        resp = client.post(f"{API_PLANS}/{plan_id}/approve", headers=hdr)
        assert resp.status_code == 403

    def test_approve_revalidates_rules(self, client, db_session):
        """Черновик согласуют повторной проверкой: если за время жизни
        черновика лимит обработок занят другими планами — 422 (RULE-AGRO-10)."""
        hdr = headers(client, db_session)
        cycle = make_cycle(db_session, field=make_field(client, hdr))
        item = make_ppp(db_session, substance="флуазифоп", max_treatments=2)
        p1 = post_plan(client, hdr, cycle, item).json()["plan_id"]
        assert post_plan(client, hdr, cycle, item).status_code == 201
        assert post_plan(client, hdr, cycle, item).status_code == 422  # лимит 2 из 2

        def set_status(plan_id: str, status: str) -> None:
            row = db_session.get(TreatmentPlan, uuid.UUID(plan_id))
            assert row is not None
            row.status = status
            db_session.commit()

        set_status(p1, "cancelled")  # p1 отменён — появилась третья обработка
        p3 = post_plan(client, hdr, cycle, item).json()["plan_id"]
        # p1 возвращают в draft: снова 2 не отменённых + p3 → лимит занят
        set_status(p1, "draft")
        make_user(db_session, login="mgr_r", role=UserRole.MANAGER)
        mgr = auth_header(client, "mgr_r", "secret123")
        resp = client.post(f"{API_PLANS}/{p3}/approve", headers=mgr)
        assert resp.status_code == 422
        assert resp.json()["rule_id"] == "RULE-AGRO-10"
