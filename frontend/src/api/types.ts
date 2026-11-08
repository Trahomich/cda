// Типы API-клиента ЦДА. Контракты: docs/06-data-api.md §5, CONTRACTS.md §8.
// Имена полей — snake_case (CONTRACTS §5), идентификаторы — UUID v4.

/** Ошибка RFC 7807 (application/problem+json), CONTRACTS §8. */
export interface Problem {
  /** URI типа ошибки, например "https://cda.example/errors/waiting-period". */
  type: string
  /** Краткий заголовок на русском. */
  title: string
  /** HTTP-статус. */
  status: number
  /** Пояснение для пользователя. */
  detail: string
  /** Расширения, например rule_id, entity (06 §5, пример 422). */
  [key: string]: unknown
}

/** Обёртка постраничного ответа (CONTRACTS §8): {"items", "total", "limit", "offset"}. */
export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

/** Статус поля (docs/03-domain-agronomy.md §1.1). */
export type FieldStatus = 'active' | 'merged' | 'archived'

/** Геометрия контура — GeoJSON Polygon, WGS84 (CONTRACTS §5). */
export interface GeoJsonPolygon {
  type: 'Polygon'
  /** Кольца: внешнее против часовой стрелки; [lon, lat]. */
  coordinates: number[][][]
}

/** Поле — ENT-FIELD (03 §1.1), формат ответа API-01/05.
 *  В GET /fields контур опущен по умолчанию (лёгкий список); добавляется
 *  при with_geometry=true (карта запрашивает с ним) — docs/06 §5, API-01. */
export interface Field {
  field_id: string
  name: string
  contour?: GeoJsonPolygon
  area_ga: number
  status: FieldStatus
  /**
   * Агрегат цикла текущего сезона (API-01: {year, culture_name, kind} | null).
   */
  current_cycle?: {
    year: number
    culture_name: string
    kind: 'main' | 'catch' | 'fallow'
  } | null
}

/** Справочник культур (API-48, ref_culture): источник раскраски полей на карте. */
export interface CultureReference {
  culture_id: string
  name: string
  kind: 'main' | 'catch' | 'fallow'
  forage_flag: boolean
  crop_group: string
  /** Стабильный цвет культуры на карте (#rrggbb); null — цвет не назначен. */
  color_hex?: string | null
}

/** Элемент истории севооборота поля — GET /fields/{id}/crop-cycles (API-49):
 *  годы по убыванию; цвет — из справочника культур (API-48). */
export interface CropCycleHistoryItem {
  year: number
  culture_id: string
  culture_name: string
  kind: 'main' | 'catch' | 'fallow'
  status: 'completed' | 'in_progress' | 'planned'
  color_hex?: string | null
}

/** Роли пользователей: enum бэкенда по docs/01-requirements.md §2 (см. #22). */
export type UserRole =
  | 'agronomist'
  | 'manager'
  | 'machine_operator'
  | 'livestock_specialist'
  | 'admin'

/** Ответ POST /auth/login (API-46, docs/06 §5). */
export interface AuthResponse {
  token: string
  user: {
    name: string
    role: UserRole
    farm_name: string
  }
}

/** Тип операции на поле (ENT-FIELD-OPERATION, 03 §1.6). */
export type OperationType = 'spraying' | 'fertilizing' | 'seeding' | 'harvesting' | 'tillage' | 'other'

/** Запись урожайности внутри операции уборки (ENT-YIELD-RECORD, 03 §1.7). */
export interface YieldRecordRequest {
  /** Урожайность, ц/га (CONTRACTS §5). */
  yield_c_ha: number
  gross_t?: number | null
  source: 'scales' | 'combine_sensor' | 'estimate'
}

/** Запись факта операции: POST /operations (API-17).
 *  client_id — ключ идемпотентности офлайн-очереди (10-mobile-pwa §3).
 *  yield_record — только для operation_type=harvesting (03 §1.6, 422 иначе). */
export interface OperationRequest {
  client_id: string
  field_id: string
  operation_type: OperationType
  /** ISO-дата YYYY-MM-DD (CONTRACTS §5). */
  date: string
  input_id?: string | null
  actual_dose?: number | null
  actual_area_ga: number
  performer?: string | null
  source: 'manual' | 'telemetry_import' | 'plan_execution'
  yield_record?: YieldRecordRequest | null
}

/** Операция — ответ API-17/18 (06 §5, пример создания).
 *  needs_review=true — RULE-AGRO-12: площадь > 1,05 × поля, сохранено,
 *  требует проверки агрономом («факт важнее плана», не 422). */
export interface FieldOperation extends Omit<OperationRequest, 'client_id'> {
  operation_id: string
  deviation_flag: boolean
  needs_review: boolean
  status: string
  /** Обоснование отклонения факта от плана (>10 %, RULE-AGRO-11). */
  deviation_rationale?: string | null
  cycle_id?: string | null
  machine_job_id?: string | null
  /** Ключ идемпотентности офлайн-очереди; в ответе может отсутствовать. */
  client_id?: string | null
}

/** Ответ GET /auth/me (API-47). */
export interface AuthMe {
  id: string
  name: string
  role: UserRole
}

/* ─── Поле 360° (API-05..07, docs/06 §5; экран E-03, docs/07-ux §3.2) ─── */

/** Цикл культуры в истории поля (ENT-CROP-CYCLE, 03 §1.3; агрегат API-05). */
export interface FieldCycle {
  cycle_id: string
  year: number
  culture_name: string
  kind: 'main' | 'catch' | 'fallow'
  status: 'planned' | 'in_progress' | 'completed'
  /** Фактическая урожайность, ц/га (для завершённых циклов). */
  yield_c_ha?: number | null
}

/** Агрохимия поля — последняя проба (ref_agrochem, 06 §3; REQ-F-004). */
export interface AgrochemSample {
  sample_date: string
  ph: number
  humus_pct: number
  n_mg100: number
  p2o5_mg100: number
  k2o_mg100: number
}

/** Проверка севооборота текущего цикла (RULE-AGRO-01/02, человекопонятно). */
export interface RotationCheck {
  ok: boolean
  message: string
}

/**
 * Карточка «Поле 360°» (экран E-03) — экранная модель, собираемая на клиенте:
 * API-05 возвращает поле целиком (контур, версии, журнал), история культур —
 * из API-08 (items планов севооборота поля), планы внесения — из API-15.
 * Агрохимия и сводная проверка севооборота в MVP-объёме backend отсутствуют.
 */
export interface FieldCard {
  field: Field & { contour: GeoJsonPolygon }
  /** История культур по годам, свежие сверху. */
  cycles: FieldCycle[]
  agrochem: AgrochemSample | null
  rotation_check: RotationCheck | null
  /** Планы внесения на текущий цикл (кратко, для блока «Планы/журнал»). */
  treatment_plans: FieldCardPlan[]
}

/** Краткий план внесения в карточке поля. */
export interface FieldCardPlan {
  plan_id: string
  input_name: string
  dose: number
  unit: string
  planned_date: string | null
  status: TreatmentPlanStatus
}

/** Точка NDVI-таймлайна — GET /fields/{id}/ndvi (API-06, REQ-F-028). */
export interface NdviPoint {
  date: string
  ndvi: number
  /** Доля облачности снимка, %: точки с cloud_pct > 80 показываются ◌. */
  cloud_pct: number
  /** Мин/макс индекса по полю в сцене (API-06): полоса min–max на графике. */
  index_min?: number | null
  index_max?: number | null
  /** Доверие источника сцены (API-06): < 1 — пометка на точке. */
  trust?: number | null
  /** PNG-рендеры обоих вариантов на диске (§7.3): дата доступна как слой. */
  render_available?: boolean
  /** Вымокание по NDWI-детектору (§7.4): true/false; null — данных нет. */
  waterlogged?: boolean | null
}

/** Погода: факт сейчас + прогноз 7 сут — GET /fields/{id}/weather (API-07). */
export interface FieldWeather {
  now: {
    date: string
    temp_c: number
    wind_ms: number
    precip_mm: number
    humidity_pct: number
  }
  forecast: Array<{
    date: string
    temp_min_c: number
    temp_max_c: number
    precip_mm: number
    /** Вероятность осадков, % (§7.2); null — провайдер не отдаёт. */
    precip_prob_pct?: number | null
  }>
  summary: {
    /** Осадки за 7 сут, мм. */
    precip_7d_mm: number
    /** Сумма эффективных температур (GDD), °C·сут. */
    gdd: number
  }
}

/* ─── План севооборота (API-08..11, экран E-04, docs/07-ux §3.3) ─── */

/** Культура справочника ref_culture (06 §3); для выбора в матрице E-04. */
export interface Culture {
  culture_id: string
  name: string
  kind: 'main' | 'catch' | 'fallow'
  /** Норма возврата на то же поле, лет (RULE-AGRO-02); у пара/сидерата 0. */
  repeat_years: number
}

/** Клетка матрицы «поле × год» (агрегат плана севооборота). */
export interface RotationCell {
  field_id: string
  field_name: string
  area_ga: number
  year: number
  /** Цикл в клетке; null — клетка не заполнена. */
  cycle: {
    cycle_id: string
    culture_id: string
    culture_name: string
    kind: 'main' | 'catch' | 'fallow'
  } | null
}

/** Нарушение бизнес-правила в плане севооборота (человекопонятно, E-04). */
export interface RotationViolation {
  violation_id: string
  rule_id: 'RULE-AGRO-01' | 'RULE-AGRO-02' | string
  /** error — блокирует, warning — требует комментария агронома. */
  severity: 'error' | 'warning'
  field_id: string
  year: number
  /** Текст для клетки матрицы и списка «ПРОВЕРКА ПЛАНА». */
  message: string
  /** Обоснование для кнопки «Почему?» (rationale, REQ-F-037). */
  rationale: string
  /** Предлагаемая культура-замена (culture_id), если есть. */
  suggestion_culture_id?: string | null
}

/** План севооборота — экранная модель матрицы E-04, собираемая из API-08
 *  (планы по полям) и API-01 (список полей): строки матрицы — все активные
 *  поля, клетки — циклы из items планов. */
export interface RotationPlan {
  plan_id: string
  /** Идентификаторы черновиков для согласования (API-10, по одному на поле). */
  draft_plan_ids: string[]
  farm_name: string
  year_from: number
  year_to: number
  status: 'draft' | 'approved' | 'archived'
  approved_by: string | null
  approved_at: string | null
  /** Справочник культур для выбора в матрице (06 §3, ref_culture);
   *  в MVP выводится из циклов существующих планов. */
  cultures: Culture[]
  /** Клетки матрицы: одна строка поля на год горизонта. */
  cells: RotationCell[]
  /** Пересчитанные нарушения (RULE-AGRO-01/02), пусто — план чист.
   *  GET API-08 в MVP не пересчитывает нарушения: они видны при добавлении
   *  цикла (422 problem+json с rule_id/rationale). */
  violations: RotationViolation[]
}

/** Создание/правка цикла — POST /crop-cycles (API-11). kind обязателен
 *  (согласуется с видом культуры, RULE-AGRO-03); reason — при warn-нарушениях. */
export interface CropCycleRequest {
  field_id: string
  year: number
  culture_id: string
  kind: 'main' | 'catch' | 'fallow'
  planned_yield_c_ha?: number | null
  planned_harvest_date?: string | null
  out_of_rotation?: boolean
  reason?: string | null
}

/** Цикл культуры — ответ API-11 (ENT-CROP-CYCLE). */
export interface CropCycle {
  cycle_id: string
  field_id: string
  year: number
  culture_id: string
  culture_name: string
  kind: 'main' | 'catch' | 'fallow'
  planned_yield_c_ha: number | null
  planned_harvest_date: string | null
  status: 'planned' | 'in_progress' | 'completed'
}

/* ─── Справочник препаратов и планы внесения (API-14..16, экран E-05) ─── */

/** Препарат/удобрение — ENT-AGRO-INPUT (03 §1.4), GET /agro-inputs (API-14). */
export interface AgroInput {
  input_id: string
  kind: 'fertilizer' | 'ppp'
  name: string
  npk_content: [number, number, number] | null
  active_substance: string | null
  concentration: number | null
  unit: 'kg_ha' | 'l_ha' | 'kg_dv_ha'
  /** Срок ожидания СЗР, дней (RULE-AGRO-08); у удобрений null. */
  waiting_days: number | null
  /** Допустимые культуры (RULE-AGRO-10); пусто = без ограничений. */
  approved_crops: string[]
  /** Максимальное число обработок за сезон (RULE-AGRO-10). */
  max_treatments_per_season: number | null
  restrictions: string | null
}

/** Статус плана внесения (ENT-TREATMENT-PLAN §2.1, 03). */
export type TreatmentPlanStatus =
  | 'draft'
  | 'approved'
  | 'in_progress'
  | 'completed'
  | 'cancelled'

/** План внесения — ENT-TREATMENT-PLAN (03 §1.5), API-15/16 (с развёрнутыми
 *  именами поля/цикла/препарата для списка E-05). */
export interface TreatmentPlan {
  plan_id: string
  cycle_id: string
  field_id: string
  field_name: string
  year: number
  culture_name: string
  input_id: string
  input_name: string
  input_kind: 'fertilizer' | 'ppp'
  unit: AgroInput['unit']
  dose: number
  operation_type: 'spraying' | 'fertilizing' | 'seed_treatment'
  planned_date: string
  planned_phase: string | null
  status: TreatmentPlanStatus
  /** WEATHER-WINDOW (§7.2): только в ответах POST/approve для spraying;
   *  null — прочие типы, GET-список или нет прогноза на planned_date. */
  weather_window?: PlanWeatherWindow | null
}

/** WEATHER-WINDOW (docs/06 §7.2): погодное окно для планов spraying.
 *  Не блокирующая подсказка: ok=false + detail («2026-06-10: ветер 6.2 м/с
 *  (>4), осадки 70 % (≥50)»), suggested_dates — до 5 подходящих дат из
 *  ближайших 7 суток. В GET-списке (API-15) поле null; объект приходит в
 *  ответах POST /treatment-plans и approve (API-16); null — нет прогноза. */
export interface PlanWeatherWindow {
  ok: boolean
  detail: string
  suggested_dates: string[]
}

/** Создание плана внесения — POST /treatment-plans (API-15). */
export interface TreatmentPlanRequest {
  cycle_id: string
  input_id: string
  dose: number
  operation_type: TreatmentPlan['operation_type']
  planned_date: string
  planned_phase?: string | null
}

/* ─── Погода: прогноз и окна операций (API-29, /weather/windows; E-10) ─── */

/** Сутки прогноза — GET /weather/forecast?field_id&days (API-29, кэш 3 ч). */
export interface WeatherForecastDay {
  date: string
  temp_min_c: number
  temp_max_c: number
  precip_mm: number
  /** Вероятность осадков, % (RULE-AGRO-09); в API-29 MVP отсутствует. */
  precip_prob_pct?: number
  wind_ms: number
  humidity_pct: number
}

/** Прогноз на горизонт days суток; stale — данные из кэша при недоступности
 *  источника (05 §6, REQ-NF-009). */
export interface WeatherForecast {
  field_id: string
  days: WeatherForecastDay[]
  stale: boolean
}

/** Тип операции для оценки погодного окна (RULE-AGRO-09, 03 §3.3). */
export type WindowOperationType = 'spraying' | 'fertilizing' | 'seeding' | 'harvesting'

/** Сутки горизонта с оценкой окна (обоснование — REQ-F-025). */
export interface WeatherWindowDay {
  date: string
  is_window: boolean
  /** Почему сутки подходят/не подходят, по-русски. */
  summary: string
}

/** Ответ GET /weather/windows?field_id&operation_type (MVP-6, RULE-AGRO-09). */
export interface WeatherWindows {
  field_id: string
  operation_type: WindowOperationType
  days: WeatherWindowDay[]
  /** Ближайшие сутки окна или null, если в горизонте нет (→ ENT-ALERT). */
  nearest_window_date: string | null
  /** Текст правила RULE-AGRO-09 (обоснование, REQ-F-025). */
  rule_text?: string
  /** Есть ли окно в горизонте. */
  window_open?: boolean
  /** Сообщение об отсутствии окна для алерта (когда nearest_window_date = null). */
  alert?: string | null
}

/* ─── Погода: история, GDD, окна по планам (#132/#133, обсуждение #134/#393) ─── */

/** Дневной факт истории погоды — GET /weather/history (API-57, #126/#132).
 *  cloud_pct/snow_mm — метрики бэкфилла #126 (могут отсутствовать в старых сутках). */
export interface WeatherHistoryDay {
  date: string
  temp_max_c: number | null
  temp_min_c: number | null
  precip_mm: number
  cloud_pct?: number | null
  snow_mm?: number | null
}

/** История за период (по возрастанию даты; > 400 суток — 422 too_wide). */
export interface WeatherHistory {
  field_id: string
  items: WeatherHistoryDay[]
  total: number
}

/** Точка накопительной кривой GDD — GET /weather/gdd (#132, base 5 °C). */
export interface GddPoint {
  date: string
  /** Эффективная температура за сутки, °C·сут. */
  gdd_day: number
  /** Накопленная с 1 января, °C·сут. */
  gdd_cum: number
}

/** Точка многолетней нормы GDD (при ≥ 2 годах истории). */
export interface GddNormPoint {
  date: string
  norm_cum: number
}

/** Ответ GET /weather/gdd?field_id&year (#132): кривая с 1 января + норма. */
export interface WeatherGdd {
  field_id: string
  year: number
  base_c: number
  /** Итог на последний день с данными, °C·сут. */
  total: number
  items: GddPoint[]
  /** Норма по тем же датам; null при < 2 годах истории. */
  norm_cum: GddNormPoint[] | null
}

/** Оценка окна одного типа операций в списочной форме /weather/windows. */
export interface WeatherWindowItem {
  operation_type: WindowOperationType
  rule_text?: string
  window_open: boolean
  nearest_window_date: string | null
  days: WeatherWindowDay[]
  alert?: string | null
}

/** Ответ GET /weather/windows?field_id&only_planned=true (без operation_type,
 *  #133): только типы операций из активных планов поля; hint — когда планов нет. */
export interface WeatherWindowsPlanned {
  field_id: string
  only_planned: boolean
  items: WeatherWindowItem[]
  hint: string | null
}

/* ─── Источники данных (API-28, экран E-14; ref_source, 06 §3) ─── */

/** Статус адаптера: ok / degraded / error (05 §6). */
export type AdapterState = 'ok' | 'degraded' | 'error'

/** Событие журнала dat_source_journal (источник, at, статус, причина). */
export interface SourceEvent {
  at: string
  status: AdapterState
  reason: string | null
}

/** Текущее состояние адаптера — GET /sources/status (API-28). */
export interface AdapterStatus {
  source_id: string
  kind: string
  title: string
  last_status: AdapterState | null
  last_success_at: string | null
  reason: string | null
  events: SourceEvent[]
}

/* ─── Техника (API-19..21, экран E-18; ENT-MACHINE/-JOB, 04 §1) ─── */

export type MachineType =
  | 'tractor'
  | 'combine'
  | 'sprayer'
  | 'spreader'
  | 'seeder'
  | 'truck'
  | 'loader'
  | 'other'

export type MachineStatus = 'active' | 'maintenance' | 'retired'

/** Машина — ENT-MACHINE (04 §1), GET /machines (API-19). */
export interface Machine {
  machine_id: string
  machine_type: MachineType
  model: string
  gov_number: string | null
  width_m: number | null
  tank_capacity_l: number | null
  engine_hours: number
  status: MachineStatus
}

/** Статус задания смены: draft — черновик из телематики, подтверждается человеком. */
export type MachineJobStatus = 'draft' | 'confirmed' | 'rejected'

/** Задание смены — ENT-MACHINE-JOB (04 §1), список + API-20 confirm. */
export interface MachineJob {
  job_id: string
  machine_id: string
  /** Развёрнутое имя машины для списка (машина, поле). */
  machine_model: string
  /** Связанный факт операции (поле раскрывается через него); null — внеполевая. */
  field_operation_id: string | null
  performed_at: string
  area_ha: number
  fuel_l: number | null
  /** Ссылка на трек в хранилище (MVP: не отдаётся списком). */
  track_ref?: string | null
  duration_h: number | null
  status: MachineJobStatus
  source: 'manual' | 'telemetry'
}

/* ─── «Сырые» ответы backend (docs/06 §5) ───
 * Форматы отличаются от экранных моделей; приведение — в api/index.ts.
 * Мок-транспорт (mock.ts) отдаёт те же сырые формы, что и реальный API. */

/** Ответ GET /fields/{id} (API-05): поле целиком + версии, журнал, спутник/погода. */
export interface Api05FieldDetail extends Field {
  contour: GeoJsonPolygon
  versions: Array<{
    version_no: number
    area_ga: number
    valid_from: string
    reason: string | null
    contour: GeoJsonPolygon
  }>
  operations: Array<{
    date: string
    operation_type: string
    status: string
    performer: string | null
  }>
  ndvi: unknown
  weather: unknown
}

/** Сцена спутника — элемент таймлайна API-06 (ENT-SATELLITE-SCENE, 05 §3). */
export interface SceneItem {
  scene_id: string
  field_id: string
  index: string
  captured_at: string
  resolution_m: number
  index_avg: number
  index_min: number
  index_max: number
  cloud_pct: number
  composite_days: number
  source_id: string
  trust: number
  anomaly_zones_ref: string | null
  /** PNG-рендеры обоих вариантов на диске (§7.3, #83). */
  render_available?: boolean
  /** Вымокание по NDWI-детектору (§7.4): null — NDWI-статистики нет. */
  waterlogged?: boolean | null
}

/** Ответ API-06: пагинация + мета источника сцен. */
export interface NdviTimelinePage extends Page<SceneItem> {
  index: string
  meta: { source_id: string; status: string | null; last_success_at: string | null; stale: boolean }
}

/** Зона дифференцированного внесения (API-52, docs/06 §7.4). */
export interface VraZoneItem {
  zone: number
  label: string
  area_ga: number
  ndvi_mean: number
  dose_factor: number
  dose_kg_ha: number | null
}

/** Ответ API-52: зоны VRA по последней чистой NDVI-сцене (§7.4). */
export interface VraZonesResponse {
  field_id: string
  /** Дата пиковой сцены, по которой построены зоны (§7.4 v1.7). */
  date: string
  /** Пиксельный IQR NDVI пиковой сцены (информационная метрика). */
  iqr_ndvi: number
  /** Пространственная неоднородность: p90−p10 средних ячеек сетки
   *  ~20×20 (§7.4 v1.7) — мелкие пятна, которые не видит IQR. */
  spatial_spread: number
  /** Даты топ-3 пиковых сцен сезона (§7.4 v1.7). */
  peak_dates: string[]
  base_dose_kg_ha: number | null
  zones: VraZoneItem[]
  map_png_url: string
}

/** Прогноз урожайности по NDVI (API-53, docs/06 §7.4; 09-ai §8.3). */
export interface YieldForecastResponse {
  field_id: string
  cycle_year: number
  culture_id: string
  culture_name: string
  method: string
  ndvi_max: number
  ndvi_integral: number
  forecast_c_ha: number
  low_c_ha: number
  high_c_ha: number
  disclaimer: string
  /** Год сезона расчёта (§7.5): дополняет ответ при ?year=YYYY;
   *  совпадает с cycle_year — год, по которому считали прогноз. */
  year?: number
}

/** Сезон NDVI поля — элемент items[] ответа API-54 (§7.5): distinct-год
 *  чистых сцен поля; culture_name — культура основного цикла года | null. */
export interface SeasonItem {
  year: number
  /** Число чистых (cloud ≤ 60) NDVI-сцен сезона. */
  scenes: number
  first_date: string
  last_date: string
  culture_name: string | null
  /** true для года последней чистой сцены поля (текущий сезон). */
  is_current: boolean
}

/** Ответ GET /fields/{id}/seasons (API-54, §7.5): годы по убыванию. */
export interface SeasonsResponse {
  field_id: string
  items: SeasonItem[]
  total: number
}

/** Градация важности числового детектора (API-51, §7.4; 09-ai §8.2). */
export type InsightSeverity = 'info' | 'warn' | 'alert'

/** Один числовой детектор — элемент items[] ответа API-51. */
export interface InsightItem {
  /** Код детектора, например ndvi_drop / waterlogging / vra_heterogeneity. */
  code: string
  severity: InsightSeverity
  title: string
  detail: string
  /** Ссылки на входные данные (сцены/пробы, 03 §1.5); в UI не показываются. */
  data_refs: Array<Record<string, unknown>>
}

/** Ответ GET /fields/{id}/insights (API-51, §7.4): items считаются всегда,
 *  summary — LLM-сводка (null при source=rules, UI показывает только items). */
export interface InsightsResponse {
  field_id: string
  generated_at: string
  source: 'rules+llm' | 'rules'
  summary: string | null
  items: InsightItem[]
}

/** Факт погоды — элемент facts[] API-07. */
export interface WeatherFactItem {
  observed_at: string
  temperature_avg_c: number
  temperature_min_c: number
  temperature_max_c: number
  precipitation_mm: number
  wind_max_ms: number
  humidity_avg_pct: number
  source_id: string
}

/** Сутки прогноза — элемент forecast[] API-07 и items[] API-29. */
export interface WeatherForecastItem {
  forecast_for?: string
  issued_at?: string
  temperature_min_c: number
  temperature_max_c: number
  precipitation_mm: number
  /** Вероятность осадков, % (docs/06 §7.2); null — провайдер не отдаёт. */
  precip_prob_pct?: number | null
  wind_max_ms: number
  humidity_avg_pct: number
  hazard?: string | null
}

/** Ответ API-07: факты + прогноз + мета источника. */
export interface FieldWeatherDetail {
  field_id: string
  facts: WeatherFactItem[]
  forecast: WeatherForecastItem[]
  meta: { source_id: string; status: string | null; last_success_at: string | null; stale: boolean }
}

/** Ответ API-29: пагинация суток прогноза + мета источника. */
export interface ForecastPage extends Page<WeatherForecastItem> {
  meta: { source_id: string; status: string | null; last_success_at: string | null; stale: boolean }
}

/** Сутки горизонта в ответе /weather/windows. */
export interface WindowsDayItem {
  day: string
  ok: boolean
  reasons: string[]
}

/** Ответ GET /weather/windows (RULE-AGRO-09). */
export interface WindowsResponse {
  operation_type: string
  field_id: string
  rule_text: string
  window_open: boolean
  days: WindowsDayItem[]
  alert: string | null
}

/** Элемент окна в списочной форме /weather/windows?only_planned (#133). */
export interface WindowsPlannedItem {
  operation_type: string
  rule_text?: string
  window_open: boolean
  days: WindowsDayItem[]
  alert?: string | null
}

/** Ответ GET /weather/windows?field_id&only_planned=true (без operation_type,
 *  #133): только типы из активных планов поля; hint при их отсутствии. */
export interface WindowsPlannedResponse {
  field_id: string
  only_planned?: boolean
  items: WindowsPlannedItem[]
  hint?: string | null
}

/** Дневное наблюдение в ответе /weather/history (API-57, сырая форма). */
export interface WeatherHistoryItem {
  date: string
  temp_max_c: number | null
  temp_min_c: number | null
  precip_mm: number
  cloud_pct?: number | null
  snow_mm?: number | null
}

/** Ответ GET /weather/gdd (#132, сырая форма). */
export interface GddResponse {
  field_id: string
  year: number
  base_c: number
  total: number
  items: GddPoint[]
  norm_cum?: GddNormPoint[] | null
}

/** Цикл культуры в items[] плана севооборота (API-08) или ответе API-11. */
export interface RotationCycleItem {
  cycle_id: string
  field_id: string
  year: number
  culture_id: string
  culture_name: string
  kind: 'main' | 'catch' | 'fallow'
  predecessor_cycle_id: string | null
  planned_yield_c_ha: number | null
  planned_harvest_date: string | null
  out_of_rotation: boolean
  reason: string | null
  status: 'planned' | 'in_progress' | 'completed'
}

/** Нарушение RULE-AGRO-* (rule_id/severity/rationale). */
export interface RotationViolationItem {
  rule_id: string
  severity: 'ban' | 'warn' | string
  rationale: string
}

/** План севооборота одного поля — элемент API-08 (PlanDetail). */
export interface RotationPlanDetail {
  plan_id: string
  field_id: string
  year_from: number
  year_to: number
  status: 'draft' | 'approved' | 'archived'
  approved_by: string | null
  approved_at: string | null
  items: RotationCycleItem[]
  violations: RotationViolationItem[]
}

/** Ответ API-11 (CycleDetail): цикл + предупреждения (warn). */
export interface CropCycleDetail extends RotationCycleItem {
  warnings: RotationViolationItem[]
}

/** Элемент API-28: состояние адаптера источника. */
export interface SourceStatusItem {
  source_id: string
  kind: string
  enabled: boolean
  last_status: string | null
  last_success_at: string | null
  last_event: { at: string; status: string; reason: string | null } | null
}
