// Сервис офлайн-очереди и синхронизации (10-mobile-pwa §3):
//  - офлайн-ввод кладёт payload в IndexedDB со статусом pending;
//  - синхронизация (авто по 'online' или вручную) отправляет записи по одной
//    через API-17 POST /operations (идемпотентность по client_id);
//  - 422 не удаляет запись: статус error + причина из problem+json;
//  - сетевая ошибка оставляет запись pending.
import { db, uuidV4, type QueueRecord } from './db'
import { createOperation } from '@/api'
import type { OperationRequest } from '@/api/types'
import { ApiError } from '@/api/client'

export type SyncResult = {
  sent: number
  rejected: number
  remaining: number
}

/** Добавить операцию в очередь (оффлайн-ввод). */
export async function enqueueOperation(
  payload: Omit<OperationRequest, 'client_id'>,
): Promise<QueueRecord> {
  const record: QueueRecord = {
    client_id: uuidV4(),
    entity: 'operation',
    payload: { ...payload, client_id: '' } as OperationRequest,
    created_at: new Date().toISOString(),
    retry_count: 0,
    status: 'pending',
  }
  record.payload.client_id = record.client_id
  await db.queue.add(record)
  return record
}

/** Все записи очереди, новые сверху. */
export async function listQueue(): Promise<QueueRecord[]> {
  return db.queue.orderBy('created_at').reverse().toArray()
}

export async function removeFromQueue(clientId: string): Promise<void> {
  await db.queue.delete(clientId)
}

/** Правка payload записи (исправление после 422). */
export async function updateQueuePayload(
  clientId: string,
  payload: OperationRequest,
): Promise<void> {
  await db.queue.update(clientId, {
    payload,
    status: 'pending',
    error_detail: undefined,
  })
}

let syncing = false

/** Синхронизация: отправляет pending и error записи по одной.
 *  422 → error (запись остаётся с причиной), сетевой сбой → прервать. */
export async function syncQueue(): Promise<SyncResult> {
  if (syncing) return { sent: 0, rejected: 0, remaining: (await listQueue()).length }
  syncing = true
  let sent = 0
  let rejected = 0
  try {
    const records = await db.queue
      .filter((r) => r.status === 'pending' || r.status === 'error')
      .sortBy('created_at')
    for (const record of records) {
      await db.queue.update(record.client_id, { status: 'sending' })
      try {
        await createOperation(record.payload)
        await db.queue.delete(record.client_id)
        sent++
      } catch (e) {
        if (e instanceof ApiError && e.status === 422) {
          // Бизнес-правило нарушено: запись сохраняется с причиной (10-pwa §3).
          await db.queue.update(record.client_id, {
            status: 'error',
            retry_count: record.retry_count + 1,
            error_detail: e.userMessage,
          })
          rejected++
        } else if (e instanceof ApiError && e.status === 0) {
          // Сеть пропала во время синхронизации: вернуть в pending и стоп.
          await db.queue.update(record.client_id, { status: 'pending' })
          break
        } else {
          // Другие ошибки сервера (4xx/5xx): не терять запись.
          await db.queue.update(record.client_id, {
            status: 'error',
            retry_count: record.retry_count + 1,
            error_detail: e instanceof ApiError ? e.userMessage : String(e),
          })
          rejected++
        }
      }
    }
  } finally {
    syncing = false
  }
  return { sent, rejected, remaining: (await listQueue()).length }
}

/** Кэш карточек полей «перед выездом» (10-pwa §3.1): GET /fields → IndexedDB. */
export async function cacheFieldsForTrip(): Promise<number> {
  const { getFields } = await import('@/api')
  const page = await getFields({ status: 'active', limit: 100 })
  const now = new Date().toISOString()
  await db.fields.clear()
  await db.fields.bulkPut(page.items.map((f) => ({ ...f, cached_at: now })))
  return page.items.length
}

/** Поля для выбора в форме E-08: кэш (офлайн) или API (онлайн). */
export async function getCachedFields(): Promise<Array<Field_ & { cached_at: string }>> {
  return db.fields.toArray()
}

type Field_ = import('@/api/types').Field
