# Единый вход для проверок backend (задача MVP-18).
# Основные цели: make up | migrate | seed | test | lint | check
# check = lint + test — минимальный набор для CI и ревью.

COMPOSE := docker compose

.PHONY: up down logs migrate seed seed-reset test lint check fmt

## up: поднять окружение (api + PostgreSQL/PostGIS + Redis), собрать образ
up:
	$(COMPOSE) up -d --build

## down: остановить окружение (данные в томах сохраняются)
down:
	$(COMPOSE) down

## logs: хвост логов сервиса api
logs:
	$(COMPOSE) logs -f api

## migrate: применить миграции Alembic до головы (в контейнере api)
migrate:
	$(COMPOSE) exec api alembic upgrade head

## seed: демо-хозяйство (MVP-13/#31): python -m app.cli seed-demo
## (справочники ref_* и погодные правила входят в сид; --reset пересоздаёт)
seed:
	python -m app.cli seed-demo

seed-reset:
	python -m app.cli seed-demo --reset

## test: юнит/интеграционные тесты (pytest)
test:
	python -m pytest

## smoke: дымовой e2e против поднятого окружения (make up): healthz/login/me
smoke:
	SMOKE=1 python -m pytest tests_e2e

## lint: ruff (lint + формат) и mypy
lint:
	ruff check .
	ruff format --check .
	mypy app

## fmt: автоправка ruff (формат и безопасные фиксы)
fmt:
	ruff check --fix .
	ruff format .

## check: полный прогон качества — то, что гоняет CI и ревьюер
check: lint test
