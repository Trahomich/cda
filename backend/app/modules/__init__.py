"""Доменные модули ЦДА (docs/02-architecture.md §4).

Границы MVP: fields, rotation, inputs, weather, satellite, machinery.
mod-livestock — этап v1, сюда не входит. Модули не делают SQL к чужим
таблицам: общение через БД (свои схемы), события шины и сервисные функции.
"""
