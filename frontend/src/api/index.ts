// Доменные вызовы API (только эндпоинты из docs/06-data-api.md §5).
// Backend отдаёт «сырые» формы (типы Raw*/…Item в types.ts); здесь они
// приводятся к экранным моделям, которые использовали вьюхи против мока.
// Мок-транспорт имитирует те же сырые формы, поэтому путь данных один.
import { apiClient, ApiError } from './client'
import type {
  AdapterState,
  AdapterStatus,
  AgroInput,
  Api05FieldDetail,
  AuthMe,
  AuthResponse,
  CropCycleHistoryItem,
  CultureReference,
  CropCycle,
  CropCycleDetail,
  CropCycleRequest,
  Field,
  FieldCard,
  FieldCardPlan,
  FieldCycle,
  FieldOperation,
  FieldStatus,
  FieldWeather,
  FieldWeatherDetail,
  ForecastPage,
  Machine,
  MachineJob,
  MachineJobStatus,
  NdviPoint,
  NdviTimelinePage,
  OperationRequest,
  RotationCell,
  RotationCycleItem,
  RotationPlan,
  RotationPlanDetail,
  SceneItem,
  SourceStatusItem,
  TreatmentPlan,
  TreatmentPlanRequest,
  TreatmentPlanStatus,
  VraZonesResponse,
  WeatherForecast,
  WeatherForecastDay,
  WeatherForecastItem,
  WeatherWindowDay,
  WeatherWindows,
  WindowOperationType,
  WindowsResponse,
  YieldForecastResponse,
} from './types'

/** API-01: GET /fields — список полей (фильтры status, q по имени).
 *  with_geometry=true — элементы с contour (для карты); по умолчанию опущен. */
export function getFields(params: {
  status?: FieldStatus | 'all'
  q?: string
  with_geometry?: boolean
  limit?: number
  offset?: number
}): Promise<{ items: Field[]; total: number; limit: number; offset: number }> {
  return apiClient.list<Field>('/fields', {
    q: params.q,
    // 'all' — все статусы: значение не отправляем (у API-01 допустимы active/merged/archived)
    status: params.status && params.status !== 'all' ? params.status : undefined,
    with_geometry: params.with_geometry === true ? 'true' : undefined,
    // API-01: limit ≤ 100, иначе 400
    limit: params.limit !== undefined ? Math.min(params.limit, 100) : undefined,
    offset: params.offset,
  })
}

/** API-48: GET /references/cultures — справочник культур (color_hex — цвет на карте).
 *  Бэкенд ограничивает limit сотней, поэтому справочник больше сотни берём постранично. */
export async function getCultureReferences(): Promise<CultureReference[]> {
  const limit = 100
  const first = await apiClient.list<CultureReference>('/references/cultures', { limit })
  const items = [...first.items]
  while (items.length < first.total) {
    const page = await apiClient.list<CultureReference>('/references/cultures', {
      limit,
      offset: items.length,
    })
    items.push(...page.items)
  }
  return items
}

/** API-17: POST /operations — запись факта операции (мобильный, офлайн-очередь).
 *  Идемпотентность по client_id: повторная отправка не создаёт дубль;
 *  RULE-AGRO-12: площадь > 1,05 × площади поля → 201/200 с needs_review=true. */
export function createOperation(payload: OperationRequest): Promise<FieldOperation> {
  return apiClient.request<FieldOperation>('/operations', { method: 'POST', body: payload })
}

/** API-18: GET /operations — журнал операций (фильтры field_id, date_from, date_to). */
export function getOperations(params: {
  field_id?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}): Promise<{ items: FieldOperation[]; total: number; limit: number; offset: number }> {
  return apiClient.list<FieldOperation>('/operations', params)
}

/** API-46: POST /auth/login — аутентификация ({login, password} → токен и пользователь). */
export function login(loginName: string, password: string): Promise<AuthResponse> {
  return apiClient.request<AuthResponse>('/auth/login', {
    method: 'POST',
    body: { login: loginName, password },
  })
}

/** API-47: GET /auth/me — текущий пользователь по JWT. */
export function getAuthMe(): Promise<AuthMe> {
  return apiClient.request<AuthMe>('/auth/me')
}

/* ─── Экран E-03 «Поле 360°» (docs/07-ux §3.2) ─── */

// Бэкенд ограничивает limit сотней (query.limit ≤ 100) — больше запросить нельзя.
const CYCLE_LIST_LIMIT = 100

function toFieldCycle(c: RotationCycleItem): FieldCycle {
  return {
    cycle_id: c.cycle_id,
    year: c.year,
    culture_name: c.culture_name,
    kind: c.kind,
    status: c.status,
    yield_c_ha: null, // фактическая урожайность — вне MVP-объёма API-05
  }
}

/**
 * API-05 + композиция: поле целиком из GET /fields/{id}, история культур —
 * из items планов севооборота поля (API-08), планы внесения — из API-15.
 */
export async function getFieldCard(fieldId: string): Promise<FieldCard> {
  const [detail, plans, treatments] = await Promise.all([
    apiClient.request<Api05FieldDetail>(`/fields/${fieldId}`),
    apiClient.list<RotationPlanDetail>('/rotation-plans', { limit: CYCLE_LIST_LIMIT }),
    apiClient.list<TreatmentPlan>('/treatment-plans', { limit: CYCLE_LIST_LIMIT }),
  ])
  const cycles = plans.items
    .filter((p) => p.field_id === fieldId)
    .flatMap((p) => p.items)
    .sort((a, b) => b.year - a.year)
    .map(toFieldCycle)
  const treatmentPlans: FieldCardPlan[] = treatments.items
    .filter((t) => t.field_id === fieldId)
    .map((t) => ({
      plan_id: t.plan_id,
      input_name: t.input_name,
      dose: t.dose,
      unit: t.unit,
      planned_date: t.planned_date,
      status: t.status,
    }))
  return {
    field: {
      field_id: detail.field_id,
      name: detail.name,
      contour: detail.contour,
      area_ga: detail.area_ga,
      status: detail.status,
      current_cycle: detail.current_cycle ?? null,
    },
    cycles,
    // Агрохимия и сводная проверка предшественника — вне MVP-объёма API-05.
    agrochem: null,
    rotation_check: null,
    treatment_plans: treatmentPlans,
  }
}

/** API-06: GET /fields/{id}/ndvi — NDVI-таймлайн сезона (cloud_pct > 80 — облачные). */
export async function getFieldNdvi(fieldId: string): Promise<NdviPoint[]> {
  // limit=200: годовая история (~70 сцен на поле за год при revisit ~5 сут);
  // порядок нормализуем по возрастанию даты (API отдаёт от свежих к старым).
  const page = await apiClient.request<NdviTimelinePage>(
    `/fields/${fieldId}/ndvi?limit=200`,
  )
  return page.items
    .map((s: SceneItem) => ({
      date: s.captured_at,
      ndvi: s.index_avg,
      cloud_pct: s.cloud_pct,
      index_min: s.index_min,
      index_max: s.index_max,
      trust: s.trust,
      render_available: s.render_available ?? false,
    }))
    .sort((a, b) => a.date.localeCompare(b.date))
}

/** §7.3: загруженный PNG-рендер сцены (ndvi/truecolor) с геопривязкой окна. */
export interface NdviRender {
  url: string
  /** Границы окна растра: minLon, minLat, maxLon, maxLat (WGS84). */
  bbox: [number, number, number, number]
  width: number
  height: number
}

/** §7.3: GET /fields/{id}/ndvi/{date}/render.png?variant= — PNG + заголовки
 *  X-Field-Bbox/X-Image-Size; null, если рендера нет (404). */
export async function getNdviRender(
  fieldId: string,
  date: string,
  variant: 'ndvi' | 'truecolor',
): Promise<NdviRender | null> {
  const res = await apiClient.raw(
    `/fields/${fieldId}/ndvi/${date}/render.png?variant=${variant}`,
  )
  if (!res.ok) return null
  const bboxRaw = (res.headers.get('X-Field-Bbox') ?? '').split(',').map(Number)
  if (bboxRaw.length !== 4 || bboxRaw.some((v) => !Number.isFinite(v))) return null
  const size = (res.headers.get('X-Image-Size') ?? '0x0').split('x').map(Number)
  if (!size[0] || !size[1]) return null
  return {
    url: URL.createObjectURL(res.blob),
    bbox: bboxRaw as [number, number, number, number],
    width: size[0],
    height: size[1],
  }
}

/** API-52: GET /fields/{id}/vra — зоны дифференцированного внесения (§7.4).
 *  Результат: ok — зоны; uniform — поле однородно (422 vra_uniform_field,
 *  IQR < 0.12); empty — прочие ошибки (404 нет сцен, сеть). */
export async function getFieldVra(
  fieldId: string,
): Promise<{ kind: 'ok'; data: VraZonesResponse } | { kind: 'uniform' } | { kind: 'empty' }> {
  try {
    const data = await apiClient.request<VraZonesResponse>(`/fields/${fieldId}/vra`)
    return { kind: 'ok', data }
  } catch (error) {
    if (
      error instanceof ApiError &&
      error.status === 422 &&
      error.problem?.code === 'vra_uniform_field'
    ) {
      return { kind: 'uniform' }
    }
    return { kind: 'empty' }
  }
}

/** API-52: GET /fields/{id}/vra/map.png — PNG-карта зон (§7.4) с
 *  геопривязкой X-Field-Bbox/X-Image-Size (как §7.3); null, если нет. */
export async function getFieldVraMap(fieldId: string): Promise<NdviRender | null> {
  const res = await apiClient.raw(`/fields/${fieldId}/vra/map.png`)
  if (!res.ok) return null
  const bboxRaw = (res.headers.get('X-Field-Bbox') ?? '').split(',').map(Number)
  if (bboxRaw.length !== 4 || bboxRaw.some((v) => !Number.isFinite(v))) return null
  const size = (res.headers.get('X-Image-Size') ?? '0x0').split('x').map(Number)
  if (!size[0] || !size[1]) return null
  return {
    url: URL.createObjectURL(res.blob),
    bbox: bboxRaw as [number, number, number, number],
    width: size[0],
    height: size[1],
  }
}

/** API-53: GET /fields/{id}/yield-forecast — прогноз урожайности (§7.4).
 *  Результат: данные; 'insufficient' — 422 (нет сцен сезона/культуры/
 *  модели) → «данных сезона недостаточно»; null — прочие ошибки. */
export async function getYieldForecast(
  fieldId: string,
): Promise<YieldForecastResponse | 'insufficient' | null> {
  try {
    return await apiClient.request<YieldForecastResponse>(`/fields/${fieldId}/yield-forecast`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) return 'insufficient'
    return null
  }
}

/** API-49: GET /fields/{id}/crop-cycles — история севооборота поля (годы по
 *  убыванию, culture_name и color_hex отдаёт бэкенд из справочника API-48);
 *  year_from/year_to — необязательный фильтр, без них — все годы. */
export async function getFieldCropCycles(
  fieldId: string,
  params: { year_from?: number; year_to?: number } = {},
): Promise<CropCycleHistoryItem[]> {
  const page = await apiClient.list<CropCycleHistoryItem>(
    `/fields/${fieldId}/crop-cycles`,
    params,
  )
  return page.items
}

/** API-07: GET /fields/{id}/weather — факты и прогноз; «сейчас» — последний
 *  факт, сводка (осадки 7 сут, GDD) считается на клиенте по фактам. */
export async function getFieldWeather(fieldId: string): Promise<FieldWeather | null> {
  const detail = await apiClient.request<FieldWeatherDetail>(`/fields/${fieldId}/weather`)
  const facts = [...detail.facts].sort((a, b) => a.observed_at.localeCompare(b.observed_at))
  const last = facts.length ? facts[facts.length - 1] : undefined
  if (!last) return null
  const week = facts.slice(-7)
  const precip7 = Math.round(week.reduce((s, f) => s + f.precipitation_mm, 0) * 10) / 10
  // GDD: сумма эффективных температур, база 5 °C (t_avg − 5, отрицательные — 0).
  const gdd = Math.round(week.reduce((s, f) => s + Math.max(0, f.temperature_avg_c - 5), 0) * 10) / 10
  return {
    now: {
      date: last.observed_at,
      temp_c: last.temperature_avg_c,
      wind_ms: last.wind_max_ms,
      precip_mm: last.precipitation_mm,
      humidity_pct: last.humidity_avg_pct,
    },
    forecast: detail.forecast.map((d) => ({
      date: d.forecast_for ?? '',
      temp_min_c: d.temperature_min_c,
      temp_max_c: d.temperature_max_c,
      precip_mm: d.precipitation_mm,
      precip_prob_pct: d.precip_prob_pct ?? null,
    })),
    summary: { precip_7d_mm: precip7, gdd },
  }
}

/* ─── Экран E-04 «План севооборота» (docs/07-ux §3.3) ─── */

/** Горизонт матрицы: от текущего года (или самого раннего цикла) вперёд. */
function horizonOf(plans: RotationPlanDetail[]): { from: number; to: number } {
  const nowYear = new Date().getFullYear()
  const years = plans.flatMap((p) => [p.year_from, p.year_to, ...p.items.map((c) => c.year)])
  const from = years.length ? Math.min(nowYear, ...years) : nowYear
  const to = years.length ? Math.max(nowYear, ...years) : nowYear + 4
  return { from, to: Math.max(to, from + 4) }
}

/**
 * API-08 + API-01 → экранная модель матрицы E-04: строки — все действующие
 * поля, клетки — циклы из планов. Справочник культур выводится из циклов
 * (отдельного эндпоинта ref_culture в API нет — открытое расширение контракта).
 */
export async function getRotationPlan(): Promise<RotationPlan> {
  const [plansPage, fieldsPage] = await Promise.all([
    apiClient.list<RotationPlanDetail>('/rotation-plans', { limit: CYCLE_LIST_LIMIT }),
    apiClient.list<Field>('/fields', { limit: CYCLE_LIST_LIMIT, status: 'active' }),
  ])
  const plans = plansPage.items
  const { from, to } = horizonOf(plans)

  const cells: RotationCell[] = []
  for (const f of fieldsPage.items) {
    for (let year = from; year <= to; year++) {
      const cycle = plans
        .find((p) => p.field_id === f.field_id)
        ?.items.find((c) => c.year === year)
      cells.push({
        field_id: f.field_id,
        field_name: f.name,
        area_ga: f.area_ga,
        year,
        cycle: cycle
          ? {
              cycle_id: cycle.cycle_id,
              culture_id: cycle.culture_id,
              culture_name: cycle.culture_name,
              kind: cycle.kind,
            }
          : null,
      })
    }
  }

  // Культуры — уникальные из циклов (culture_id уникален глобально).
  const cultureMap = new Map<string, RotationPlan['cultures'][number]>()
  for (const c of plans.flatMap((p) => p.items)) {
    if (!cultureMap.has(c.culture_id)) {
      cultureMap.set(c.culture_id, {
        culture_id: c.culture_id,
        name: c.culture_name,
        kind: c.kind,
        repeat_years: 0, // нормы возврата (RULE-AGRO-02) в API не отдаются
      })
    }
  }

  const drafts = plans.filter((p) => p.status === 'draft')
  const approved = plans.find((p) => p.status === 'approved')
  return {
    plan_id: (drafts[0] ?? plans[0])?.plan_id ?? '',
    draft_plan_ids: drafts.map((p) => p.plan_id),
    farm_name: '',
    year_from: from,
    year_to: to,
    status: drafts.length ? 'draft' : (approved?.status ?? 'draft'),
    approved_by: approved?.approved_by ?? null,
    approved_at: approved?.approved_at ?? null,
    cultures: [...cultureMap.values()].sort((a, b) => a.name.localeCompare(b.name, 'ru')),
    cells,
    violations: [], // GET API-08 не пересчитывает нарушения (см. types.ts)
  }
}

/** API-10: POST /rotation-plans/{id}/approve — согласование руководителем. */
export async function approveRotationPlan(
  planId: string,
): Promise<{ plan_id: string; status: RotationPlan['status'] }> {
  const detail = await apiClient.request<RotationPlanDetail>(
    `/rotation-plans/${planId}/approve`,
    { method: 'POST' },
  )
  return { plan_id: detail.plan_id, status: detail.status }
}

/** API-11: POST /crop-cycles — добавить/заменить цикл в клетке матрицы;
 *  422 с problem+json (rule_id) при запретном предшественнике RULE-AGRO-01. */
export async function createCropCycle(payload: CropCycleRequest): Promise<CropCycle> {
  const detail = await apiClient.request<CropCycleDetail>('/crop-cycles', {
    method: 'POST',
    body: payload,
  })
  return {
    cycle_id: detail.cycle_id,
    field_id: detail.field_id,
    year: detail.year,
    culture_id: detail.culture_id,
    culture_name: detail.culture_name,
    kind: detail.kind,
    planned_yield_c_ha: detail.planned_yield_c_ha,
    planned_harvest_date: detail.planned_harvest_date,
    status: detail.status,
  }
}

/* ─── Экран E-05 «Планы удобрений и СЗР» (docs/07-ux §2) ─── */

/** API-14: GET /agro-inputs — справочник препаратов и удобрений. */
export function getAgroInputs(params: { kind?: 'fertilizer' | 'ppp'; q?: string } = {}): Promise<{
  items: AgroInput[]
  total: number
}> {
  return apiClient.list<AgroInput>('/agro-inputs', { kind: params.kind, q: params.q })
}

/** API-15: GET /treatment-plans — планы внесения (фильтры status, cycle_id,
 *  field_id — поддерживаются backend, docs/06 §5). */
export function getTreatmentPlans(params: {
  status?: TreatmentPlanStatus
  field_id?: string
  cycle_id?: string
} = {}): Promise<{
  items: TreatmentPlan[]
  total: number
}> {
  return apiClient.list<TreatmentPlan>('/treatment-plans', {
    status: params.status,
    field_id: params.field_id,
    cycle_id: params.cycle_id,
  })
}

/** API-15: POST /treatment-plans — создать план внесения; 422 с problem+json
 *  при нарушении RULE-AGRO-07 (доза) / 08 (срок ожидания) / 10 (культуры, лимит).
 *  Для operation_type=spraying ответ дополняется weather_window (§7.2). */
export function createTreatmentPlan(payload: TreatmentPlanRequest): Promise<TreatmentPlan> {
  return apiClient.request<TreatmentPlan>('/treatment-plans', { method: 'POST', body: payload })
}

/** API-16: POST /treatment-plans/{id}/approve — согласование руководителем;
 *  для spraying ответ содержит weather_window (§7.2). */
export function approveTreatmentPlan(planId: string): Promise<TreatmentPlan> {
  return apiClient.request<TreatmentPlan>(`/treatment-plans/${planId}/approve`, { method: 'POST' })
}

/* ─── Экран E-10 «Погода и погодные окна» (docs/07-ux, SC-03) ─── */

/** API-29: GET /weather/forecast?field_id&days — прогноз на N суток (кэш 3 ч). */
export async function getWeatherForecast(fieldId: string, days = 7): Promise<WeatherForecast> {
  const page = await apiClient.request<ForecastPage>('/weather/forecast', {
    query: { field_id: fieldId, days },
  })
  const dayList: WeatherForecastDay[] = page.items.map((d) => ({
    date: d.forecast_for ?? '',
    temp_min_c: d.temperature_min_c,
    temp_max_c: d.temperature_max_c,
    precip_mm: d.precipitation_mm,
    precip_prob_pct: d.precip_prob_pct ?? undefined,
    wind_ms: d.wind_max_ms,
    humidity_pct: d.humidity_avg_pct,
  }))
  return { field_id: fieldId, days: dayList, stale: page.meta?.stale ?? false }
}

/** GET /weather/windows?field_id&operation_type — оценка погодного окна по
 *  RULE-AGRO-09 (03 §3.3): ближайшие подходящие сутки с обоснованием. */
export async function getWeatherWindows(
  fieldId: string,
  operationType: WindowOperationType,
): Promise<WeatherWindows> {
  const raw = await apiClient.request<WindowsResponse>('/weather/windows', {
    query: { field_id: fieldId, operation_type: operationType },
  })
  const days: WeatherWindowDay[] = raw.days.map((d) => ({
    date: d.day,
    is_window: d.ok,
    summary: d.ok ? 'Все условия выполнены' : d.reasons.join('; '),
  }))
  const nearest = days.find((d) => d.is_window)
  return {
    field_id: raw.field_id,
    operation_type: operationType,
    days,
    nearest_window_date: nearest ? nearest.date : null,
    rule_text: raw.rule_text,
    window_open: raw.window_open,
    alert: raw.alert,
  }
}

/* ─── Экран E-14 «Источники данных» (API-28) ─── */

const ADAPTER_STATES: AdapterState[] = ['ok', 'degraded', 'error']

/** API-28: GET /sources/status — журнал адаптеров (доступ: manager/admin). */
export async function getSourcesStatus(): Promise<{ items: AdapterStatus[] }> {
  const page = await apiClient.list<SourceStatusItem>('/sources/status')
  const items = page.items.map((s) => {
    const status = ADAPTER_STATES.includes(s.last_status as AdapterState)
      ? (s.last_status as AdapterState)
      : null
    return {
      source_id: s.source_id,
      kind: s.kind,
      title: s.source_id,
      last_status: status,
      last_success_at: s.last_success_at,
      reason: s.last_event?.reason ?? null,
      events: s.last_event
        ? [{ at: s.last_event.at, status: s.last_event.status as AdapterState, reason: s.last_event.reason }]
        : [],
    }
  })
  return { items }
}

/* ─── Экран E-18 «Техника и задания» (API-19/20) ─── */

/** API-19: GET /machines — парк техники (read-only в MVP). */
export function getMachines(): Promise<{ items: Machine[] }> {
  return apiClient.list<Machine>('/machines')
}

/** GET /machine-jobs — задания смены с именем машины (read-only + confirm). */
export function getMachineJobs(params: { status?: MachineJobStatus } = {}): Promise<{
  items: MachineJob[]
  total: number
}> {
  return apiClient.list<MachineJob>('/machine-jobs', { status: params.status })
}

/** API-20: POST /machine-jobs/{id}/confirm — подтверждение черновика из телематики. */
export function confirmMachineJob(jobId: string): Promise<MachineJob> {
  return apiClient.request<MachineJob>(`/machine-jobs/${jobId}/confirm`, { method: 'POST' })
}
