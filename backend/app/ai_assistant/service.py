"""Сервис «Выводов ассистента» (AI-01b, docs/06 §7.4, 09-ai §8.2, #93).

GET /fields/{id}/insights: items — числовые детекторы, всегда синхронно
(< 200 мс: только запросы к БД и мягкий кэш); summary — LLM-сводка из кэша.
Первый запрос — source="rules", summary=null, фоновая доработка (BackgroundTasks):
пересчёт items (включая VRA-неоднородность по COG — тяжёлый, поэтому только
фоном) и LLM-сводка (timeout 300 с без повтора, keep_alive 30m, §8.1);
повторный запрос увидит готовую сводку. Кэш — Redis/Dragonfly, ключ
``insights:{field_id}:{дата последней чистой сцены}``, TTL 24 ч; новая сцена
даёт новый ключ (старая запись дотухнет по TTL).

Сводка без Ollama: summarize → None, ответ остаётся source="rules",
summary=null (REQ-NF-009). Если детекторы не сработали — items=[] и
статический summary «отклонений не выявлено» (LLM не зовётся). Числа сводки
обязаны встречаться в items — иначе fallback на конкатенацию items (§8.2).
"""

import json
import logging
import re
import threading
import uuid
from datetime import UTC, date, datetime

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_assistant import detectors, llm
from app.ai_assistant.schemas import InsightItem, InsightsResponse
from app.core.cache import cache_get, cache_set
from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models.agronomy import CropCycle, Field, FieldOperation
from app.models.references import Culture
from app.modules.satellite import vra
from app.modules.satellite.waterlog import precip_sums_7d, waterlogged

logger = logging.getLogger(__name__)

#: кэш ответа: 24 ч или до новой чистой сцены (§7.4)
INSIGHTS_TTL_SECONDS = 24 * 3600

#: фоновая LLM-сводка: 600 с без повтора + keep_alive (§8.1, #112: на CPU
#: реальный контекст генерируется дольше 300 с — ReadTimeout и вечный null)
LLM_BACKGROUND_TIMEOUT_SECONDS = 600.0
LLM_KEEP_ALIVE = "30m"

#: лимит длины генерации сводки, токенов (#112): сводка короткая (§8.1),
#: а CPU-инференс ~7 ток/с — главный резерв по времени
LLM_NUM_PREDICT = 256

#: параллелизм фоновых LLM-вызовов (CPU-инференс: конкурентные вызовы
#: перегружают сервер и рвутся по таймауту, #101) и ожидание очереди.
#: Не дождались — сводка появится при следующем GET (get_insights
#: перезапускает доработку, пока summary в кэше null).
LLM_PARALLELISM = 1
LLM_QUEUE_TIMEOUT_SECONDS = 600.0
_llm_semaphore = threading.BoundedSemaphore(LLM_PARALLELISM)

#: пустой набор детекторов — сводка фиксированная, LLM не зовётся (§8.2)
NO_DEVIATIONS_SUMMARY = "Отклонений по данным детекторов не выявлено."

#: системный промпт (§8.2): только переформулировать items, чисел не выдумывать
PROMPT = (
    "Ты — агроном-ассистент. По фактическим данным детекторов (см. context) "
    "напиши краткое резюме по-русски: что происходит на поле и что проверить. "
    "1–2 абзаца, только факты из детекторов, без выдуманных чисел и новых фактов; "
    "числа можно брать только из детекторов."
)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

_OPERATION_TITLES = {
    "spraying": "обработка СЗР",
    "fertilizing": "внесение удобрений",
    "seeding": "посев",
    "harvesting": "уборка",
    "tillage": "обработка почвы",
    "other": "прочая работа",
}


def _key(field_id: uuid.UUID, day: date) -> str:
    return f"insights:{field_id}:{day.isoformat()}"


def build_items(
    db: Session, field_id: uuid.UUID, *, vra_iqr: float | None = None, vra_day: date | None = None
) -> list[InsightItem]:
    """Все детекторы по данным поля (чистые функции над собранными данными)."""
    scenes = detectors.load_scenes(db, field_id)
    latest = scenes[-1] if scenes else None

    # осадки 7 суток к дате каждой сцены: waterlogging смотрит все сцены (§8.2)
    precip_map: dict[date, float | None] = (
        precip_sums_7d(db, field_id, [s.captured_at for s in scenes]) if scenes else {}
    )

    drought: InsightItem | None = None
    if latest is not None:
        precip_14d, gdd_14d, gdd_days = detectors.weather_window_14d(
            db, field_id, latest.captured_at
        )
        drought = detectors.detect_drought(precip_14d, gdd_14d, gdd_days, latest.captured_at)

    culture_name: str | None = None
    if latest is not None:
        culture_name = detectors.current_culture_name(db, field_id, latest.captured_at)

    candidates: list[InsightItem | None] = [
        detectors.detect_ndvi_drop(scenes),
        detectors.detect_ndvi_stagnation(scenes, culture_name),
        detectors.detect_waterlogging(scenes, precip_map),
        drought,
        detectors.detect_ndvi_lag_last_year(scenes),
        detectors.detect_agrochem_low(detectors.latest_agrochem(db, field_id)),
        detectors.detect_vra_heterogeneity(vra_iqr, vra_day),
    ]
    return [item for item in candidates if item is not None]


def _from_cache(data: dict, field_id: uuid.UUID) -> InsightsResponse | None:
    """Разбор кэша; битый — игнорируем и пересчитываем."""
    try:
        resp = InsightsResponse(**data)
    except (ValueError, TypeError):
        logger.warning("insights: битый кэш поля %s — пересчитаю", field_id, exc_info=True)
        return None
    resp.field_id = field_id
    return resp


def get_insights(
    db: Session, field: Field, background: BackgroundTasks, settings: Settings
) -> InsightsResponse:
    """Ответ GET /fields/{id}/insights (§7.4): items синхронно, summary из кэша."""
    scene = vra.latest_scene(db, field.id)
    if scene is None:  # нет сцен — нет точки привязки кэша: считаем без него
        items = build_items(db, field.id)
        return InsightsResponse(
            field_id=field.id,
            generated_at=datetime.now(UTC),
            source="rules",
            summary=NO_DEVIATIONS_SUMMARY if not items else None,
            items=items,
        )

    day = scene.captured_at
    key = _key(field.id, day)
    if (cached := cache_get(key)) is not None and (resp := _from_cache(cached, field.id)):
        if resp.summary is None and settings.ollama_base_url:
            # прошлая фоновая доработка не успела/не дождалась очереди (#101):
            # перезапускаем, пока в кэше rules-ответ без сводки
            background.add_task(refresh_insights, field.id, day)
        return resp

    vra_info = _vra_peak_cached(field.id)
    items = build_items(
        db,
        field.id,
        vra_iqr=vra_info[1] if vra_info else None,
        vra_day=vra_info[0] if vra_info else None,
    )
    resp = InsightsResponse(
        field_id=field.id,
        generated_at=datetime.now(UTC),
        source="rules",
        summary=NO_DEVIATIONS_SUMMARY if not items else None,
        items=items,
    )
    cache_set(key, resp.model_dump(mode="json"), INSIGHTS_TTL_SECONDS)

    if settings.ollama_base_url and items:
        # фоновая доработка: VRA по COG + LLM-сводка (повторный запрос увидит).
        # В tests Ollama выключен — фон не запускается вовсе.
        background.add_task(refresh_insights, field.id, day)
    return resp


def _vra_peak_cached(field_id: uuid.UUID) -> tuple[date, float] | None:
    """(дата, IQR) пиковой сцены из кэша VRA (vra.py #106); нет — детектор молчит.

    VRA-зоны теперь строятся по пиковым сценам сезона — их даты отличаются
    от последней сцены, поэтому vra.py кладёт указатель текущих зон
    отдельно (ключ vra:peak:{field_id}).
    """
    data = cache_get(f"vra:peak:{field_id}")
    if isinstance(data, dict):
        try:
            return date.fromisoformat(data["date"]), float(data["iqr_ndvi"])
        except (KeyError, ValueError, TypeError):
            return None
    return None


#: вердикт модели дифвнесения для контекста LLM (§8.2, #105), когда расчёта нет
VRA_NO_DATA_VERDICT = "Модель дифвнесения: нет расчёта."


def _llm_context(
    db: Session, field: Field, items: list[InsightItem], *, vra_verdict: str = VRA_NO_DATA_VERDICT
) -> str:
    """Контекст для LLM: поле, культура, агрохимия, работы, NDVI-динамика
    и вердикты моделей вымокания/дифвнесения (§8.2, #105)."""
    parts = [f"Поле «{field.name}», площадь {float(field.area_ga or 0):.1f} га."]
    cycle = db.execute(
        select(CropCycle, Culture.name)
        .join(Culture, CropCycle.culture_id == Culture.id)
        .where(CropCycle.field_id == field.id)
        .order_by(CropCycle.year.desc())
        .limit(1)
    ).first()
    if cycle is not None:
        parts.append(f"Культура текущего сезона: {cycle[1]} ({cycle[0].year}).")
    probe = detectors.latest_agrochem(db, field.id)
    if probe is not None:
        ph = f"pH {float(probe.ph):.1f}" if probe.ph is not None else "pH нет"
        humus = (
            f"гумус {float(probe.humus_pct):.1f} %" if probe.humus_pct is not None else "гумус нет"
        )
        parts.append(f"Агрохимия (проба {probe.sample_date.isoformat()}): {ph}, {humus}.")
    ops = db.scalars(
        select(FieldOperation)
        .where(FieldOperation.field_id == field.id)
        .order_by(FieldOperation.date.desc())
        .limit(3)
    ).all()
    if ops:
        works = ", ".join(
            f"{_OPERATION_TITLES.get(op.operation_type, op.operation_type)} {op.date.isoformat()}"
            for op in ops
        )
        parts.append(f"Последние работы: {works}.")

    scenes = detectors.load_scenes(db, field.id)
    if scenes:
        last10 = scenes[-10:]
        series = ", ".join(f"{s.captured_at.isoformat()}: {s.ndvi_mean:.3f}" for s in last10)
        lo = min(s.ndvi_mean for s in scenes)
        hi = max(s.ndvi_mean for s in scenes)
        parts.append(
            f"NDVI-динамика сезона (последние {len(last10)} чистых сцен, "
            f"дата: среднее по полю, по возрастанию): {series}. "
            f"За сезон min {lo:.3f} / max {hi:.3f}."
        )
        # вердикт модели вымокания — по всем чистым сценам сезона (§8.2, #103)
        precip_map = precip_sums_7d(db, field.id, [s.captured_at for s in scenes])
        flags = [
            waterlogged(s.ndwi_p90, s.ndvi_mean, precip_map.get(s.captured_at)) for s in scenes
        ]
        episode_dates = [
            s.captured_at.isoformat() for s, flag in zip(scenes, flags, strict=True) if flag is True
        ]
        checked = sum(1 for flag in flags if flag is not None)
        if episode_dates:
            parts.append(f"Модель вымокания: эпизоды по сценам {', '.join(episode_dates)}.")
        else:
            parts.append(f"Модель вымокания: признаков вымокания нет по {checked} сценам.")

    parts.append(vra_verdict)
    parts.append(
        "Детекторы (items):\n"
        + json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)
    )
    return "\n".join(parts)


def _checked_summary(
    summary: str | None, items: list[InsightItem], context: str | None = None
) -> str | None:
    """Guardrail §8.2: числа сводки обязаны встречаться в items или контексте.

    Контекст (#105): NDVI-средние, IQR, счётчики сцен/зон тоже допустимы —
    LLM цитирует их при анализе, это не выдумка.
    """
    if not summary:
        return None
    allowed = set(
        _NUM_RE.findall(
            json.dumps(
                [item.model_dump(mode="json") for item in items], ensure_ascii=False
            ).replace(",", ".")
        )
    )
    if context:
        allowed |= set(_NUM_RE.findall(context.replace(",", ".")))
    found = set(_NUM_RE.findall(summary.replace(",", ".")))
    if found <= allowed:
        return summary
    logger.warning("insights: LLM-сводка с посторонними числами — fallback на items")
    fallback = " ".join(f"{item.title}: {item.detail}" for item in items)
    return fallback[:2000]


def refresh_insights(field_id: uuid.UUID, day: date) -> None:
    """Фоновая доработка (BackgroundTasks): VRA-неоднородность + LLM-сводка.

    Любые ошибки — только в лог: пользователь получает rules-ответ из кэша,
    повторный запрос перезапустит доработку, пока summary в кэше null (#101).

    Соединение БД НЕ держится во время инференса (#101): сессия закрывается
    до вызова LLM (до 300 с), иначе параллельные фоновые задачи исчерпывают
    QueuePool Postgres («idle in transaction» на всё время вызова). После
    LLM нужна только запись в кэш (Redis) — БД не открывается.
    """
    # 1) сбор данных и контекста — с сессией БД
    db = SessionLocal()
    try:
        field = db.get(Field, field_id)
        if field is None:
            return
        scene = vra.latest_scene(db, field_id)
        if scene is None or scene.captured_at != day:
            return  # появилась новая сцена — её ключ дорабатывается отдельно

        vra_iqr: float | None = None
        vra_day: date | None = None
        vra_verdict = VRA_NO_DATA_VERDICT
        try:
            # зоны по пиковым сценам (#106): iqr и дата — подтверждённой пиковой
            result = vra.vra_zones(db, field)  # кэшируется у себя (24 ч)
            vra_iqr, vra_day = result.iqr_ndvi, result.captured_at
            vra_verdict = (
                f"Модель дифвнесения: поле неоднородно, {len(result.zones)} зон дифвнесения, "
                f"IQR NDVI = {result.iqr_ndvi:.3f} "
                f"(пиковая сцена {result.captured_at.isoformat()})."
            )
        except AppError as exc:
            if exc.code == "vra_uniform_field":
                # IQR достаём из detail: «…IQR NDVI = 0.058 < 0.120…» (vra.py)
                m = re.search(r"IQR NDVI = (\d+\.\d+)", exc.detail or "")
                iqr_str = f", IQR NDVI = {m.group(1)}" if m else ""
                vra_verdict = f"Модель дифвнесения: поле однородно{iqr_str} — зоны не строятся."
            # прочие AppError (нет ассетов/сцен) — вердикт «нет расчёта»
        except Exception:
            logger.warning("insights: VRA-расчёт не удался", exc_info=True)

        items = build_items(db, field_id, vra_iqr=vra_iqr, vra_day=vra_day)
        context = _llm_context(db, field, items, vra_verdict=vra_verdict) if items else ""
    finally:
        db.close()  # вернуть соединение в пул ДО тяжёлого шага

    # 2) инференс LLM — без БД, с ограничением параллелизма (CPU, #101)
    summary: str | None = None
    if items:
        if not _llm_semaphore.acquire(timeout=LLM_QUEUE_TIMEOUT_SECONDS):
            logger.warning(
                "insights: очередь LLM (>%s c) — сводка поля %s отложена до повторного запроса",
                LLM_QUEUE_TIMEOUT_SECONDS,
                field_id,
            )
            return
        try:
            summary = _checked_summary(
                llm.summarize(
                    PROMPT,
                    context,
                    timeout=LLM_BACKGROUND_TIMEOUT_SECONDS,
                    attempts=1,
                    keep_alive=LLM_KEEP_ALIVE,
                ),
                items,
                context,
            )
        finally:
            _llm_semaphore.release()
    else:
        summary = NO_DEVIATIONS_SUMMARY

    # 3) публикация — только Redis, сессия БД не нужна
    resp = InsightsResponse(
        field_id=field_id,
        generated_at=datetime.now(UTC),
        source="rules+llm" if summary is not None else "rules",
        summary=summary,
        items=items,
    )
    cache_set(_key(field_id, day), resp.model_dump(mode="json"), INSIGHTS_TTL_SECONDS)


__all__ = [
    "INSIGHTS_TTL_SECONDS",
    "LLM_BACKGROUND_TIMEOUT_SECONDS",
    "NO_DEVIATIONS_SUMMARY",
    "PROMPT",
    "build_items",
    "get_insights",
    "refresh_insights",
]
