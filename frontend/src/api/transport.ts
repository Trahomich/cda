// Транспорт HTTP. Реальный — fetch к /api/v1 (docs/06-data-api.md §5);
// в dev-режиме по умолчанию подключается мок-транспорт (демо-хозяйство,
// ADR-07), чтобы каркас работал до появления backend.
import type { Problem } from './types'

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface TransportResponse {
  status: number
  ok: boolean
  /** Тело ответа (parsed JSON) или null. */
  json: unknown
  /** Тело ошибки, если это application/problem+json. */
  problem: Problem | null
}

export interface Transport {
  request(method: HttpMethod, path: string, body?: unknown): Promise<TransportResponse>
  /** Бинарный GET (image/png и т.п.): blob + избранные заголовки. */
  raw(path: string): Promise<{ ok: boolean; status: number; blob: Blob; headers: Headers }>
}

/** Разбор ошибки RFC 7807 из ответа fetch. */
export function parseProblem(contentType: string | null, text: string): Problem | null {
  if (!contentType || !contentType.includes('application/problem+json')) return null
  try {
    const parsed = JSON.parse(text) as Problem
    if (typeof parsed?.title !== 'string' || typeof parsed?.status !== 'number') return null
    return parsed
  } catch {
    return null
  }
}

/** Реальный транспорт поверх fetch. */
export class FetchTransport implements Transport {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => string | null,
  ) {}

  async request(method: HttpMethod, path: string, body?: unknown): Promise<TransportResponse> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    const token = this.getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    if (body !== undefined) headers['Content-Type'] = 'application/json'

    const res = await fetch(this.baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    let json: unknown = null
    if (text) {
      try {
        json = JSON.parse(text)
      } catch {
        json = null
      }
    }
    return {
      status: res.status,
      ok: res.ok,
      json,
      problem: res.ok ? null : parseProblem(res.headers.get('content-type'), text),
    }
  }

  async raw(path: string): Promise<{ ok: boolean; status: number; blob: Blob; headers: Headers }> {
    const headers: Record<string, string> = { Accept: 'image/png' }
    const token = this.getToken()
    if (token) headers.Authorization = `Bearer ${token}`
    const res = await fetch(this.baseUrl + path, { method: 'GET', headers })
    return { ok: res.ok, status: res.status, blob: await res.blob(), headers: res.headers }
  }
}
