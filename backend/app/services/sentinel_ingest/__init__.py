"""Сервис sentinel-ingest (ADR-09, v1-04 #50): STAC-каталог Sentinel-2 → БД.

Отдельный контейнер-воркер без внешнего API (ADR-09): по расписанию 05 §4
ищет сцены sentinel-2-l2a в каталоге earth-search по bbox хозяйства,
сопоставляет их контурам активных полей (PostGIS) и каталогизирует
метаданные (item_id, tile, cloud, ссылки COG) в dat_satellite_catalog.
Расчёт индексов по COG — этап v1-05 (#51), он же переводит status
cataloged → processed; сервис остаётся сервисом каталога.

Общая БД с api через модели пакета app; состояние источника —
ref_source + dat_source_journal (API-28, REQ-F-034).
"""
