# Интеграции: погода, спутниковый мониторинг и другие источники

Статус: готово · Версия: 1.0 · Дата: 2026-11-05 · Автор: Иван Куликов

Опирается на CONTRACTS.md (§4–§8), docs/01-requirements.md (REQ-F-016, 023..034,
REQ-NF-009), docs/02-architecture.md (§4 ingest-gateway, §5 потоки) и
docs/research/02-data-sources.md (источники И1–И20, рекомендации REC-16..25).

## 1. Принципы слоя приёма данных (ingest-gateway)

- Все внешние источники подключаются адаптерами единой формы CONTRACTS §7; клиенты
  и доменные модули читают только нормализованные сущности, никогда — сырые ответы.
- Один адаптер = один внешний сервис; тип источника (погода, спутник, ...) покрывается
  набором адаптеров, взаимозаменяемых внутри типа.
- Адаптер не пишет в доменные таблицы напрямую: он отдаёт нормализованные записи
  ingest-gateway, тот валидирует, дедуплицирует и публикует события `observation.ingested`
  / `scene.ingested` / `telemetry.imported` (02-architecture §4).
- Ошибки адаптера — только три типа из CONTRACTS §7: SourceUnavailableError (нет связи —
  ретраи), DataGapError (нет данных за период — норма), DataQualityError (данные есть,
  но не прошли валидацию — запись в журнал, к пользователю не попадают).

## 2. Интерфейсы адаптеров (IF-)

Базовая форма — CONTRACTS §7. Ниже конкретизация: `source_id`, методы, ограничения.
Нотация: возвращаемые структуры — §3 и §4; все datetime — ISO 8601 с зоной (§5).

### IF-WEATHER — погода (источники И1 Open-Meteo [REC-16], И2 OpenWeather, И4 NASA POWER)

```python
source_id = "weather.open-meteo"          # MVP; "weather.openweather" — резерв

capabilities() -> {"history": True, "current": True, "forecast_days": 16,
                   "metrics": ["temperature_c", "precipitation_mm", "wind_ms",
                               "humidity_pct", "pressure_hpa"]}

fetch_history(field_id: UUID, metric: MetricType,
              date_from: date, date_to: date) -> list[Observation]
    # суточная агрегация; архив с 1940 г.; DataGapError, если период вне архива

fetch_forecast(field_id: UUID, days: int = 7) -> list[ForecastItem]
    # days ≤ 16; выдача на сутки, обновление не реже раза в 3 ч

fetch_current(field_id: UUID) -> Observation   # опционально, для дашборда
```

Координата точки наблюдения — центроид контура ENT-FIELD; вычисляется ingest-gateway
и адаптеру не передаётся в явном виде (адаптер берёт из контекста поля по field_id).

### IF-SATELLITE — спутниковый мониторинг (И5 Sentinel-2 [REC-17]; И6 Landsat [REC-21] после MVP)

```python
source_id = "satellite.sentinel2-l2a"

capabilities() -> {"indices": ["ndvi", "ndre", "ndmi", "savi"],
                   "resolution_m": 10, "revisit_days": 5, "scenes_since": "2017-03"}

fetch_scenes(field_id: UUID, index: IndexType,
             date_from: date, date_to: date,
             max_cloud_pct: int = 20) -> list[Scene]
    # сцены по пересечению контура поля; облачность считается по полезной площади
    # поля (доля пикселей с меткой cloud), а не по всей плашке снимка
```

Расписание: опрос каталога раз в 6 ч; при появлении новой безоблачной сцены —
вычисление индексов по контуру, публикация `scene.ingested`. Композит: если за 14
суток нет сцены с облачностью ≤20 %, берётся лучшая доступная с пометкой качества.

### IF-SOIL — почвы и агрохимия (И10, И11 [REC-18]; И13 ГЗУ)

```python
source_id = "soil.import"                   # файловый импорт + ручной ввод

import_file(field_id: UUID | None,          # None — распознавание по названию поля
            format: enum(shp, csv, pdf_scan_stub),
            payload_ref: string) -> ImportReport
    # ImportReport: {created: int, skipped: int, errors: list[{"row": int,
    #   "reason": string}]}; PDF/бумага — ручной перенос, файл только фиксируется
```

Периодичность: по факту поступления (цикл обследования 3–5 лет, И10). Нормативы
(вынос N-P-K, И12 [REC-19]) — внутренний редактируемый справочник, не адаптер.

### IF-TELEMETRY — телематика техники (И14 Wialon/Omnicomm, И15 ISOXML [REC-20])

```python
source_id = "telemetry.csv-import"          # MVP: файлы CSV/ISOXML; API поставщиков — v1

import_tracks(machine_id: UUID, date_from: date, date_to: date,
              payload_ref: string) -> list[TelemetryDraft]
    # TelemetryDraft -> черновик ENT-MACHINE-JOB (status=draft, см. 04 §1);
    # площадь считается буфером трека шириной захвата (RULE-TECH-04)
```

Периодичность: импорт за смену/сутки; реальные API интеграторов — вторая очередь.

### IF-PRICES — цены (И16 ФСПИ, И17 Росстат/прайсы [REC-24, REC-25])

```python
source_id = "prices.manual"                 # MVP: ручной ввод прайсов/справочник;
                                            # API ФСПИ — только после REC-25
fetch_prices(item: enum(crop, input), item_id: string,
             date_from: date, date_to: date) -> list[PricePoint] | None
```

Используется только REQ-F-033 (оценка экономического эффекта рекомендаций, Could);
недоступность не влияет на ядро.

## 3. Нормализованные структуры данных

### ENT-WEATHER-OBSERVATION (сутки × поле)

| Поле | Тип | Пояснение |
|---|---|---|
| observation_id | uuid | PK |
| field_id | uuid | поле, к которому отнесено наблюдение |
| observed_at | date | сутки |
| temperature_avg_c / temperature_min_c / temperature_max_c | decimal(4,1) | °C |
| precipitation_mm | decimal(5,1) | осадки за сутки, мм |
| wind_max_ms | decimal(4,1) | максимальный ветер, м/с |
| humidity_avg_pct | decimal(4,1) | средняя влажность, % |
| source_id | string | какой адаптер дал запись |
| trust | decimal(3,2) | доверие 0–1 (см. §5) |

```json
{
  "observation_id": "c1d2e3f4-0000-4000-8000-abcdefabcdef",
  "field_id": "550e8400-e29b-41d4-a716-446655440000",
  "observed_at": "2026-05-12",
  "temperature_avg_c": 14.6, "temperature_min_c": 7.1, "temperature_max_c": 21.3,
  "precipitation_mm": 3.2, "wind_max_ms": 6.4, "humidity_avg_pct": 68.0,
  "source_id": "weather.open-meteo", "trust": 0.95
}
```

### ENT-FORECAST (прогноз на сутки × поле)

| Поле | Тип | Пояснение |
|---|---|---|
| forecast_id | uuid | PK |
| field_id | uuid | — |
| forecast_for | date | сутки прогноза |
| issued_at | datetime | момент выдачи прогноза источником |
| temperature_min_c / temperature_max_c | decimal(4,1) | °C |
| precipitation_mm | decimal(5,1) | ожидаемые осадки, мм |
| wind_max_ms | decimal(4,1) | м/с |
| humidity_avg_pct | decimal(4,1) | % |
| hazard | enum(none, frost, heavy_rain, hail, wind) | опасное явление (REQ-F-026) |

```json
{
  "forecast_id": "e3f4a5b6-0000-4000-8000-abcdefabcdef",
  "field_id": "550e8400-e29b-41d4-a716-446655440000",
  "forecast_for": "2026-05-15",
  "issued_at": "2026-05-14T21:00:00+03:00",
  "temperature_min_c": 8.2, "temperature_max_c": 19.5,
  "precipitation_mm": 1.0, "wind_max_ms": 5.1, "humidity_avg_pct": 71.0,
  "hazard": "none"
}
```

Прогноз не хранит историю всех выдач: сохраняется последняя актуальная версия на
горизонт 7 сут; предыдущая перезаписывается (прогноз — не факт).

### ENT-SATELLITE-SCENE (индекс × поле × дата)

| Поле | Тип | Пояснение |
|---|---|---|
| scene_id | uuid | PK |
| field_id | uuid | — |
| index | enum(ndvi, ndre, ndmi, savi) | тип индекса |
| captured_at | date | дата съёмки |
| resolution_m | int | пространственное разрешение сцены, м (Sentinel-2 — 10, Landsat — 30) |
| index_avg | decimal(4,3) | среднее по полезной площади поля |
| index_min / index_max | decimal(4,3) | разброс по полю |
| anomaly_zones_ref | string | ссылка на полигоны аномалий в хранилище, nullable (REQ-F-029) |
| cloud_pct | decimal(4,1) | доля облачных пикселей полезной площади, % |
| composite_days | int | из скольких суток композит (1 — одиночная сцена) |
| source_id | string | "satellite.sentinel2-l2a" / "satellite.landsat" |
| trust | decimal(3,2) | 1 − cloud_pct/100 при одиночной сцене (см. §5) |

```json
{
  "scene_id": "d2e3f4a5-0000-4000-8000-abcdefabcdef",
  "field_id": "550e8400-e29b-41d4-a716-446655440000",
  "index": "ndvi", "captured_at": "2026-06-04", "resolution_m": 10,
  "index_avg": 0.612, "index_min": 0.31, "index_max": 0.78,
  "anomaly_zones_ref": "zones/2026/06/04/field-550e.json",
  "cloud_pct": 4.0, "composite_days": 1,
  "source_id": "satellite.sentinel2-l2a", "trust": 0.96
}
```

Зоны аномалий (REQ-F-029) хранятся как GeoJSON-полигоны в объектном хранилище;
аномалия — отклонение среднего по зоне NDVI от медианы поля более чем на 0.15.

## 4. Расписания, ретраи, кэширование

| Адаптер | Расписание | Ретраи | Кэш (TTL) |
|---|---|---|---|
| IF-WEATHER текущая | каждые 30 мин | 5 попыток, экспоненциально 1→30 мин | 30 мин |
| IF-WEATHER прогноз | каждые 3 ч | 5 попыток, 1→30 мин | 3 ч |
| IF-WEATHER архив | раз в сутки, докачка пропусков | 10 попыток, 10 мин→6 ч | не кэшируется (лежит в БД) |
| IF-SATELLITE каталог | каждые 6 ч | 5 попыток, 1→6 ч | каталог 6 ч |
| IF-SOIL импорт | по факту файла | повторный импорт вручную | — |
| IF-TELEMETRY импорт | по факту файла (смена/сутки) | вручную | — |
| IF-PRICES | раз в неделю | 3 попытки, 1 ч | 7 суток |

Ретраи выполняет планировщик ingest-gateway; между попытками источник помечен
degraded (см. §6). Идемпотентность: повторная загрузка периода не создаёт дублей —
ключ дедупликации: (field_id, наблюдение_сутки, source_id) для погоды,
(scene: field_id, index, captured_at, source_id) для спутника.

## 5. Валидация качества и доверие данным (trust)

Правила (каждое — условие → метка):

1. Пропуск ряда: сутки без наблюдения при работающем источнике → интерполяция
   соседних дней, метка `gap_filled`, trust ×0.8.
2. Облачность сцены: cloud_pct >20 % → сцена не участвует в таймлайне по умолчанию,
   отображается серым; >60 % → не сохраняется вовсе.
3. Расхождение источников: |Δ температура| >3 °C или |Δ осадки| >5 мм между двумя
   погодными адаптерами за одни сутки → обе записи trust ×0.7, в журнал качества.
4. Выпадающие значения: температура вне [−60; +60] °C, ветер >60 м/с, NDVI вне
   [−1; 1] → DataQualityError, запись отбрасывается, инцидент в журнал.
5. Устаревание: возраст текущей погоды >3 ч или прогноза >12 ч → в UI метка
   «данные устарели» с датой последнего обновления (REQ-NF-009).
6. Телематика: трек с разрывом >30 мин → нарезка на несколько черновиков работ
   с пометкой `track_gap` для ручной проверки.

trust хранится в записи и используется rules-engine: рекомендация с источником
trust <0.5 помечается «низкая уверенность».

## 6. Поведение системы при ошибках (REQ-NF-009)

| Ошибка | Поведение системы | Что видит пользователь |
|---|---|---|
| SourceUnavailableError | ретраи по §4; источнику статус degraded; функции источника помечены недоступными | экран источника показывает последнее значение с датой и меткой «данные устарели»/«источник недоступен»; ядро (планы, журнал, животноводство) работает |
| DataGapError | запрос соседнего адаптера того же типа (погодный резерв OpenWeather); если и его нет — пробел в ряду | пустая клетка таймлайна с датой границы данных |
| DataQualityError | запись отбрасывается, инцидент в журнал адаптера (REQ-F-034) | данные не показываются; администратор видит причину |

## 7. Сырые и нормализованные данные

| Категория | Где хранится | Срок |
|---|---|---|
| Сырые ответы погодного API | не хранятся (нормализация при приёме) | — |
| Спутниковые сцены (растры, обрезки по полям) | объектное хранилище (ADR-02, 02-architecture §6) | 3 сезона, далее — только выжимки |
| Файлы импорта (агрохимия, телематика, ISOXML, треки) | объектное хранилище | постоянно (исходник для переобработки) |
| Нормализованные ENT- записи | PostgreSQL (DAT- в 06-data-api) | постоянно |

## 8. Карта «источник → адаптер → потребитель»

| Источник (research/02) | Адаптер | Потребители | REQ |
|---|---|---|---|
| И1 Open-Meteo [REC-16] | IF-WEATHER | mod-weather (окна, алерты), mod-fields (карточка), RULE-TECH-01 (04) | REQ-F-023..026 |
| И5 Sentinel-2 [REC-17] | IF-SATELLITE | mod-satellite, mod-inputs (дозы по NDVI/NDRE) | REQ-F-027..030 |
| И10–И13 почвы/ГЗУ [REC-18] | IF-SOIL | mod-fields (агрохимия), mod-inputs (баланс) | REQ-F-004, 031 |
| И14–И15 телематика [REC-20] | IF-TELEMETRY | mod-machinery (черновики работ) | REQ-F-016, 032 |
| И16–И17 цены [REC-24, REC-25] | IF-PRICES | rules-engine (экономика рекомендаций) | REQ-F-033 |
| И18–И19 Ветис, зоотехучёт [REC-22] | — (ручной ввод/CSV, v1+) | mod-livestock | REQ-F-018..022 |
| И12 нормативы [REC-19] | внутренний справочник (DAT-, 06) | mod-inputs, mod-livestock (нормы кормления) | REQ-F-011, 020 |
| все адаптеры | ingest-gateway: журнал источников | экран администратора | REQ-F-034 |

## 9. Открытые вопросы

- Прямые API телематических интеграторов (Wialon/Omnicomm) — выбор после опроса
  хозяйств (research/02 §4, п. 3) [REC-20].
- API ФСПИ/Гидрометцентра — проверка на площадке заказчика [REC-25].
- Формат зон аномалий NDVI (пиксель/полигон) — решён в пользу полигонов (§3);
  порог 0.15 — конфигурируемый параметр.
