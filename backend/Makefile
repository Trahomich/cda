# Единый вход для проверок backend (задача MVP-18).
# Основные цели: make up | migrate | seed | test | lint | check
# check = lint + test — минимальный набор для CI и ревью.

COMPOSE := docker compose

.PHONY: up down logs migrate seed test lint check fmt

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

## seed: наполнить демо-хозяйство (появится в задаче MVP-13/#31)
seed:
	@echo "ОШИБКА: make seed ещё не реализован — цель-заглушка." >&2
	@echo "Демо-хозяйство (python -m app.cli seed-demo) появится в задаче #31 (MVP-13)." >&2
	@echo "Сейчас доступны: python -m app.cli create-admin | seed-references" >&2
	@exit 1

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
