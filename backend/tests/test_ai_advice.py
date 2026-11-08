"""Тесты AI-рекомендаций «что делать сейчас» (docs/06 §7.6, 09-ai §9, #120).

Проверяются API-55/56 (последняя рекомендация, 404 advice_not_found /
field_not_found, POST 202 queued, история desc по created_at с from/to
по advice_date и limit ≤ 100), RBAC группы satellite, событийный пересчёт
по scene.ingested (только чистые NDVI), кандидат-поля суточного прогона,
create_advice_scheduler при провайдере без advice и CLI advice-refresh
(идемпотентность по advice_date + context_hash). БД — PostgreSQL
(DATABASE_URL_TEST, паттерн #20)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.ai_assistant import advice, advice_scheduler
from app.core import events
from app.models.agronomy import Field
from app.models.ai import AIFieldAdvice
from app.models.auth import UserRole
from app.models.integrations import SatelliteScene
from app.modules.satellite.ingest import SCENE_INGESTED_EVENT
from app.modules.satellite.router import SATELLITE_SOURCE_ID
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_field, make_satellite_source

API = "/api/v1"
TODAY = date.today()


def make_advice(
    db: Session,
    field_id: uuid.UUID,
    *,
    advice_date: date,
    created_at: datetime,
    context_hash="hash-1",
    answer="Проверить посевы.",
    status="ok",
) -> AIFieldAdvice:
    record = AIFieldAdvice(
        field_id=field_id,
        created_at=created_at,
        model="glm-5.3-flash",
        provider="anthropic",
        advice_date=advice_date,
        answer=answer,
        context_hash=context_hash,
        status=status,
    )
    db.add(record)
    db.commit()
    return record


def make_scene(db: Session, field_id: uuid.UUID, day: date, *, cloud_pct=0.0):
    db.add(
        SatelliteScene(
            field_id=field_id,
            index="ndvi",
            captured_at=day,
            resolution_m=10,
            index_avg=0.5,
            index_min=0.3,
            index_max=0.7,
            cloud_pct=cloud_pct,
            source_id=SATELLITE_SOURCE_ID,
        )
    )
    db.commit()


class TestAdviceAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, client, db_session):
        self.client = client
        self.db = db_session
        make_user(db_session, login="adv-agro", role=UserRole.AGRONOMIST, password="pw-adv")
        make_user(db_session, login="adv-mach", role=UserRole.MACHINE_OPERATOR, password="pw-m2")
        self.auth = auth_header(client, "adv-agro", "pw-adv")
        self.field_id = make_field(db_session)
        make_satellite_source(db_session)
        yield

    def test_get_last_advice(self):
        """GET отдаёт последнюю по created_at запись со всеми полями §9.3."""
        make_advice(self.db, self.field_id, advice_date=TODAY - timedelta(days=1), created_at=datetime(2026, 11, 6, 5, 0, tzinfo=UTC))
        last = make_advice(self.db, self.field_id, advice_date=TODAY, created_at=datetime(2026, 11, 7, 5, 0, tzinfo=UTC), context_hash="hash-2", answer="Провести боронование.", status="fallback")
        resp = self.client.get(f"{API}/fields/{self.field_id}/ai/advice", headers=self.auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["field_id"] == str(self.field_id)
        assert data["advice_date"] == TODAY.isoformat()
        assert data["model"] == "glm-5.3-flash"
        assert data["provider"] == "anthropic"
        assert data["status"] == "fallback"
        assert data["answer"] == last.answer

    def test_get_advice_not_found(self):
        """Нет записей → 404 advice_not_found (problem+json §5)."""
        resp = self.client.get(f"{API}/fields/{self.field_id}/ai/advice", headers=self.auth)
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "advice_not_found"

    def test_advice_field_not_found(self):
        """Несуществующее поле → 404 field_not_found у всех трёх эндпоинтов."""
        missing = uuid.uuid4()
        for method, path in (
            ("get", f"/ai/advice"),
            ("post", f"/ai/advice"),
            ("get", f"/ai/advice/history"),
        ):
            resp = getattr(self.client, method)(f"{API}/fields/{missing}{path}", headers=self.auth)
            assert resp.status_code == 404
            assert resp.json()["code"] == "field_not_found"

    def test_post_queues_generation(self, monkeypatch):
        """POST 202 {status: queued} и ставит поле в фоновую очередь."""
        queued: list[uuid.UUID] = []
        monkeypatch.setattr(advice, "queue_advice", lambda fid, **kw: queued.append(fid))
        resp = self.client.post(f"{API}/fields/{self.field_id}/ai/advice", headers=self.auth)
        assert resp.status_code == 202
        assert resp.json() == {"status": "queued"}
        assert queued == [self.field_id]

    def test_history_sorted_limited_filtered(self):
        """История: desc по created_at, from/to по advice_date, limit."""
        for i in range(4):
            make_advice(
                self.db,
                self.field_id,
                advice_date=TODAY - timedelta(days=i),
                created_at=datetime(2026, 11, 1 + i, 5, 0, tzinfo=UTC),
                context_hash=f"h{i}",
            )
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ai/advice/history",
            headers=self.auth,
            params={"limit": 3},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3
        created = [it["created_at"] for it in items]
        assert created == sorted(created, reverse=True)
        assert set(resp.json()) == {"items"}  # контракт API-56: только items

        # from/to фильтруют по advice_date
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ai/advice/history",
            headers=self.auth,
            params={"from": (TODAY - timedelta(days=1)).isoformat(), "to": TODAY.isoformat()},
        )
        days = {it["advice_date"] for it in resp.json()["items"]}
        assert days == {TODAY.isoformat(), (TODAY - timedelta(days=1)).isoformat()}

        # limit > 100 → 422
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ai/advice/history",
            headers=self.auth,
            params={"limit": 101},
        )
        assert resp.status_code == 422

    def test_history_empty(self):
        """История без записей — 200 с пустым items (не 404)."""
        resp = self.client.get(f"{API}/fields/{self.field_id}/ai/advice/history", headers=self.auth)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_rbac_machine_operator_forbidden(self):
        """Группа satellite (07-ux §5 E-09): машинист-оператор — 403."""
        auth = auth_header(self.client, "adv-mach", "pw-m2")
        for method, path in (
            ("get", "/ai/advice"),
            ("post", "/ai/advice"),
            ("get", "/ai/advice/history"),
        ):
            resp = getattr(self.client, method)(
                f"{API}/fields/{self.field_id}{path}", headers=auth
            )
            assert resp.status_code == 403

    def test_no_auth_401(self):
        resp = self.client.get(f"{API}/fields/{self.field_id}/ai/advice")
        assert resp.status_code == 401


class TestAdviceTriggers:
    @pytest.fixture(autouse=True)
    def _setup(self, client, db_session):
        self.db = db_session
        self.field_id = make_field(db_session)
        make_satellite_source(db_session)
        yield
        events.reset_bus()

    def _spy_queue(self, monkeypatch) -> list[uuid.UUID]:
        queued: list[uuid.UUID] = []
        monkeypatch.setattr(advice, "queue_advice", lambda fid, **kw: queued.append(fid))
        return queued

    def test_scene_ingested_ndvi_queues(self, monkeypatch):
        """Новая чистая NDVI-сцена → поле в очередь (§9.4 п.2)."""
        queued = self._spy_queue(monkeypatch)
        events.publish(
            SCENE_INGESTED_EVENT,
            {"field_id": self.field_id, "index": "ndvi", "cloud_pct": 10.0},
        )
        assert queued == [self.field_id]

    def test_scene_ingested_non_ndvi_or_cloudy_ignored(self, monkeypatch):
        """Не-NDVI и заоблачная сцена пересчёт не запускают."""
        queued = self._spy_queue(monkeypatch)
        events.publish(
            SCENE_INGESTED_EVENT,
            {"field_id": self.field_id, "index": "ndwi", "cloud_pct": 0.0},
        )
        events.publish(
            SCENE_INGESTED_EVENT,
            {"field_id": self.field_id, "index": "ndvi", "cloud_pct": 80.0},
        )
        assert queued == []

    def test_candidate_field_ids(self):
        """Кандидаты — активные поля с чистой NDVI-сценой текущего года."""
        make_scene(self.db, self.field_id, TODAY)
        last_year = make_field(self.db, name="Прошлый сезон")
        make_scene(self.db, last_year, TODAY - timedelta(days=400))
        archived = make_field(self.db, name="В архиве", status="archived")
        make_scene(self.db, archived, TODAY)
        cloudy = make_field(self.db, name="Заоблачное")
        make_scene(self.db, cloudy, TODAY, cloud_pct=80.0)
        assert advice_scheduler.candidate_field_ids(self.db, TODAY) == [self.field_id]

    def test_daily_job_queues_candidates(self, monkeypatch):
        """Суточный прогон ставит всех кандидатов в очередь."""
        make_scene(self.db, self.field_id, TODAY)
        queued = self._spy_queue(monkeypatch)

        class _Ctx:
            def __init__(self, db):
                self.db = db

            def __enter__(self):
                return self.db

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(advice_scheduler, "SessionLocal", lambda: _Ctx(self.db))
        advice_scheduler._run_daily_job()
        assert queued == [self.field_id]

    def test_parse_daily_at(self):
        assert advice_scheduler.parse_daily_at("05:00") == (5, 0)
        assert advice_scheduler.parse_daily_at("07:30 ") == (7, 30)
        # невалидное — fallback 05:00
        assert advice_scheduler.parse_daily_at("25:99") == (5, 0)
        assert advice_scheduler.parse_daily_at("утро") == (5, 0)

    def test_scheduler_none_for_ollama(self, monkeypatch):
        """Дефолт ollama (тестовый env) — планировщик не создаётся (§9)."""
        from app.core.config import get_settings

        assert advice_scheduler.create_advice_scheduler(get_settings()) is None

    def test_scheduler_cron_for_anthropic(self, monkeypatch):
        """Anthropic с ключом — cron на AI_ADVICE_DAILY_AT, локальное время."""
        from app.core.config import get_settings

        settings = get_settings().model_copy(
            update={"llm_provider": "anthropic", "anthropic_api_key": "test-key", "ai_advice_daily_at": "06:30"}
        )
        scheduler = advice_scheduler.create_advice_scheduler(settings)
        assert scheduler is not None
        try:
            (job,) = scheduler.get_jobs()
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "6" and fields["minute"] == "30"
        finally:
            scheduler.shutdown(wait=False)


class TestAdviceCli:
    @pytest.fixture(autouse=True)
    def _setup(self, client, db_session):
        self.db = db_session
        self.field_id = make_field(db_session)
        make_satellite_source(db_session)
        yield

    def _run_cli(self, monkeypatch, *, field_id=None) -> dict:
        import json

        from app.cli import main as cli_main

        monkeypatch.setattr(
            advice,
            "build_advice_context",
            lambda db, fid, today: advice.AdviceContext(
                context="Поле. Севооборот.", context_hash="hash-cli", images=[]
            ),
        )
        generated: list[uuid.UUID] = []

        def fake_generate(fid, *, today=None):
            generated.append(fid)
            record = AIFieldAdvice(
                field_id=fid,
                created_at=datetime.now(tz=UTC),
                model="glm-5.3-flash",
                provider="anthropic",
                advice_date=today or TODAY,
                answer="ok",
                context_hash="hash-cli",
                status="ok",
            )
            self.db.add(record)
            self.db.commit()
            return record

        monkeypatch.setattr(advice, "generate_advice", fake_generate)

        class _Ctx:
            def __init__(self, db):
                self.db = db

            def __enter__(self):
                return self.db

            def __exit__(self, *args):
                return False

        from app import cli as cli_module

        monkeypatch.setattr(cli_module, "SessionLocal", lambda: _Ctx(self.db))
        argv = ["advice-refresh"]
        if field_id:
            argv += ["--field-id", str(field_id)]
        code = cli_main(argv)
        assert code == 0
        return json.loads("\n".join(self.captured))

    @pytest.fixture(autouse=True)
    def _capture_stdout(self, monkeypatch):
        captured: list[str] = []

        def fake_print(*args, **kwargs):
            captured.append(" ".join(str(a) for a in args))

        monkeypatch.setattr("builtins.print", fake_print)
        self.captured = captured
        yield

    def test_cli_generates_once_then_skips(self, monkeypatch):
        """Первый запуск — ok (генерация), повторный — skipped (идемпотент)."""
        report = self._run_cli(monkeypatch, field_id=self.field_id)
        assert report["fields"][str(self.field_id)]["status"] == "ok"

        # запись за сегодня с тем же hash уже есть → skipped
        report = self._run_cli(monkeypatch, field_id=self.field_id)
        assert report["fields"][str(self.field_id)]["status"] == "skipped"

    def test_cli_empty_season(self, monkeypatch):
        """Пустой сезон (build_advice_context → None) — empty, без генерации."""
        from app.ai_assistant import advice
        from app.cli import main as cli_main

        monkeypatch.setattr(advice, "build_advice_context", lambda db, fid, today: None)
        report_ctx = self._patched_session(monkeypatch)
        code = cli_main(["advice-refresh", "--field-id", str(self.field_id)])
        assert code == 0
        import json

        data = json.loads("\n".join(self.captured))
        assert data["fields"][str(self.field_id)]["status"] == "empty"

    def _patched_session(self, monkeypatch):
        class _Ctx:
            def __init__(self, db):
                self.db = db

            def __enter__(self):
                return self.db

            def __exit__(self, *args):
                return False

        from app import cli as cli_module

        monkeypatch.setattr(cli_module, "SessionLocal", lambda: _Ctx(self.db))

    def test_cli_no_llm(self, monkeypatch):
        """generate_advice → None (нет провайдера) — no_llm."""
        from app.ai_assistant import advice
        from app.cli import main as cli_main

        monkeypatch.setattr(
            advice,
            "build_advice_context",
            lambda db, fid, today: advice.AdviceContext(context="x", context_hash="h", images=[]),
        )
        monkeypatch.setattr(advice, "generate_advice", lambda fid, *, today=None: None)
        self._patched_session(monkeypatch)
        import json

        code = cli_main(["advice-refresh", "--field-id", str(self.field_id)])
        assert code == 0
        data = json.loads("\n".join(self.captured))
        assert data["fields"][str(self.field_id)]["status"] == "no_llm"

    def test_cli_error_does_not_stop(self, monkeypatch):
        """Ошибка одного поля — error, но CLI завершается 0."""
        from app.ai_assistant import advice
        from app.cli import main as cli_main

        monkeypatch.setattr(
            advice,
            "build_advice_context",
            lambda db, fid, today: advice.AdviceContext(context="x", context_hash="h", images=[]),
        )

        def boom(fid, *, today=None):
            raise RuntimeError("llm down")

        monkeypatch.setattr(advice, "generate_advice", boom)
        self._patched_session(monkeypatch)
        import json

        code = cli_main(["advice-refresh", "--field-id", str(self.field_id)])
        assert code == 0
        data = json.loads("\n".join(self.captured))
        assert data["fields"][str(self.field_id)]["status"] == "error"
