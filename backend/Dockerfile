# Образ API: Python 3.12, приложение в /srv/app
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq для psycopg (binary-колёса уже включают её; curl не нужен — healthcheck на python)
WORKDIR /srv/app

COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.32" \
    "sqlalchemy>=2.0" "alembic>=1.13" "psycopg[binary]>=3.2" \
    "pydantic>=2.9" "pydantic-settings>=2.6" "redis>=5.2"

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Миграции применяются при старте; затем запускается API
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
