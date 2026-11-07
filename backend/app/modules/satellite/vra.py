"""Зоны дифференцированного внесения (VRA) — docs/06 §7.4 (v3-04 #94, v1.4 #106).

Расчёт по требованию: ПИКОВЫЕ чистые (cloud ≤ 60) NDVI-сцены сезона —
топ-3 по index_avg (последние снимки осенью однородны: вегетации нет).
Для каждой окно COG b04/b08/scl перечитывается (как в рендерах §7.3:
целочисленное окно по контуру, SCL → сетка 10 м nearest, прореживание
до 1024 px), NDVI внутри контура (без облаков), IQR = p75 − p25.
Неоднородность подтверждена, если IQR ≥ 0.12 минимум на 2 из 3 пиковых
сцен («зоны повторяются из недели в неделю»); иначе 422 vra_uniform_field.
Зоны (кластеризация на 3 по перцентилям <30-й, 30–70-й, >70-й) и PNG
строятся по одной сцене — с максимальным index_avg среди подтверждённых.

Кэш 24 ч в Redis/Dragonfly (soft-fail, §7.4): полный расчёт — по
(field_id, дата пиковой сцены), IQR однородных сцен и указатель текущих
зон — отдельными ключами; PNG зон кэшируется вместе с зонами.

Дозы — рекомендация: dose_factor 1.15/1.0/0.85 (пониженное развитие —
дозу выше), база — последний approved план fertilizing (units kg_ha).
"""

import base64
import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import cache_get, cache_set
from app.core.errors import AppError
from app.integrations import DataGapError, SourceUnavailableError
from app.integrations.raster import compute_index_array, contour_mask
from app.models.agronomy import AgroInput, CropCycle, Field, TreatmentPlan
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.ndvi import (
    CLOUD_SCL_VALUES,
    _read_band_window,
    _read_scl_grid,
)
from app.services.sentinel_ingest.renders import MAX_SIDE_PX

logger = logging.getLogger(__name__)

#: минимальная неоднородность для построения зон (§7.4): IQR NDVI ≥ 0.12
VRA_MIN_IQR = 0.12

#: кэш расчёта по (field_id, дата сцены) — 24 ч (§7.4)
CACHE_TTL_SECONDS = 24 * 3600

#: зоны: подписи, множители дозы и цвета PNG (красный → жёлтый → зелёный)
ZONE_LABELS = ("пониженное развитие", "среднее развитие", "повышенное развитие")
ZONE_DOSE_FACTORS = (1.15, 1.0, 0.85)
ZONE_COLORS = ((192, 57, 43), (241, 196, 15), (39, 174, 96))
