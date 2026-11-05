"""Тесты модуля севооборота (задача MVP-8, API-08..11).

Проверяется: RULE-AGRO-01..04 на чистых функциях и через API (422 rule_violation
с rule_id/severity/rationale/entity — RFC 7807 + доменные расширения), авто-
predecessor_cycle_id, warn-нарушения с обязательным reason, согласование планов
(API-10) с аудитом, RBAC (группа rotation; approve — manager/admin).
Тестовый контур — PostgreSQL+PostGIS (DATABASE_URL_TEST, паттерн #20).
"""

import uuid

import pytest
from app.models.agronomy import CropCycle, RotationPlan
from app.models.auth import AuditRecord, UserRole
from app.models.references import Culture, PredecessorRule
from app.modules.rotation import rules
from sqlalchemy import select
from tests.test_auth import auth_header, make_user
from tests.test_fields import square

API_PLANS = "/api/v1/rotation-plans"
API_CYCLES = "/api/v1/crop-cycles"

REF_POLYGON = square(41.0, 0.0, 0.01, 0.01)


# ---------- утилиты ----------


def agro_headers(client, db_session, *, login="agro_r", role=UserRole.AGRONOMIST) -> dict:
    make_user(db_session, login=login, role=role)
    return auth_header(client, login, "secret123")


def make_culture(db_session, *, name: str, kind: str = "main", repeat_years: int = 3) -> Culture:
    culture = Culture(
        name=f"{name}-{uuid.uuid4().hex[:6]}", kind=kind, forage_flag=False,
        crop_group="test", repeat_years=repeat_years,
    )
    db_session.add(culture)
    db_session.commit()
    return culture


def make_matrix(db_session, culture: Culture, predecessor: Culture, verdict: str,
                comment: str | None = None) -> None:
    db_session.add(
        PredecessorRule(
            culture_id=culture.id, predecessor_id=predecessor.id, verdict=verdict,
            comment=comment,
        )
    )
    db_session.commit()


def make_field(client, headers, *, name="Поле-Р") -> dict:
    resp = client.post(
        "/api/v1/fields", json={"name": f"{name}-{uuid.uuid4().hex[:6]}",
                                "contour": REF_POLYGON},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def post_cycle(client, headers, field_id, culture_id, *, year=2026, kind="main",
               reason=None, out_of_rotation=False):
    return client.post(
        API_CYCLES,
        json={
            "field_id": str(field_id),
            "year": year,
            "culture_id": str(culture_id),
            "kind": kind,
            "out_of_rotation": out_of_rotation,
            "reason": reason,
        },
        headers=headers,
    )


# ---------- юнит-тесты чистых правил (rules.py) ----------


class TestPureRules:
    def test_01_ok_absent_and_allowed(self):
        assert rules.check_predecessor(None, culture_name="Пшеница",
                                       predecessor_name="Горох", year=2026) is None
        assert rules.check_predecessor("ok", culture_name="Пшеница",
                                       predecessor_name="Горох", year=2026) is None

    def test_01_ban(self):
        v = rules.check_predecessor("ban", culture_name="Пшеница",
                                    predecessor_name="Пшеница", year=2026,
                                    comment="истощает почву")
        assert (v.rule_id, v.severity) == (rules.RULE_01, rules.SEVERITY_BAN)
        assert "Пшеница" in v.rationale and "2026" in v.rationale

    def test_01_warn(self):
        v = rules.check_predecessor("warn", culture_name="Картофель",
                                    predecessor_name="Картофель", year=2026)
        assert (v.rule_id, v.severity) == (rules.RULE_01, rules.SEVERITY_WARN)

    def test_02_pass_and_fail(self):
        assert rules.check_repeat_years(year=2029, last_year=2026, repeat_years=3,
                                        culture_name="Рапс") is None
        v = rules.check_repeat_years(year=2028, last_year=2026, repeat_years=3,
                                     culture_name="Рапс")
        assert (v.rule_id, v.severity) == (rules.RULE_02, rules.SEVERITY_WARN)
        assert "2028" in v.rationale and "3" in v.rationale

    def test_03_kind_mismatch_and_out_of_rotation(self):
        v = rules.check_kind_culture(kind="fallow", culture_kind="main",
                                     culture_name="Пшеница")
        assert (v.rule_id, v.severity) == (rules.RULE_03, rules.SEVERITY_BAN)
        assert rules.check_kind_culture(kind="main", culture_kind="main",
                                        culture_name="Пшеница") is None
        v = rules.check_out_of_rotation(out_of_rotation=True, reason=None)
        assert v is not None and v.rule_id == rules.RULE_03

    def test_04_main_unique(self):
        assert rules.check_main_cycle_unique(year=2026, existing_main_year=None) is None
        v = rules.check_main_cycle_unique(year=2026, existing_main_year=2026)
        assert v.rule_id == rules.RULE_04


# ---------- API-11 POST /crop-cycles ----------


class TestCreateCycle:
    def test_first_cycle_201_no_predecessor(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        resp = post_cycle(client, headers, field["field_id"], wheat.id)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "main"
        assert body["status"] == "planned"
        assert body["predecessor_cycle_id"] is None
        assert body["culture_name"].startswith("Пшеница")
        assert resp.headers["location"].startswith(f"{API_CYCLES}/")
        assert body["warnings"] == []

    def test_auto_predecessor_last_cycle_before_year(self, client, db_session):
        """predecessor_cycle_id заполняет система: последний цикл с year < нового."""
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        peas = make_culture(db_session, name="Горох")
        first = post_cycle(client, headers, field["field_id"], wheat.id, year=2024)
        second = post_cycle(client, headers, field["field_id"], peas.id, year=2025)
        assert second.json()["predecessor_cycle_id"] == first.json()["cycle_id"]
        third = post_cycle(client, headers, field["field_id"], wheat.id, year=2027)
        body = third.json()
        assert body["predecessor_cycle_id"] == second.json()["cycle_id"]
        # возвращение пшеницы: 2027 - 2024 = 3 ≥ repeat_years 3 — без warn
        assert body["warnings"] == []

    def test_01_ban_422_even_with_reason(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        make_matrix(db_session, wheat, wheat, "ban", "повторный посев зерновых")
        assert post_cycle(client, headers, field["field_id"], wheat.id, year=2026).status_code \
            == 201
        resp = post_cycle(client, headers, field["field_id"], wheat.id, year=2027,
                          reason="агроном настаивает")
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "rule_violation"
        assert body["rule_id"] == "RULE-AGRO-01"
        assert body["severity"] == "ban"
        assert body["rationale"]
        assert body["entity"] == "crop_cycle"
        assert "problem" in resp.headers["content-type"]

    def test_01_warn_requires_reason_then_201_with_warnings(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        potato = make_culture(db_session, name="Картофель", repeat_years=5)
        make_matrix(db_session, potato, potato, "warn", "риск нематоды")
        assert post_cycle(client, headers, field["field_id"], potato.id, year=2026).status_code \
            == 201
        resp = post_cycle(client, headers, field["field_id"], potato.id, year=2027)
        assert resp.status_code == 422
        body = resp.json()
        assert body["rule_id"] == "RULE-AGRO-01"
        assert body["severity"] == "warn"
        resp = post_cycle(client, headers, field["field_id"], potato.id, year=2027,
                          reason="песчаные почвы, обработка от нематоды")
        assert resp.status_code == 201, resp.text
        warnings = resp.json()["warnings"]
        assert {w["rule_id"] for w in warnings} == {"RULE-AGRO-01", "RULE-AGRO-02"}
        assert all(w["severity"] == "warn" for w in warnings)

    def test_02_repeat_years_from_reference(self, client, db_session):
        """repeat_years из ref_culture (правила как данные, 03 §6.4): рапс 4."""
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        rape = make_culture(db_session, name="Рапс", repeat_years=4)
        assert post_cycle(client, headers, field["field_id"], rape.id, year=2026).status_code \
            == 201
        # 2029 - 2026 = 3 < 4 → warn без reason → 422 RULE-AGRO-02
        resp = post_cycle(client, headers, field["field_id"], rape.id, year=2029)
        assert resp.status_code == 422
        assert resp.json()["rule_id"] == "RULE-AGRO-02"
        # 2030 - 2026 = 4 ≥ 4 → без нарушений
        resp = post_cycle(client, headers, field["field_id"], rape.id, year=2030)
        assert resp.status_code == 201
        assert resp.json()["warnings"] == []

    def test_03_fallow_is_full_predecessor(self, client, db_session):
        """RULE-AGRO-03: пар и сидерат — полноправные предшественники."""
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        fallow = make_culture(db_session, name="Пар", kind="fallow")
        wheat = make_culture(db_session, name="Пшеница")
        make_matrix(db_session, wheat, fallow, "ok")
        resp = post_cycle(client, headers, field["field_id"], fallow.id, year=2026,
                          kind="fallow")
        assert resp.status_code == 201, resp.text
        resp = post_cycle(client, headers, field["field_id"], wheat.id, year=2027)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["predecessor_cycle_id"] is not None  # пар стал предшественником
        assert body["warnings"] == []  # ok-пара в матрице

    def test_03_kind_culture_mismatch_422(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        resp = post_cycle(client, headers, field["field_id"], wheat.id, kind="fallow")
        assert resp.status_code == 422
        assert resp.json()["rule_id"] == "RULE-AGRO-03"

    def test_03_out_of_rotation_requires_reason(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        resp = post_cycle(client, headers, field["field_id"], wheat.id,
                          out_of_rotation=True)
        assert resp.status_code == 422
        assert resp.json()["rule_id"] == "RULE-AGRO-03"
        resp = post_cycle(client, headers, field["field_id"], wheat.id,
                          out_of_rotation=True, reason="залитый участок")
        assert resp.status_code == 201

    def test_04_one_main_per_year(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        barley = make_culture(db_session, name="Ячмень")
        assert post_cycle(client, headers, field["field_id"], wheat.id, year=2026).status_code \
            == 201
        resp = post_cycle(client, headers, field["field_id"], barley.id, year=2026)
        assert resp.status_code == 422
        assert resp.json()["rule_id"] == "RULE-AGRO-04"

    def test_04_catch_cycles_unlimited(self, client, db_session):
        """Произвольное число catch-циклов на год + main (RULE-AGRO-04)."""
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        mustard = make_culture(db_session, name="Горчица", kind="catch")
        wheat = make_culture(db_session, name="Пшеница")
        assert post_cycle(client, headers, field["field_id"], wheat.id, year=2026).status_code \
            == 201
        assert post_cycle(client, headers, field["field_id"], mustard.id, year=2026,
                          kind="catch").status_code == 201
        resp = post_cycle(client, headers, field["field_id"], mustard.id, year=2026,
                          kind="catch")
        assert resp.status_code == 201, resp.text

    def test_404_field_or_culture(self, client, db_session):
        headers = agro_headers(client, db_session)
        wheat = make_culture(db_session, name="Пшеница")
        resp = post_cycle(client, headers, str(uuid.uuid4()), wheat.id)
        assert resp.status_code == 404
        field = make_field(client, headers)
        resp = post_cycle(client, headers, field["field_id"], str(uuid.uuid4()))
        assert resp.status_code == 404

    def test_rbac_machine_operator_403(self, client, db_session):
        headers = agro_headers(client, db_session, login="op_r",
                               role=UserRole.MACHINE_OPERATOR)
        resp = client.post(API_CYCLES, json={}, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "insufficient_role"

    def test_audit_record_written(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        post_cycle(client, headers, field["field_id"], wheat.id)
        audit = db_session.scalars(
            select(AuditRecord).where(AuditRecord.entity == "crop_cycle")
        ).all()
        assert len(audit) == 1 and audit[0].action == "create"


# ---------- API-08..10 rotation-plans ----------


class TestRotationPlans:
    def make_plan(self, client, headers, field_id, *, year_from=2026, year_to=2030,
                  items=None):
        return client.post(
            API_PLANS,
            json={"field_id": field_id, "year_from": year_from, "year_to": year_to,
                  "items": items or []},
            headers=headers,
        )

    def test_create_and_list_plans(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        resp = self.make_plan(client, headers, field["field_id"])
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "draft"
        assert body["approved_by"] is None
        assert body["violations"] == []
        assert resp.headers["location"].startswith(f"{API_PLANS}/")

        resp = client.get(API_PLANS, headers=headers)
        assert resp.status_code == 200
        page = resp.json()
        assert page["items"][0]["plan_id"] == body["plan_id"]
        assert page["total"] == 1
        # фильтры
        resp = client.get(API_PLANS, params={"status": "approved"}, headers=headers)
        assert resp.json()["total"] == 0
        resp = client.get(API_PLANS, params={"field_id": str(uuid.uuid4())},
                          headers=headers)
        assert resp.json()["total"] == 0

    def test_items_dry_run_returns_violations(self, client, db_session):
        """items[] валидируются как dry-run; записи не создаются."""
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        rape = make_culture(db_session, name="Рапс", repeat_years=4)
        make_matrix(db_session, wheat, wheat, "ban", "повторный посев")
        items = [
            {"year": 2026, "culture_id": str(wheat.id), "kind": "main"},
            {"year": 2027, "culture_id": str(wheat.id), "kind": "main"},  # ban + repeat
        ]
        resp = self.make_plan(client, headers, field["field_id"], items=items)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        found = {(v["rule_id"], v["severity"]) for v in body["violations"]}
        assert ("RULE-AGRO-01", "ban") in found
        assert ("RULE-AGRO-02", "warn") in found
        # dry-run: циклы не созданы
        cycles = db_session.scalars(
            select(CropCycle).where(CropCycle.field_id == field["field_id"])
        ).all()
        assert cycles == []

    def test_plan_horizon_422_and_bad_range_400(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        assert self.make_plan(client, headers, field["field_id"],
                              year_from=2026, year_to=2026).status_code == 422
        assert self.make_plan(client, headers, field["field_id"],
                              year_from=2026, year_to=2025).status_code == 400

    def test_overlapping_active_plan_409(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        assert self.make_plan(client, headers, field["field_id"],
                              year_from=2026, year_to=2030).status_code == 201
        resp = self.make_plan(client, headers, field["field_id"],
                              year_from=2028, year_to=2032)
        assert resp.status_code == 409
        assert resp.json()["code"] == "overlapping_plan"

    def test_approve_flow(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        plan = self.make_plan(client, headers, field["field_id"]).json()

        # согласовать может только manager/admin (агроном — 403)
        resp = client.post(f"{API_PLANS}/{plan['plan_id']}/approve", headers=headers)
        assert resp.status_code == 403

        manager_headers = agro_headers(client, db_session, login="mgr_r",
                                       role=UserRole.MANAGER)
        resp = client.post(f"{API_PLANS}/{plan['plan_id']}/approve",
                           headers=manager_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        assert body["approved_by"]
        assert body["approved_at"] is not None

        # повтор → 409 invalid_state
        resp = client.post(f"{API_PLANS}/{plan['plan_id']}/approve",
                           headers=manager_headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "invalid_state"

        # аудит status_change
        audit = db_session.scalars(
            select(AuditRecord).where(
                AuditRecord.entity == "rotation_plan", AuditRecord.action == "status_change"
            )
        ).all()
        assert len(audit) == 1
        assert audit[0].changed["status_to"] == "approved"

    def test_plan_items_are_cycles_in_range(self, client, db_session):
        headers = agro_headers(client, db_session)
        field = make_field(client, headers)
        wheat = make_culture(db_session, name="Пшеница")
        peas = make_culture(db_session, name="Горох")
        post_cycle(client, headers, field["field_id"], wheat.id, year=2025)
        post_cycle(client, headers, field["field_id"], peas.id, year=2027)
        plan = self.make_plan(client, headers, field["field_id"],
                              year_from=2026, year_to=2030).json()
        # items — main-циклы в диапазоне (2025 вне диапазона)
        assert [i["year"] for i in plan["items"]] == [2027]

    def test_401_and_operator_403(self, client, db_session):
        assert client.get(API_PLANS).status_code == 401
        headers = agro_headers(client, db_session, login="op2_r",
                               role=UserRole.MACHINE_OPERATOR)
        assert client.get(API_PLANS, headers=headers).status_code == 403


@pytest.mark.parametrize("endpoint", [API_PLANS, API_CYCLES])
def test_unknown_plan_404(client, db_session, endpoint):
    headers = agro_headers(client, db_session)
    resp = client.post(f"{endpoint}/{uuid.uuid4()}/approve", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
