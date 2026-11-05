<script setup lang="ts">
// E-08 «Журнал работ» — полевой ввод операции (07-ux §3.4, мобильный).
// Онлайн: сразу POST /operations (API-17, идемпотентность по client_id).
// Оффлайн: запись уходит в очередь E-17 (IndexedDB) со статусом pending.
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getOperations } from '@/api'
import type { Field, FieldOperation, OperationRequest, OperationType } from '@/api/types'
import { ApiError } from '@/api/client'
import { cacheFieldsForTrip, enqueueOperation, getCachedFields } from '@/services/offline'
import { uuidV4 } from '@/services/db'
import { useAuthStore } from '@/stores/auth'
import { useOfflineStore } from '@/stores/offline'
import { useToastStore } from '@/stores/toast'

const auth = useAuthStore()
const offline = useOfflineStore()
const toast = useToastStore()
const router = useRouter()

const fields = ref<Field[]>([])
const cacheInfo = ref('')
const journal = ref<FieldOperation[]>([])
const submitting = ref(false)

const OPERATION_TYPES: Array<{ value: OperationType; label: string }> = [
  { value: 'spraying', label: 'Обработка СЗР' },
  { value: 'fertilizing', label: 'Внесение удобрений' },
  { value: 'seeding', label: 'Посев' },
  { value: 'harvesting', label: 'Уборка' },
  { value: 'tillage', label: 'Обработка почвы' },
  { value: 'other', label: 'Другое' },
]

const form = reactive({
  field_id: '',
  operation_type: 'spraying' as OperationType,
  date: new Date().toISOString().slice(0, 10),
  actual_area_ga: '' as string,
  actual_dose: '' as string,
})

const selectedField = computed(() => fields.value.find((f) => f.field_id === form.field_id))

async function loadFields() {
  // Онлайн — берём из API (и обновляем кэш «перед выездом»), офлайн — из кэша.
  if (offline.online) {
    try {
      await cacheFieldsForTrip()
    } catch {
      /* кэш обновится позже; ниже есть кэш */
    }
  }
  const cached = await getCachedFields()
  fields.value = cached
  if (cached.length > 0) cacheInfo.value = `Кэш полей: ${cached.length}`
  else cacheInfo.value = 'Нет кэша полей — обновите при наличии сети'
}

async function loadJournal() {
  if (!offline.online) return
  try {
    const page = await getOperations({ limit: 10 })
    journal.value = page.items
  } catch {
    /* журнал недоступен офлайн — это нормальное состояние */
  }
}

onMounted(() => {
  void loadFields()
  void loadJournal()
})

async function submit() {
  if (!form.field_id || !form.actual_area_ga) return
  const payload: Omit<OperationRequest, 'client_id'> = {
    field_id: form.field_id,
    operation_type: form.operation_type,
    date: form.date,
    actual_area_ga: Number(form.actual_area_ga),
    actual_dose: form.actual_dose ? Number(form.actual_dose) : null,
    input_id: null,
    performer: auth.user?.name ?? null,
    source: 'manual',
  }
  submitting.value = true
  try {
    if (offline.online) {
      // Онлайн — сразу на сервер (мимо очереди, как в 10-pwa §3.2).
      const { createOperation } = await import('@/api')
      await createOperation({ ...payload, client_id: uuidV4() })
      toast.push('info', 'Записано', 'Операция отправлена на сервер.')
    } else {
      await enqueueOperation(payload)
      await offline.refresh()
      toast.push('info', 'Сохранено офлайн', 'Запись уйдёт при появлении сети.')
    }
    form.actual_area_ga = ''
    form.actual_dose = ''
    await loadJournal()
  } catch (e) {
    if (e instanceof ApiError && e.status === 0 && offline.online) {
      // Сеть пропала в момент отправки — унести в очередь, не терять факт.
      await enqueueOperation(payload)
      await offline.refresh()
      toast.push('info', 'Сохранено офлайн', 'Сеть пропала — запись уйдёт при появлении сети.')
    } else if (!(e instanceof ApiError)) {
      // Неожиданная ошибка (например, IndexedDB) — не глотать молча.
      console.error('Ошибка сохранения операции:', e)
      toast.push('error', 'Не сохранено', 'Неожиданная ошибка, запись не добавлена.')
    }
    // Иные ошибки (422 и т.п.) уже показаны тостом problem+json.
  } finally {
    submitting.value = false
  }
}

function goQueue() {
  router.push({ name: 'field-queue' })
}
</script>

<template>
  <section class="op-entry">
    <div class="op-entry__head">
      <h1 class="op-entry__title">
        Журнал работ — ввод операции
      </h1>
      <button
        type="button"
        class="op-entry__queue-btn"
        :class="{ 'op-entry__queue-btn--offline': !offline.online }"
        @click="goQueue"
      >
        {{ offline.online ? 'ОНЛАЙН' : 'ОФЛАЙН' }}
        <template v-if="offline.pendingCount > 0">
          ⛁ {{ offline.pendingCount }}
        </template>
      </button>
    </div>

    <form
      class="op-entry__form"
      @submit.prevent="submit"
    >
      <label
        class="op-entry__label"
        for="field"
      >Поле</label>
      <select
        id="field"
        v-model="form.field_id"
        class="op-entry__control"
        required
      >
        <option
          value=""
          disabled
        >
          Выберите поле
        </option>
        <option
          v-for="f in fields"
          :key="f.field_id"
          :value="f.field_id"
        >
          {{ f.name }} · {{ f.area_ga }} га
        </option>
      </select>
      <p
        v-if="selectedField?.current_cycle"
        class="op-entry__hint"
      >
        Культура сезона: {{ selectedField.current_cycle.culture_name }} ({{ selectedField.current_cycle.year }})
      </p>
      <p class="op-entry__hint">
        {{ cacheInfo }}
      </p>

      <label
        class="op-entry__label"
        for="type"
      >Тип операции</label>
      <select
        id="type"
        v-model="form.operation_type"
        class="op-entry__control"
      >
        <option
          v-for="t in OPERATION_TYPES"
          :key="t.value"
          :value="t.value"
        >
          {{ t.label }}
        </option>
      </select>

      <label
        class="op-entry__label"
        for="date"
      >Дата</label>
      <input
        id="date"
        v-model="form.date"
        class="op-entry__control"
        type="date"
        required
      >

      <label
        class="op-entry__label"
        for="area"
      >Факт. площадь, га</label>
      <input
        id="area"
        v-model="form.actual_area_ga"
        class="op-entry__control"
        type="number"
        step="0.01"
        min="0.01"
        placeholder="182"
        required
      >
      <p
        v-if="selectedField"
        class="op-entry__hint"
      >
        Лимит по RULE-AGRO-12 — {{ (selectedField.area_ga * 1.05).toFixed(1) }} га;
        превышение отклонит сервер с пояснением при синхронизации.
      </p>

      <label
        class="op-entry__label"
        for="dose"
      >Доза факт. (л/га или кг/га)</label>
      <input
        id="dose"
        v-model="form.actual_dose"
        class="op-entry__control"
        type="number"
        step="0.01"
        min="0"
        placeholder="0.6"
      >

      <button
        type="submit"
        class="op-entry__submit"
        :disabled="submitting"
      >
        {{ offline.online ? '✓ ЗАПИСАТЬ' : '✓ ЗАПИСАТЬ — уйдёт при появлении сети' }}
      </button>
    </form>

    <div class="op-entry__journal">
      <h2 class="op-entry__subtitle">
        Последние операции (журнал)
      </h2>
      <p
        v-if="!offline.online"
        class="op-entry__hint"
      >
        Журнал доступен при наличии сети.
      </p>
      <ul
        v-else-if="journal.length"
        class="op-entry__journal-list"
      >
        <li
          v-for="op in journal"
          :key="op.operation_id"
        >
          {{ op.date }} · {{ fields.find((f) => f.field_id === op.field_id)?.name ?? op.field_id }} ·
          {{ OPERATION_TYPES.find((t) => t.value === op.operation_type)?.label ?? op.operation_type }} ·
          {{ op.actual_area_ga }} га
        </li>
      </ul>
      <p
        v-else
        class="op-entry__hint"
      >
        Записей пока нет.
      </p>
    </div>
  </section>
</template>

<style scoped>
.op-entry {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 640px;
  width: 100%;
  margin: 0 auto;
}

.op-entry__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.op-entry__title {
  font-size: 1.2rem;
  margin: 0;
}

.op-entry__queue-btn {
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 999px;
  padding: 0.45rem 0.9rem;
  font-weight: 700;
  font-size: 1rem;
  min-height: 48px;
  cursor: pointer;
}

.op-entry__queue-btn--offline {
  background: #e65100;
}

.op-entry__form {
  background: #fff;
  border-radius: 12px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  box-shadow: 0 2px 10px rgb(0 0 0 / 8%);
}

.op-entry__label {
  font-size: 1rem;
  margin-top: 0.5rem;
}

.op-entry__control {
  font-size: 1.05rem;
  padding: 0.7rem 0.75rem;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  min-height: 48px;
  background: #fff;
}

.op-entry__control:focus {
  outline: 3px solid #2e7d32;
  border-color: #2e7d32;
}

.op-entry__hint {
  font-size: 0.95rem;
  color: #616161;
  margin: 0.15rem 0 0;
}

.op-entry__submit {
  margin-top: 0.9rem;
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.9rem;
  font-size: 1.1rem;
  font-weight: 700;
  min-height: 48px;
  cursor: pointer;
}

.op-entry__submit:disabled {
  opacity: 0.6;
}

.op-entry__journal {
  background: #fff;
  border-radius: 12px;
  padding: 1rem;
  box-shadow: 0 2px 10px rgb(0 0 0 / 8%);
}

.op-entry__subtitle {
  font-size: 1.05rem;
  margin: 0 0 0.5rem;
}

.op-entry__journal-list {
  margin: 0;
  padding-left: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 1rem;
}
</style>
