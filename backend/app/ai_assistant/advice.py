"""AI-рекомендации «что делать сейчас» (docs/09-ai.md §9, эпик #118/#119).

Контекст §9.1 собирается только из БД ЦДА (пользовательский ввод не
попадает): поле, севооборот, агрохимия, работы, NDVI-сезон, погода,
вердикты моделей + PNG-рендеры NDVI (файловые §7.3, ≤2) — только для
anthropic-провайдера (vision). Генерация — LLM glm-5.3-flash с промптом
§9.2; guardrail: числа ответа обязаны встречаться в context/items, иначе
повтор с уточнением, затем fallback на конкатенацию items §8.2.

Запись — ai_field_advice (§9.3): только ok/fallback; empty (пустой сезон)
и ошибки провайдера не пишем (§9.5). advice_date = дата генерации.

Провайдер: только anthropic (решение #358): при LLM_PROVIDER=ollama/без
ключа advice не формируется (деградация §9), детекторы §8.2 работают.

Инвариант #101 сохранён: сессия БД закрывается ДО инференса; запись —
отдельной короткой сессией после.
"""

import hashlib
import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.ai_assistant import detectors, llm, service
from app.ai_assistant.schemas import InsightItem
from app.core.cache import cache_get
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.agronomy import AgroInput, CropCycle, Field, FieldOperation, Recommendation
from app.models.ai import AIFieldAdvice
from app.models.integrations import Forecast, SatelliteScene
from app.models.references import Culture
from app.modules.satellite.waterlog import precip_sums_7d, waterlogged
from app.services.sentinel_ingest.renders import NDVI_RENDER_HI, NDVI_RENDER_LO, render_path

logger = logging.getLogger(__name__)

#: «чистая» сцена для ИИ-продуктов (docs/06 §7.4) — как в satellite/router
CLEAN_CLOUD_PCT = 60

#: таймаут инференса advice: облако отвечает за секунды (§9.4 — 120 с)
ADVICE_LLM_TIMEOUT_SECONDS = 120.0

#: максимум PNG-рендеров в одном запросе (§9.1: не более 2 картинок)
MAX_IMAGES = 3  # NDVI-последняя, NDVI-пик, NDRE-последняя (§9.1, #153)

ADVICE_PROMPT = (
    "Ты — агроном-ассистент цифрового двойника хозяйства. Сегодня {date}. "
    "По данным хозяйства (context, включая спутниковые снимки) дай "
    "рекомендации на текущий момент: 1) что сделать или проверить в ближайшие "
    "дни с приоритетами; 2) окно работ с учётом прогноза погоды; 3) на что "
    "обратить внимание по снимкам. 2–4 абзаца или короткий список. Только "
    "факты из данных: числа (даты, дозы, осадки, NDVI) можно брать только из "
    "context, свои нормативы и числа не выдумывать. Даты и числа приводи "
    "точно в том виде, как они записаны в context: не переводи форматы, не "
    "округляй, не пересчитывай и не суммируй. В context есть значения других "
    "индексов — NDRE (азотный статус: обоснование дифвнесения азота), NDMI "
    "(влажность биомассы: риск засухи до падения NDVI), EVI (вегетация на "
    "ранних фазах), NDSI (снег: перезимовка озимых), BSI (голая почва: "
    "контроль обработок) — используй их в рекомендациях, интерпретируя по "
    "смыслу индекса. По снимкам описывай только "
    "качественные наблюдения (пятна, градиенты), без числовых оценок NDVI "
    "и без цитирования шкалы легенды рендера. "
    "Ты советуешь — решение "
    "принимает агроном. Пиши простым текстом без markdown-разметки "
    "(без *, **, #, _, `), списки — через тире с новой строки."
)

_GUARDRAIL_RETRY_SUFFIX = (
    "\n\nВажно: в предыдущем ответе были числа, которых нет в данных "
    "хозяйства. Ответь снова, используя только числа из context."
)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

#: даты в ответе/контексте: ISO «2026-07-05» и точечные «17.08»/«05.11.2026»
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DOT_DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b")

#: честный fallback при guardrail-провале и пустых детекторах (§9.2, #144):
#: «данных недостаточно» вводило в заблуждение — контекст собран, не прошла
#: проверка чисел модели
_GUARDRAIL_FAILED_ANSWER = (
    "Не удалось получить рекомендацию модели: ответ не прошёл проверку по "
    "данным хозяйства. Показатели поля — в разделе «Выводы ассистента»."
)

_OPERATION_TITLES = dict(service._OPERATION_TITLES)  # noqa: SLF001 — общие названия работ


@dataclass(frozen=True, slots=True)
class AdviceContext:
    """Собранный контекст §9.1: текст, его sha256 и PNG-рендеры (≤2)."""

    context: str
    context_hash: str
    images: list[bytes] = field(default_factory=list)


def _season_scene_rows(
    db: Session, field_id: uuid.UUID, year: int, *, as_of: date | None = None
) -> list[SatelliteScene]:
    """Чистые NDVI-сцены сезона year по возрастанию даты (§7.5).

    as_of (ретро #159) — только сцены, снятые не позже даты.
    """
    filters = [
        SatelliteScene.field_id == field_id,
        SatelliteScene.index == "ndvi",
        SatelliteScene.cloud_pct <= CLEAN_CLOUD_PCT,
        func.extract("year", SatelliteScene.captured_at) == year,
    ]
    if as_of is not None:
        filters.append(SatelliteScene.captured_at <= as_of)
    return list(
        db.scalars(
            select(SatelliteScene)
            .where(*filters)
            .order_by(SatelliteScene.captured_at, SatelliteScene.created_at)
        )
    )


def _latest_clean_scene_year(
    db: Session, field_id: uuid.UUID, *, as_of: date | None = None
) -> int | None:
    filters = [
        SatelliteScene.field_id == field_id,
        SatelliteScene.index == "ndvi",
        SatelliteScene.cloud_pct <= CLEAN_CLOUD_PCT,
    ]
    if as_of is not None:
        filters.append(SatelliteScene.captured_at <= as_of)
    return db.scalar(
        select(func.extract("year", func.max(SatelliteScene.captured_at))).where(*filters)
    )


def _fmt(v: float | None, nd: int = 1) -> str:
    return "нет" if v is None else f"{float(v):.{nd}f}"


def build_advice_context(
    db: Session, field_id: uuid.UUID, today: date, *, as_of: date | None = None
) -> AdviceContext | None:
    """Контекст рекомендации §9.1; None — пустой сезон (чистых сцен нет).

    Ретро-режим (#159): as_of=T — контекст «на дату T», только данные
    ДО T включительно (сцены, погода-факты, работы, агрохимия, индексы);
    прогноза нет — секции будущих данных не пишутся.
    Региона в модели Field нет — секция опущена (решение Ивана, #381).
    """
    retro = as_of is not None
    day = as_of if as_of is not None else today
    f = db.get(Field, field_id)
    if f is None:
        return None
    season_year = _latest_clean_scene_year(db, field_id, as_of=day if retro else None)
    if season_year is None:
        return None
    season_year = int(season_year)
    parts: list[str] = [f"Поле «{f.name}», площадь {float(f.area_ga):.1f} га, сезон {season_year}."]
    if retro:
        parts.append(
            f"Ретроспективная рекомендация на дату {day.isoformat()}: доступны только "
            "данные, полученные до этой даты включительно; прогноза погоды нет."
        )

    # 2. культура сезона + история севооборота (последние 5 main-циклов);
    # ретро: цикл «будущего» сезона известен только с осени (посев озимых)
    pred_cycle = aliased(CropCycle)
    pred_culture = aliased(Culture)
    cycle_filters = [CropCycle.field_id == field_id, CropCycle.kind == "main"]
    if retro:
        cycle_filters.append(CropCycle.year <= season_year + (1 if day.month >= 9 else 0))
    rows = db.execute(
        select(CropCycle, Culture.name, pred_culture.name)
        .join(Culture, CropCycle.culture_id == Culture.id)
        .outerjoin(pred_cycle, CropCycle.predecessor_cycle_id == pred_cycle.id)
        .outerjoin(pred_culture, pred_cycle.culture_id == pred_culture.id)
        .where(*cycle_filters)
        .order_by(CropCycle.year.desc())
        .limit(5)
    ).all()
    if rows:
        history = ", ".join(
            f"{r[0].year} — {r[1]}" + (f" (предшественник: {r[2]})" if r[2] else "") for r in rows
        )
        parts.append(f"Севооборот (последние циклы): {history}.")

    # 3. агрохимия: последняя проба (ретро — взятая не позже даты)
    probe = detectors.latest_agrochem(db, field_id, as_of=day if retro else None)
    if probe is not None:
        parts.append(
            f"Агрохимия (проба {probe.sample_date.isoformat()}): "
            f"pH {_fmt(probe.ph, 1)}, гумус {_fmt(probe.humus_pct, 1)} %, "
            f"P2O5 {_fmt(probe.p2o5_mg100, 1)} мг/100 г, "
            f"K2O {_fmt(probe.k2o_mg100, 1)} мг/100 г."
        )

    # 4. работы: план (незавершённые) + журнал (последние 10); ретро —
    # только работы, датируемые не позже даты (планы на будущее не видны)
    op_filters = [FieldOperation.field_id == field_id]
    if retro:
        op_filters.append(FieldOperation.date <= day)
    plan_ops = db.execute(
        select(FieldOperation, AgroInput.name)
        .outerjoin(AgroInput, FieldOperation.input_id == AgroInput.id)
        .where(
            *op_filters,
            FieldOperation.status.in_(("draft", "planned", "in_progress")),
        )
        .order_by(FieldOperation.date)
        .limit(10)
    ).all()
    if plan_ops:
        plan = ", ".join(
            f"{op.date.isoformat()} {_OPERATION_TITLES.get(op.operation_type, op.operation_type)}"
            + (f" ({name})" if name else "")
            for op, name in plan_ops
        )
        parts.append(f"План работ (незавершённые): {plan}.")
    journal_ops = db.execute(
        select(FieldOperation, AgroInput.name)
        .outerjoin(AgroInput, FieldOperation.input_id == AgroInput.id)
        .where(FieldOperation.field_id == field_id, FieldOperation.status == "done")
        .order_by(FieldOperation.date.desc())
        .limit(10)
    ).all()
    if journal_ops:
        journal = ", ".join(
            f"{op.date.isoformat()} {_OPERATION_TITLES.get(op.operation_type, op.operation_type)}"
            + (f" ({name})" if name else "")
            for op, name in reversed(journal_ops)
        )
        parts.append(f"Журнал работ (последние {len(journal_ops)}): {journal}.")

    # 5. NDVI сезона: точки avg/min/max + пик вегетации (ретро — сцены ≤ T)
    scenes = _season_scene_rows(db, field_id, season_year, as_of=day if retro else None)
    peak = max(scenes, key=lambda s: float(s.index_avg)) if scenes else None
    if scenes:
        points = ", ".join(
            f"{s.captured_at.isoformat()}: avg {float(s.index_avg):.3f}"
            f"/min {float(s.index_min):.3f}/max {float(s.index_max):.3f}"
            + (" [пик вегетации]" if s is peak else "")
            for s in scenes
        )
        parts.append(f"NDVI сезона {season_year} (дата: avg/min/max): {points}.")

    # 6. погода: факт 14 сут до даты; прогноз 7 сут — только в live-режиме
    precip14, gdd14, gdd_days = detectors.weather_window_14d(db, field_id, day)
    if precip14 is not None:
        parts.append(
            f"Погода, факт 14 сут до {day.isoformat()}: осадки "
            f"{precip14:.1f} мм, сумма GDD {gdd14 or 0:.0f} °C·сут "
            f"({gdd_days} сут с температурами)."
        )
    forecasts: Sequence[Forecast] = []
    if not retro:
        forecasts = list(
            db.scalars(
                select(Forecast)
                .where(
                    Forecast.field_id == field_id,
                    Forecast.forecast_for >= today,
                    Forecast.forecast_for <= today + timedelta(days=6),
                )
                .order_by(Forecast.forecast_for, Forecast.issued_at.desc())
            )
        )
    latest_by_day: dict[date, Forecast] = {}
    for fc in forecasts:  # issued_at desc в пределах дня — первый и есть свежий
        latest_by_day.setdefault(fc.forecast_for, fc)
    if latest_by_day:
        fc_line = ", ".join(
            f"{d.isoformat()}: осадки {_fmt(fc.precipitation_mm)} мм "
            f"(риск {_fmt(fc.precip_prob_pct, 0)} %), "
            f"t {_fmt(fc.temperature_min_c)}…{_fmt(fc.temperature_max_c)} °C"
            for d, fc in sorted(latest_by_day.items())
        )
        parts.append(f"Прогноз погоды на 7 сут: {fc_line}.")

    # 7. выводы моделей: вымокания, АХО, VRA, урожайность
    precip_map = precip_sums_7d(db, field_id, [s.captured_at for s in scenes]) if scenes else {}
    episode_dates = [
        s.captured_at.isoformat()
        for s in scenes
        if waterlogged(s.ndwi_p90, s.index_avg, precip_map.get(s.captured_at)) is True
    ]
    if episode_dates:
        parts.append(f"Модель вымокания: эпизоды по сценам {', '.join(episode_dates)}.")
    else:
        parts.append(f"Модель вымокания: признаков вымокания нет по {len(scenes)} сценам.")
    rec_filters = [
        Recommendation.target_type == "field",
        Recommendation.target_id == field_id,
        Recommendation.status == "new",
    ]
    if retro:
        rec_filters.append(
            Recommendation.created_at <= datetime.combine(day, dtime.max, tzinfo=UTC)
        )
    active_recs = db.scalars(
        select(Recommendation)
        .where(*rec_filters)
        .order_by(Recommendation.created_at.desc())
        .limit(5)
    ).all()
    if active_recs:
        parts.append(
            "Активные рекомендации системы: "
            + "; ".join(f"[{r.rule_id}] {r.action}" for r in active_recs)
        )
    if retro:
        # VRA-вердикт из кэша строился бы по ВСЕМ сценам сезона (вкл. будущее
        # после T) — в ретро честная строка по пикам до даты, без зон по COG
        if peak is not None:
            parts.append(
                "Модель дифвнесения: ретро-режим — зоны не пересчитываются; "
                f"пик NDVI до даты: {peak.captured_at.isoformat()} "
                f"(avg {float(peak.index_avg):.3f})."
            )
    else:
        parts.append(_vra_verdict_line(field_id, season_year))

    # 8. Другие индексы сезона (§9.1, #153): NDRE/NDMI/EVI/NDSI/BSI —
    # ассистент аргументирует азот/влажность/перезимовку данными индексов
    extra = detectors.extra_indices_context(
        db, field_id, year=season_year, as_of=day if retro else None
    )
    if extra:
        parts.append(extra)
    yield_line = _yield_verdict(db, field_id, season_year, scenes)
    if yield_line:
        parts.append(yield_line)

    # шкала легенды рендеров §7.3 — числа детерминированы бэкендом: LLM
    # цитирует легенду («шкала −0.10…0.80») → диапазон легален через context
    # (guardrail не флапает), сам текст — до хэша (#145)
    settings = get_settings()
    if settings.ndvi_render_dir and scenes:
        parts.append(
            "Рендеры NDVI (приложены): шкала раскраски "
            f"от {NDVI_RENDER_LO:.2f} до {NDVI_RENDER_HI:.2f}."
        )

    context = "\n".join(parts)
    context_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()

    # 9. PNG-рендеры NDVI (§7.3, файловые): последняя чистая + пик, ≤2;
    # NDRE последней сцены — третий слот при наличии (§9.1, #153)
    images: list[bytes] = []
    render_dir = settings.ndvi_render_dir
    if render_dir and scenes:
        seen_days: set[tuple[date, str]] = set()
        for img_day, variant in (
            (scenes[-1].captured_at, "ndvi"),
            (peak.captured_at if peak else None, "ndvi"),
            (scenes[-1].captured_at, "ndre"),  # азотные зоны, #153
        ):
            if img_day is None or (img_day, variant) in seen_days or len(images) >= MAX_IMAGES:
                continue
            seen_days.add((img_day, variant))
            path = render_path(render_dir, field_id, variant, img_day)
            if path.is_file():
                try:
                    images.append(path.read_bytes())
                except OSError:
                    logger.warning("advice: рендер %s не читается", path)
    return AdviceContext(context=context, context_hash=context_hash, images=images)


def _vra_verdict_line(field_id: uuid.UUID, season_year: int) -> str:
    """VRA-вердикт §9.1 п.7 из кэша VRA (без COG-пересчёта, #121).

    Указатель vra:peak:{field_id}[:{year}] (его пишет vra_zones) содержит
    дату сцены-вердикта и метрики пиков; число зон — из кэша зон
    vra:{field_id}:{date} (24 ч). Нет указателя/кэша — «нет расчёта».
    """
    pointer = cache_get(f"vra:peak:{field_id}:{season_year}")
    if not isinstance(pointer, dict):
        pointer = cache_get(f"vra:peak:{field_id}")  # ключ текущего сезона
    if not isinstance(pointer, dict):
        return "Модель дифвнесения: нет расчёта."

    peaks_raw = pointer.get("peaks")
    if isinstance(peaks_raw, list) and peaks_raw:
        peak_dates = ", ".join(
            str(p.get("date")) for p in peaks_raw if isinstance(p, dict) and p.get("date")
        )
    else:  # старый формат указателя (одна пиковая сцена)
        peak_dates = str(pointer.get("date") or "")

    verdict_date = pointer.get("date")
    iqr = pointer.get("iqr_ndvi")
    spread = pointer.get("spatial_spread")
    zone_count: int | None = None
    if verdict_date:
        zones_cache = cache_get(f"vra:{field_id}:{verdict_date}")
        if isinstance(zones_cache, dict) and isinstance(zones_cache.get("zones"), list):
            zone_count = len(zones_cache["zones"])

    details: list[str] = []
    if iqr is not None:
        details.append(f"IQR NDVI = {float(iqr):.3f}")
    if spread is not None:
        details.append(f"пространственный разброс = {float(spread):.3f}")
    if zone_count is not None:
        details.append(f"зон дифвнесения {zone_count} (/vra)")
    else:
        details.append("нет расчёта зон")
    line = "Модель дифвнесения: "
    if peak_dates:
        line += f"пиковые сцены {peak_dates}; "
    line += f"вердикт по сцене {verdict_date}: " + ", ".join(details) + "."
    return line


def _yield_verdict(
    db: Session, field_id: uuid.UUID, year: int, scenes: list[SatelliteScene]
) -> str | None:
    """Прогноз урожайности (§8.3, та же эмпирика, что API-53)."""
    if len(scenes) < 3:
        return None
    row = db.execute(
        select(CropCycle, Culture)
        .join(Culture, CropCycle.culture_id == Culture.id)
        .where(CropCycle.field_id == field_id, CropCycle.year == year, CropCycle.kind == "main")
    ).first()
    if row is None:
        return None
    _, culture = row
    k_base, k_max, k_int = culture.yield_k_base, culture.yield_k_ndvi_max, culture.yield_k_ndvi_int
    if culture.kind == "fallow" or k_base is None or k_max is None or k_int is None:
        return None
    avgs = [float(s.index_avg) for s in scenes]
    ndvi_max = max(avgs)
    ndvi_integral = sum(max(v - 0.2, 0.0) for v in avgs) / 100
    forecast = float(k_base) + float(k_max) * ndvi_max + float(k_int) * ndvi_integral
    return (
        f"Прогноз урожайности ({culture.name}, {year}): {forecast:.2f} "
        f"ц/га (интервал {forecast * 0.75:.2f}–{forecast * 1.25:.2f})."
    )


def _date_pairs(text: str) -> set[tuple[int, int]]:
    """Пары (день, месяц) всех дат текста — ISO и точечных («17.08», «05.11»)."""
    pairs: set[tuple[int, int]] = set()
    for tok in _ISO_DATE_RE.findall(text):
        year, month, day = tok.split("-")
        pairs.add((int(day), int(month)))
    for tok in _DOT_DATE_RE.findall(text):
        day, month = tok.split(".")[:2]
        pairs.add((int(day), int(month)))
    return pairs


def _ok_number(token: str, allowed_nums: set[str], allowed_floats: list[float]) -> bool:
    """Число ответа допустимо: точное совпадение или округление разрешённого.

    LLM округляет («~0.20» при контекстном 0.204) — это не выдумка: дробный
    токен допустим, если совпадает с разрешённым с точностью до своей
    разрядности. Целый токен допустим только из целого разрешённого числа
    (иначе «0–4 %» просказывало бы через округление 0.4 и 4.0 — #144).
    """
    if token in allowed_nums:
        return True
    try:
        value = float(token)
    except ValueError:  # pragma: no cover — регэксп даёт только числа
        return False
    decimals = len(token.partition(".")[2])
    if decimals == 0:
        return any(round(v, 0) == value and float(v).is_integer() for v in allowed_floats)
    return any(round(v, decimals) == value for v in allowed_floats)


def _is_dot_date(token: str, allowed_pairs: set[tuple[int, int]]) -> bool:
    """Точечный токен — дата «DD.MM», чья пара (день, месяц) есть в контексте.

    Различает «17.08» (17 августа) от числа «3.5» (мм осадков): датой
    считается только валидный день 1–31 и месяц 1–12 с парой из контекста.
    """
    match = _DOT_DATE_RE.fullmatch(token)
    if match is None:
        return False
    day, month = token.split(".")[:2]
    d, m = int(day), int(month)
    return 1 <= d <= 31 and 1 <= m <= 12 and (d, m) in allowed_pairs


def _is_day_component(token: str, allowed_pairs: set[tuple[int, int]]) -> bool:
    """Целое 1–31 — день месяца из дат контекста («…08 и 09 осадки»)."""
    if not token.isdigit():
        return False
    day = int(token)
    return 1 <= day <= 31 and any(d == day for d, _ in allowed_pairs)


def _numbers_within(text: str, allowed_nums: set[str], allowed_pairs: set[tuple[int, int]]) -> bool:
    """Guardrail §9.2 (#144): числа и даты ответа обязаны быть из контекста.

    Даты обрабатываются отдельно от чисел: «17.08» — это 17 августа, а не
    число 17.08 (NDVI-масштаб), раньше такие токены ложно резались и все
    ответы падали в fallback. ISO-даты — по паре (день, месяц) контекста;
    точечный токен — сначала число, при промахе — дата с парой из контекста.
    """
    for tok in _ISO_DATE_RE.findall(text):
        year, month, day = tok.split("-")
        if (int(day), int(month)) not in allowed_pairs:
            return False
    rest = _ISO_DATE_RE.sub(" ", text)
    allowed_floats = [float(n) for n in allowed_nums]
    for token in set(_NUM_RE.findall(rest.replace(",", "."))):
        if _ok_number(token, allowed_nums, allowed_floats):
            continue
        if _is_dot_date(token, allowed_pairs):
            continue
        if _is_day_component(token, allowed_pairs):
            continue
        return False
    return True


def _allowed_numbers(context: str, items: list[InsightItem]) -> set[str]:
    allowed = set(_NUM_RE.findall(_ISO_DATE_RE.sub(" ", context).replace(",", ".")))
    allowed |= set(
        _NUM_RE.findall(
            json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False).replace(
                ",", "."
            )
        )
    )
    return allowed


def _allowed_pairs(context: str, items: list[InsightItem]) -> set[tuple[int, int]]:
    """Пары (день, месяц) дат контекста и items (guardrail дат, #144)."""
    pairs = _date_pairs(context)
    pairs |= _date_pairs(json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False))
    return pairs


def generate_advice(
    field_id: uuid.UUID,
    *,
    today: date | None = None,
    as_of: date | None = None,
    skip_existing: bool = False,
) -> AIFieldAdvice | None:
    """Полный цикл генерации рекомендации для поля (§9.1–9.3).

    None — провайдер не anthropic/выключен, поля/сезона нет или LLM
    недоступен/пуст (§9.5: записи не создаём). Успех — запись ok/fallback
    в ai_field_advice (возвращается тем же объектом).

    Ретро-режим (#159): as_of=T — контекст и детекторы только по данным
    ≤ T, advice_date = T. skip_existing — идемпотентность CLI-бэкфилла:
    при существующей записи (поле, advice_date) инференс не выполняется.
    """
    settings = get_settings()
    if settings.llm_provider.strip().lower() != "anthropic" or not llm.llm_enabled(settings):
        return None  # деградация §9: advice только у облачного провайдера
    day = today or date.today()
    effective_date = as_of or day

    # 1) сбор контекста и items — с сессией БД (#101: закрыть до инференса)
    db = SessionLocal()
    try:
        if db.get(Field, field_id) is None:
            return None
        if skip_existing:
            exists = db.scalar(
                select(func.count())
                .select_from(AIFieldAdvice)
                .where(
                    AIFieldAdvice.field_id == field_id,
                    AIFieldAdvice.advice_date == effective_date,
                )
            )
            if exists:
                return None  # уже сгенерировано (идемпотентность #159)
        ctx = build_advice_context(db, field_id, day, as_of=as_of)
        if ctx is None:
            return None  # пустой сезон — empty не пишем (§9.3)
        items = service.build_items(db, field_id, as_of=as_of)
    finally:
        db.close()

    # 2) инференс — без БД; guardrail §9.2 (повтор с уточнением → fallback)
    prompt = ADVICE_PROMPT.format(date=effective_date.isoformat())
    model_name = (settings.llm_model or llm.ANTHROPIC_DEFAULT_MODEL).strip()
    answer, status = _infer_with_guardrail(prompt, ctx, items, model_name)
    if answer is None:
        return None  # провайдер недоступен/пуст — записи нет (§9.5)
    # #131: после guardrail — санитайзер (вывод без markdown, §9.2)
    answer = service.strip_markdown(answer)

    # 3) запись — короткой сессией
    db = SessionLocal()
    try:
        record = AIFieldAdvice(
            field_id=field_id,
            model=model_name,
            provider="anthropic",
            advice_date=effective_date,
            answer=answer,
            context_hash=ctx.context_hash,
            status=status,
        )
        db.add(record)
        db.commit()
        db.refresh(record)  # загрузить атрибуты до close (иначе detached)
        return record
    finally:
        db.close()


def _infer_with_guardrail(
    prompt: str, ctx: AdviceContext, items: list[InsightItem], model_name: str
) -> tuple[str | None, str]:
    """LLM-инференс с guardrail §9.2: ok / fallback / (None, —)."""
    allowed = _allowed_numbers(ctx.context, items)
    allowed_pairs = _allowed_pairs(ctx.context, items)
    answer = llm.summarize(
        prompt,
        ctx.context,
        timeout=ADVICE_LLM_TIMEOUT_SECONDS,
        attempts=1,
        provider="anthropic",
        model=model_name,
        images=ctx.images or None,
    )
    if answer is None:
        return None, ""
    if _numbers_within(answer, allowed, allowed_pairs):
        return answer, "ok"
    logger.warning("advice: посторонние числа в ответе — повтор с уточнением")
    retry = llm.summarize(
        prompt + _GUARDRAIL_RETRY_SUFFIX,
        ctx.context,
        timeout=ADVICE_LLM_TIMEOUT_SECONDS,
        attempts=1,
        provider="anthropic",
        model=model_name,
        images=ctx.images or None,
    )
    if retry is not None and _numbers_within(retry, allowed, allowed_pairs):
        return retry, "ok"
    logger.warning("advice: guardrail дважды не пройден — fallback на items §8.2")
    fallback = " ".join(f"{i.title}: {i.detail}" for i in items) or _GUARDRAIL_FAILED_ANSWER
    return fallback[:2000], "fallback"


#: фоновая очередь генерации (§9.4): сериализация по AI_ADVICE_PARALLELISM
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _worker_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=max(1, get_settings().ai_advice_parallelism),
                thread_name_prefix="ai-advice",
            )
        return _pool


def queue_advice(field_id: uuid.UUID, *, today: date | None = None) -> None:
    """Поставить генерацию рекомендации в фоновую очередь (§9.4).

    Ошибки не бросаются: worker всё логирует (§9.5 — очередь продолжает
    остальные поля).
    """
    _worker_pool().submit(_run_advice, field_id, today)


def _run_advice(field_id: uuid.UUID, today: date | None) -> None:
    try:
        generate_advice(field_id, today=today)
    except Exception:
        logger.warning("advice: генерация для поля %s не удалась", field_id, exc_info=True)


__all__ = [
    "ADVICE_LLM_TIMEOUT_SECONDS",
    "ADVICE_PROMPT",
    "AdviceContext",
    "MAX_IMAGES",
    "build_advice_context",
    "generate_advice",
    "queue_advice",
]
