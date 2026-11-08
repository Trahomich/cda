// Типизированный API-клиент: единый разбор ошибок RFC 7807 (CONTRACTS §8),
// пагинация ?limit&offset (06 §5), мок-ранспорт в dev-режиме.
// 401 (истёкший/невалидный токен, API-46/47) не показывается тостом:
// клиент зовёт onUnauthorized — приложение сбрасывает сессию и ведёт на /login.
import { FetchTransport, type Transport } from './transport'
import { MockTransport } from './mock'
import type { Page, Problem } from './types'

/** Ошибка API: либо RFC 7807 problem+json, либо сетевая/неожиданная. */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly problem: Problem | null,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** Текст для пользователя: detail из problem+json или запасной. */
  get userMessage(): string {
    if (this.problem) {
      const extra: string[] = []
      if (typeof this.problem.rule_id === 'string') extra.push(this.problem.rule_id)
      return extra.length ? `${this.problem.detail} (${extra.join(', ')})` : this.problem.detail
    }
    return this.message
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | undefined | null>
  /** Не показывать тост ошибки: вызывающий код сам обрабатывает ответ
   *  (ожидаемый 422 «поле однородно» — нормальное состояние, не ошибка). */
  silent?: boolean
}

/** Обработчик ошибок (подключается из приложения: тост problem+json). */
type ErrorHandler = (error: ApiError) => void

/** Обработчик 401 — сброс сессии и переход на /login (подключает приложение). */
type UnauthorizedHandler = () => void

const useMock = import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== 'false'

/** Пути аутентификации: их 401 («неверный логин/пароль») — не конец сессии. */
const AUTH_PATHS = ['/auth/login']

export class ApiClient {
  private transport: Transport
  private onError: ErrorHandler | null = null
  private onUnauthorized: UnauthorizedHandler | null = null
  private token: string | null = null
  /** Признак уже сброшенной сессии: глушит повторные 401 до нового логина. */
  private unauthorizedHandled = false

  constructor() {
    const baseUrl = import.meta.env.VITE_API_BASE ?? '/api/v1'
    this.transport = useMock ? new MockTransport() : new FetchTransport(baseUrl, () => this.token)
  }

  setErrorHandler(handler: ErrorHandler): void {
    this.onError = handler
  }

  setUnauthorizedHandler(handler: UnauthorizedHandler): void {
    this.onUnauthorized = handler
  }

  setToken(token: string | null): void {
    this.token = token
    if (token) this.unauthorizedHandled = false // новая сессия — 401 снова значимы
  }

  /** Запрос: бросает ApiError с разобранным problem+json при ошибке. */
  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const method = options.method ?? 'GET'
    let fullPath = path
    if (options.query) {
      const params = new URLSearchParams()
      for (const [key, value] of Object.entries(options.query)) {
        if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
      }
      const qs = params.toString()
      if (qs) fullPath += `?${qs}`
    }

    let response
    try {
      response = await this.transport.request(method, fullPath, options.body)
    } catch {
      const error = new ApiError('Нет связи с сервером. Проверьте подключение к сети.', 0, null)
      if (!options.silent) this.onError?.(error)
      throw error
    }

    if (!response.ok) {
      const problem = response.problem
      const error = new ApiError(
        problem?.detail ?? `Ошибка запроса ${method} ${path} (HTTP ${response.status})`,
        response.status,
        problem,
      )
      // 401 вне /auth/*: токен истёк/невалиден (API-46) — сбрасываем сессию
      // и молча уходим на /login; тост не показываем, повторные 401 глохнем,
      // пока не появится новый токен (баг #59: лавина «сервер недоступен»).
      if (response.status === 401 && !AUTH_PATHS.some((p) => path.startsWith(p))) {
        if (!this.unauthorizedHandled) {
          this.unauthorizedHandled = true
          this.onUnauthorized?.()
        }
        throw error
      }
      this.onError?.(error)
      throw error
    }

    // 201/204 по CONTRACTS §8: POST создаёт (тело может быть), DELETE — 204 без тела.
    if (response.status === 204) return undefined as T
    return response.json as T
  }

  /** GET списочного эндпоинта с пагинацией (обёртка Page<T>). */
  list<T>(
    path: string,
    query: { limit?: number; offset?: number } & Record<string, string | number | undefined> = {},
  ): Promise<Page<T>> {
    return this.request<Page<T>>(path, { query: { limit: 50, ...query } })
  }

  /** Бинарный GET (image/png и пр.): blob + заголовки ответа. */
  raw(path: string): Promise<{ ok: boolean; status: number; blob: Blob; headers: Headers }> {
    return this.transport.raw(path)
  }
}

export const apiClient = new ApiClient()
