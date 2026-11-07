<script setup lang="ts">
// Панель «Список полей» экрана E-02 (карта полей): таблица с фильтрами
// (q по имени, status) и пагинацией limit/offset (API-01 GET /fields).
// Разметка по аналогии с матрицей E-04 (07-ux §3.3); отдельным экраном
// не является — решено в обсуждении задачи #21 (пост #52).
import { onMounted, ref, watch } from 'vue'
import { getFields } from '@/api'
import type { Field, FieldStatus } from '@/api/types'

const rows = ref<Field[]>([])
const total = ref(0)
const loading = ref(true)
const failed = ref(false)

const search = ref('')
const status = ref<FieldStatus | 'all'>('active')
const offset = ref(0)
const LIMIT = 20

const STATUS_LABELS: Record<Field['status'], string> = {
  active: 'действует',
  merged: 'объединено',
  archived: 'в архиве',
}

const STATUS_OPTIONS: Array<{ value: FieldStatus | 'all'; label: string }> = [
  { value: 'active', label: 'Действующие' },
  { value: 'merged', label: 'Объединённые' },
  { value: 'archived', label: 'В архиве' },
  { value: 'all', label: 'Все' },
]

let searchTimer: ReturnType<typeof setTimeout> | undefined

async function load() {
  loading.value = true
  failed.value = false
  try {
    const page = await getFields({
      q: search.value.trim() || undefined,
      status: status.value,
      limit: LIMIT,
      offset: offset.value,
    })
    rows.value = page.items
    total.value = page.total
  } catch {
    failed.value = true // тост problem+json показывает API-клиент
  } finally {
    loading.value = false
  }
}

onMounted(load)

watch(status, () => {
  offset.value = 0
  void load()
})

// Поиск с задержкой, чтобы не дёргать API на каждый символ.
watch(search, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    offset.value = 0
    void load()
  }, 400)
})

function prev() {
  offset.value = Math.max(0, offset.value - LIMIT)
  void load()
}

function next() {
  if (offset.value + LIMIT < total.value) {
    offset.value += LIMIT
    void load()
  }
}

function fmtArea(v: number): string {
  return v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
}

function cultureOf(field: Field): string {
  const cycle = field.current_cycle
  if (!cycle) return '—'
  return cycle.kind === 'fallow' ? 'Пар' : `${cycle.culture_name} (${cycle.year})`
}
</script>

<template>
  <div class="fields-list">
    <form
      class="fields-list__filters"
      @submit.prevent
    >
      <label
        class="fields-list__label"
        for="q"
      >Поиск по названию</label>
      <input
        id="q"
        v-model="search"
        class="fields-list__input"
        type="search"
        placeholder="Например, 1/ВОСХОД/85га"
      >
      <label
        class="fields-list__label"
        for="status"
      >Статус</label>
      <select
        id="status"
        v-model="status"
        class="fields-list__input"
      >
        <option
          v-for="opt in STATUS_OPTIONS"
          :key="opt.value"
          :value="opt.value"
        >
          {{ opt.label }}
        </option>
      </select>
    </form>

    <div
      class="fields-list__table-wrap"
      role="region"
      aria-label="Таблица полей"
    >
      <table class="fields-list__table">
        <thead>
          <tr>
            <th scope="col">
              Название
            </th>
            <th scope="col">
              Площадь, га
            </th>
            <th scope="col">
              Культура сезона
            </th>
            <th scope="col">
              Статус
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td
              colspan="4"
              class="fields-list__state"
            >
              <Skeleton
                :lines="5"
                height="1.1rem"
              />
            </td>
          </tr>
          <tr v-else-if="failed">
            <td
              colspan="4"
              class="fields-list__state fields-list__state--error"
            >
              Не удалось загрузить список
            </td>
          </tr>
          <tr v-else-if="rows.length === 0">
            <td
              colspan="4"
              class="fields-list__state"
            >
              Полей не найдено
            </td>
          </tr>
          <tr
            v-for="field in rows"
            v-else
            :key="field.field_id"
          >
            <td>
              <RouterLink
                class="fields-list__link"
                :to="{ name: 'field-card', params: { fieldId: field.field_id } }"
              >
                {{ field.name }}
              </RouterLink>
            </td>
            <td class="fields-list__num">
              {{ fmtArea(field.area_ga) }}
            </td>
            <td>{{ cultureOf(field) }}</td>
            <td>{{ STATUS_LABELS[field.status] }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="fields-list__pager">
      <button
        type="button"
        class="fields-list__pager-btn"
        :disabled="offset === 0 || loading"
        @click="prev"
      >
        ← Назад
      </button>
      <span class="fields-list__pager-info">
        Показаны {{ rows.length ? offset + 1 : 0 }}–{{ offset + rows.length }} из {{ total }}
      </span>
      <button
        type="button"
        class="fields-list__pager-btn"
        :disabled="offset + LIMIT >= total || loading"
        @click="next"
      >
        Вперёд →
      </button>
    </div>
  </div>
</template>

<style scoped>
.fields-list {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  width: 100%;
}

.fields-list__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.fields-list__label {
  font-size: 1rem;
  margin-left: 0.5rem;
}

.fields-list__input {
  font-size: 1rem;
  padding: 0.6rem 0.65rem;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  min-height: 48px;
  background: #fff;
}

.fields-list__table-wrap {
  overflow-x: auto;
}

.fields-list__table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  font-size: 1rem;
}

.fields-list__table th,
.fields-list__table td {
  text-align: left;
  padding: 0.65rem 0.75rem;
  border-bottom: 1px solid #e0e0e0;
}

.fields-list__table th {
  background: #e8f5e9;
}

.fields-list__num {
  font-variant-numeric: tabular-nums;
}

.fields-list__link {
  color: #1b5e20;
  font-weight: 600;
  text-decoration: none;
}

.fields-list__link:hover {
  text-decoration: underline;
}

.fields-list__state {
  color: #424242;
  text-align: center;
  padding: 1.25rem 0.75rem;
}

.fields-list__state--error {
  color: #b00020;
  font-weight: 600;
}

.fields-list__pager {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.fields-list__pager-btn {
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.6rem 1rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.fields-list__pager-btn:disabled {
  background: #9e9e9e;
  cursor: default;
}

.fields-list__pager-info {
  font-size: 1rem;
  color: #424242;
}
</style>
