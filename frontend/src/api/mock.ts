// Мок-транспорт для dev-режима: демо-хозяйство по ADR-07 (5–10 полей со
// синтетическими контурами, культуры средней полосы). Реализует только
// эндпоинты, нужные каркасу: POST /auth (допущение, см. types.ts) и
// GET /fields (API-01) с фильтрами status, q и пагинацией limit/offset.
// Ошибки отдаются в формате RFC 7807 (CONTRACTS §8).
import type { Transport, TransportResponse } from './transport'
import type { Field, FieldStatus, Page } from './types'

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
    name: `Степное-${n}`,
    contour: { type: 'Polygon', coordinates: [rectContour(lon, lat, wDeg, hDeg)] },
    area_ga: areaHa,
    status,
    current_cycle: cycle,
  }
}

// Демо-хозяйство КФХ «Восход»: окрестности н.п. Степное (~48.73 с.ш., 41.98 в.д.).
const FIELDS: MockField[] = [  makeField(1, 41.9812, 48.7261, 0.0098, 0.0051, 84.5, 'active', {
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

function problem(status: number, type: string, title: string, detail: string): TransportResponse {
  return {
    status,
    ok: false,
    json: null,
    problem: { type, title, status, detail },
  }
}

function ok(json: unknown, status = 200): TransportResponse {
  return { status, ok: true, json, problem: null }
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// Журнал операций (API-17/18). Идемпотентность: повторный POST /operations
// с тем же client_id не создаёт дубль — возвращает уже созданную операцию.
const OPERATIONS: Array<FieldOperation & { created_at: string }> = []

function uuid(n: number): string {
  return `f1e2d3c4-0000-4000-8000-00000000${String(n).padStart(4, '0')}`
}

export class MockTransport implements Transport {
  async request(method: string, path: string, body?: unknown): Promise<TransportResponse> {
    await delay(200) // имитация сети
    const [pathname, search = ''] = path.split('?')
    const params = new URLSearchParams(search)

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
      // Идемпотентность офлайн-очереди: тот же client_id → та же операция (201).
      const existing = OPERATIONS.find((o) => o.client_id === op.client_id)
      if (existing) return ok(existing, 201)

      const field = FIELDS.find((f) => f.field_id === op.field_id)
      if (!field) {
        return problem(
          404,
          'https://cda.example/errors/not-found',
          'Нет ресурса',
          `Поле ${op.field_id} не найдено`,
        )
      }
      // RULE-AGRO-12: факт. площадь ≤ 1,05 × площади поля → 422 (06 §5).
      if ((op.actual_area_ga ?? 0) > 1.05 * field.area_ga) {
        return {
          status: 422,
          ok: false,
          json: null,
          problem: {
            type: 'https://cda.example/errors/area-limit',
            title: 'Нарушено бизнес-правило',
            status: 422,
            detail: `Фактическая площадь ${op.actual_area_ga} га превышает 1,05 × площадь поля ${field.name} (${field.area_ga} га)`,
            rule_id: 'RULE-AGRO-12',
            entity: 'operation',
          },
        }
      }
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

    return problem(
      404,
      'https://cda.example/errors/not-found',
      'Не найдено',
      `Мок не реализует ${method} ${pathname}`,
    )
  }
}
