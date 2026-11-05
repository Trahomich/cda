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

/** Роли пользователей (docs/01-requirements.md §2, docs/07-ux.md §5). */
export type UserRole = 'agronom' | 'manager' | 'operator' | 'livestock' | 'admin'

/** Ответ POST /auth/login (API-46, docs/06 §5). */
export interface AuthResponse {
  token: string
  user: {
    name: string
    role: UserRole
    farm_name: string
  }
}

/** Ответ GET /auth/me (API-47). */
export interface AuthMe {
  id: string
  name: string
  role: UserRole
}
