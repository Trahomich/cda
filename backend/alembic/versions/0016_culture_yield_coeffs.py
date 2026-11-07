"""Стартовые коэффициенты модели NDVI→урожайность в ref_culture (v3-ai, #95).

Контракт: docs/06-data-api.md §7.4 «Прогноз урожайности», docs/09-ai.md §8.3.
Линейная эмпирика `forecast = k_base + k_ndvi_max·ndvi_max +
k_ndvi_int·ndvi_integral` (ц/га; ndvi_integral = Σ(ndvi−0.2)+ по чистым
сценам сезона / 100 — уже делённое, см. сервис). Колонки decimal(6,2) NULL:
NULL у культуры → GET /fields/{id}/yield-forecast отвечает 422 yield_no_model.
Значения наполняет сид (app/seed.py: пшеница, ячмень, кукуруза,
подсолнечник, рапс), существующие строки актуализируются через
ON CONFLICT DO UPDATE; пересчёт по накопленным сезонам — не MVP (09 §8.3).

Revision ID: 0016_culture_yield_coeffs
Revises: 0015_forecast_precip_prob
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0016_culture_yield_coeffs"
down_revision = "0015_forecast_precip_prob"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("yield_k_base", "yield_k_ndvi_max", "yield_k_ndvi_int")
