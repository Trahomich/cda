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
