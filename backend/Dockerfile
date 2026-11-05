# Образ API: Python 3.12, приложение в /srv/app
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq для psycopg (binary-колёса уже включают её; curl не нужен — healthcheck на python)
WORKDIR /srv/app

# Установка зависимостей из pyproject.toml ([project].dependencies),
# чтобы список не расходился с локальной разработкой
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic

# Миграции применяются при старте; затем запускается API
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
