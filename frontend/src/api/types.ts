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

/** Запись факта операции: POST /operations (API-17).
 *  client_id — ключ идемпотентности офлайн-очереди (10-mobile-pwa §3). */
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
}

/** Операция — ответ API-17/18 (06 §5, пример создания). */
export interface FieldOperation extends OperationRequest {
  operation_id: string
  deviation_flag: boolean
  status: string
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

/** Карточка «Поле 360°» — агрегат GET /fields/{id} (API-05). */
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
  planned_date: string
  status: TreatmentPlanStatus
}

/** Точка NDVI-таймлайна — GET /fields/{id}/ndvi (API-06, REQ-F-028). */
export interface NdviPoint {
  date: string
  ndvi: number
  /** Доля облачности снимка, %: точки с cloud_pct > 80 показываются ◌. */
  cloud_pct: number
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

/** План севооборота — GET /rotation-plans (API-08; ENT-ROTATION-PLAN, 03 §1.2). */
export interface RotationPlan {
  plan_id: string
  farm_name: string
  year_from: number
  year_to: number
  status: 'draft' | 'approved' | 'archived'
  approved_by: string | null
  approved_at: string | null
  /** Справочник культур для выбора в матрице (06 §3, ref_culture). */
  cultures: Culture[]
  /** Клетки матрицы: одна строка поля на год горизонта. */
  cells: RotationCell[]
  /** Пересчитанные нарушения (RULE-AGRO-01/02), пусто — план чист. */
  violations: RotationViolation[]
}

/** Создание/правка цикла — POST /crop-cycles (API-11). */
export interface CropCycleRequest {
  field_id: string
  year: number
  culture_id: string
  planned_yield_c_ha?: number | null
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
