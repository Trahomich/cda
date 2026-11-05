// Доменные вызовы API (только эндпоинты из docs/06-data-api.md §5).
import { apiClient } from './client'
import type {
  AgroInput,
  AuthMe,
  AuthResponse,
  CropCycle,
  CropCycleRequest,
  Field,
  FieldCard,
  FieldOperation,
  FieldStatus,
  FieldWeather,
  NdviPoint,
  OperationRequest,
  RotationPlan,
  TreatmentPlan,
  TreatmentPlanRequest,
  TreatmentPlanStatus,
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
    status: params.status,
    with_geometry: params.with_geometry === true ? 'true' : undefined,
    limit: params.limit,
    offset: params.offset,
  })
}

/** API-17: POST /operations — запись факта операции (мобильный, офлайн-очередь).
 *  Идемпотентность по client_id: повторная отправка не создаёт дубль. */
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
export function login(login: string, password: string): Promise<AuthResponse> {
  return apiClient.request<AuthResponse>('/auth/login', {
    method: 'POST',
    body: { login, password },
  })
}

/** API-47: GET /auth/me — текущий пользователь по JWT. */
export function getAuthMe(): Promise<AuthMe> {
  return apiClient.request<AuthMe>('/auth/me')
}

/* ─── Экран E-03 «Поле 360°» (docs/07-ux §3.2) ─── */

/** API-05: GET /fields/{id} — карточка «Поле 360°»: агрегат поля. */
export function getFieldCard(fieldId: string): Promise<FieldCard> {
  return apiClient.request<FieldCard>(`/fields/${fieldId}`)
}

/** API-06: GET /fields/{id}/ndvi — NDVI-таймлайн сезона (cloud_pct > 80 — облачные). */
export function getFieldNdvi(fieldId: string): Promise<NdviPoint[]> {
  return apiClient.request<NdviPoint[]>(`/fields/${fieldId}/ndvi`)
}

/** API-07: GET /fields/{id}/weather — погода сейчас + прогноз 7 сут. */
export function getFieldWeather(fieldId: string): Promise<FieldWeather> {
  return apiClient.request<FieldWeather>(`/fields/${fieldId}/weather`)
}

/* ─── Экран E-04 «План севооборота» (docs/07-ux §3.3) ─── */

/** API-08: GET /rotation-plans — план севооборота (матрица поле × год,
 *  справочник культур и пересчитанные нарушения RULE-AGRO-01/02). */
export function getRotationPlan(): Promise<RotationPlan> {
  return apiClient.request<RotationPlan>('/rotation-plans')
}

/** API-10: POST /rotation-plans/{id}/approve — согласование руководителем. */
export function approveRotationPlan(planId: string): Promise<{ plan_id: string; status: RotationPlan['status'] }> {
  return apiClient.request(`/rotation-plans/${planId}/approve`, { method: 'POST' })
}

/** API-11: POST /crop-cycles — добавить/заменить цикл в клетке матрицы;
 *  422 с problem+json (rule_id) при запретном предшественнике RULE-AGRO-01. */
export function createCropCycle(payload: CropCycleRequest): Promise<CropCycle> {
  return apiClient.request<CropCycle>('/crop-cycles', { method: 'POST', body: payload })
}

/* ─── Экран E-05 «Планы удобрений и СЗР» (docs/07-ux §2) ─── */

/** API-14: GET /agro-inputs — справочник препаратов и удобрений. */
export function getAgroInputs(params: { kind?: 'fertilizer' | 'ppp'; q?: string } = {}): Promise<{
  items: AgroInput[]
  total: number
}> {
  return apiClient.list<AgroInput>('/agro-inputs', { kind: params.kind, q: params.q })
}

/** API-15: GET /treatment-plans — планы внесения (фильтр status). */
export function getTreatmentPlans(params: { status?: TreatmentPlanStatus } = {}): Promise<{
  items: TreatmentPlan[]
  total: number
}> {
  return apiClient.list<TreatmentPlan>('/treatment-plans', { status: params.status })
}

/** API-15: POST /treatment-plans — создать план внесения; 422 с problem+json
 *  при нарушении RULE-AGRO-07 (доза) / 08 (срок ожидания) / 10 (культуры, лимит). */
export function createTreatmentPlan(payload: TreatmentPlanRequest): Promise<TreatmentPlan> {
  return apiClient.request<TreatmentPlan>('/treatment-plans', { method: 'POST', body: payload })
}

/** API-16: POST /treatment-plans/{id}/approve — согласование руководителем. */
export function approveTreatmentPlan(planId: string): Promise<TreatmentPlan> {
  return apiClient.request<TreatmentPlan>(`/treatment-plans/${planId}/approve`, { method: 'POST' })
}
