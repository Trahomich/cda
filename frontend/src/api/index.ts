// Доменные вызовы API (только эндпоинты из docs/06-data-api.md §5).
import { apiClient } from './client'
import type { AuthResponse, Field, FieldStatus } from './types'

/** API-01: GET /fields — список полей (фильтры status, q по имени). */
export function getFields(params: {
  status?: FieldStatus | 'all'
  q?: string
  limit?: number
  offset?: number
}): Promise<{ items: Field[]; total: number; limit: number; offset: number }> {
  return apiClient.list<Field>('/fields', {
    q: params.q,
    status: params.status,
    limit: params.limit,
    offset: params.offset,
  })
}

/** POST /auth — вход (эндпоинт появится в backend; формат ответа — types.ts). */
export function login(login: string, password: string): Promise<AuthResponse> {
  return apiClient.request<AuthResponse>('/auth', { method: 'POST', body: { login, password } })
}
