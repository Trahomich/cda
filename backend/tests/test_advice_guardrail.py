"""Тесты бага #144: guardrail advice ложно резал ответы, fallback-текст.

Реальный случай со стенда (поле 45): ответ LLM качественный, но guardrail
отвергал (а) даты «17.08»/«05.07», распознававшиеся как дробные числа,
(б) округления «~0.20» при контекстном 0.204, (в) дни с ведущим нулём
(«08 и 09 осадки»); при пустых детекторах fallback-текст был вводящим в
заблуждение «Данных для рекомендации недостаточно». Чистые юнит-тесты
guardrail + fallback-ветка + GET отдаёт fallback-текст (регрессия)."""

from datetime import UTC, date, datetime

from app.ai_assistant import advice
from app.ai_assistant.schemas import InsightItem

#: фрагмент реального ответа glm-5.3-flash со стенда (поле 45, 08.11)
REAL_ANSWER = (
    "Итоги сезона. Уборка 2025-08-05, операций сезона 2026 нет. "
    "2026-11-08 и 09 осадки (3.5 и 1.7 мм, риск 100 %), морозы до -4.9…-5.0 °C "
    "(10 и 14 ноября), сухие дни 10–14 ноября. Пик вегетации 2026-07-05 "
    "(avg 0.709), спад до ~0.32 в августе и ~0.20 к ноябрю; текущие "
    "2026-11-05 и 2026-11-07 (avg 0.204 и 0.214, max до 0.567). "
    "Прогноз 14.76 ц/га (интервал 11.07–18.45). Осадки за 14 суток 4.4 мм; "
    "min NDVI -0.281 17.08, -0.218 02.08 — артефакты снимков."
)

#: фрагмент реального контекста поля 45 (числа/даты, сокращено)
REAL_CONTEXT = (
    "Поле «45/ЖАМ/348га», площадь 395.9 га, сезон 2026. "
    "Журнал работ: 2025-08-05 уборка. "
    "NDVI сезона 2026 (дата: avg/min/max): 2026-04-03: avg 0.168/min -0.116/max 0.577, "
    "2026-05-13: avg 0.274/min 0.126/max 0.927, 2026-07-05: avg 0.709/min 0.412/max 0.931, "
    "2026-08-02: avg 0.318/min -0.218/max 0.412, 2026-08-17: avg 0.316/min -0.281/max 0.451, "
    "2026-11-05: avg 0.204/min 0.104/max 0.567, 2026-11-07: avg 0.214/min 0.118/max 0.601. "
    "Погода, факт 14 сут до 2026-11-08: осадки 4.4 мм. "
    "Прогноз погоды: 2026-11-08: осадки 3.5 мм (риск 100 %), t -0.4…4.0 °C; "
    "2026-11-09: осадки 1.7 мм (риск 90 %), t -0.9…3.0 °C; "
    "2026-11-10: t -4.9…1.0 °C; 2026-11-14: t -5.0…2.0 °C. "
    "Модель дифвнесения: пиковые сцены 2026-07-05, IQR NDVI последнего пика = 0.102 "
    "(3 зоны). Прогноз урожайности (Гречиха, 2026): 14.76 ц/га (интервал 11.07–18.45)."
)


def _allowed(context: str = REAL_CONTEXT):
    items = [
        InsightItem(
            code="x",
            severity="info",
            title="Тест",
            detail="осадки 2026-11-08: 3.5 мм",
            data_refs=[],
        )
    ]
    return (
        advice._allowed_numbers(context, items),
        advice._allowed_pairs(context, items),
    )


class TestNumbersWithin:
    def test_real_stand_answer_passes(self):
        """Ответ со стенда (даты DD.MM, округления, ведущие нули) — проходит."""
        nums, pairs = _allowed()
        assert advice._numbers_within(REAL_ANSWER, nums, pairs) is True

    def test_dot_date_vs_number_ambiguity(self):
        """«17.08» — дата (пара из контекста), «3.5» — число из контекста."""
        nums, pairs = _allowed()
        assert advice._numbers_within("обработка 17.08", nums, pairs) is True
        assert advice._numbers_within("осадки 3.5 мм", nums, pairs) is True
        # та же дата без пары в контексте — посторонняя
        assert advice._numbers_within("обработка 31.12", nums, pairs) is False

    def test_rounding_not_fabrication(self):
        """Округление контекстного числа (0.204 → «~0.20») допустимо."""
        nums, pairs = _allowed()
        assert advice._numbers_within("NDVI ~0.20 к ноябрю", nums, pairs) is True
        assert advice._numbers_within("NDVI ~0.32 в августе", nums, pairs) is True

    def test_leading_zero_day_component(self):
        """«…08 и 09 осадки» — день месяца из дат контекста, не постороннее число."""
        nums, pairs = _allowed()
        assert advice._numbers_within("осадки 2026-11-08 и 09", nums, pairs) is True

    def test_fabricated_numbers_still_rejected(self):
        """Выдуманные величины guardrail по-прежнему отвергает."""
        nums, pairs = _allowed()
        assert advice._numbers_within("внести 250 кг/га", nums, pairs) is False
        assert advice._numbers_within("NDVI 0.99 на всём поле", nums, pairs) is False
        # агрегат-сумма чисел контекста — не буквальное число, отвергается
        assert advice._numbers_within("осадки суммарно 5.2 мм", nums, pairs) is False
        # интервалы из пересчёта («риск 0–4 %» при 3.5 и 1.7 мм) — отвергаются
        assert advice._numbers_within("риск 0–4 %", nums, pairs) is False
        # число с PNG-рендера, отсутствующее в context («0.56» при 0.567)
        assert advice._numbers_within("NDVI до 0.56 по снимку", nums, pairs) is False
        assert advice._numbers_within("NDVI до 0.67 по снимку", nums, pairs) is False

    def test_russian_date_interval_from_iso_context(self):
        """«с 10.11 по 14.11» — легальные русские даты ISO-дат контекста."""
        nums, pairs = _allowed()
        assert advice._numbers_within("окно с 10.11 по 14.11", nums, pairs) is True

    def test_render_legend_scale_legal_via_context(self):
        """Цитата легенды рендера («шкала −0.10…0.80», #145).

        Без строки о шкале в context число 0.80 постороннее (guardrail
        отвергает); AdviceContext дописывает строку «шкала раскраски от
        −0.10 до 0.80» — цитата становится легальной.
        """
        nums_no, pairs = _allowed()
        assert advice._numbers_within("шкала легенды −0.10…0.80", nums_no, pairs) is False
        nums_yes, pairs_yes = _allowed(
            REAL_CONTEXT + "\nРендеры NDVI (приложены): шкала раскраски от -0.10 до 0.80."
        )
        assert advice._numbers_within("шкала легенды −0.10…0.80", nums_yes, pairs_yes) is True

    def test_iso_date_outside_context(self):
        """ISO-дата с парой (день, месяц) не из контекста — посторонняя."""
        nums, pairs = _allowed()
        assert advice._numbers_within("к 2026-12-25", nums, pairs) is False

    def test_comma_decimals(self):
        """Запятая как десятичный разделитель — тоже число."""
        nums, pairs = _allowed()
        assert advice._numbers_within("осадки 3,5 мм", nums, pairs) is True


class TestFallbackBranch:
    def test_guardrail_fail_twice_items_empty_honest_text(self, monkeypatch):
        """Двойной провал + пустые детекторы → честный fallback, не «данных нет»."""
        calls = []

        def fake_summarize(*args, **kwargs):
            calls.append(args)
            return "внести 250 кг/га повсеместно"  # постороннее число

        monkeypatch.setattr(advice.llm, "summarize", fake_summarize)
        ctx = advice.AdviceContext(context=REAL_CONTEXT, context_hash="h", images=[])
        answer, status = advice._infer_with_guardrail("p", ctx, [], "glm-5.3-flash")
        assert status == "fallback"
        assert "недостаточно" not in answer
        assert "не прошёл проверку" in answer
        assert len(calls) == 2  # первая попытка + повтор с уточнением

    def test_guardrail_fail_items_concat(self, monkeypatch):
        """Провал при непустых детекторах → конкатенация items §8.2."""
        monkeypatch.setattr(advice.llm, "summarize", lambda *a, **k: "выдумка 777 кг")
        items = [
            InsightItem(
                code="drought",
                severity="warn",
                title="Дефицит осадков",
                detail="за 14 суток 4.4 мм",
                data_refs=[],
            )
        ]
        ctx = advice.AdviceContext(context=REAL_CONTEXT, context_hash="h", images=[])
        answer, status = advice._infer_with_guardrail("p", ctx, items, "glm-5.3-flash")
        assert status == "fallback"
        assert "Дефицит осадков" in answer and "за 14 суток 4.4 мм" in answer

    def test_guardrail_pass_after_retry(self, monkeypatch):
        """Первая попытка с посторонним числом, повтор чистый → ok."""
        answers = iter(["внести 250 кг/га", "осадки 3.5 мм, окно 2026-11-10"])
        monkeypatch.setattr(advice.llm, "summarize", lambda *a, **k: next(answers))
        ctx = advice.AdviceContext(context=REAL_CONTEXT, context_hash="h", images=[])
        answer, status = advice._infer_with_guardrail("p", ctx, [], "glm-5.3-flash")
        assert status == "ok"
        assert answer == "осадки 3.5 мм, окно 2026-11-10"


class TestGetReturnsFallback:
    def test_get_advice_returns_fallback_answer(self, client, db_session):
        """GET отдаёт непустой answer и при status=fallback (регрессия #144)."""

        from app.models.ai import AIFieldAdvice
        from app.models.auth import UserRole
        from tests.test_auth import auth_header, make_user
        from tests.test_satellite import make_field

        make_user(db_session, login="adv144", role=UserRole.AGRONOMIST, password="pw144")
        field_id = make_field(db_session)
        db_session.add(
            AIFieldAdvice(
                field_id=field_id,
                created_at=datetime.now(tz=UTC),
                model="glm-5.3-flash",
                provider="anthropic",
                advice_date=date.today(),
                answer="Не удалось получить рекомендацию модели: ответ не прошёл "
                "проверку по данным хозяйства.",
                context_hash="h144",
                status="fallback",
            )
        )
        db_session.commit()
        hdr = auth_header(client, "adv144", "pw144")
        resp = client.get(f"/api/v1/fields/{field_id}/ai/advice", headers=hdr)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "fallback"
        assert len(data["answer"]) > 0
        assert "не прошёл проверку" in data["answer"]
