// IndexedDB через Dexie: таблицы офлайн-режима по docs/10-mobile-pwa.md §2.
// Выбор: Dexie (а не idb-keyval), потому что очереди нужны индексы
// (статус, дата создания), запросы «все pending», транзакции и правка
// отдельных записей; idb-keyval — только ключ-значение.
import Dexie, { type Table } from 'dexie'
import type { Field, OperationRequest } from '@/api/types'

/** Запись офлайн-очереди (10-mobile-pwa §3: entity, payload, created_at, retry_count). */
export interface QueueRecord {
  /** Ключ идемпотентности POST /operations (генерируется при создании). */
  client_id: string
  entity: 'operation' | 'machine_job'
  /** Тело запроса по формату API (06 §5). */
  payload: OperationRequest
  created_at: string
  retry_count: number
  /** pending — ждёт сети; sending — отправляется; error — отклонена (422). */
  status: 'pending' | 'sending' | 'error'
  /** Причина из problem+json для status=error. */
  error_detail?: string
}

/** Кэш карточек полей «перед выездом» (10-mobile-pwa §3.1) — поля плоско. */
export type CachedField = Field & { cached_at: string }

export class CdaDb extends Dexie {
  queue!: Table<QueueRecord, string>
  fields!: Table<CachedField, string>

  constructor() {
    super('cda-offline')
    this.version(1).stores({
      queue: 'client_id, status, created_at, entity',
      fields: 'field_id, status',
    })
  }
}

export const db = new CdaDb()

/** UUID v4 — ключ идемпотентности записи очереди. */
export function uuidV4(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  // Запасной вариант для сред без crypto.randomUUID.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}
