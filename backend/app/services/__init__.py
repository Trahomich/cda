"""Сервисы-воркеры приёма внешних данных (ADR-09, docs/02-architecture.md §8).

Каждый источник — отдельный контейнер без внешнего API, пишет в общую
PostgreSQL через те же модели и ingest-слой, что и api. Точка входа сервиса:
``python -m app.services.<name>`` (например, ``app.services.weather_ingest``).
"""
