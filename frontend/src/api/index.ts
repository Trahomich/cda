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
  Page,
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
  FieldAdvice,
  FieldOperation,
  FieldStatus,
  FieldWeather,
  FieldWeatherDetail,
  ForecastPage,
  InsightsResponse,
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
  SeasonItem,
  SeasonsResponse,
  SourceStatusItem,
  TreatmentPlan,
  TreatmentPlanRequest,
  TreatmentPlanStatus,
  VraZonesResponse,
  GddResponse,
  WeatherForecast,
  WeatherForecastDay,
  WeatherGdd,
  WeatherHistory,
  WeatherHistoryItem,
  WeatherWindowDay,
  WeatherWindowItem,
  WeatherWindows,
  WeatherWindowsPlanned,
  WindowOperationType,
  WindowsPlannedResponse,
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

/** API-06: GET /fields/{id}/ndvi — NDVI-таймлайн (cloud_pct > 80 — облачные).
 *  year (§7.5) — только сцены сезона YYYY; без него — вся история. */
export async function getFieldNdvi(fieldId: string, year?: number): Promise<NdviPoint[]> {
  // limit=200: годовая история (~70 сцен на поле за год при revisit ~5 сут);
  // порядок нормализуем по возрастанию даты (API отдаёт от свежих к старым).
  const yearQuery = year ? `&year=${year}` : ''
  const page = await apiClient.request<NdviTimelinePage>(
    `/fields/${fieldId}/ndvi?limit=200${yearQuery}`,
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
      waterlogged: s.waterlogged ?? null,
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

/** §7.3/§7.4: GET /fields/{id}/ndvi/{date}/render.png?variant= — PNG + заголовки
 *  X-Field-Bbox/X-Image-Size; null, если рендера нет (404). */
export async function getNdviRender(
  fieldId: string,
  date: string,
  variant: 'ndvi' | 'truecolor' | 'ndwi',
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

/** API-52: GET /fields/{id}/vra — зоны дифференцированного внесения (§7.4
 *  v1.7: зоны строятся ВСЕГДА при наличии чистых сцен сезона, код
 *  422 vra_uniform_field больше не возвращается). year (§7.5) — сезон YYYY;
 *  404 season_not_found и 422 vra_no_season_data → 'empty' («нет данных»). */
export async function getFieldVra(
  fieldId: string,
  year?: number,
): Promise<{ kind: 'ok'; data: VraZonesResponse } | { kind: 'empty' }> {
  const yearQuery = year ? `?year=${year}` : ''
  try {
    return {
      kind: 'ok',
      data: await apiClient.request<VraZonesResponse>(`/fields/${fieldId}/vra${yearQuery}`),
    }
  } catch {
    return { kind: 'empty' }
  }
}

/** API-60 (§7.4, #140): файл выгрузки карты зон — blob + имя из
 *  Content-Disposition (ISOXML/SHP — ZIP, GeoJSON — .geojson). */
export interface VraExportFile {
  blob: Blob
  filename: string
}

/** API-60: GET /fields/{id}/vra/export?format=isoxml|shp|geojson — выгрузка
 *  карты дифвнесения (§7.4). baseDoseKgHa — базовая доза, кг/га (иначе
 *  бэкенд берёт последний approved fertilizing-план); year — сезон (§7.5).
 *  'no-dose' — 422 vra_no_base_dose (нет базовой дозы: нужна из формы),
 *  'error' — прочие ошибки (нет сцен, неизвестный формат и т.п.). */
export async function exportFieldVra(
  fieldId: string,
  format: 'isoxml' | 'shp' | 'geojson',
  opts: { baseDoseKgHa?: number; year?: number } = {},
): Promise<
  { kind: 'file'; file: VraExportFile } | { kind: 'no-dose' } | { kind: 'error' }
> {
  const params = new URLSearchParams({ format })
  if (opts.baseDoseKgHa != null) params.set('base_dose_kg_ha', String(opts.baseDoseKgHa))
  if (opts.year != null) params.set('year', String(opts.year))
  const res = await apiClient.raw(`/fields/${fieldId}/vra/export?${params.toString()}`)
  if (res.ok) {
    const cd = res.headers.get('Content-Disposition') ?? ''
    const filename =
      /filename="([^"]+)"/.exec(cd)?.[1] ??
      `vra_${fieldId.slice(0, 8)}.${format === 'geojson' ? 'geojson' : 'zip'}`
    return { kind: 'file', file: { blob: res.blob, filename } }
  }
  if (res.status === 422) {
    // различаем vra_no_base_dose от прочих 422 (например, битая доза)
    try {
      const problem = JSON.parse(await res.blob.text()) as { code?: string }
      if (problem.code === 'vra_no_base_dose') return { kind: 'no-dose' }
    } catch {
      // тело не JSON — считаем общей ошибкой
    }
  }
  return { kind: 'error' }
}

/** API-52: GET /fields/{id}/vra/map.png — PNG-карта зон (§7.4) с
 *  геопривязкой X-Field-Bbox/X-Image-Size (как §7.3); null, если нет. */
export async function getFieldVraMap(fieldId: string, year?: number): Promise<NdviRender | null> {
  const yearQuery = year ? `?year=${year}` : ''
  const res = await apiClient.raw(`/fields/${fieldId}/vra/map.png${yearQuery}`)
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
 *  year (§7.5) — цикл года YYYY; нет цикла — 422 yield_no_culture,
 *  нет сцен — 422 yield_no_season_data (оба → 'insufficient' — «данных
 *  сезона недостаточно»); null — прочие ошибки. */
export async function getYieldForecast(
  fieldId: string,
  year?: number,
): Promise<YieldForecastResponse | 'insufficient' | null> {
  const yearQuery = year ? `?year=${year}` : ''
  try {
    return await apiClient.request<YieldForecastResponse>(
      `/fields/${fieldId}/yield-forecast${yearQuery}`,
    )
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) return 'insufficient'
    return null
  }
}

/** API-51: GET /fields/{id}/insights — «Выводы ассистента» (§7.4):
 *  items — числовые детекторы (всегда), summary — LLM-сводка
 *  (null при source=rules); year (§7.5) — детекторы и сводка по сезону
 *  YYYY; null — поле не найдено или прочая ошибка (блок в карточке
 *  скрывается, кэш на клиенте не делаем). */
export async function getInsights(
  fieldId: string,
  year?: number,
): Promise<InsightsResponse | null> {
  const yearQuery = year ? `?year=${year}` : ''
  try {
    return await apiClient.request<InsightsResponse>(`/fields/${fieldId}/insights${yearQuery}`)
  } catch {
    return null
  }
}

/** API-54: GET /fields/{id}/seasons — сезоны NDVI поля (§7.5): годы по
 *  убыванию, is_current у текущего; переключатель сезонов в паспорте поля. */
export async function getFieldSeasons(fieldId: string): Promise<SeasonItem[]> {
  const data = await apiClient.request<SeasonsResponse>(`/fields/${fieldId}/seasons`)
  return data.items
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
    hazards: d.hazards ?? [], // ОЯ суток (#139, docs/06 §7.8)
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
  return windowsFromRaw(raw, operationType)
}

/** GET /weather/windows?field_id&only_planned=true — окна только по типам
 *  операций из активных планов поля (#133, §7.8 API-59); без планов —
 *  items=[] и hint «нет активных планов». Silent: до деплоя #133 бэкенд
 *  может не знать списочной формы — экран деградирует нейтральным «скоро»
 *  без тоста об ошибке (#416). */
export async function getWeatherWindowsPlanned(
  fieldId: string,
): Promise<WeatherWindowsPlanned> {
  const raw = await apiClient.request<WindowsPlannedResponse>('/weather/windows', {
    query: { field_id: fieldId, only_planned: 'true' },
    silent: true,
  })
  const items: WeatherWindowItem[] = raw.items.map((it) => ({
    operation_type: it.operation_type as WindowOperationType,
    rule_text: it.rule_text,
    window_open: it.window_open,
    nearest_window_date: it.nearest_window_date
      ?? it.days.find((d) => d.ok)?.day
      ?? null,
    days: it.days.map((d) => ({
      date: d.day,
      is_window: d.ok,
      summary: d.ok ? 'Все условия выполнены' : d.reasons.join('; '),
    })),
    alert: it.alert,
  }))
  return { field_id: raw.field_id, only_planned: true, items, hint: raw.hint ?? null }
}

/** API-57: GET /weather/history?field_id&date_from&date_to — дневные факты
 *  погоды за период (§7.8; полные имена полей как в API-07, дата — observed_at;
 *  limit до 1500 закрывает несколько сезонов одним запросом). */
export async function getWeatherHistory(
  fieldId: string,
  dateFrom: string,
  dateTo: string,
): Promise<WeatherHistory> {
  const page = await apiClient.request<Page<WeatherHistoryItem>>('/weather/history', {
    query: { field_id: fieldId, date_from: dateFrom, date_to: dateTo, limit: 1500 },
  })
  return {
    field_id: fieldId,
    items: page.items.map((d) => ({
      date: d.observed_at.slice(0, 10),
      temp_max_c: d.temperature_max_c,
      temp_min_c: d.temperature_min_c,
      precip_mm: d.precipitation_mm,
      cloud_pct: d.cloud_cover_mean_pct ?? null,
      snow_cm: d.snowfall_cm ?? null,
    })),
    total: page.total,
  }
}

/** GET /weather/gdd?field_id&year — накопительная кривая эффективных
 *  температур с 1 января года (base 5 °C) и норма при ≥ 2 годах истории (#132). */
export async function getWeatherGdd(fieldId: string, year: number): Promise<WeatherGdd> {
  const raw = await apiClient.request<GddResponse>('/weather/gdd', {
    query: { field_id: fieldId, year },
  })
  return {
    field_id: raw.field_id,
    year: raw.year,
    base_c: raw.base_c,
    total: raw.total,
    items: raw.items,
    norm_cum: raw.norm_cum ?? null,
  }
}

/** Сырой ответ /weather/windows (сутки с причинами) → форма E-10.
 *  nearest_window_date приходит с сервера (§7.8); на всякий случай — фолбэк. */
function windowsFromRaw(raw: WindowsResponse, operationType: WindowOperationType): WeatherWindows {
  const days: WeatherWindowDay[] = raw.days.map((d) => ({
    date: d.day,
    is_window: d.ok,
    summary: d.ok ? 'Все условия выполнены' : d.reasons.join('; '),
  }))
  const nearest = raw.nearest_window_date ?? days.find((d) => d.is_window)?.date ?? null
  return {
    field_id: raw.field_id,
    operation_type: operationType,
    days,
    nearest_window_date: nearest,
    rule_text: raw.rule_text,
    window_open: raw.window_open,
    alert: raw.alert,
  }
}

/* ─── AI-рекомендации «что делать сейчас» (API-55/56, docs/09 §9.3, #137) ─── */

/** API-55: GET /fields/{id}/ai/advice — последняя рекомендация или null
 *  (404 advice_not_found — нормальное пустое состояние, без тоста). */
export async function getFieldAdvice(fieldId: string): Promise<FieldAdvice | null> {
  try {
    return await apiClient.request<FieldAdvice>(`/fields/${fieldId}/ai/advice`, { silent: true })
  } catch {
    return null
  }
}

/** POST /fields/{id}/ai/advice — принудительный пересчёт: 202 queued;
 *  результат — поллингом того же GET (§9.3). */
export async function refreshFieldAdvice(fieldId: string): Promise<boolean> {
  const res = await apiClient.request<{ status: string }>(`/fields/${fieldId}/ai/advice`, {
    method: 'POST',
  })
  return res.status === 'queued'
}

/** API-56: GET /fields/{id}/ai/advice/history?limit&offset — история
 *  рекомендаций по убыванию created_at (только items). */
export async function getFieldAdviceHistory(
  fieldId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<FieldAdviceHistory> {
  return apiClient.request<FieldAdviceHistory>(`/fields/${fieldId}/ai/advice/history`, {
    query: { limit: params.limit ?? 20, offset: params.offset ?? 0 },
    silent: true,
  })
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
