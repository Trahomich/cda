"""Сервис «Выводов ассистента» (AI-01b, docs/06 §7.4, 09-ai §8.2, #93).

GET /fields/{id}/insights: items — числовые детекторы, всегда синхронно
(< 200 мс: только запросы к БД и мягкий кэш); summary — LLM-сводка из кэша.
Первый запрос — source="rules", summary=null, фоновая доработка (BackgroundTasks):
пересчёт items (включая VRA-неоднородность по COG — тяжёлый, поэтому только
фоном) и LLM-сводка (timeout 600 с без повтора, keep_alive 30m, num_predict 256 —
§8.1, #112);
повторный запрос увидит готовую сводку. Кэш — Redis/Dragonfly, ключ
``insights:{field_id}:{дата последней чистой сцены}``, TTL 24 ч; новая сцена
даёт новый ключ (старая запись дотухнет по TTL).

Сводка без LLM (провайдер выключен/недоступен): summarize → None, ответ
остаётся source="rules", summary=null (REQ-NF-009). Если детекторы не сработали — items=[] и
статический summary «отклонений не выявлено» (LLM не зовётся). Числа сводки
обязаны встречаться в items — иначе fallback на конкатенацию items (§8.2).
"""

import json
import logging
import re
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_assistant import detectors, llm
from app.ai_assistant.schemas import InsightItem, InsightsResponse
from app.core.cache import cache_get, cache_set
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models.agronomy import CropCycle, Field, FieldOperation
from app.models.references import Culture
from app.modules.satellite import vra
from app.modules.satellite.waterlog import precip_sums_7d, waterlogged
from app.services.sentinel_ingest.renders import render_path

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

#: деградация при недоступном LLM и пустых детекторах (§8.2 v1.3, #138):
#: при работающем провайдере сводка генерируется даже при items=[]
NO_DEVIATIONS_SUMMARY = "Отклонений по данным детекторов не выявлено."

#: системный промпт (§8.2 v1.3): только переформулировать items/контекст,
#: чисел не выдумывать; пустые детекторы — не повод для пустого ответа
PROMPT = (
    "Ты — агроном-ассистент. По фактическим данным детекторов (см. context) "
    "напиши краткое резюме по-русски: что происходит на поле и что проверить. "
    "1–2 абзаца, только факты из детекторов и context, без выдуманных чисел "
    "и новых фактов; числа можно брать только из детекторов и context. "
    "Если детекторы не сработали (items пуст) — это не повод для пустого "
    "ответа: охарактеризуй состояние поля по context (культура, агрохимия, "
    "NDVI-динамика сезона, другие индексы, последние работы, выводы моделей "
    "вымокания и дифвнесения): что в норме и за чем следить дальше. "
    "В context есть значения других индексов — NDRE (азотный статус), "
    "NDMI (влажность биомассы), EVI (вегетация), NDSI (снег), BSI (голая "
    "почва): используй их в рассуждениях, интерпретируя по смыслу индекса. "
    "Пиши простым текстом без markdown-разметки (без *, **, #, _, `), "
    "списки — через тире с новой строки."
)

#: Промпт vision-сводки (§8.5, #117): модели приложены PNG-рендеры поля
#: (NDVI/truecolor/NDWI последней чистой сцены и NDVI пика вегетации).
#: Guardrail §8.2 не ослабляется: визуальные наблюдения — качественные,
#: числа только из context.
VISION_PROMPT = (
    "Ты — агроном-аналитик спутниковых снимков. По фактическим данным "
    "детекторов (context) и приложенным изображениям поля — рендерам "
    "последней чистой сцены (NDVI-раскраска, truecolor, NDWI, при наличии "
    "NDRE) и NDVI пика вегетации — напиши краткое резюме по-русски: что "
    "происходит на поле и что проверить. Добавь визуальные наблюдения по "
    "снимкам (пятна, градиенты, края поля, следы техники), соотнося их с "
    "числами детекторов. В context есть значения других индексов — NDRE "
    "(азотный статус), NDMI (влажность биомассы), EVI (вегетация), NDSI "
    "(снег), BSI (голая почва): используй их в рассуждениях, интерпретируя "
    "по смыслу индекса; на NDRE-снимке зоны разной обеспеченности азотом "
    "видны даже на пике вегетации, когда NDVI уже насыщен. Если детекторы "
    "не сработали (items пуст) — это не повод "
    "для пустого ответа: дай вердикт по context и снимкам — что в норме и "
    "за чем следить. 1–2 абзаца; визуальные наблюдения — качественные, без "
    "новых фактов; числа можно брать только из context, даты и числа "
    "приводи в том же виде, как в context; если перечисляешь пункты — "
    "маркируй их тире, без цифровой нумерации. Пиши простым текстом без "
    "markdown-разметки (без *, **, #, _, `)."
)

#: максимум PNG-рендеров в одной vision-сводке (§8.5): 3 варианта последней
#: чистой сцены + NDVI пика вегетации (все — уже отрендерены для §7.3)
VISION_MAX_IMAGES = 4

#: Санитайзер #131 (§8.2/§9.2): LLM-вывод — простой текст, фронт markdown
#: не рендерит и показывает символы сырыми. Регэкспы консервативны:
#: срезаем только явную разметку, остальной текст не трогаем.
_MD_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\n?|```")  # ограждения кода
_MD_HEADER_RE = re.compile(r"(?m)^#{1,6}\s*")  # заголовки в начале строки
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_MD_ITALIC_RE = re.compile(
    r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])|(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])"
)
_MD_CODE_RE = re.compile(r"`+([^`\n]*)`+")
_MD_LIST_RE = re.compile(r"(?m)^(\s*)[*+-]\s+")  # маркеры списков → тире


def strip_markdown(text: str) -> str:
    """Срезать остаточную markdown-разметку из LLM-текста (#131, §8.2/§9.2).

    Применяется ПОСЛЕ guardrail: жирный/курсив/заголовки/бэктики → чистый
    текст, маркеры списков (*, +, -) → тире. Чистый текст не меняется.
    """
    out = _MD_FENCE_RE.sub("", text)
    out = _MD_HEADER_RE.sub("", out)
    out = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", out)
    out = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", out)
    out = _MD_CODE_RE.sub(r"\1", out)
    out = _MD_LIST_RE.sub(r"\1- ", out)
    return out


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

_OPERATION_TITLES = {
    "spraying": "обработка СЗР",
    "fertilizing": "внесение удобрений",
    "seeding": "посев",
    "harvesting": "уборка",
    "tillage": "обработка почвы",
    "other": "прочая работа",
}


#: версия ключа кэша insights (§8.2 v1.3, #138): v2 — LLM-вердикт обязателен
#: и при items=[]; поднятие версии сбрасывает кэш со старыми статическими
#: сводками «отклонений не выявлено» (TTL 24 ч иначе держал бы их сутки)
INSIGHTS_CACHE_VERSION = 2


def _key(field_id: uuid.UUID, day: date) -> str:
    return f"insights:v{INSIGHTS_CACHE_VERSION}:{field_id}:{day.isoformat()}"


def build_items(
    db: Session,
    field_id: uuid.UUID,
    *,
    vra_peaks: Sequence[tuple[date, float, float]] = (),
    year: int | None = None,
    as_of: date | None = None,
) -> list[InsightItem]:
    """Все детекторы по данным поля (чистые функции над собранными данными).

    vra_peaks — кортежи (дата, IQR, spatial_spread) пиковых сцен из кэша VRA
    (детектор vra_heterogeneity §8.2 v1.7). year (§7.5) — сцены сезона YYYY
    (исторические выводы); без него — вся история (текущее поведение).
    as_of (ретро #159) — детекторы только по данным ≤ даты (без подглядывания).
    """
    scenes = detectors.load_scenes(db, field_id, year=year, as_of=as_of)
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
        # NDMI-сигнал (§7.7 #129): последняя ndmi-сцена сезона, NDVI — последняя сцена
        ndmi_mean = detectors.latest_index_mean(
            db, field_id, "ndmi", year=year if year is not None else latest.captured_at.year,
            as_of=as_of,
        )
        drought = detectors.detect_drought(
            precip_14d,
            gdd_14d,
            gdd_days,
            latest.captured_at,
            ndmi_mean=ndmi_mean,
            ndvi_mean=latest.ndvi_mean,
        )

    culture_name: str | None = None
    if latest is not None:
        culture_name = detectors.current_culture_name(db, field_id, latest.captured_at)

    # перезимовка озимых (§7.7 #129): зимние сцены (дек–мар) сезона; сезон —
    # год последней сцены (декабрьские сцены — уже сезон следующего года)
    winterkill: InsightItem | None = None
    if latest is not None:
        season_year = (
            year
            if year is not None
            else latest.captured_at.year + (1 if latest.captured_at.month == 12 else 0)
        )
        winterkill = detectors.detect_winterkill(
            detectors.load_winter_pairs(db, field_id, season_year, as_of=as_of), culture_name
        )

    candidates: list[InsightItem | None] = [
        detectors.detect_ndvi_drop(scenes),
        detectors.detect_ndvi_stagnation(scenes, culture_name),
        detectors.detect_waterlogging(scenes, precip_map),
        drought,
        winterkill,
        detectors.detect_ndvi_lag_last_year(scenes),
        detectors.detect_agrochem_low(detectors.latest_agrochem(db, field_id, as_of=as_of)),
        detectors.detect_vra_heterogeneity(vra_peaks),
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
    db: Session,
    field: Field,
    background: BackgroundTasks,
    settings: Settings,
    *,
    year: int | None = None,
) -> InsightsResponse:
    """Ответ GET /fields/{id}/insights (§7.4): items синхронно, summary из кэша.

    year (§7.5) — сезон YYYY: точка привязки кэша — последняя сцена сезона;
    сезона нет — 404 season_not_found. Ключ insights:{field}:{date} уже
    содержит дату сезона, так что кэши сезонов не пересекаются.
    """
    scene = vra.latest_scene(db, field.id, year=year)
    if scene is None:
        if year is not None:
            raise AppError(
                f"У поля {field.id} нет NDVI-сцен сезона {year}",
                code="season_not_found",
                title="Сезон не найден",
                status_code=404,
            )
        items = build_items(db, field.id)  # нет сцен — нет точки привязки кэша
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
        if resp.summary is None and llm.llm_enabled(settings):
            # прошлая фоновая доработка не успела/не дождалась очереди (#101):
            # перезапускаем, пока в кэше rules-ответ без сводки
            background.add_task(refresh_insights, field.id, day, year)
        return resp

    vra_peaks = _vra_peaks_cached(field.id, year)
    items = build_items(
        db,
        field.id,
        vra_peaks=vra_peaks,
        year=year,
    )
    resp = InsightsResponse(
        field_id=field.id,
        generated_at=datetime.now(UTC),
        source="rules",
        # сводки пока нет: LLM обязателен и при items=[] (§8.2 v1.3, #138).
        # Деградация: провайдер выключен и детекторы молчат — статическая
        # строка сразу (source=rules); при непустых items — null как раньше
        summary=(NO_DEVIATIONS_SUMMARY if not items and not llm.llm_enabled(settings) else None),
        items=items,
    )
    cache_set(key, resp.model_dump(mode="json"), INSIGHTS_TTL_SECONDS)

    if llm.llm_enabled(settings):
        # фоновая доработка: VRA по COG + LLM-вердикт (§8.2 v1.3 — включая
        # items=[]; повторный запрос увидит). В tests LLM выключен — фон
        # не запускается вовсе.
        background.add_task(refresh_insights, field.id, day, year)
    return resp


def _vra_peaks_cached(
    field_id: uuid.UUID, year: int | None = None
) -> list[tuple[date, float, float]]:
    """(дата, IQR, spatial_spread) пиковых сцен из кэша VRA (§8.2 v1.7, #121).

    vra.py кладёт указатель текущих зон отдельно (ключ vra:peak:{field_id};
    сезонный год — §7.5 — с суффиксом :{year}) и с v1.7 хранит метрики ВСЕХ
    пиковых сцен (ключ «peaks»). Старый формат без «peaks» — одна пиковая
    сцена с spread=0.0 (детектор доработает по IQR при пересчёте зон).
    Битый/отсутствующий указатель — [] (детектор молчит).
    """
    key = f"vra:peak:{field_id}" if year is None else f"vra:peak:{field_id}:{year}"
    data = cache_get(key)
    if not isinstance(data, dict):
        return []
    raw_peaks = data.get("peaks")
    if not isinstance(raw_peaks, list):  # старый формат указателя
        raw_peaks = [
            {"date": data.get("date"), "iqr_ndvi": data.get("iqr_ndvi"), "spatial_spread": 0.0}
        ]
    peaks: list[tuple[date, float, float]] = []
    for row in raw_peaks:
        if not isinstance(row, dict):
            continue
        try:
            peaks.append(
                (
                    date.fromisoformat(str(row["date"])),
                    float(row["iqr_ndvi"]),
                    float(row.get("spatial_spread", 0.0)),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return peaks


#: вердикт модели дифвнесения для контекста LLM (§8.2, #105), когда расчёта нет
VRA_NO_DATA_VERDICT = "Модель дифвнесения: нет расчёта."


def _llm_context(
    db: Session,
    field: Field,
    items: list[InsightItem],
    *,
    vra_verdict: str = VRA_NO_DATA_VERDICT,
    year: int | None = None,
) -> str:
    """Контекст для LLM: поле, культура, агрохимия, работы, NDVI-динамика
    и вердикты моделей вымокания/дифвнесения (§8.2, #105).

    year (§7.5) — сезон YYYY: культура — main-цикл года, NDVI-серия сезона.
    """
    parts = [f"Поле «{field.name}», площадь {float(field.area_ga or 0):.1f} га."]
    if year is not None:
        # сезонный контекст (§7.5): культура основного цикла года YYYY
        cycle = db.execute(
            select(CropCycle, Culture.name)
            .join(Culture, CropCycle.culture_id == Culture.id)
            .where(
                CropCycle.field_id == field.id,
                CropCycle.year == year,
                CropCycle.kind == "main",
            )
            .limit(1)
        ).first()
    else:
        cycle = db.execute(
            select(CropCycle, Culture.name)
            .join(Culture, CropCycle.culture_id == Culture.id)
            .where(CropCycle.field_id == field.id)
            .order_by(CropCycle.year.desc())
            .limit(1)
        ).first()
    if cycle is not None:
        season_word = f"сезона {year}" if year is not None else "текущего сезона"
        parts.append(f"Культура {season_word}: {cycle[1]} ({cycle[0].year}).")
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

    scenes = detectors.load_scenes(db, field.id, year=year)
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

    # новые индексы §7.7 в рассуждениях LLM (§8.2, #153): NDRE/NDMI/EVI/NDSI/BSI
    season_year = year if year is not None else (scenes[-1].captured_at.year if scenes else None)
    extra = detectors.extra_indices_context(db, field.id, year=season_year)
    if extra:
        parts.append(extra)

    parts.append(vra_verdict)
    parts.append(
        "Детекторы (items):\n"
        + json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)
    )
    return "\n".join(parts)


def _vision_renders(
    field_id: uuid.UUID, latest_day: date, peak_day: date | None
) -> list[tuple[str, bytes]]:
    """PNG-рендеры для vision-сводки (§8.5, #117/#153): ≤ VISION_MAX_IMAGES.

    Приоритет слотов (#153): NDVI последней чистой сцены, NDVI пика
    вегетации, NDRE последней сцены (азотные зоны видны даже на пике
    вегетации), затем truecolor/NDWI последней.

    Нет render_dir или файлов (рендер не строился, сцена старая) — пустой
    список: сводка уйдёт текстовым PROMPT (деградация как при ollama).
    """
    render_dir = get_settings().ndvi_render_dir
    if not render_dir:
        return []
    out: list[tuple[str, bytes]] = []

    def _try(day: date | None, variant: str) -> None:
        if day is None or len(out) >= VISION_MAX_IMAGES:
            return
        path = render_path(render_dir, field_id, variant, day)
        if not path.is_file():
            return
        try:
            out.append((f"{variant} за {day.isoformat()}", path.read_bytes()))
        except OSError:
            logger.warning("insights: рендер %s не читается", path)

    _try(latest_day, "ndvi")
    _try(peak_day, "ndvi")
    _try(latest_day, "ndre")
    _try(latest_day, "truecolor")
    _try(latest_day, "ndwi")
    return out


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
    logger.warning(
        "insights: LLM-сводка с посторонними числами — fallback на items; "
        "отброшенная сводка: %.300s",
        summary,
    )
    fallback = " ".join(f"{item.title}: {item.detail}" for item in items)
    # items=[] → fallback пуст: None (не пустая строка) — деградация #138
    return fallback[:2000] if fallback else None


def refresh_insights(field_id: uuid.UUID, day: date, year: int | None = None) -> None:
    """Фоновая доработка (BackgroundTasks): VRA-неоднородность + LLM-сводка.

    year (§7.5) — сезон YYYY (точка привязки day = последняя сцена сезона;
    без year — текущий сезон). Любые ошибки — только в лог: пользователь
    получает rules-ответ из кэша, повторный запрос перезапустит доработку,
    пока summary в кэше null (#101).

    Соединение БД НЕ держится во время инференса (#101): сессия закрывается
    до вызова LLM (до 600 с), иначе параллельные фоновые задачи исчерпывают
    QueuePool Postgres («idle in transaction» на всё время вызова). После
    LLM нужна только запись в кэш (Redis) — БД не открывается.
    """
    # 1) сбор данных и контекста — с сессией БД
    db = SessionLocal()
    try:
        field = db.get(Field, field_id)
        if field is None:
            return
        scene = vra.latest_scene(db, field_id, year=year)
        if scene is None or scene.captured_at != day:
            return  # появилась новая сцена — её ключ дорабатывается отдельно

        vra_peaks: list[tuple[date, float, float]] = []
        vra_verdict = VRA_NO_DATA_VERDICT
        peak_day: date | None = None
        try:
            # зоны по пиковым сценам (#106): ворота однородности удалены (§7.4
            # v1.7) — вердикт всегда о построенных зонах и метриках пиков
            result = vra.vra_zones(db, field, year=year)  # кэшируется у себя (24 ч)
            peak_day = result.captured_at
            vra_peaks = _vra_peaks_cached(field_id, year) or [
                (result.captured_at, result.iqr_ndvi, result.spatial_spread)
            ]
            vra_verdict = (
                f"Модель дифвнесения: {len(result.zones)} зон дифвнесения, "
                f"IQR NDVI = {result.iqr_ndvi:.3f}, пространственный разброс = "
                f"{result.spatial_spread:.3f} "
                f"(пиковая сцена {result.captured_at.isoformat()})."
            )
        except AppError:
            # нет ассетов/сцен — вердикт «нет расчёта»
            pass
        except Exception:
            logger.warning("insights: VRA-расчёт не удался", exc_info=True)

        items = build_items(db, field_id, vra_peaks=vra_peaks, year=year)
        # контекст обязателен и при items=[] (§8.2 v1.3, #138): вердикт
        # «агронома по полю» строится по NDVI-динамике/агрохимии/работам
        context = _llm_context(db, field, items, vra_verdict=vra_verdict, year=year)
    finally:
        db.close()  # вернуть соединение в пул ДО тяжёлого шага

    # 2) инференс LLM — без БД, с ограничением параллелизма (CPU, #101).
    #    v1.3 (#138): вердикт обязателен и при items=[] — short-circuit убран.
    summary: str | None = None
    if not _llm_semaphore.acquire(timeout=LLM_QUEUE_TIMEOUT_SECONDS):
        logger.warning(
            "insights: очередь LLM (>%s c) — сводка поля %s отложена до повторного запроса",
            LLM_QUEUE_TIMEOUT_SECONDS,
            field_id,
        )
        return
    try:
        # vision-сводка (§8.5, #117): рендеры приложены только облачному
        # провайдеру (REQ-F-036); ollama/нет файлов — текстовый PROMPT
        prompt = PROMPT
        images: list[bytes] = []
        settings = get_settings()
        if settings.llm_provider.strip().lower() == "anthropic" and llm.llm_enabled(settings):
            renders = _vision_renders(field_id, day, peak_day)
            if renders:
                images = [png for _, png in renders]
                # подпись рендеров в context — их даты легальны для guardrail
                context += (
                    "\nПриложены рендеры поля (изображения): "
                    + ", ".join(label for label, _ in renders)
                    + "."
                )
                prompt = VISION_PROMPT
        summary = _checked_summary(
            llm.summarize(
                prompt,
                context,
                timeout=LLM_BACKGROUND_TIMEOUT_SECONDS,
                attempts=1,
                keep_alive=LLM_KEEP_ALIVE,
                num_predict=LLM_NUM_PREDICT,
                images=images or None,
            ),
            items,
            context,
        )
        # #131: после guardrail — санитайзер (вывод без markdown)
        summary = strip_markdown(summary) if summary is not None else None
    finally:
        _llm_semaphore.release()
    llm_ok = summary is not None  # вердикт LLM прошёл guardrail и санитайзер
    if summary is None and not items:
        # деградация §8.2 v1.3: провайдер недоступен/вернул пусто и детекторы
        # молчат — статическая строка (source=rules); при непустых items
        # summary остаётся null и доработка перезапустится (#101)
        summary = NO_DEVIATIONS_SUMMARY

    # 3) публикация — только Redis, сессия БД не нужна
    resp = InsightsResponse(
        field_id=field_id,
        generated_at=datetime.now(UTC),
        source="rules+llm" if llm_ok else "rules",
        summary=summary,
        items=items,
    )
    cache_set(_key(field_id, day), resp.model_dump(mode="json"), INSIGHTS_TTL_SECONDS)


__all__ = [
    "INSIGHTS_CACHE_VERSION",
    "INSIGHTS_TTL_SECONDS",
    "LLM_BACKGROUND_TIMEOUT_SECONDS",
    "LLM_NUM_PREDICT",
    "NO_DEVIATIONS_SUMMARY",
    "PROMPT",
    "VISION_PROMPT",
    "VISION_MAX_IMAGES",
    "build_items",
    "get_insights",
    "refresh_insights",
    "strip_markdown",
]
