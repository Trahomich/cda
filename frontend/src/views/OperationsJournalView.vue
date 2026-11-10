<script setup lang="ts">
// E-13 «Журнал операций» (сценарий SC-03, шаг 6): фильтры поле/даты/тип,
// статусы отклонений — строки с deviation_flag подсвечены, rationale
// (REQ-F-013, RULE-AGRO-11) = сравнение факт. дозы с планом внесения.
// Данные: GET /operations (API-18, пагинация limit/offset), планы — API-15.
// Примечание #665: сезонные итоги, чипы план/факт и стоимости живут в
// карточке поля (блок «Планы и журнал») — журнал остаётся списком фактов.
import { computed, onMounted, ref } from 'vue'
import { getFields, getOperations, getTreatmentPlans } from '@/api'
import type {
  Field,
  FieldOperation,
  OperationType,
  TreatmentPlan,
} from '@/api/types'

const PAGE_SIZE = 20

const fields = ref<Field[]>([])
const items = ref<FieldOperation[]>([])
const total = ref(0)
const plans = ref<TreatmentPlan[]>([])
const loading = ref(true)
const failed = ref(false)
const busy = ref(false)

const fieldId = ref('')
const dateFrom = ref('')
const dateTo = ref('')
const opType = ref<OperationType | 'all'>('all')

const OP_LABELS: Record<OperationType, string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seeding: 'посев',
  harvesting: 'уборка',
  tillage: 'обработка почвы',
  other: 'прочее',
}

const SOURCE_LABELS: Record<FieldOperation['source'], string> = {
  manual: 'вручную',
  telemetry_import: 'телематика',
  plan_execution: 'по плану',
}

const OP_OPTIONS: Array<{ value: OperationType | 'all'; label: string }> = [
  { value: 'all', label: 'Все типы' },
  ...Object.entries(OP_LABELS).map(([value, label]) => ({ value: value as OperationType, label })),
]

/** Обоснование отклонения (RULE-AGRO-11): факт против ближайшего плана
 *  того же препарата на этом поле. Вне плана — отдельная пометка. */
function deviationOf(op: FieldOperation): { text: string; level: 'warn' | 'info' | 'none' } {
  const dose = op.actual_dose ?? null
  if (op.input_id == null || dose === null) return { text: '—', level: 'none' }
  const candidates = plans.value.filter(
    (p) => p.field_id === op.field_id && p.input_id === op.input_id,
  )
  if (candidates.length === 0) {
    return { text: 'вне плана внесения', level: 'info' }
  }
  // Ближайший план по дате (план ≤ факта), иначе первый будущий.
  const past = candidates
    .filter((p) => p.planned_date <= op.date)
    .sort((a, b) => b.planned_date.localeCompare(a.planned_date))
  const plan = past[0] ?? candidates.sort((a, b) => a.planned_date.localeCompare(b.planned_date))[0]
  const delta = ((dose - plan.dose) / plan.dose) * 100
  const sign = delta > 0 ? '+' : ''
  const pct = `${sign}${delta.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`
  const text = `факт ${dose.toLocaleString('ru-RU')} против плана ${plan.dose.toLocaleString('ru-RU')} (${pct})`
  return { text, level: Math.abs(delta) > 10 ? 'warn' : 'none' }
}

/** Доза факта для таблицы («—» если не указана). */
function fmtDose(op: FieldOperation): string {
  return op.actual_dose != null ? op.actual_dose.toLocaleString('ru-RU') : '—'
}

/** Показывать строку как отклонение: флаг от backend или пересчёт >10 %. */
function isDeviation(op: FieldOperation): boolean {
  return op.deviation_flag || deviationOf(op).level === 'warn'
}

const filtered = computed(() =>
  opType.value === 'all' ? items.value : items.value.filter((o) => o.operation_type === opType.value),
)

const fieldName = (id: string) => fields.value.find((f) => f.field_id === id)?.name ?? id.slice(0, 8);

const shownText = computed(
  () => `Показано ${filtered.value.length} из ${total.value}${filtered.value.length !== items.value.length ? ' (после фильтра по типу)' : ''}`,
)

async function load(reset = true) {
  loading.value = true
  failed.value = false
  try {
    if (reset) items.value = []
    const [page, tp] = await Promise.all([
      getOperations({
        field_id: fieldId.value || undefined,
        date_from: dateFrom.value || undefined,
        date_to: dateTo.value || undefined,
        limit: PAGE_SIZE,
        offset: reset ? 0 : items.value.length,
      }),
      plans.value.length ? Promise.resolve({ items: plans.value }) : getTreatmentPlans(),
    ])
    items.value = reset ? page.items : [...items.value, ...page.items]
    total.value = page.total
    plans.value = tp.items
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  busy.value = true
  try {
    await load(false)
  } finally {
    busy.value = false
  }
}

const hasMore = computed(() => items.value.length < total.value)

onMounted(async () => {
  try {
    const [f, tp] = await Promise.all([getFields({ status: 'all' }), getTreatmentPlans()])
    fields.value = f.items
    plans.value = tp.items
    await load(true)
  } catch {
    failed.value = true
    loading.value = false
  }
})
</script>

<template>
  <section class="jrn">
    <header class="jrn__header">
      <h1 class="jrn__title">
        Журнал операций
      </h1>
    </header>

    <form
      class="jrn__filters"
      @submit.prevent="load(true)"
    >
      <label class="jrn__field">
        <span>Поле</span>
        <select
          v-model="fieldId"
          class="jrn__input"
        >
          <option value="">
            Все поля
          </option>
          <option
            v-for="f in fields"
            :key="f.field_id"
            :value="f.field_id"
          >
            {{ f.name }}
          </option>
        </select>
      </label>
      <label class="jrn__field">
        <span>Дата с</span>
        <input
          v-model="dateFrom"
          class="jrn__input"
          type="date"
        >
      </label>
      <label class="jrn__field">
        <span>по</span>
        <input
          v-model="dateTo"
          class="jrn__input"
          type="date"
        >
      </label>
      <label class="jrn__field">
        <span>Тип</span>
        <select
          v-model="opType"
          class="jrn__input"
        >
          <option
            v-for="opt in OP_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
      </label>
      <button
        type="submit"
        class="jrn__apply"
      >
        Применить
      </button>
    </form>

    <p
      v-if="loading && items.length === 0"
      class="jrn__state"
    >
      Загрузка журнала…
    </p>
    <p
      v-else-if="failed"
      class="jrn__state jrn__state--error"
    >
      Не удалось загрузить журнал операций
    </p>

    <template v-else>
      <div class="jrn__table-wrap">
        <table class="jrn__table">
          <thead>
            <tr>
              <th scope="col">
                Дата
              </th>
              <th scope="col">
                Поле
              </th>
              <th scope="col">
                Операция
              </th>
              <th scope="col">
                Доза (факт)
              </th>
              <th scope="col">
                Площадь, га
              </th>
              <th scope="col">
                Исполнитель
              </th>
              <th scope="col">
                Источник
              </th>
              <th scope="col">
                Отклонение
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="op in filtered"
              :key="op.operation_id"
              :class="{ 'jrn__row--deviation': isDeviation(op) }"
            >
              <td>{{ op.date }}</td>
              <td>{{ fieldName(op.field_id) }}</td>
              <td>{{ OP_LABELS[op.operation_type] }}</td>
              <td class="jrn__num">
                {{ fmtDose(op) }}
              </td>
              <td class="jrn__num">
                {{ op.actual_area_ga.toLocaleString('ru-RU') }}
              </td>
              <td>{{ op.performer ?? '—' }}</td>
              <td>{{ SOURCE_LABELS[op.source] }}</td>
              <td>
                <span
                  v-if="isDeviation(op)"
                  class="jrn__deviation"
                  :title="deviationOf(op).text"
                >⚠ {{ deviationOf(op).text }}</span>
                <span
                  v-else-if="deviationOf(op).level === 'info'"
                  class="jrn__muted"
                >вне плана</span>
                <span
                  v-else
                  class="jrn__muted"
                >—</span>
              </td>
            </tr>
            <tr v-if="filtered.length === 0">
              <td
                colspan="8"
                class="jrn__state"
              >
                Операций не найдено
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="jrn__footer">
        <span class="jrn__muted">{{ shownText }}</span>
        <button
          v-if="hasMore"
          type="button"
          class="jrn__more"
          :disabled="busy || loading"
          @click="loadMore"
        >
          Показать ещё
        </button>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.jrn {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 80rem;
}

.jrn__title {
  font-size: 1.35rem;
  margin: 0;
}

.jrn__filters {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  align-items: flex-end;
}

.jrn__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.9rem;
  color: #444;
}

.jrn__input {
  font-size: 1rem;
  min-height: 44px;
}

.jrn__apply {
  min-height: 44px;
  background: #2e7d32;
  color: #fff;
  font-weight: 600;
  border: none;
  border-radius: 6px;
  padding: 0 1rem;
  cursor: pointer;
}

.jrn__state {
  color: #555;
}

.jrn__state--error {
  color: #b3261e;
}

.jrn__table-wrap {
  overflow-x: auto;
}

.jrn__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}

.jrn__table th,
.jrn__table td {
  border: 1px solid #d7e0d8;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.jrn__table th {
  background: #eef4ef;
}

.jrn__row--deviation {
  background: #fff8e1;
}

.jrn__num {
  font-variant-numeric: tabular-nums;
}

.jrn__deviation {
  color: #a15c00;
  font-weight: 600;
  cursor: help;
}

.jrn__muted {
  color: #777;
  font-size: 0.88rem;
}

.jrn__footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.jrn__more {
  min-height: 44px;
  background: #fff;
  color: #2e7d32;
  border: 2px solid #2e7d32;
  border-radius: 6px;
  padding: 0 1rem;
  font-weight: 600;
  cursor: pointer;
}
</style>
