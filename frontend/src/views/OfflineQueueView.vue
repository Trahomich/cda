<script setup lang="ts">
// E-17 «Офлайн-очередь» (07-ux §2; 10-mobile-pwa §3): несинхронизированные
// записи, статусы, причина 422 из problem+json, правка/удаление, ручная
// синхронизация. Ошибка 422 не удаляет запись — правило «факт важнее плана».
import { computed, onMounted, ref } from 'vue'
import type { OperationType } from '@/api/types'
import { removeFromQueue, updateQueuePayload } from '@/services/offline'
import { useOfflineStore } from '@/stores/offline'
import type { QueueRecord } from '@/services/db'

const offline = useOfflineStore()
const editing = ref<QueueRecord | null>(null)
const editArea = ref('')

const TYPE_LABELS: Record<OperationType, string> = {
  spraying: 'Обработка СЗР',
  fertilizing: 'Внесение удобрений',
  seeding: 'Посев',
  harvesting: 'Уборка',
  tillage: 'Обработка почвы',
  other: 'Другое',
}

const STATUS_LABELS: Record<QueueRecord['status'], string> = {
  pending: 'ждёт сети',
  sending: 'отправляется…',
  error: 'отклонено сервером',
}

const rejectedCount = computed(() => offline.queue.filter((r) => r.status === 'error').length)

onMounted(() => {
  void offline.refresh()
})

function fieldName(record: QueueRecord): string {
  return record.payload.field_id.slice(0, 8) + '…'
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU')
}

async function remove(record: QueueRecord) {
  await removeFromQueue(record.client_id)
  await offline.refresh()
}

function startEdit(record: QueueRecord) {
  editing.value = record
  editArea.value = String(record.payload.actual_area_ga)
}

async function saveEdit(dropYield = false) {
  if (!editing.value) return
  const payload = { ...editing.value.payload, actual_area_ga: Number(editArea.value) }
  if (dropYield) payload.yield_record = null
  await updateQueuePayload(editing.value.client_id, payload)
  editing.value = null
  await offline.refresh()
}
</script>

<template>
  <section class="queue">
    <div class="queue__head">
      <h1 class="queue__title">
        Офлайн-очередь
      </h1>
      <button
        type="button"
        class="queue__sync"
        :disabled="offline.syncing || !offline.online || offline.queue.length === 0"
        @click="offline.runSync()"
      >
        {{ offline.syncing ? 'Синхронизация…' : '⟳ Синхронизировать' }}
      </button>
    </div>
    <p class="queue__state">
      {{ offline.online ? 'Сеть есть' : 'Офлайн — записи ждут появления сети' }} ·
      в очереди: {{ offline.queue.length }}
      <template v-if="rejectedCount > 0">
        · отклонено сервером: {{ rejectedCount }}
      </template>
    </p>

    <p
      v-if="offline.queue.length === 0"
      class="queue__empty"
    >
      Очередь пуста — всё синхронизировано.
    </p>

    <ul
      v-else
      class="queue__list"
    >
      <li
        v-for="record in offline.queue"
        :key="record.client_id"
        class="queue__item"
        :class="`queue__item--${record.status}`"
      >
        <div class="queue__item-main">
          <strong>{{ TYPE_LABELS[record.payload.operation_type] }}</strong>
          · поле {{ fieldName(record) }}
          · {{ record.payload.date }}
          · {{ record.payload.actual_area_ga }} га
          <span class="queue__badge">{{ STATUS_LABELS[record.status] }}</span>
        </div>
        <p
          v-if="record.status === 'error'"
          class="queue__error"
          role="alert"
        >
          {{ record.error_detail }} (попыток: {{ record.retry_count }}) — исправьте или удалите запись.
        </p>
        <p class="queue__meta">
          Создано: {{ fmtDate(record.created_at) }} · client_id: {{ record.client_id.slice(0, 8) }}…
        </p>
        <div class="queue__actions">
          <button
            v-if="record.status === 'error'"
            type="button"
            class="queue__btn"
            @click="startEdit(record)"
          >
            Править
          </button>
          <button
            type="button"
            class="queue__btn queue__btn--danger"
            @click="remove(record)"
          >
            Удалить
          </button>
        </div>

        <form
          v-if="editing?.client_id === record.client_id"
          class="queue__edit"
          @submit.prevent="saveEdit(false)"
        >
          <label :for="`area-${record.client_id}`">Факт. площадь, га</label>
          <input
            :id="`area-${record.client_id}`"
            v-model="editArea"
            type="number"
            step="0.01"
            min="0.01"
            required
          >
          <button type="submit">
            Сохранить и в очередь
          </button>
          <button
            v-if="editing.payload.yield_record"
            type="button"
            @click="saveEdit(true)"
          >
            Убрать урожайность и в очередь
          </button>
        </form>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.queue {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-width: 720px;
  width: 100%;
  margin: 0 auto;
}

.queue__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.queue__title {
  font-size: 1.2rem;
  margin: 0;
}

.queue__sync {
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.7rem 1rem;
  font-size: 1rem;
  font-weight: 700;
  min-height: 48px;
  cursor: pointer;
}

.queue__sync:disabled {
  background: #9e9e9e;
  cursor: default;
}

.queue__state {
  margin: 0;
  font-size: 1rem;
  color: #424242;
}

.queue__empty {
  background: #fff;
  border-radius: 12px;
  padding: 1rem;
  font-size: 1rem;
  color: #424242;
}

.queue__exit {
  align-self: flex-start;
  background: #fff;
  color: #1b5e20;
  border: 2px solid #2e7d32;
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.queue__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.queue__item {
  background: #fff;
  border-radius: 12px;
  padding: 0.85rem 1rem;
  border-left: 6px solid #2e7d32;
  box-shadow: 0 2px 8px rgb(0 0 0 / 8%);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.queue__item--error {
  border-left-color: #b00020;
}

.queue__item--sending {
  border-left-color: #f9a825;
}

.queue__item-main {
  font-size: 1rem;
}

.queue__badge {
  background: #e8f5e9;
  border-radius: 999px;
  padding: 0.1rem 0.6rem;
  font-size: 0.95rem;
}

.queue__item--error .queue__badge {
  background: #ffcdd2;
}

.queue__error {
  margin: 0;
  color: #b00020;
  font-size: 1rem;
  font-weight: 600;
}

.queue__meta {
  margin: 0;
  color: #616161;
  font-size: 0.9rem;
}

.queue__actions {
  display: flex;
  gap: 0.5rem;
}

.queue__btn {
  background: #e8f5e9;
  color: #1b5e20;
  border: 2px solid #2e7d32;
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.queue__btn--danger {
  border-color: #b00020;
  color: #b00020;
  background: #ffebee;
}

.queue__edit {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  background: #f6faf6;
  border-radius: 8px;
  padding: 0.6rem;
}

.queue__edit input {
  font-size: 1.05rem;
  padding: 0.6rem;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  min-height: 48px;
  min-width: 9rem;
}

.queue__edit button {
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.6rem 1rem;
  font-size: 1rem;
  font-weight: 700;
  min-height: 48px;
  cursor: pointer;
}
</style>
