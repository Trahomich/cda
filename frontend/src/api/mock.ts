// Мок-транспорт для dev-режима: демо-хозяйство по ADR-07 (5–10 полей со
// синтетическими контурами, культуры средней полосы). Реализует эндпоинты
// каркаса и экранов E-03/E-04/E-05/E-10/E-13/E-14/E-15/E-18 (docs/06 §5):
// auth (API-46), fields (API-01/05/06/07), rotation-plans + crop-cycles
// (API-08/10/11), agro-inputs (API-14), treatment-plans (API-15/16),
// operations (API-17/18), weather/forecast + weather/windows (API-29,
// RULE-AGRO-09), sources/status (API-28), machines + machine-jobs (API-19/20).
// Ошибки — RFC 7807 (CONTRACTS §8), нарушения бизнес-правил — 422 c rule_id.
import type { Transport, TransportResponse } from './transport'
import type {
  AdapterStatus,
  AgroInput,
  Api05FieldDetail,
  CropCycleDetail,
  Field,
  FieldOperation,
  FieldStatus,
  Machine,
  MachineJob,
  NdviPoint,
  Page,
  PlanWeatherWindow,
  TreatmentPlan,
  TreatmentPlanRequest,
  WeatherForecastDay,
  WindowOperationType,
} from './types'

/* ───────────────────────── Демо-справочники ───────────────────────── */

interface MockCulture {
  culture_id: string
  name: string
  kind: 'main' | 'catch' | 'fallow'
  /** Норма возврата, лет (RULE-AGRO-02). */
  repeat_years: number
  /** Примерная дата плановой уборки ММ-ДД (для RULE-AGRO-08). */
  harvest_md: string
  /** Группа культур (API-48) — фильтр справочника. */
  crop_group: string
  /** Цвет на карте (API-48, синхронизирован с backend/app/seed.py #54). */
  color_hex: string
}

const CULTURES: MockCulture[] = [
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a1', name: 'Пшеница озимая', kind: 'main', repeat_years: 3, harvest_md: '07-25', crop_group: 'cereals', color_hex: '#e8c46b' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a2', name: 'Ячмень', kind: 'main', repeat_years: 3, harvest_md: '07-15', crop_group: 'cereals', color_hex: '#d9a441' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a3', name: 'Овёс', kind: 'main', repeat_years: 2, harvest_md: '08-01', crop_group: 'cereals', color_hex: '#efe0a8' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a4', name: 'Горох', kind: 'main', repeat_years: 4, harvest_md: '07-10', crop_group: 'legumes', color_hex: '#66bb6a' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a5', name: 'Рапс яровой', kind: 'main', repeat_years: 4, harvest_md: '08-10', crop_group: 'oilseeds', color_hex: '#d4e157' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a6', name: 'Гречиха', kind: 'main', repeat_years: 2, harvest_md: '09-01', crop_group: 'cereals', color_hex: '#e29a5e' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a7', name: 'Кукуруза на силос', kind: 'main', repeat_years: 4, harvest_md: '09-05', crop_group: 'forage', color_hex: '#e5761b' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a8', name: 'Пар', kind: 'fallow', repeat_years: 0, harvest_md: '09-30', crop_group: 'other', color_hex: '#c8c8c8' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000a9', name: 'Горчица белая (сидерат)', kind: 'catch', repeat_years: 0, harvest_md: '09-30', crop_group: 'grasses', color_hex: '#c5e1a5' },
  { culture_id: 'c1a0d9e0-0000-4000-8000-0000000000aa', name: 'Рапс (сидерат)', kind: 'catch', repeat_years: 0, harvest_md: '09-30', crop_group: 'oilseeds', color_hex: '#c0ca33' },
]

function cultureById(id: string): MockCulture | undefined {
  return CULTURES.find((c) => c.culture_id === id)
}

/** Группы культур для RULE-AGRO-01 (упрощённая матрица предшественников). */
const GRAINS = new Set(['a1', 'a2', 'a3', 'a6']) // пшеница, ячмень, овёс, гречиха
const LEGUMES = new Set(['a4']) // горох

type Verdict = 'ok' | 'warn' | 'ban'

/** Совместимость «предшественник → культура» (RULE-AGRO-01; пар/сидерат —
 *  RULE-AGRO-03: любой предшественник допустим). Упрощённая матрица:
 *  одна и та же main-культура подряд — warn для зерновых (по 07-ux §3.3
 *  возврат 3 лет для пшеницы — норма, подряд — нежелательно) и ban для
 *  остальных (бобовые, рапс, кукуруза); разные зерновые — допустимо. */
function compat(pred: MockCulture, cur: MockCulture): Verdict {
  if (pred.kind !== 'main' || cur.kind !== 'main') return 'ok'
  const p = pred.culture_id.slice(-2)
  const c = cur.culture_id.slice(-2)
  if (p === c) return GRAINS.has(p) ? 'warn' : 'ban' // та же культура подряд
  if (LEGUMES.has(p) && LEGUMES.has(c)) return 'ban' // бобовые по бобовым
  return 'ok'
}

/* ───────────────────────── Поля ───────────────────────── */

interface MockCycle {
  year: number
  culture_name: string
  kind: 'main' | 'catch' | 'fallow'
}

interface MockField extends Omit<Field, 'current_cycle'> {
  current_cycle: MockCycle | null
}

/** Прямоугольный контур вокруг базовой точки [lon, lat], сторона ~sizeDeg. */
function rectContour(lon: number, lat: number, wDeg: number, hDeg: number): number[][] {
  // Внешнее кольцо — против часовой стрелки (CONTRACTS §5).
  return [
    [lon, lat],
    [lon, lat + hDeg],
    [lon + wDeg, lat + hDeg],
    [lon + wDeg, lat],
    [lon, lat],
  ]
}

function makeField(
  n: number,
  lon: number,
  lat: number,
  wDeg: number,
  hDeg: number,
  areaHa: number,
  status: FieldStatus,
  cycle: MockCycle | null,
): MockField {
  return {
    field_id: `0000000a-0000-4000-8000-0000000000${String(n).padStart(2, '0')}`,
    name: `${n}/ВОСХОД/${Math.round(areaHa)}га`,
    contour: { type: 'Polygon', coordinates: [rectContour(lon, lat, wDeg, hDeg)] },
    area_ga: areaHa,
    status,
    current_cycle: cycle,
  }
}

// Демо-хозяйство КФХ «Восход»: окрестности н.п. Степное (~48.73 с.ш., 41.98 в.д.).
// current_cycle соответствует строке 2027 матрицы плана севооборота.
const FIELDS: MockField[] = [
  makeField(1, 41.9812, 48.7261, 0.0098, 0.0051, 84.5, 'active', {
    year: 2027,
    culture_name: 'Ячмень',
    kind: 'main',
  }),
  makeField(2, 41.9955, 48.7265, 0.0121, 0.006, 122.3, 'active', {
    year: 2027,
    culture_name: 'Пшеница озимая',
    kind: 'main',
  }),
  makeField(3, 41.9810, 48.7330, 0.0135, 0.0058, 210.0, 'active', {
    year: 2027,
    culture_name: 'Ячмень',
    kind: 'main',
  }),
  makeField(4, 41.9970, 48.7340, 0.0082, 0.0042, 47.0, 'active', {
    year: 2027,
    culture_name: 'Пар',
    kind: 'fallow',
  }),
  makeField(5, 42.0060, 48.7262, 0.0114, 0.0055, 96.8, 'active', {
    year: 2027,
    culture_name: 'Горох',
    kind: 'main',
  }),
  makeField(6, 42.0072, 48.7338, 0.0089, 0.0048, 64.2, 'archived', null),
  makeField(7, 42.0158, 48.7290, 0.0150, 0.0062, 184.6, 'active', {
    year: 2027,
    culture_name: 'Пшеница озимая',
    kind: 'main',
  }),
]

const FIELD_N = new Map(FIELDS.map((f) => [f.field_id, Number(f.field_id.slice(-2))]))

/* ────────────── Севооборот: история 2023–2026 и план 2027–2031 ────────────── */

const YEAR_FROM = 2027
const YEAR_TO = 2031

/** История полей до горизонта плана: field № → годы 2023–2026. */
const HISTORY: Record<number, Array<{ year: number; cid: string; yield_c_ha?: number }>> = {
  1: [
    { year: 2023, cid: 'a1', yield_c_ha: 41.2 },
    { year: 2024, cid: 'a2', yield_c_ha: 36.8 },
    { year: 2025, cid: 'a4', yield_c_ha: 19.4 },
    { year: 2026, cid: 'a1', yield_c_ha: 44.0 },
  ],
  2: [
    { year: 2023, cid: 'a2', yield_c_ha: 35.1 },
    { year: 2024, cid: 'a1', yield_c_ha: 43.7 },
    { year: 2025, cid: 'a8' },
    { year: 2026, cid: 'a6', yield_c_ha: 14.2 },
  ],
  3: [
    { year: 2023, cid: 'a8' },
    { year: 2024, cid: 'a1', yield_c_ha: 45.0 },
    { year: 2025, cid: 'a2', yield_c_ha: 37.2 },
    { year: 2026, cid: 'a4', yield_c_ha: 21.2 },
  ],
  4: [
    { year: 2023, cid: 'a2', yield_c_ha: 33.4 },
    { year: 2024, cid: 'a1', yield_c_ha: 42.9 },
    { year: 2025, cid: 'a4', yield_c_ha: 18.6 },
    { year: 2026, cid: 'a2', yield_c_ha: 33.9 },
  ],
  5: [
    { year: 2023, cid: 'a1', yield_c_ha: 40.8 },
    { year: 2024, cid: 'a8' },
    { year: 2025, cid: 'a1', yield_c_ha: 46.2 },
    { year: 2026, cid: 'a2', yield_c_ha: 35.4 },
  ],
  7: [
    { year: 2023, cid: 'a1', yield_c_ha: 42.4 },
    { year: 2024, cid: 'a2', yield_c_ha: 36.1 },
    { year: 2025, cid: 'a4', yield_c_ha: 19.8 },
    { year: 2026, cid: 'a1', yield_c_ha: 45.8 },
  ],
}

/** Матрица плана севооборота: field № → культура по годам 2027..2031
 *  (суффиксы culture_id). Демо-нарушения: №3 2031 (RULE-AGRO-02) и
 *  №7 2028 (RULE-AGRO-01) — как в wireframe E-04 (07-ux §3.3). */
const MATRIX: Record<number, string[]> = {
  1: ['a2', 'a8', 'a1', 'a4', 'a3'],
  2: ['a1', 'a4', 'a2', 'a1', 'a3'],
  3: ['a6', 'a8', 'a1', 'a4', 'a1'],
  4: ['a8', 'a1', 'a2', 'a8', 'a1'],
  5: ['a4', 'a1', 'a2', 'a8', 'a1'],
  7: ['a1', 'a1', 'a2', 'a8', 'a1'],
}

/** Статус плана севооборота (меняется approve). */
const PLAN = {
  plan_id: '20ce0001-0000-4000-8000-0000000000p1'.replace('p1', '001'),
  status: 'draft' as 'draft' | 'approved' | 'archived',
  approved_by: null as string | null,
  approved_at: null as string | null,
}

/** Идентификатор по-полевого плана в моке (суффикс — номер поля). */
function planUuid(n: number): string {
  return PLAN.plan_id.slice(0, -2) + String(n).padStart(2, '0')
}

function cultureBySuffix(sfx: string): MockCulture {
  const c = CULTURES.find((x) => x.culture_id.endsWith(sfx))
  if (!c) throw new Error(`нет культуры ${sfx}`)
  return c
}

/** Детерминированный cycle_id для (поле №, год). */
function cycleUuid(n: number, year: number): string {
  const h = ((n * 0x9e3779b1 ^ year * 0x85ebca6b) >>> 0).toString(16).padStart(8, '0')
  return `1a2b3c4d-0000-4000-8000-0000${h}`
}

/** Цикл поля за год — по истории или матрице плана. */
function cycleAt(n: number, year: number): { cid: string; yield?: number } | null {
  if (year < YEAR_FROM) {
    const rec = HISTORY[n]?.find((h) => h.year === year)
    return rec ? { cid: rec.cid, yield: rec.yield_c_ha } : null
  }
  const i = year - YEAR_FROM
  const sfx = MATRIX[n]?.[i]
  return sfx ? { cid: sfx } : null
}

function fieldOfCycle(cycleId: string): { field: MockField; n: number; year: number } | null {
  for (const f of FIELDS) {
    const n = Number(f.field_id.slice(-2))
    for (let year = 2023; year <= YEAR_TO; year++) {
      if (cycleUuid(n, year) === cycleId) return { field: f, n, year }
    }
  }
  return null
}

/** Ближайший предыдущий цикл с основной культурой (пары/сидераты пропускаем
 *  при проверке предшественника — RULE-AGRO-03). */
function prevMain(n: number, year: number): { year: number; culture: MockCulture } | null {
  for (let y = year - 1; y >= 2023; y--) {
    const rec = cycleAt(n, y)
    if (rec) {
      const culture = cultureBySuffix(rec.cid)
      if (culture.kind === 'main') return { year: y, culture }
    }
  }
  return null
}

/* ───────────────────────── СЗР и удобрения ───────────────────────── */

const INPUTS: AgroInput[] = [
  {
    input_id: '3d4e5f6a-0000-4000-8000-00000000a001',
    kind: 'ppp',
    name: 'Карбендазим, КС',
    npk_content: null,
    active_substance: 'карбендазим',
    concentration: 500,
    unit: 'l_ha',
    waiting_days: 20,
    approved_crops: [cultureBySuffix('a1').culture_id, cultureBySuffix('a2').culture_id],
    max_treatments_per_season: 2,
    restrictions: 'Не применять при температуре ниже +10 °C.',
  },
  {
    input_id: '3d4e5f6a-0000-4000-8000-00000000a002',
    kind: 'ppp',
    name: 'Лямбда-цигалотрин, КС',
    npk_content: null,
    active_substance: 'лямбда-цигалотрин',
    concentration: 50,
    unit: 'l_ha',
    waiting_days: 30,
    approved_crops: [cultureBySuffix('a1').culture_id, cultureBySuffix('a2').culture_id, cultureBySuffix('a4').culture_id, cultureBySuffix('a5').culture_id],
    max_treatments_per_season: 2,
    restrictions: 'Опасен для пчёл: обработка вне лёта.',
  },
  {
    input_id: '3d4e5f6a-0000-4000-8000-00000000a003',
    kind: 'ppp',
    name: 'Рапкол, КЭ (только рапс)',
    npk_content: null,
    active_substance: 'бифентрин',
    concentration: 100,
    unit: 'l_ha',
    waiting_days: 25,
    approved_crops: [cultureBySuffix('a5').culture_id],
    max_treatments_per_season: 1,
    restrictions: null,
  },
  {
    input_id: '3d4e5f6a-0000-4000-8000-00000000b001',
    kind: 'fertilizer',
    name: 'Аммиачная селитра',
    npk_content: [34.4, 0, 0],
    active_substance: null,
    concentration: null,
    unit: 'kg_ha',
    waiting_days: null,
    approved_crops: [],
    max_treatments_per_season: null,
    restrictions: null,
  },
  {
    input_id: '3d4e5f6a-0000-4000-8000-00000000b002',
    kind: 'fertilizer',
    name: 'Аммофос',
    npk_content: [12, 52, 0],
    active_substance: null,
    concentration: null,
    unit: 'kg_ha',
    waiting_days: null,
    approved_crops: [],
    max_treatments_per_season: null,
    restrictions: null,
  },
  {
    input_id: '3d4e5f6a-0000-4000-8000-00000000b003',
    kind: 'fertilizer',
    name: 'Сульфат калия',
    npk_content: [0, 0, 50],
    active_substance: null,
    concentration: null,
    unit: 'kg_ha',
    waiting_days: null,
    approved_crops: [],
    max_treatments_per_season: null,
    restrictions: null,
  },
]

interface MockTreatment {
  plan_id: string
  cycle_id: string
  input_id: string
  dose: number
  operation_type: TreatmentPlanRequest['operation_type']
  planned_date: string
  planned_phase: string | null
  status: TreatmentPlan['status']
}

const TREATMENTS: MockTreatment[] = [
  {
    plan_id: '5e6f7a8b-0000-4000-8000-000000000001',
    cycle_id: cycleUuid(2, 2027),
    input_id: INPUTS[0].input_id,
    dose: 0.6,
    operation_type: 'spraying',
    planned_date: '2027-05-20',
    planned_phase: 'флаговый лист',
    status: 'approved',
  },
  {
    plan_id: '5e6f7a8b-0000-4000-8000-000000000002',
    cycle_id: cycleUuid(3, 2027),
    input_id: INPUTS[3].input_id,
    dose: 140,
    operation_type: 'fertilizing',
    planned_date: '2027-04-12',
    planned_phase: 'кущение',
    status: 'in_progress',
  },
  {
    plan_id: '5e6f7a8b-0000-4000-8000-000000000003',
    cycle_id: cycleUuid(5, 2027),
    input_id: INPUTS[1].input_id,
    dose: 0.2,
    operation_type: 'spraying',
    planned_date: '2027-06-08',
    planned_phase: 'бутонизация',
    status: 'draft',
  },
  {
    plan_id: '5e6f7a8b-0000-4000-8000-000000000004',
    cycle_id: cycleUuid(7, 2027),
    input_id: INPUTS[0].input_id,
    dose: 0.5,
    operation_type: 'spraying',
    planned_date: '2027-05-28',
    planned_phase: 'колошение',
    status: 'draft',
  },
  {
    plan_id: '5e6f7a8b-0000-4000-8000-000000000005',
    cycle_id: cycleUuid(1, 2027),
    input_id: INPUTS[3].input_id,
    dose: 120,
    operation_type: 'fertilizing',
    planned_date: '2027-04-25',
    planned_phase: 'кущение',
    status: 'draft',
  },
]

function treatmentView(t: MockTreatment): TreatmentPlan {
  const ctx = fieldOfCycle(t.cycle_id)
  const input = INPUTS.find((i) => i.input_id === t.input_id)!
  const culture = ctx ? cultureBySuffix(cycleAt(ctx.n, ctx.year)!.cid) : null
  return {
    plan_id: t.plan_id,
    cycle_id: t.cycle_id,
    field_id: ctx?.field.field_id ?? '',
    field_name: ctx?.field.name ?? '—',
    year: ctx?.year ?? 0,
    culture_name: culture?.name ?? '—',
    input_id: t.input_id,
    input_name: input.name,
    input_kind: input.kind,
    unit: input.unit,
    dose: t.dose,
    operation_type: t.operation_type,
    planned_date: t.planned_date,
    planned_phase: t.planned_phase,
    status: t.status,
    // GET-список (API-15): weather_window не считается (null), как в backend.
    weather_window: null,
  }
}

/* ───────────────────────── Пользователи, NDVI, погода ───────────────────────── */

interface MockUser {
  login: string
  password: string
  name: string
  role: 'agronomist' | 'manager' | 'machine_operator' | 'livestock_specialist' | 'admin'
}

const USERS: MockUser[] = [
  { login: 'agronom', password: 'demo', name: 'Агроном', role: 'agronomist' },
  { login: 'manager', password: 'demo', name: 'Руководитель', role: 'manager' },
]

function problem(
  status: number,
  type: string,
  title: string,
  detail: string,
  code?: string,
): TransportResponse {
  const extra = code ? { code } : {}
  return { status, ok: false, json: null, problem: { type, title, status, detail, ...extra } }
}

function ok(json: unknown, status = 200): TransportResponse {
  return { status, ok: true, json, problem: null }
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// Журнал операций (API-17/18). Идемпотентность: повторный POST /operations
// с тем же client_id не создаёт дубль — возвращает уже созданную операцию.
const OPERATIONS: Array<FieldOperation & { created_at: string }> = [
  {
    client_id: 'seed-0001-0000-4000-8000-000000000001',
    field_id: FIELDS[2].field_id,
    operation_type: 'fertilizing',
    date: '2027-04-14',
    input_id: INPUTS[3].input_id,
    actual_dose: 138,
    actual_area_ga: 208.4,
    performer: 'Сидоров В.',
    source: 'plan_execution',
    operation_id: uuid(901),
    deviation_flag: false,
    needs_review: false,
    status: 'done',
    created_at: '2027-04-14T18:02:00Z',
  },
  {
    client_id: 'seed-0002-0000-4000-8000-000000000002',
    field_id: FIELDS[1].field_id,
    operation_type: 'spraying',
    date: '2027-05-21',
    input_id: INPUTS[0].input_id,
    actual_dose: 0.6,
    actual_area_ga: 121.9,
    performer: 'Петров И.',
    source: 'plan_execution',
    operation_id: uuid(902),
    deviation_flag: false,
    needs_review: false,
    status: 'done',
    created_at: '2027-05-21T19:40:00Z',
  },
  {
    // Отклонение дозы >10 % от плана (RULE-AGRO-11): план 0,2 л/га → факт 0,32.
    client_id: 'seed-0003-0000-4000-8000-000000000003',
    field_id: FIELDS[4].field_id,
    operation_type: 'spraying',
    date: '2027-06-08',
    input_id: INPUTS[1].input_id,
    actual_dose: 0.32,
    actual_area_ga: 96.3,
    performer: 'Петров И.',
    source: 'plan_execution',
    operation_id: uuid(903),
    deviation_flag: true,
    needs_review: false,
    status: 'done',
    created_at: '2027-06-08T18:30:00Z',
  },
  {
    // Отклонение в минус: план 0,5 л/га → факт 0,38 (−24 %, RULE-AGRO-11).
    client_id: 'seed-0004-0000-4000-8000-000000000004',
    field_id: FIELDS[6].field_id,
    operation_type: 'spraying',
    date: '2027-05-29',
    input_id: INPUTS[0].input_id,
    actual_dose: 0.38,
    actual_area_ga: 152.1,
    performer: 'Смирнов А.',
    source: 'plan_execution',
    operation_id: uuid(904),
    deviation_flag: true,
    needs_review: false,
    status: 'done',
    created_at: '2027-05-29T20:05:00Z',
  },
  {
    // Вне плана внесения (без препарата) — обработка почвы.
    client_id: 'seed-0005-0000-4000-8000-000000000005',
    field_id: FIELDS[0].field_id,
    operation_type: 'tillage',
    date: '2027-07-02',
    input_id: null,
    actual_dose: null,
    actual_area_ga: 84.5,
    performer: 'Ильин П.',
    source: 'manual',
    operation_id: uuid(905),
    deviation_flag: false,
    needs_review: false,
    status: 'done',
    created_at: '2027-07-02T17:45:00Z',
  },
]

function uuid(n: number): string {
  return `f1e2d3c4-0000-4000-8000-00000000${String(n).padStart(4, '0')}`
}

/** NDVI-таймлайн сезона (§7.5): 9 точек апрель–июль, детерминированно
 *  от поля; year — сезон (мок-мир: 2027 текущий, 2026 прошлый), прошлые
 *  сезоны чуть ниже кривой, чтобы данные сезонов различались на графике. */
function ndviOf(n: number, year = 2027): NdviPoint[] {
  const dates = [
    '04-10', '04-24', '05-08', '05-22', '06-05',
    '06-19', '07-03', '07-17', '07-31',
  ].map((md) => `${year}-${md}`)
  const base = 0.62 + n * 0.013 - (2027 - year) * 0.04
  return dates.map((date, i) => {
    const wave = Math.sin((i / (dates.length - 1)) * Math.PI) // рост → пик → спад
    const ndvi = Math.round((0.18 + wave * base) * 100) / 100
    const cloudy = i === 2 || i === 6
    return { date, ndvi: cloudy ? Math.round((0.3 + n * 0.01) * 100) / 100 : ndvi, cloud_pct: cloudy ? 92 : 5 + ((n + i) % 12) }
  })
}

/** §7.5 (API-54): сезоны NDVI мока — текущий 2027 и прошлый 2026 у всех
 *  полей (культура сезона — из истории/матрицы циклов, main | null). */
const SEASON_YEARS = [2027, 2026]

function seasonCultureName(n: number, year: number): string | null {
  const rec = cycleAt(n, year)
  if (!rec) return null
  const culture = cultureBySuffix(rec.cid)
  return culture.kind === 'main' ? culture.name : null
}

/** API-52 (§7.4 v1.7, #97/#122): зоны дифвнесения по пиковой чистой NDVI-сцене
 *  сезона — 3 зоны по перцентилям; строятся ВСЕГДА при наличии чистых сцен
 *  (ворота однородности удалены, фидбек #364). Однородность — метрики в
 *  ответе: iqr_ndvi и spatial_spread (у поля 6 обе малы — зон меньше
 *  контраста, но они есть). */
function vraOf(n: number, fieldId: string, year = 2027): {
  field_id: string
  date: string
  iqr_ndvi: number
  spatial_spread: number
  peak_dates: string[]
  base_dose_kg_ha: number | null
  zones: Array<{ zone: number; label: string; area_ga: number; ndvi_mean: number; dose_factor: number; dose_kg_ha: number | null }>
  map_png_url: string
} {
  const points = ndviOf(n, year)
  // пиковая сцена — максимальный NDVI среди чистых; топ-3 для peak_dates
  const clear = points.filter((p) => p.cloud_pct <= 60)
  const byPeak = [...clear].sort((a, b) => b.ndvi - a.ndvi)
  const peak = byPeak[0] ?? points[points.length - 1]
  const peakDates = byPeak.slice(0, 3).map((p) => p.date)
  const area = 60 + n * 18.7
  const share = n === 6 ? [0.42, 0.5, 0.08] : [0.32, 0.45, 0.23] // доли зон 1/2/3
  const means = [0.52, 0.63, 0.75].map((v) => Math.round((v + n * 0.005) * 100) / 100)
  const factors = [1.15, 1.0, 0.85]
  const labels = ['пониженное развитие', 'среднее развитие', 'повышенное развитие']
  // база — последний approved fertilizing-план; у поля 3 планов нет → null
  const base = n === 3 ? null : 70 + (n % 4) * 5
  return {
    field_id: fieldId,
    date: peak.date,
    iqr_ndvi: Math.round((n === 6 ? 0.04 : 0.18 + (n % 3) * 0.02) * 1000) / 1000,
    spatial_spread: Math.round((n === 6 ? 0.02 : 0.05 + (n % 3) * 0.015) * 1000) / 1000,
    peak_dates: peakDates,
    base_dose_kg_ha: base,
    zones: share.map((s, i) => ({
      zone: i + 1,
      label: labels[i],
      area_ga: Math.round(area * s * 10) / 10,
      ndvi_mean: means[i],
      dose_factor: factors[i],
      dose_kg_ha: base === null ? null : Math.round(base * factors[i] * 10) / 10,
    })),
    map_png_url: `/fields/${fieldId}/vra/map.png`,
  }
}

/** API-53 (§7.4, #97): прогноз урожайности по эмпирике NDVI (детерминирован
 *  от поля); у поля 4 (пар) бэкенд отдал бы 422 yield_no_culture. */
function yieldOf(n: number, fieldId: string, cultureName: string, year = 2027): {
  field_id: string
  cycle_year: number
  year: number
  culture_id: string
  culture_name: string
  method: string
  ndvi_max: number
  ndvi_integral: number
  forecast_c_ha: number
  low_c_ha: number
  high_c_ha: number
  disclaimer: string
} {
  const points = ndviOf(n, year).filter((p) => p.cloud_pct <= 60)
  const ndviMax = Math.max(...points.map((p) => p.ndvi))
  const integral = Math.round((points.reduce((s, p) => s + Math.max(0, p.ndvi - 0.2), 0) / 100) * 1000) / 1000
  const forecast = Math.round((12 + n * 1.4 + ndviMax * 15 + integral * 6) * 10) / 10
  const culture = CULTURES.find((c) => c.name === cultureName) ?? CULTURES[0]
  return {
    field_id: fieldId,
    cycle_year: year,
    year,
    culture_id: culture.culture_id,
    culture_name: cultureName,
    method: 'ndvi-empirical',
    ndvi_max: ndviMax,
    ndvi_integral: integral,
    forecast_c_ha: forecast,
    low_c_ha: Math.round(forecast * 0.75 * 10) / 10,
    high_c_ha: Math.round(forecast * 1.25 * 10) / 10,
    disclaimer: 'оценка по спутниковым данным, точность растёт с числом сезонов',
  }
}

/** API-51 (§7.4, #96): «Выводы ассистента» — только числовые детекторы
 *  (source='rules': Ollama недоступен → summary=null, UI покажет items);
 *  состав детерминирован от поля. Поле 7 — без отклонений (пустые items),
 *  поле 6 — однородное (без vra_heterogeneity), поле 4 — пар. */
function insightsOf(
  n: number,
  fieldId: string,
  year = 2027,
): {
  field_id: string
  generated_at: string
  source: 'rules'
  summary: null
  items: Array<{
    code: string
    severity: 'info' | 'warn' | 'alert'
    title: string
    detail: string
    data_refs: Array<Record<string, unknown>>
  }>
} {
  // §7.5: прошлый сезон — те же детекторы по сценам своего года (даты в
  // текстах заменяются на год сезона, чтобы e2e различал сезоны).
  const lastScene = `${year}-07-31`
  const scene = { kind: 'satellite_scene', scene_id: `scene-${lastScene}-${fieldId.slice(-4)}` }
  const sample = { kind: 'agrochem_sample', sample_date: '2026-09-12' }
  const items: Array<{
    code: string
    severity: 'info' | 'warn' | 'alert'
    title: string
    detail: string
    data_refs: Array<Record<string, unknown>>
  }> = []
  if (n === 1) {
    items.push(
      {
        code: 'waterlogging',
        severity: 'alert',
        title: 'Вымокание посевов',
        detail:
          'NDWI p90 ≥ 0,10 на сцене 31.07.2027 при осадках 42 мм за 7 суток — проверьте переувлажнённые участки в северо-западной части поля'.replace(/2027/g, String(year)),
        data_refs: [scene],
      },
      {
        code: 'ndvi_drop',
        severity: 'warn',
        title: 'Падение NDVI',
        detail:
          'NDVI снизился на 0,14 за 14 суток (17.07–31.07) — вероятен стресс посевов, нужна полевая проверка',
        data_refs: [scene],
      },
      {
        code: 'vra_heterogeneity',
        severity: 'info',
        title: 'Неоднородность NDVI',
        detail: 'IQR 0,20 (≥ 0,12) — рекомендуются зоны дифференцированного внесения',
        data_refs: [scene],
      },
      {
        code: 'agrochem_low',
        severity: 'info',
        title: 'Низкий фосфор',
        detail: 'P₂O₅ 4,2 мг/100 г почвы (проба 2026) — ниже оптимума для зерновых',
        data_refs: [sample],
      },
    )
  } else if (n === 4) {
    items.push({
      code: 'drought',
      severity: 'warn',
      title: 'Дефицит влаги',
      detail:
        'Осадков 6 мм за 14 суток, NDWI p90 −0,18 — почва подсохла; пар требует обработки для сохранения влаги',
      data_refs: [scene],
    })
  } else if (n === 6) {
    items.push({
      code: 'agrochem_low',
      severity: 'info',
      title: 'Низкий калий',
      detail: 'K₂O 8,1 мг/100 г почвы (проба 2026) — ниже оптимума',
      data_refs: [sample],
    })
  } else if (n !== 7) {
    items.push({
      code: 'vra_heterogeneity',
      severity: 'info',
      title: 'Неоднородность NDVI',
      detail: 'IQR ≥ 0,12 — рекомендуются зоны дифференцированного внесения',
      data_refs: [scene],
    })
  }
  return {
    field_id: fieldId,
    generated_at: `${year}-07-31T10:00:00Z`,
    source: 'rules',
    summary: null,
    items,
  }
}

/** Погода (API-07): факты за неделю + прогноз 7 сут (детерминированный паттерн),
 *  сырая форма backend — факты/прогноз/мета. */
function weatherOf(n: number, fieldId: string): unknown {
  const day = 24 * 3600 * 1000
  const iso = (offset: number) => new Date(Date.now() + offset * day).toISOString().slice(0, 10)
  const pattern = [
    { min: 8 + (n % 3), max: 19, precip: 0, prob: 10 },
    { min: 9 + (n % 3), max: 21, precip: 0, prob: 15 },
    { min: 10, max: 23, precip: 2.5, prob: 60 },
    { min: 11, max: 20, precip: 7.8, prob: 90 },
    { min: 10, max: 17, precip: 4.1, prob: 70 },
    { min: 7, max: 15, precip: 0.5, prob: 30 },
    { min: 6, max: 16, precip: 0, prob: 5 },
  ]
  const fact = (d: { min: number; max: number; precip: number; prob: number }, i: number, offset: number) => {
    const avg = Math.round(((d.min + d.max) / 2) * 10) / 10
    return {
      observed_at: iso(offset),
      temperature_avg_c: avg,
      temperature_min_c: d.min,
      temperature_max_c: d.max,
      precipitation_mm: d.precip,
      wind_max_ms: 2 + ((n + i) % 7),
      humidity_avg_pct: 55 + ((n * 3 + i) % 30),
      source_id: 'weather.open-meteo',
    }
  }
  return {
    field_id: fieldId,
    // Факты: прошедшая неделя (сегодня — последний факт, «сейчас» в E-10).
    facts: pattern.map((d, i) => fact(d, i, i - (pattern.length - 1))),
    forecast: pattern.map((d, i) => ({
      forecast_for: iso(i + 1),
      issued_at: new Date().toISOString(),
      temperature_min_c: d.min,
      temperature_max_c: d.max,
      precipitation_mm: d.precip,
      // precip_prob_pct — docs/06 §7.2 (в агрегат API-07 тоже входит).
      precip_prob_pct: d.prob,
      wind_max_ms: 2 + ((n + i) % 7),
      humidity_avg_pct: 55 + ((n * 3 + i) % 30),
      hazard: 'none',
    })),
    meta: { source_id: 'weather.open-meteo', status: 'ok', last_success_at: null, stale: false },
  }
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / (24 * 3600 * 1000))
}

/* ─────────── Техника (E-18, API-19/20): парк и задания смены ─────────── */

const MACHINES: Machine[] = [
  {
    machine_id: 'a11ce000-0000-4000-8000-000000000001',
    machine_type: 'tractor',
    model: 'МТЗ-1221',
    gov_number: 'А321ВС40',
    width_m: 3.5,
    tank_capacity_l: null,
    engine_hours: 5420.5,
    status: 'active',
  },
  {
    machine_id: 'a11ce000-0000-4000-8000-000000000002',
    machine_type: 'sprayer',
    model: 'Amazone UX 5200',
    gov_number: 'Е501АО40',
    width_m: 24,
    tank_capacity_l: 3600,
    engine_hours: 1875.2,
    status: 'active',
  },
  {
    machine_id: 'a11ce000-0000-4000-8000-000000000003',
    machine_type: 'combine',
    model: 'John Deere S660',
    gov_number: 'В777МК40',
    width_m: null,
    tank_capacity_l: null,
    engine_hours: 2310.0,
    status: 'active',
  },
  {
    machine_id: 'a11ce000-0000-4000-8000-000000000004',
    machine_type: 'seeder',
    model: 'Kverneland Exact CD',
    gov_number: 'К905РТ40',
    width_m: 6,
    tank_capacity_l: null,
    engine_hours: 940.7,
    status: 'maintenance',
  },
  {
    machine_id: 'a11ce000-0000-4000-8000-000000000005',
    machine_type: 'truck',
    model: 'MAN TGS 26.400',
    gov_number: 'О120НХ40',
    width_m: null,
    tank_capacity_l: null,
    engine_hours: 3812.3,
    status: 'active',
  },
]

interface MockMachineJob extends MachineJob {
  track_ref: string | null
}

const MACHINE_JOBS: MockMachineJob[] = [
  {
    // Черновик из телематики — подтверждается человеком (SC-04, API-20).
    job_id: 'b0b5f000-0000-4000-8000-000000000001',
    machine_id: MACHINES[1].machine_id,
    machine_model: MACHINES[1].model,
    field_operation_id: null,
    performed_at: '2027-06-08',
    area_ha: 82.4,
    fuel_l: 96.5,
    duration_h: 5.2,
    status: 'draft',
    source: 'telemetry',
    track_ref: 's3://tracks/2027/06/08/ux5200-stepnoe-1.jsonl',
  },
  {
    job_id: 'b0b5f000-0000-4000-8000-000000000002',
    machine_id: MACHINES[0].machine_id,
    machine_model: MACHINES[0].model,
    field_operation_id: null,
    performed_at: '2027-04-14',
    area_ha: 208.1,
    fuel_l: 64.0,
    duration_h: 7.5,
    status: 'confirmed',
    source: 'manual',
    track_ref: null,
  },
  {
    job_id: 'b0b5f000-0000-4000-8000-000000000003',
    machine_id: MACHINES[2].machine_id,
    machine_model: MACHINES[2].model,
    field_operation_id: null,
    performed_at: '2027-07-28',
    area_ha: 96.2,
    fuel_l: 210.3,
    duration_h: 9.1,
    status: 'confirmed',
    source: 'telemetry',
    track_ref: 's3://tracks/2027/07/28/s660-stepnoe-4.jsonl',
  },
]

/** Копия задания без track_ref: внутренняя ссылка на телеметрию
 *  (S3) не отдаётся клиенту. */
function withoutTrackRef(job: MockMachineJob): Omit<MockMachineJob, 'track_ref'> {
  const { track_ref, ...rest } = job
  void track_ref
  return rest
}

/* ───── Источники данных (E-14, API-28): адаптеры и журнал состояний ───── */

const NOW = Date.now()
const HOUR = 3600 * 1000
const DAY = 24 * HOUR
const isoAgo = (ms: number) => new Date(NOW - ms).toISOString()

const SOURCES: AdapterStatus[] = [
  {
    source_id: 'weather.open-meteo',
    kind: 'weather',
    title: 'Погода Open-Meteo',
    last_status: 'ok',
    last_success_at: isoAgo(0.6 * HOUR),
    reason: null,
    events: [
      { at: isoAgo(0.6 * HOUR), status: 'ok', reason: null },
      { at: isoAgo(1.6 * HOUR), status: 'ok', reason: null },
    ],
  },
  {
    // Деградация: свежих снимков нет — NDVI-таймлайн показывает последнюю точку
    // с пометкой (05 §6); ядро продолжает работать.
    source_id: 'satellite.sentinel2-l2a',
    kind: 'satellite',
    title: 'Спутник Sentinel-2 L2A',
    last_status: 'degraded',
    last_success_at: isoAgo(2 * DAY),
    reason: 'Нет снимков с облачностью ≤60 % за последние 5 суток',
    events: [
      { at: isoAgo(3 * HOUR), status: 'degraded', reason: 'Нет снимков с облачностью ≤60 % за последние 5 суток' },
      { at: isoAgo(2 * DAY), status: 'ok', reason: null },
    ],
  },
  {
    source_id: 'manual',
    kind: 'manual',
    title: 'Ручной ввод',
    last_status: 'ok',
    last_success_at: isoAgo(5 * HOUR),
    reason: null,
    events: [{ at: isoAgo(5 * HOUR), status: 'ok', reason: null }],
  },
]

/* ───────── Погодные окна (RULE-AGRO-09, 03 §3.3) и прогноз (API-29) ───────── */

/** Прогноз 7 суток с деталями для оценки окон (детерминированный паттерн). */
function forecastDays(n: number): WeatherForecastDay[] {
  const iso = (offset: number) => new Date(NOW + offset * DAY).toISOString().slice(0, 10)
  const pattern = [
    { min: 8 + (n % 3), max: 19, precip: 0, prob: 10, wind: 3.2, hum: 62 },
    { min: 9 + (n % 3), max: 21, precip: 0, prob: 15, wind: 2.8, hum: 58 },
    { min: 10, max: 23, precip: 2.5, prob: 60, wind: 4.6, hum: 72 },
    { min: 11, max: 20, precip: 7.8, prob: 90, wind: 7.4, hum: 88 },
    { min: 10, max: 17, precip: 4.1, prob: 70, wind: 5.1, hum: 76 },
    { min: 7, max: 15, precip: 0.5, prob: 30, wind: 3.0, hum: 60 },
    { min: 6, max: 16, precip: 0, prob: 5, wind: 2.2 + (n % 2) * 0.4, hum: 54 },
  ]
  return pattern.map((d, i) => ({
    date: iso(i + 1),
    temp_min_c: d.min,
    temp_max_c: d.max,
    precip_mm: d.precip,
    precip_prob_pct: d.prob,
    wind_ms: d.wind,
    humidity_pct: d.hum,
  }))
}

const OP_WINDOW_LABELS: Record<WindowOperationType, string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seeding: 'посев',
  harvesting: 'уборка',
}

/** Сутки входят в окно операции T? Условия — RULE-AGRO-09 (03 §3.3);
 *  возвращает [флаг, список нарушенных условий по-русски]. */
function windowCheck(
  type: WindowOperationType,
  day: WeatherForecastDay,
  prevPrecip: number | null,
): [boolean, string[]] {
  const tAvg = (day.temp_min_c + day.temp_max_c) / 2
  const fails: string[] = []
  if (type === 'spraying') {
    if (tAvg < 5) fails.push(`t° ${tAvg} < 5 °C`)
    if (tAvg > 25) fails.push(`t° ${tAvg} > 25 °C`)
    if (day.wind_ms > 4) fails.push(`ветер ${day.wind_ms} > 4 м/с`)
    if ((day.precip_prob_pct ?? 0) >= 50) fails.push(`вероятность осадков ${day.precip_prob_pct} % ≥ 50 %`)
    if (day.humidity_pct < 40 || day.humidity_pct > 80)
      fails.push(`влажность ${day.humidity_pct} % вне 40–80 %`)
  } else if (type === 'fertilizing') {
    if (tAvg < 5) fails.push(`t° ${tAvg} < 5 °C`)
    if (day.wind_ms > 6) fails.push(`ветер ${day.wind_ms} > 6 м/с`)
    if ((day.precip_prob_pct ?? 0) >= 50 || day.precip_mm > 0)
      fails.push(`осадки ожидаются (${day.precip_mm} мм, ${day.precip_prob_pct} %)`)
  } else if (type === 'seeding') {
    if (tAvg - 1 < 4) fails.push(`t° почвы ~${tAvg - 1} < 4 °C`)
    if (prevPrecip !== null && prevPrecip >= 10)
      fails.push(`осадки за предшествующие сутки ${prevPrecip} ≥ 10 мм`)
  } else {
    if (day.precip_mm >= 2) fails.push(`осадки ${day.precip_mm} ≥ 2 мм`)
    if (day.humidity_pct >= 90) fails.push(`влажность ${day.humidity_pct} % ≥ 90 %`)
  }
  return [fails.length === 0, fails]
}

/** WEATHER-WINDOW (docs/06 §7.2) для плана spraying: окно на planned_date по
 *  живому прогнозу мока (7 сут от сегодня). null — дата вне горизонта
 *  прогноза (как в backend: weather_window: null). */
function windowForPlan(plannedDate: string): PlanWeatherWindow | null {
  const days = forecastDays(7)
  const day = days.find((d) => d.date === plannedDate)
  if (!day) return null
  const i = days.indexOf(day)
  const [ok, fails] = windowCheck('spraying', day, i === 0 ? 0 : days[i - 1].precip_mm)
  // suggested_dates — до 5 подходящих дат из 7 суток, по возрастанию даты
  // (planned_date при ok=true входит в список сама собой).
  const suggested = days
    .filter((d, j) => {
      const [dOk] = windowCheck('spraying', d, j === 0 ? 0 : days[j - 1].precip_mm)
      return dOk
    })
    .slice(0, 5)
    .map((d) => d.date)
  return {
    ok,
    detail: ok
      ? `${plannedDate}: условия опрыскивания выполнены (RULE-AGRO-09)`
      : `${plannedDate}: ${fails.join(', ')}`,
    suggested_dates: suggested,
  }
}

/** GET /weather/windows: сырая форма backend (сутки с причинами, rule_text). */
function windowsOf(n: number, type: WindowOperationType, fieldId: string) {
  const days = forecastDays(n)
  const raw = days.map((d, i) => {
    const [isWindow, fails] = windowCheck(type, d, i === 0 ? 0 : days[i - 1].precip_mm)
    return { day: d.date, ok: isWindow, reasons: fails }
  })
  const nearest = raw.find((d) => d.ok)
  return {
    operation_type: type,
    field_id: fieldId,
    rule_text: `Условия ${OP_WINDOW_LABELS[type]} (RULE-AGRO-09, docs/03 §3.3)`,
    window_open: Boolean(nearest),
    days: raw,
    alert: nearest
      ? null
      : `В горизонте ${days.length} суток нет погодного окна для «${type}» (${raw[0]?.reasons.join('; ') ?? 'условия не выполнены'})`,
  }
}

/* ───────────────────────── Транспорт ───────────────────────── */

/** Отрисовка демо-PNG 200×100 (canvas) для raw(): ndvi — палитра
 *  коричневый→жёлтый→зелёный (как бэкенд), truecolor — оливково-зелёные
 *  тона, ndwi (§7.4) — «сухо→влажно→вода» жёлтый→бирюзовый→синий;
 *  «пятно» детерминировано от индекса даты, чтобы сцены различались. */
async function renderScenePng(
  variant: 'ndvi' | 'truecolor' | 'ndwi',
  sceneIdx: number,
): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = 200
  canvas.height = 100
  const ctx = canvas.getContext('2d')
  if (!ctx) return new Blob()
  if (variant === 'ndvi') {
    const grad = ctx.createLinearGradient(0, 100, 0, 0)
    grad.addColorStop(0, '#8a5a2b')
    grad.addColorStop(0.5, '#d9c34a')
    grad.addColorStop(1, '#2e7d32')
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, 200, 100)
  } else if (variant === 'ndwi') {
    const grad = ctx.createLinearGradient(0, 100, 0, 0)
    grad.addColorStop(0, '#e0c24a')
    grad.addColorStop(0.5, '#26a69a')
    grad.addColorStop(1, '#1565c0')
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, 200, 100)
  } else {
    ctx.fillStyle = '#6f7d3c'
    ctx.fillRect(0, 0, 200, 100)
    ctx.fillStyle = '#8d9a52'
    for (let i = 0; i < 8; i++) {
      ctx.fillRect((i * 37 + sceneIdx * 11) % 190, (i * 53 + sceneIdx * 7) % 90, 10, 8)
    }
  }
  ctx.fillStyle =
    variant === 'ndvi'
      ? 'rgba(46,125,50,0.5)'
      : variant === 'ndwi'
        ? 'rgba(21,101,192,0.55)'
        : 'rgba(59,69,31,0.5)'
  ctx.beginPath()
  ctx.arc(60 + (sceneIdx * 13) % 80, 40 + (sceneIdx * 7) % 30, 18, 0, Math.PI * 2)
  ctx.fill()
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b ?? new Blob()), 'image/png'))
}

/** Отрисовка демо-PNG карты зон VRA (API-52, §7.4, #97): 3 вертикальные
 *  полосы красный→жёлтый→зелёный (зоны 1/2/3) по долям площади зон. */
async function vraMapPng(
  zones: Array<{ zone: number; area_ga: number }>,
): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = 200
  canvas.height = 100
  const ctx = canvas.getContext('2d')
  if (!ctx) return new Blob()
  const total = zones.reduce((s, z) => s + z.area_ga, 0) || 1
  const colors = ['#e57373', '#fff176', '#81c784'] // пониженное → повышенное
  let x = 0
  for (const z of zones) {
    const w = (z.area_ga / total) * 200
    ctx.fillStyle = colors[z.zone - 1] ?? '#cccccc'
    ctx.fillRect(x, 0, w + 1, 100)
    x += w
  }
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b ?? new Blob()), 'image/png'))
}

export class MockTransport implements Transport {
  async request(method: string, path: string, body?: unknown): Promise<TransportResponse> {
    // Имитация сети; задержку можно переопределить из e2e через
    // window.__CDA_MOCK_DELAY__ (v2-fix-E: проверка скелетонов).
    const hook = globalThis as { __CDA_MOCK_DELAY__?: number }
    await delay(hook.__CDA_MOCK_DELAY__ ?? 200)
    const [pathname, search = ''] = path.split('?')
    const params = new URLSearchParams(search)

    /* ── Аутентификация (API-46/47) ── */
    if (method === 'POST' && pathname === '/auth/login') {
      const { login = '', password = '' } = (body ?? {}) as Record<string, string>
      const user = USERS.find((u) => u.login === login && u.password === password)
      if (!user) {
        return {
          status: 401,
          ok: false,
          json: null,
          problem: {
            type: 'https://cda.example/errors/bad-credentials',
            title: 'Ошибка входа',
            status: 401,
            detail: 'Неверный логин или пароль. Демо-доступ: agronom / demo.',
            code: 'bad_credentials',
          },
        }
      }
      return ok({
        token: `mock-token-${user.login}`,
        user: { name: user.name, role: user.role, farm_name: 'КФХ «Восход»' },
      })
    }

    /* ── Справочники (API-48) ── */
    if (method === 'GET' && pathname === '/references/cultures') {
      const cropGroup = params.get('crop_group')
      const q = (params.get('q') ?? '').trim().toLowerCase()
      let items = CULTURES.map((c) => ({
        culture_id: c.culture_id,
        name: c.name,
        kind: c.kind,
        forage_flag: false,
        crop_group: c.crop_group,
        repeat_years: c.repeat_years,
        color_hex: c.color_hex,
      }))
      if (cropGroup) items = items.filter((c) => c.crop_group === cropGroup)
      if (q) items = items.filter((c) => c.name.toLowerCase().includes(q))
      const limit = Math.min(Number(params.get('limit') ?? 50), 100)
      const offset = Number(params.get('offset') ?? 0)
      return ok({ items: items.slice(offset, offset + limit), total: items.length, limit, offset })
    }

    /* ── Поля (API-01) ── */
    if (method === 'GET' && pathname === '/fields') {
      const q = (params.get('q') ?? '').trim().toLowerCase()
      const status = params.get('status')
      const withGeometry = params.get('with_geometry') === 'true'
      const limit = Number(params.get('limit') ?? 50) || 50
      const offset = Number(params.get('offset') ?? 0) || 0

      let items = FIELDS
      if (q) items = items.filter((f) => f.name.toLowerCase().includes(q))
      if (status && status !== 'all') items = items.filter((f) => f.status === status)

      // API-01: contour опущен по умолчанию, with_geometry=true его добавляет.
      const pageItems = items.slice(offset, offset + limit).map((f) =>
        withGeometry ? f : ({ ...f, contour: undefined } as Field),
      )
      const page: Page<Field> = {
        items: pageItems,
        total: items.length,
        limit,
        offset,
      }
      return ok(page)
    }

    /* ── История севооборота поля (API-49): годы 2023..YEAR_TO по убыванию ── */
    const cropCyclesMatch = /^\/fields\/([\w-]+)\/crop-cycles$/.exec(pathname)
    if (method === 'GET' && cropCyclesMatch) {
      const n = FIELD_N.get(cropCyclesMatch[1])
      if (n === undefined) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${cropCyclesMatch[1]} не найдено`)
      }
      const nowYear = new Date().getFullYear()
      const all: Array<{
        year: number
        culture_id: string
        culture_name: string
        kind: MockCulture['kind']
        status: 'completed' | 'in_progress' | 'planned'
        color_hex: string | null
      }> = []
      for (let year = 2023; year <= YEAR_TO; year++) {
        const rec = cycleAt(n, year)
        if (!rec) continue
        const culture = cultureBySuffix(rec.cid)
        all.push({
          year,
          culture_id: culture.culture_id,
          culture_name: culture.name,
          kind: culture.kind,
          status: year < nowYear ? 'completed' : year === nowYear ? 'in_progress' : 'planned',
          color_hex: culture.color_hex,
        })
      }
      all.sort((a, b) => b.year - a.year)
      const from = Number(params.get('year_from') ?? 0)
      const to = Number(params.get('year_to') ?? 0)
      const items = all.filter((i) => (!from || i.year >= from) && (!to || i.year <= to))
      return ok({ items, total: items.length, limit: items.length, offset: 0 })
    }

    /* ── Карточка «Поле 360°» (API-05): поле целиком, как реальный backend ── */
    const cardMatch = /^\/fields\/([\w-]+)$/.exec(pathname)
    if (method === 'GET' && cardMatch) {
      const field = FIELDS.find((f) => f.field_id === cardMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${cardMatch[1]} не найдено`)
      }
      const n = FIELD_N.get(field.field_id)!

      // Журнал поля — последние операции мока (API-18), как в агрегате API-05.
      const ops = [...OPERATIONS]
        .filter((o) => o.field_id === field.field_id)
        .slice(-3)
        .map((o) => ({
          date: o.date,
          operation_type: o.operation_type,
          status: o.status,
          performer: o.performer ?? null,
        }))

      // Сырая форма API-05: поле + версии + журнал; ndvi/weather — null
      // (спутник/погода в MVP агрегируются отдельными вызовами API-06/07).
      const detail: Api05FieldDetail = {
        ...(field as Field),
        contour: field.contour ?? {
          type: 'Polygon',
          coordinates: [[[41.3, 52.0], [41.4, 52.0], [41.4, 52.1], [41.3, 52.1], [41.3, 52.0]]],
        },
        versions: [
          {
            version_no: 1,
            area_ga: field.area_ga,
            valid_from: '2026-01-01',
            reason: 'демо-хозяйство (мок)',
            contour: field.contour ?? {
              type: 'Polygon',
              coordinates: [[[41.3, 52.0], [41.4, 52.0], [41.4, 52.1], [41.3, 52.1], [41.3, 52.0]]],
            },
          },
        ],
        operations: ops,
        ndvi: null,
        weather: null,
      }
      void n
      return ok(detail)
    }

    /* ── Сезоны NDVI (API-54, §7.5): годы по убыванию, текущий помечен ── */
    const seasonsMatch = /^\/fields\/([\w-]+)\/seasons$/.exec(pathname)
    if (method === 'GET' && seasonsMatch) {
      const field = FIELDS.find((f) => f.field_id === seasonsMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${seasonsMatch[1]} не найдено`)
      }
      const n = FIELD_N.get(field.field_id)!
      const items = SEASON_YEARS.map((year) => {
        const clear = ndviOf(n, year).filter((p) => p.cloud_pct <= 60)
        return {
          year,
          scenes: clear.length,
          first_date: clear[0].date,
          last_date: clear[clear.length - 1].date,
          culture_name: seasonCultureName(n, year),
          is_current: year === 2027,
        }
      })
      return ok({ field_id: field.field_id, items, total: items.length })
    }

    /* ── NDVI-таймлайн (API-06): пагинация + мета источника, как backend;
       ?year=YYYY (§7.5) — сцены сезона; неизвестный сезон — пустой список ── */
    const ndviMatch = /^\/fields\/([\w-]+)\/ndvi$/.exec(pathname)
    if (method === 'GET' && ndviMatch) {
      const field = FIELDS.find((f) => f.field_id === ndviMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${ndviMatch[1]} не найдено`)
      }
      const yearParam = Number(params.get('year') ?? 0)
      const seasonUnknown = yearParam !== 0 && !SEASON_YEARS.includes(yearParam)
      const ndviPoints = ndviOf(FIELD_N.get(field.field_id)!, yearParam || 2027)
      const n = FIELD_N.get(field.field_id)!
      const scenes = seasonUnknown ? [] : ndviPoints.map((p, i) => ({
        scene_id: `scene-${p.date}-${field.field_id.slice(-4)}`,
        field_id: field.field_id,
        index: 'ndvi',
        captured_at: p.date,
        resolution_m: 10,
        index_avg: p.ndvi,
        index_min: Math.round((p.ndvi - 0.06) * 100) / 100,
        index_max: Math.round((p.ndvi + 0.06) * 100) / 100,
        cloud_pct: p.cloud_pct,
        composite_days: 1,
        source_id: 'satellite.sentinel2-l2a',
        trust: 0.9,
        anomaly_zones_ref: null,
        // §7.3: рендеры есть у последних дат сезона (старые — как исторические),
        // но не у облачных сцен (cloud ≤ 60, как в ingest).
        render_available: i >= ndviPoints.length - 3 && p.cloud_pct <= 60,
        // §7.4: NDWI-детектор — вымокание у отдельных сцен (у поля 1 —
        // и на последней рендерной дате, для бейджа по умолчанию).
        waterlogged: i === 4 || (n === 1 && i === 8),
      }))
      return ok({
        items: scenes,
        total: scenes.length,
        limit: 50,
        offset: 0,
        index: 'ndvi',
        meta: { source_id: 'satellite.sentinel2-l2a', status: 'ok', last_success_at: null, stale: false },
      })
    }

    /* ── Зоны дифвнесения (API-52, §7.4 v1.7): строятся всегда при чистых
       сценах; ?year=YYYY (§7.5) — сезон; нет сезона — 404 season_not_found ── */
    const vraMatch = /^\/fields\/([\w-]+)\/vra$/.exec(pathname)
    if (method === 'GET' && vraMatch) {
      const field = FIELDS.find((f) => f.field_id === vraMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${vraMatch[1]} не найдено`)
      }
      const n = FIELD_N.get(field.field_id)!
      const yearParam = Number(params.get('year') ?? 0)
      if (yearParam !== 0 && !SEASON_YEARS.includes(yearParam)) {
        return problem(
          404,
          'https://cda.example/errors/season-not-found',
          'Нет сезона',
          `У поля нет чистых NDVI-сцен сезона ${yearParam}`,
          'season_not_found',
        )
      }
      return ok(vraOf(n, field.field_id, yearParam || 2027))
    }

    /* ── Прогноз урожайности (API-53, §7.4, #97); поле 4 — пар (без культуры);
       ?year=YYYY (§7.5) — цикл года YYYY, ответ дополняется полем year ── */
    const yieldMatch = /^\/fields\/([\w-]+)\/yield-forecast$/.exec(pathname)
    if (method === 'GET' && yieldMatch) {
      const field = FIELDS.find((f) => f.field_id === yieldMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${yieldMatch[1]} не найдено`)
      }
      const n = FIELD_N.get(field.field_id)!
      const yearParam = Number(params.get('year') ?? 0)
      if (yearParam !== 0) {
        if (!SEASON_YEARS.includes(yearParam)) {
          return problem(
            422,
            'https://cda.example/errors/yield-no-season-data',
            'Прогноз недоступен',
            `Чистых NDVI-сцен сезона ${yearParam} меньше трёх — прогноз не рассчитывается`,
            'yield_no_season_data',
          )
        }
        const cultureName = seasonCultureName(n, yearParam)
        if (!cultureName) {
          return problem(
            422,
            'https://cda.example/errors/yield-no-culture',
            'Прогноз недоступен',
            `Основной культуры в цикле ${yearParam} нет — прогноз не рассчитывается`,
            'yield_no_culture',
          )
        }
        return ok(yieldOf(n, field.field_id, cultureName, yearParam))
      }
      if (field.current_cycle?.kind === 'fallow') {
        return problem(
          422,
          'https://cda.example/errors/yield-no-culture',
          'Прогноз недоступен',
          'Цикл 2027 поля — пар: культура для прогноза отсутствует',
        )
      }
      return ok(yieldOf(n, field.field_id, field.current_cycle?.culture_name ?? 'Пшеница озимая'))
    }

    /* ── «Выводы ассистента» (API-51, §7.4, #96): только числовые
       детекторы, source='rules' (мок-мир без Ollama), поле 7 — пусто;
       ?year=YYYY (§7.5) — детекторы по сезону YYYY ── */
    const insightsMatch = /^\/fields\/([\w-]+)\/insights$/.exec(pathname)
    if (method === 'GET' && insightsMatch) {
      const field = FIELDS.find((f) => f.field_id === insightsMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${insightsMatch[1]} не найдено`)
      }
      const yearParam = Number(params.get('year') ?? 0)
      if (yearParam !== 0 && !SEASON_YEARS.includes(yearParam)) {
        return problem(
          404,
          'https://cda.example/errors/season-not-found',
          'Нет сезона',
          `У поля нет чистых NDVI-сцен сезона ${yearParam}`,
          'season_not_found',
        )
      }
      return ok(insightsOf(FIELD_N.get(field.field_id)!, field.field_id, yearParam || 2027))
    }

    /* ── Погода (API-07): факты + прогноз + мета, как backend ── */
    const weatherMatch = /^\/fields\/([\w-]+)\/weather$/.exec(pathname)
    if (method === 'GET' && weatherMatch) {
      const field = FIELDS.find((f) => f.field_id === weatherMatch[1])
      if (!field) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${weatherMatch[1]} не найдено`)
      }
      return ok(weatherOf(FIELD_N.get(field.field_id)!, field.field_id))
    }

    /* ── План севооборота (API-08): по-полевые планы с items-циклами ── */
    if (method === 'GET' && pathname === '/rotation-plans') {
      const plans = FIELDS.filter((f) => f.status === 'active').map((f) => {
        const n = FIELD_N.get(f.field_id)!
        const items = []
        for (let year = YEAR_FROM; year <= YEAR_TO; year++) {
          const rec = cycleAt(n, year)
          if (!rec) continue
          const culture = cultureBySuffix(rec.cid)
          items.push({
            cycle_id: cycleUuid(n, year),
            field_id: f.field_id,
            year,
            culture_id: culture.culture_id,
            culture_name: culture.name,
            kind: culture.kind,
            predecessor_cycle_id: null,
            planned_yield_c_ha: null,
            planned_harvest_date: `${year}-${culture.harvest_md}`,
            out_of_rotation: false,
            reason: null,
            status: 'planned' as const,
          })
        }
        return {
          plan_id: planUuid(n),
          field_id: f.field_id,
          year_from: YEAR_FROM,
          year_to: YEAR_TO,
          status: PLAN.status,
          approved_by: PLAN.approved_by,
          approved_at: PLAN.approved_at,
          items,
          violations: [],
        }
      })
      return ok({ items: plans, total: plans.length, limit: 200, offset: 0 })
    }

    // POST /rotation-plans (API-09) — демо-план уже существует.
    if (method === 'POST' && pathname === '/rotation-plans') {
      return problem(
        409,
        'https://cda.example/errors/conflict',
        'Конфликт',
        `План севооборота ${YEAR_FROM}–${YEAR_TO} уже существует; для нового плана сначала отправьте текущий в архив`,
      )
    }

    /* ── Согласование плана (API-10): ответ PlanDetail, как backend ── */
    const approvePlanMatch = /^\/rotation-plans\/([\w-]+)\/approve$/.exec(pathname)
    if (method === 'POST' && approvePlanMatch) {
      const n = Number(approvePlanMatch[1].slice(-2))
      if (approvePlanMatch[1] !== PLAN.plan_id && !FIELDS.some((f) => FIELD_N.get(f.field_id) === n)) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `План ${approvePlanMatch[1]} не найден`)
      }
      if (PLAN.status === 'approved') {
        return problem(409, 'https://cda.example/errors/conflict', 'Конфликт', 'План уже согласован')
      }
      if (PLAN.status === 'archived') {
        return problem(409, 'https://cda.example/errors/conflict', 'Конфликт', 'План в архиве')
      }
      PLAN.status = 'approved'
      PLAN.approved_by = 'Руководитель'
      PLAN.approved_at = new Date().toISOString()
      const field = FIELDS.find((f) => FIELD_N.get(f.field_id) === n)!
      return ok({
        plan_id: approvePlanMatch[1],
        field_id: field.field_id,
        year_from: YEAR_FROM,
        year_to: YEAR_TO,
        status: PLAN.status,
        approved_by: PLAN.approved_by,
        approved_at: PLAN.approved_at,
        items: [],
        violations: [],
      })
    }

    /* ── Добавление/правка цикла в плане (API-11) ── */
    if (method === 'POST' && pathname === '/crop-cycles') {
      const req = (body ?? {}) as { field_id?: string; year?: number; culture_id?: string; planned_yield_c_ha?: number }
      if (!req.field_id || !req.year || !req.culture_id) {
        return problem(400, 'https://cda.example/errors/validation', 'Некорректный запрос', 'Обязательны field_id, year, culture_id')
      }
      const field = FIELDS.find((f) => f.field_id === req.field_id)
      if (!field || field.status !== 'active') {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Поле ${req.field_id} не найдено`)
      }
      const culture = cultureById(req.culture_id)
      if (!culture) {
        return problem(400, 'https://cda.example/errors/validation', 'Некорректный запрос', `Культура ${req.culture_id} не найдена в справочнике`)
      }
      const n = FIELD_N.get(field.field_id)!
      const year = req.year
      if (year < YEAR_FROM || year > YEAR_TO) {
        return problem(422, 'https://cda.example/errors/validation', 'Нарушено бизнес-правило', `Год ${year} вне горизонта плана ${YEAR_FROM}–${YEAR_TO}`)
      }

      // RULE-AGRO-01: запретные предшественник/преемник блокируют цикл (422).
      const checkPair = (a: MockCulture, aYear: number, b: MockCulture, bYear: number): TransportResponse | null => {
        // b следует за a: смотрим совместимость a → b.
        const verdict = compat(a, b)
        if (verdict === 'ban') {
          return {
            status: 422,
            ok: false,
            json: null,
            problem: {
              type: 'https://cda.example/errors/predecessor',
              title: 'Нарушено бизнес-правило',
              status: 422,
              detail:
                `«${field.name} · ${bYear} ${b.name} после ${a.name} ${aYear}»: ` +
                `такой предшественник запрещён правилом севооборота`,
              rule_id: 'RULE-AGRO-01',
              entity: 'crop_cycle',
            },
          }
        }
        return null
      }
      const prev = prevMain(n, year)
      if (prev && culture.kind === 'main') {
        const blocked = checkPair(prev.culture, prev.year, culture, year)
        if (blocked) return blocked
      }
      // Новый цикл сам становится предшественником для следующей культуры.
      for (let y = year + 1; y <= YEAR_TO; y++) {
        const rec = cycleAt(n, y)
        if (!rec) continue
        const next = cultureBySuffix(rec.cid)
        if (next.kind === 'main' && culture.kind === 'main') {
          const blocked = checkPair(culture, year, next, y)
          if (blocked) return blocked
          break
        }
      }

      // Upsert клетки матрицы.
      MATRIX[n][year - YEAR_FROM] = req.culture_id.slice(-2)
      // Обновляем агрегат текущего цикла поля (для списка полей).
      if (year === 2027) {
        field.current_cycle = culture.kind === 'fallow'
          ? { year, culture_name: culture.name, kind: 'fallow' }
          : { year, culture_name: culture.name, kind: culture.kind }
      }
      const cycle: CropCycleDetail = {
        cycle_id: cycleUuid(n, year),
        field_id: field.field_id,
        year,
        culture_id: culture.culture_id,
        culture_name: culture.name,
        kind: culture.kind,
        predecessor_cycle_id: null,
        planned_yield_c_ha: req.planned_yield_c_ha ?? null,
        planned_harvest_date: `${year}-${culture.harvest_md}`,
        out_of_rotation: false,
        reason: null,
        status: 'planned',
        warnings: [],
      }
      return ok(cycle, 201)
    }

    /* ── Справочник препаратов (API-14) ── */
    if (method === 'GET' && pathname === '/agro-inputs') {
      const kind = params.get('kind')
      const q = (params.get('q') ?? '').trim().toLowerCase()
      let items = INPUTS
      if (kind) items = items.filter((i) => i.kind === kind)
      if (q) items = items.filter((i) => i.name.toLowerCase().includes(q))
      return ok({ items, total: items.length, limit: 100, offset: 0 })
    }

    /* ── Планы внесения (API-15; фильтры status, field_id, cycle_id) ── */
    if (method === 'GET' && pathname === '/treatment-plans') {
      const status = params.get('status')
      const fieldId = params.get('field_id')
      const cycleId = params.get('cycle_id')
      let items = TREATMENTS
      if (status) items = items.filter((t) => t.status === status)
      if (cycleId) items = items.filter((t) => t.cycle_id === cycleId)
      if (fieldId) {
        items = items.filter((t) => fieldOfCycle(t.cycle_id)?.field.field_id === fieldId)
      }
      const limit = Number(params.get('limit') ?? 50) || 50
      const offset = Number(params.get('offset') ?? 0) || 0
      const views = items.map(treatmentView)
      return ok({ items: views.slice(offset, offset + limit), total: views.length, limit, offset })
    }

    if (method === 'POST' && pathname === '/treatment-plans') {
      const req = (body ?? {}) as Partial<TreatmentPlanRequest>
      for (const key of ['cycle_id', 'input_id', 'dose', 'operation_type', 'planned_date'] as const) {
        if (req[key] === undefined || req[key] === null || req[key] === '') {
          return problem(400, 'https://cda.example/errors/validation', 'Некорректный запрос', `Не заполнено обязательное поле: ${key}`)
        }
      }
      const ctx = fieldOfCycle(req.cycle_id!)
      if (!ctx) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Цикл ${req.cycle_id} не найден`)
      }
      const input = INPUTS.find((i) => i.input_id === req.input_id)
      if (!input) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `Препарат ${req.input_id} не найден`)
      }
      const dose = Number(req.dose)

      // RULE-AGRO-07: доза ≥ 0.
      if (!Number.isFinite(dose) || dose < 0) {
        return {
          status: 422,
          ok: false,
          json: null,
          problem: {
            type: 'https://cda.example/errors/dose-limit',
            title: 'Нарушено бизнес-правило',
            status: 422,
            detail: `Доза препарата «${input.name}» не может быть отрицательной (${dose})`,
            rule_id: 'RULE-AGRO-07',
            entity: 'treatment_plan',
          },
        }
      }

      const cycleRec = cycleAt(ctx.n, ctx.year)!
      const cycleCulture = cultureBySuffix(cycleRec.cid)

      // RULE-AGRO-10: допустимые культуры препарата.
      if (input.approved_crops.length > 0 && !input.approved_crops.includes(cycleCulture.culture_id)) {
        return {
          status: 422,
          ok: false,
          json: null,
          problem: {
            type: 'https://cda.example/errors/approved-crops',
            title: 'Нарушено бизнес-правило',
            status: 422,
            detail:
              `Препарат «${input.name}» не разрешён для культуры «${cycleCulture.name}» ` +
              `(поле ${ctx.field.name}, ${ctx.year})`,
            rule_id: 'RULE-AGRO-10',
            entity: 'treatment_plan',
          },
        }
      }

      // RULE-AGRO-08: срок ожидания до плановой уборки цикла.
      if (input.waiting_days !== null && ctx.year >= YEAR_FROM) {
        const harvestDate = `${ctx.year}-${cycleCulture.harvest_md}`
        const daysLeft = daysBetween(req.planned_date!, harvestDate)
        if (daysLeft < input.waiting_days) {
          return {
            status: 422,
            ok: false,
            json: null,
            problem: {
              type: 'https://cda.example/errors/waiting-period',
              title: 'Нарушено бизнес-правило',
              status: 422,
              detail:
                `Срок ожидания препарата «${input.name}» ${input.waiting_days} дн., ` +
                `до плановой уборки (${cycleCulture.name}, ${harvestDate}) осталось ` +
                `${Math.max(daysLeft, 0)} дн. — обработка ${req.planned_date} слишком поздняя`,
              rule_id: 'RULE-AGRO-08',
              entity: 'treatment_plan',
            },
          }
        }
      }

      // RULE-AGRO-10: максимум обработок одного препарата за сезон.
      if (input.max_treatments_per_season !== null) {
        const active = TREATMENTS.filter(
          (t) => t.cycle_id === req.cycle_id && t.input_id === req.input_id && t.status !== 'cancelled' && t.status !== 'completed',
        ).length
        if (active >= input.max_treatments_per_season) {
          return {
            status: 422,
            ok: false,
            json: null,
            problem: {
              type: 'https://cda.example/errors/max-treatments',
              title: 'Нарушено бизнес-правило',
              status: 422,
              detail:
                `Превышен лимит обработок «${input.name}» за сезон: ` +
                `${active} из ${input.max_treatments_per_season} уже запланировано`,
              rule_id: 'RULE-AGRO-10',
              entity: 'treatment_plan',
            },
          }
        }
      }

      const created: MockTreatment = {
        plan_id: uuid(500 + TREATMENTS.length),
        cycle_id: req.cycle_id!,
        input_id: req.input_id!,
        dose,
        operation_type: req.operation_type!,
        planned_date: req.planned_date!,
        planned_phase: req.planned_phase ?? null,
        status: 'draft',
      }
      TREATMENTS.unshift(created)
      const view = treatmentView(created)
      // WEATHER-WINDOW (§7.2): подсказка для spraying, не блокирующая.
      if (created.operation_type === 'spraying') {
        view.weather_window = windowForPlan(created.planned_date)
      }
      return ok(view, 201)
    }

    /* ── Согласование плана внесения (API-16) ── */
    const approveTpMatch = /^\/treatment-plans\/([\w-]+)\/approve$/.exec(pathname)
    if (method === 'POST' && approveTpMatch) {
      const t = TREATMENTS.find((x) => x.plan_id === approveTpMatch[1])
      if (!t) {
        return problem(404, 'https://cda.example/errors/not-found', 'Нет ресурса', `План внесения ${approveTpMatch[1]} не найден`)
      }
      if (t.status !== 'draft') {
        return problem(409, 'https://cda.example/errors/conflict', 'Конфликт', `План уже в статусе «${t.status}»`)
      }
      t.status = 'approved'
      const view = treatmentView(t)
      // WEATHER-WINDOW (§7.2): тот же расчёт, что в POST (API-15).
      if (t.operation_type === 'spraying') {
        view.weather_window = windowForPlan(t.planned_date)
      }
      return ok(view)
    }

    /* ── Операции (API-17/18) ── */
    if (method === 'POST' && pathname === '/operations') {
      const op = (body ?? {}) as Partial<FieldOperation>
      // Валидация обязательных полей (03 §1.6) → 400.
      for (const key of ['client_id', 'field_id', 'operation_type', 'date', 'actual_area_ga']) {
        if (!op[key as keyof FieldOperation]) {
          return problem(
            400,
            'https://cda.example/errors/validation',
            'Некорректный запрос',
            `Не заполнено обязательное поле: ${key}`,
          )
        }
      }
      // Идемпотентность офлайн-очереди: тот же client_id → 200 уже созданной
      // операции, дубль не создаётся (06 §5, API-17).
      const existing = OPERATIONS.find((o) => o.client_id === op.client_id)
      if (existing) return ok(existing, 200)

      const field = FIELDS.find((f) => f.field_id === op.field_id)
      if (!field) {
        return problem(
          404,
          'https://cda.example/errors/not-found',
          'Нет ресурса',
          `Поле ${op.field_id} не найдено`,
        )
      }
      // Урожайность — только в операции уборки (03 §1.6, backend #27):
      // yield_record при operation_type≠harvesting → 422 (code как в backend).
      if (op.yield_record && op.operation_type !== 'harvesting') {
        return {
          status: 422,
          ok: false,
          json: null,
          problem: {
            type: 'https://cda.example/errors/yield-requires-harvesting',
            title: 'Нарушено бизнес-правило',
            status: 422,
            detail: 'Урожайность принимается только для операции типа harvesting',
            code: 'yield_requires_harvesting',
            entity: 'operation',
          },
        }
      }
      // RULE-AGRO-12 (06 §5, коммит 5afd553): площадь > 1,05 × поля —
      // НЕ 422, «факт важнее плана»: сохраняем с needs_review=true.
      const needsReview = (op.actual_area_ga ?? 0) > 1.05 * field.area_ga
      const created: FieldOperation & { created_at: string } = {
        client_id: op.client_id!,
        field_id: op.field_id!,
        operation_type: op.operation_type!,
        date: op.date!,
        input_id: op.input_id ?? null,
        actual_dose: op.actual_dose ?? null,
        actual_area_ga: op.actual_area_ga!,
        performer: op.performer ?? null,
        source: op.source ?? 'manual',
        operation_id: uuid(OPERATIONS.length + 1),
        deviation_flag: false,
        needs_review: needsReview,
        status: 'done',
        created_at: new Date().toISOString(),
      }
      OPERATIONS.unshift(created)
      return ok(created, 201)
    }

    if (method === 'GET' && pathname === '/operations') {
      // API-18: журнал операций, фильтры field_id, date_from, date_to.
      const fieldId = params.get('field_id')
      const dateFrom = params.get('date_from')
      const dateTo = params.get('date_to')
      let items: FieldOperation[] = [...OPERATIONS]
      if (fieldId) items = items.filter((o) => o.field_id === fieldId)
      if (dateFrom) items = items.filter((o) => o.date >= dateFrom)
      if (dateTo) items = items.filter((o) => o.date <= dateTo)
      const limit = Number(params.get('limit') ?? 50) || 50
      const offset = Number(params.get('offset') ?? 0) || 0
      return ok({ items: items.slice(offset, offset + limit), total: items.length, limit, offset })
    }

    /* ── Погода: прогноз (API-29) и окна по RULE-AGRO-09 (MVP-6) ── */
    if (method === 'GET' && pathname === '/weather/forecast') {
      const fieldId = params.get('field_id') ?? ''
      if (!FIELDS.some((f) => f.field_id === fieldId)) {
        return problem(
          404,
          'https://cda.example/errors/not-found',
          'Нет ресурса',
          `Поле ${fieldId} не найдено`,
        )
      }
      // days 1..14, по умолчанию 7 (05 §6, REQ-NF-009: кэш при недоступности).
      const days = Math.min(Math.max(Number(params.get('days') ?? 7) || 7, 1), 14)
      const items = forecastDays(7)
        .slice(0, days)
        .map((d) => ({
          forecast_for: d.date,
          issued_at: new Date().toISOString(),
          temperature_min_c: d.temp_min_c,
          temperature_max_c: d.temp_max_c,
          precipitation_mm: d.precip_mm,
          precip_prob_pct: d.precip_prob_pct ?? null,
          wind_max_ms: d.wind_ms,
          humidity_avg_pct: d.humidity_pct,
          hazard: 'none',
        }))
      return ok({
        items,
        total: items.length,
        limit: items.length,
        offset: 0,
        meta: { source_id: 'weather.open-meteo', status: 'ok', last_success_at: null, stale: false },
      })
    }

    if (method === 'GET' && pathname === '/weather/windows') {
      const fieldId = params.get('field_id') ?? ''
      const n = FIELD_N.get(fieldId)
      if (n === undefined) {
        return problem(
          404,
          'https://cda.example/errors/not-found',
          'Нет ресурса',
          `Поле ${fieldId} не найдено`,
        )
      }
      const type = params.get('operation_type') as WindowOperationType | null
      if (!type || !(type in OP_WINDOW_LABELS)) {
        return problem(
          400,
          'https://cda.example/errors/validation',
          'Некорректный запрос',
          `Неизвестный тип операции: ${params.get('operation_type')}`,
        )
      }
      return ok(windowsOf(n, type, fieldId))
    }

    /* ── Источники данных (E-14, API-28) ── */
    if (method === 'GET' && pathname === '/sources/status') {
      const items = SOURCES.map((s) => ({
        source_id: s.source_id,
        kind: s.kind,
        enabled: true,
        last_status: s.last_status,
        last_success_at: s.last_success_at,
        last_event: s.events[0]
          ? { at: s.events[0].at, status: s.events[0].status, reason: s.events[0].reason }
          : null,
      }))
      return ok({ items, total: items.length, limit: 50, offset: 0 })
    }

    /* ── Техника и задания (E-18, API-19/20) ── */
    if (method === 'GET' && pathname === '/machines') {
      return ok({ items: MACHINES })
    }

    // GET /machine-jobs — расширение мока (нет в 06 §5): список заданий смены
    // с развёрнутыми именами машины и поля для таблицы на E-18.
    if (method === 'GET' && pathname === '/machine-jobs') {
      const status = params.get('status')
      const stripped = MACHINE_JOBS.map(withoutTrackRef)
      let items = stripped
      if (status) items = items.filter((j) => j.status === status)
      const limit = Number(params.get('limit') ?? 50) || 50
      const offset = Number(params.get('offset') ?? 0) || 0
      return ok({ items: items.slice(offset, offset + limit), total: items.length, limit, offset })
    }

    // API-20: подтверждение черновика из телематики человеком.
    const confirmJobMatch = /^\/machine-jobs\/([\w-]+)\/confirm$/.exec(pathname)
    if (method === 'POST' && confirmJobMatch) {
      const job = MACHINE_JOBS.find((j) => j.job_id === confirmJobMatch[1])
      if (!job) {
        return problem(
          404,
          'https://cda.example/errors/not-found',
          'Нет ресурса',
          `Задание ${confirmJobMatch[1]} не найдено`,
        )
      }
      if (job.status !== 'draft') {
        return problem(
          409,
          'https://cda.example/errors/conflict',
          'Конфликт',
          `Задание уже ${job.status === 'confirmed' ? 'подтверждено' : 'отклонено'}`,
        )
      }
      job.status = 'confirmed'
      return ok(withoutTrackRef(job))
    }

    return problem(
      404,
      'https://cda.example/errors/not-found',
      'Не найдено',
      `Мок не реализует ${method} ${pathname}`,
    )
  }

  /** §7.3: бинарный GET рендера сцены (PNG 200×100, canvas);
   *  имитирует эндпоинт /fields/{id}/ndvi/{date}/render.png. */
  async raw(path: string): Promise<{ ok: boolean; status: number; blob: Blob; headers: Headers }> {
    const hook = globalThis as { __CDA_MOCK_DELAY__?: number }
    await delay(hook.__CDA_MOCK_DELAY__ ?? 200)
    const [pathname, search = ''] = path.split('?')
    const params = new URLSearchParams(search)

    const empty = (status: number) => ({
      ok: false,
      status,
      blob: new Blob(),
      headers: new Headers(),
    })

    const vraMapMatch = /^\/fields\/([\w-]+)\/vra\/map\.png$/.exec(pathname)
    if (vraMapMatch) {
      const field = FIELDS.find((f) => f.field_id === vraMapMatch[1])
      if (!field || FIELD_N.get(field.field_id) === 6) return empty(404)
      const vraYear = Number(params.get('year') ?? 0)
      if (vraYear !== 0 && !SEASON_YEARS.includes(vraYear)) return empty(404)
      const vra = vraOf(FIELD_N.get(field.field_id)!, field.field_id, vraYear || 2027)
      const blob = await vraMapPng(vra.zones)
      const ring = field.contour?.coordinates[0] as number[][] | undefined
      if (!ring || ring.length < 4) return empty(404)
      const lons = ring.map((c) => c[0])
      const lats = ring.map((c) => c[1])
      const bbox = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)]
        .map((v) => v.toFixed(6))
        .join(',')
      return {
        ok: true,
        status: 200,
        blob,
        headers: new Headers({
          'Content-Type': 'image/png',
          'X-Field-Bbox': bbox,
          'X-Image-Size': '200x100',
        }),
      }
    }

    const renderMatch = /^\/fields\/([\w-]+)\/ndvi\/(\d{4}-\d{2}-\d{2})\/render\.png$/.exec(pathname)
    if (!renderMatch) return empty(404)

    const field = FIELDS.find((f) => f.field_id === renderMatch[1])
    if (!field) return empty(404)

    const variant = params.get('variant') ?? 'ndvi'
    if (variant !== 'ndvi' && variant !== 'truecolor' && variant !== 'ndwi') return empty(422)

    // Рендер есть только у дат с render_available (условие как в ndvi-эндпоинте);
    // сцена ищется в сезоне своей даты (§7.5 — у прошлых сезонов свои рендеры).
    const sceneYear = Number(renderMatch[2].slice(0, 4))
    const ndviPoints = ndviOf(
      FIELD_N.get(field.field_id)!,
      SEASON_YEARS.includes(sceneYear) ? sceneYear : 2027,
    )
    const idx = ndviPoints.findIndex((p) => p.date === renderMatch[2])
    if (idx < 0 || !(idx >= ndviPoints.length - 3 && ndviPoints[idx].cloud_pct <= 60)) {
      return empty(404)
    }

    const blob = await renderScenePng(variant, idx)
    const ring = field.contour?.coordinates[0] as number[][] | undefined
    if (!ring || ring.length < 4) return empty(404)
    const lons = ring.map((c) => c[0])
    const lats = ring.map((c) => c[1])
    const bbox = [
      Math.min(...lons),
      Math.min(...lats),
      Math.max(...lons),
      Math.max(...lats),
    ]
      .map((v) => v.toFixed(6))
      .join(',')
    const headers = new Headers({
      'Content-Type': 'image/png',
      'X-Field-Bbox': bbox,
      'X-Image-Size': '200x100',
    })
    return { ok: true, status: 200, blob, headers }
  }
}
