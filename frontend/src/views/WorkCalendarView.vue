<script setup lang="ts">
// «Календарь работ» (v2, docs/06 §7.2): месячная сетка по хозяйству, на дне —
// плановые даты treatment-plans (API-15) и операции-факты (API-18), различимые
// визуально: план — рамка, факт — заливка. Отдельного API нет — композит на
// клиенте. Фильтр по типу операции, переключатель план/факт, клик по дню —
// список работ с переходом в карточку поля (E-03).
import { computed, onMounted, ref, watch } from 'vue'
import { getFields, getOperations, getTreatmentPlans } from '@/api'
import type {
  Field,
  FieldOperation,
  OperationType,
  TreatmentPlan,
  TreatmentPlanStatus,
} from '@/api/types'
import Skeleton from '@/components/Skeleton.vue'

const fields = ref<Field[]>([])
/** Операции-факты показанного месяца (API-18, date_from/date_to). */
const operations = ref<FieldOperation[]>([])
/** Все планы внесения (API-15); месячный срез — на клиенте. */
const plans = ref<TreatmentPlan[]>([])
const loading = ref(true)
const failed = ref(false)

/** Показанный месяц 'YYYY-MM' (по умолчанию — текущий). */
const month = ref(new Date().toISOString().slice(0, 7))
const typeFilter = ref<OperationType | 'all'>('all')
/** План/факт: all | plan | fact. */
const showMode = ref<'all' | 'plan' | 'fact'>('all')
const selectedDay = ref<string | null>(null)

const OP_LABELS: Record<OperationType, string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seeding: 'посев',
  harvesting: 'уборка',
  tillage: 'обработка почвы',
  other: 'прочее',
}

/** Типы планов внесения (API-15): seed_treatment ≠ seeding факта. */
const PLAN_OP_LABELS: Record<TreatmentPlan['operation_type'], string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seed_treatment: 'протравливание семян',
}

const STATUS_LABELS: Record<TreatmentPlanStatus, string> = {
  draft: 'черновик',
  approved: 'согласован',
  in_progress: 'выполняется',
  completed: 'выполнен',
  cancelled: 'отменён',
}

const MONTH_TITLES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const monthTitle = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  return `${MONTH_TITLES[m - 1]} ${y}`
})

/** Границы месяца для запроса API-18 и клиентского среза планов. */
const monthRange = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const from = `${month.value}-01`
  const to = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)
  return { from, to }
})

async function load() {
  loading.value = true
  failed.value = false
  try {
    // Факты месяца — API-18 с фильтром дат; планы — весь список (их немного,
    // месячный срез и фильтры делаются на клиенте).
    const [ops, tps] = await Promise.all([
      getOperations({ date_from: monthRange.value.from, date_to: monthRange.value.to, limit: 100 }),
      getTreatmentPlans(),
    ])
    operations.value = ops.items
    plans.value = tps.items
    selectedDay.value = null
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const f = await getFields({ status: 'all' })
    fields.value = f.items
  } catch {
    // имена полей — вторичны; сетка работает и по field_id
  }
  await load()
})

watch(month, load)

/** Планы месяца после фильтров (отменённые не показываем). */
const monthPlans = computed(() =>
  plans.value.filter(
    (p) =>
      p.planned_date.startsWith(month.value) &&
      p.status !== 'cancelled' &&
      (typeFilter.value === 'all' || p.operation_type === typeFilter.value),
  ),
)

/** Факты месяца после фильтров. */
const monthOperations = computed(() =>
  operations.value.filter(
    (o) => typeFilter.value === 'all' || o.operation_type === typeFilter.value,
  ),
)

/** Записи одного дня: план и факт в одной ленте, план сверху. */
function entriesOf(dateIso: string): Array<{ kind: 'plan' | 'fact'; plan?: TreatmentPlan; op?: FieldOperation }> {
  const out: Array<{ kind: 'plan' | 'fact'; plan?: TreatmentPlan; op?: FieldOperation }> = []
  if (showMode.value !== 'fact') {
    for (const p of monthPlans.value.filter((p) => p.planned_date === dateIso)) {
      out.push({ kind: 'plan', plan: p })
    }
  }
  if (showMode.value !== 'plan') {
    for (const o of monthOperations.value.filter((o) => o.date === dateIso)) {
      out.push({ kind: 'fact', op: o })
    }
  }
  return out
}

/** Сетка месяца: недели (пн-вс) ячейками { date | null }. */
const weeks = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const first = new Date(Date.UTC(y, m - 1, 1))
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate()
  // Понедельник — первый день недели (ru).
  const lead = (first.getUTCDay() + 6) % 7
  const cells: Array<string | null> = [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => `${month.value}-${String(i + 1).padStart(2, '0')}`),
  ]
  while (cells.length % 7 !== 0) cells.push(null)
  const rows: Array<Array<string | null>> = []
  for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7))
  return rows
})

const fieldName = (id: string) => fields.value.find((f) => f.field_id === id)?.name ?? id.slice(0, 8)

/** Лента выбранного дня (клик по ячейке). */
const selectedEntries = computed(() => (selectedDay.value ? entriesOf(selectedDay.value) : []))

function shiftMonth(delta: number) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(Date.UTC(y, m - 1 + delta, 1))
  month.value = d.toISOString().slice(0, 7)
}

const factCount = computed(() => monthOperations.value.length)
const planCount = computed(() => monthPlans.value.length)
</script>

<template>
  <section class="cal">
    <header class="cal__header">
      <h1 class="cal__title">
        Календарь работ
      </h1>
      <span class="cal__spacer" />
      <button
        type="button"
        class="cal__nav"
        aria-label="Предыдущий месяц"
        @click="shiftMonth(-1)"
      >
        ‹
      </button>
      <input
        v-model="month"
        class="cal__month"
        type="month"
        aria-label="Месяц"
      >
      <button
        type="button"
        class="cal__nav"
        aria-label="Следующий месяц"
        @click="shiftMonth(1)"
      >
        ›
      </button>
      <label
        class="cal__label"
        for="cal-type"
      >Тип</label>
      <select
        id="cal-type"
        v-model="typeFilter"
        class="cal__select"
      >
        <option value="all">
          Все типы
        </option>
        <option
          v-for="(label, value) in OP_LABELS"
          :key="value"
          :value="value"
        >
          {{ label }}
        </option>
        <option value="seed_treatment">
          протравливание семян
        </option>
      </select>
      <select
        v-model="showMode"
        class="cal__select"
        aria-label="План или факт"
      >
        <option value="all">
          План и факт
        </option>
        <option value="plan">
          Только план
        </option>
        <option value="fact">
          Только факт
        </option>
      </select>
    </header>

    <p class="cal__legend">
      <span class="cal__legend-item"><span class="cal__chip cal__chip--plan">план</span> плановая дата (API-15)</span>
      <span class="cal__legend-item"><span class="cal__chip cal__chip--fact">факт</span> выполнено (API-18)</span>
      <span class="cal__muted">{{ monthTitle }}: планов {{ loading ? '…' : planCount }}, фактов {{ loading ? '…' : factCount }}</span>
    </p>

    <p
      v-if="failed"
      class="cal__state cal__state--error"
    >
      Не удалось загрузить календарь работ
    </p>

    <!-- Сетка месяца рендерится сразу (v2-fix-E): номера дней видны до
         загрузки, чипы работ — скелетоны-пилюли, заполняются по готовности. -->
    <template v-else>
      <div class="cal__grid-wrap">
        <table class="cal__grid">
          <thead>
            <tr>
              <th
                v-for="d in ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']"
                :key="d"
                scope="col"
              >
                {{ d }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="week in weeks"
              :key="week.join()"
            >
              <td
                v-for="(date, i) in week"
                :key="i"
                :class="{
                  'cal__cell': date !== null,
                  'cal__cell--has': date !== null && entriesOf(date).length > 0,
                  'cal__cell--selected': date !== null && date === selectedDay,
                  'cal__cell--weekend': date !== null && i >= 5,
                }"
              >
                <template v-if="date !== null">
                  <button
                    type="button"
                    class="cal__day"
                    :data-date="date"
                    @click="selectedDay = selectedDay === date ? null : date"
                  >
                    <span class="cal__day-num">{{ Number(date.slice(8)) }}</span>
                    <span class="cal__chips">
                      <Skeleton
                        v-if="loading"
                        width="2.4rem"
                        height="0.65rem"
                        pill
                      />
                      <template v-else>
                        <span
                          v-for="(e, j) in entriesOf(date).slice(0, 4)"
                          :key="j"
                          class="cal__chip"
                          :class="e.kind === 'plan' ? 'cal__chip--plan' : 'cal__chip--fact'"
                          :data-kind="e.kind"
                        >{{ e.kind === 'plan'
                          ? PLAN_OP_LABELS[e.plan!.operation_type]
                          : OP_LABELS[e.op!.operation_type] }}</span>
                        <span
                          v-if="entriesOf(date).length > 4"
                          class="cal__chip cal__chip--more"
                        >+{{ entriesOf(date).length - 4 }}</span>
                      </template>
                    </span>
                  </button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Лента выбранного дня -->
      <div
        v-if="selectedDay"
        class="cal__day-panel"
        data-testid="cal-day-panel"
      >
        <h2 class="cal__panel-title">
          {{ selectedDay.split('-').reverse().join('.') }}
        </h2>
        <ul class="cal__entries">
          <li
            v-for="(e, i) in selectedEntries"
            :key="i"
            class="cal__entry"
            :class="e.kind === 'plan' ? 'cal__entry--plan' : 'cal__entry--fact'"
          >
            <span
              class="cal__chip"
              :class="e.kind === 'plan' ? 'cal__chip--plan' : 'cal__chip--fact'"
            >{{ e.kind === 'plan' ? 'план' : 'факт' }}</span>
            <RouterLink
              class="cal__field-link"
              :to="{ name: 'field-card', params: { fieldId: e.kind === 'plan' ? e.plan!.field_id : e.op!.field_id } }"
            >
              {{ e.kind === 'plan' ? e.plan!.field_name : fieldName(e.op!.field_id) }}
            </RouterLink>
            <span v-if="e.kind === 'plan'">
              — {{ PLAN_OP_LABELS[e.plan!.operation_type] }} · {{ e.plan!.input_name }} ·
              {{ e.plan!.dose.toLocaleString('ru-RU') }} {{ e.plan!.unit === 'l_ha' ? 'л/га' : e.plan!.unit === 'kg_ha' ? 'кг/га' : 'кг д.в./га' }} ·
              {{ STATUS_LABELS[e.plan!.status] }}
            </span>
            <span v-else>
              — {{ OP_LABELS[e.op!.operation_type] }} ·
              {{ e.op!.actual_area_ga.toLocaleString('ru-RU') }} га<span v-if="e.op!.actual_dose != null"> ·
                доза {{ e.op!.actual_dose.toLocaleString('ru-RU') }}</span><span v-if="e.op!.performer"> ·
                {{ e.op!.performer }}</span>
            </span>
          </li>
          <li
            v-if="selectedEntries.length === 0"
            class="cal__muted"
          >
            Работ на этот день нет
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>

<style scoped>
.cal {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
  padding: 1rem;
}

.cal__header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.cal__title {
  font-size: 1.2rem;
  margin: 0;
}

.cal__spacer {
  flex: 1;
}

.cal__nav {
  min-width: 44px;
  min-height: 44px;
  font-size: 1.2rem;
  background: #fff;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  cursor: pointer;
}

.cal__month,
.cal__select {
  font-size: 1rem;
  padding: 0.45rem 0.55rem;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  min-height: 44px;
  background: #fff;
}

.cal__label {
  font-size: 0.95rem;
  color: #424242;
}

.cal__legend {
  display: flex;
  gap: 1rem;
  align-items: center;
  flex-wrap: wrap;
  margin: 0;
  font-size: 0.9rem;
  color: #424242;
}

.cal__legend-item {
  display: inline-flex;
  gap: 0.35rem;
  align-items: center;
}

.cal__chip {
  display: inline-block;
  border-radius: 6px;
  padding: 0.05rem 0.35rem;
  font-size: 0.78rem;
  white-space: nowrap;
}

.cal__chip--plan {
  border: 1.5px dashed #2e7d32;
  color: #1b5e20;
  background: #f1f8f1;
}

.cal__chip--fact {
  background: #2e7d32;
  color: #fff;
}

.cal__chip--more {
  background: #eee;
  color: #424242;
}

.cal__grid-wrap {
  overflow-x: auto;
}

.cal__grid {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  background: #fff;
}

.cal__grid th {
  background: #e8f5e9;
  color: #1b5e20;
  font-size: 0.9rem;
  padding: 0.35rem 0.4rem;
  border: 1px solid #c8e6c9;
}

.cal__cell {
  border: 1px solid #c8e6c9;
  padding: 0;
  vertical-align: top;
  height: 92px;
}

.cal__cell--weekend {
  background: #fafafa;
}

.cal__cell--has .cal__day {
  background: #f9fbe7;
}

.cal__cell--selected .cal__day {
  outline: 3px solid #2e7d32;
  outline-offset: -3px;
}

.cal__day {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  width: 100%;
  height: 100%;
  border: none;
  background: none;
  padding: 0.25rem;
  text-align: left;
  font: inherit;
  cursor: pointer;
  box-sizing: border-box;
}

.cal__day-num {
  font-weight: 600;
  font-size: 0.9rem;
}

.cal__chips {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  align-items: flex-start;
}

.cal__day-panel {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.cal__panel-title {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
  color: #1b5e20;
}

.cal__entries {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.cal__entry {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
  flex-wrap: wrap;
  font-size: 0.95rem;
}

.cal__field-link {
  color: #1b5e20;
  font-weight: 600;
  text-decoration: none;
}

.cal__field-link:hover {
  text-decoration: underline;
}

.cal__muted {
  color: #757575;
  font-size: 0.9rem;
}

.cal__state {
  color: #424242;
  text-align: center;
  padding: 1rem 0.75rem;
}

.cal__state--error {
  color: #b00020;
  font-weight: 600;
}
</style>
