<script setup lang="ts">
// «Календарь работ» — на ЕДИНОМ источнике операций §7.10 (#175): месячная
// сетка из API-18: открытые планы — плановая дата (рамка), выполненные —
// фактическая дата (заливка); детализация дня с расходниками, техникой и
// стоимостями. Навигация по месяцам и фильтры — как раньше.
import { computed, onMounted, ref, watch } from 'vue'
import { getFields, getOperations } from '@/api'
import type { Field, FieldOperation, OperationType } from '@/api/types'
import { shortFieldNumber, opTypeColor, OP_TYPE_COLORS } from '@/utils/field'
import Skeleton from '@/components/Skeleton.vue'

const fields = ref<Field[]>([])
/** Единые операции показанного месяца (API-18 §7.10: план+факт в строке). */
const ops = ref<FieldOperation[]>([])
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
  seed_treatment: 'протравливание семян',
  seeding: 'посев',
  harvesting: 'уборка',
  tillage: 'обработка почвы',
  other: 'прочее',
}

const MONTH_TITLES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const monthTitle = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  return `${MONTH_TITLES[m - 1]} ${y}`
})

/** Границы месяца: запрос API-18 по date (= факт или плановая дата). */
const monthRange = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const from = `${month.value}-01`
  const to = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)
  return { from, to }
})

/** Загрузка операций месяца: пагинация по 100 (лимит API-18, #729/#186);
 *  потолок 500 операций — дальше заглушка-предупреждение. */
const CAL_PAGE = 100
const CAL_MAX = 500
const calTruncated = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  calTruncated.value = false
  try {
    const collected: FieldOperation[] = []
    let offset = 0
    for (;;) {
      const page = await getOperations({
        date_from: monthRange.value.from,
        date_to: monthRange.value.to,
        limit: CAL_PAGE,
        offset,
      })
      collected.push(...page.items)
      offset += page.items.length
      if (collected.length >= page.total || page.items.length < CAL_PAGE) break
      if (collected.length >= CAL_MAX) {
        calTruncated.value = true
        break
      }
    }
    ops.value = collected
    selectedDay.value = null
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const f = await getFields({ status: 'all', limit: 100 })
    fields.value = f.items
  } catch {
    // имена полей — вторичны; сетка работает и по field_id
  }
  await load()
})

watch(month, load)

/** Открытые планы месяца после фильтров (рамка). */
const monthPlans = computed(() =>
  ops.value.filter(
    (o) =>
      !o.fact_date &&
      o.planned_date?.startsWith(month.value) &&
      (typeFilter.value === 'all' || o.operation_type === typeFilter.value),
  ),
)

/** Выполненные операции месяца после фильтров (заливка по fact_date). */
const monthFacts = computed(() =>
  ops.value.filter(
    (o) =>
      o.fact_date?.startsWith(month.value) &&
      (typeFilter.value === 'all' || o.operation_type === typeFilter.value),
  ),
)

/** Записи одного дня: план (открытый) и факт в одной ленте, план сверху. */
function entriesOf(dateIso: string): Array<{ kind: 'plan' | 'fact'; op: FieldOperation }> {
  const out: Array<{ kind: 'plan' | 'fact'; op: FieldOperation }> = []
  if (showMode.value !== 'fact') {
    for (const o of monthPlans.value.filter((o) => o.planned_date === dateIso)) {
      out.push({ kind: 'plan', op: o })
    }
  }
  if (showMode.value !== 'plan') {
    for (const o of monthFacts.value.filter((o) => o.fact_date === dateIso)) {
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

/** Подсказка работы в ячейке (#763/#192): поле + площадь (+ исполнитель
 *  у факта) — полное название при наведении. */
function chipTitle(op: FieldOperation): string {
  const f = fields.value.find((x) => x.field_id === op.field_id)
  const parts = [f ? `${f.name} · ${f.area_ga.toLocaleString('ru-RU')} га` : op.field_id]
  if (op.fact_date && op.performer) parts.push(op.performer)
  return parts.join(' · ')
}

/** Стиль плашки по типу работы (#805/#206): и план, и факт — ЗАЛИВКА цветом
 *  типа (один тип = один цвет в любом месяце); план/факт различаются
 *  рамкой: план — пунктирная бордера типа, факт — сплошная тонкая. */
function chipStyle(type: string, isPlan: boolean): Record<string, string> {
  const c = opTypeColor(type)
  return {
    background: c.bg,
    color: '#263238',
    borderColor: c.border,
    ...(isPlan
      ? { borderStyle: 'dashed', borderWidth: '1.5px' }
      : { borderStyle: 'solid', borderWidth: '1px' }),
  }
}

/** Лента выбранного дня (клик по ячейке). */
const selectedEntries = computed(() => (selectedDay.value ? entriesOf(selectedDay.value) : []))

function shiftMonth(delta: number) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(Date.UTC(y, m - 1 + delta, 1))
  month.value = d.toISOString().slice(0, 7)
}

/** Расходник строки: имя + доза (+ расход всего у факта). */
function inputText(op: FieldOperation): string {
  if (!op.input_name) return ''
  const dose = op.input_dose != null ? ` · ${op.input_dose.toLocaleString('ru-RU')} кг(л)/га` : ''
  const amount =
    op.fact_date && op.input_amount_total != null
      ? ` · всего ${op.input_amount_total.toLocaleString('ru-RU')}`
      : ''
  return ` · ${op.input_name}${dose}${amount}`
}

/** Стоимость строки ленты дня. */
function costText(op: FieldOperation, kind: 'plan' | 'fact'): string {
  const v = kind === 'plan' ? op.cost_plan_rub : op.cost_fact_rub
  return v != null ? ` · ${Math.round(v).toLocaleString('ru-RU')} ₽` : ''
}

/** Техника кратко. */
function machinesText(op: FieldOperation): string {
  const list = op.machines ?? []
  if (!list.length) return ''
  return ` · ${list.map((m) => m.machine_name).join(', ')}`
}

const factCount = computed(() => monthFacts.value.length)
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

    <!-- #806/#206: легенда типов — сверху, рядом с план/факт (один ряд,
         сворачиваемость на узких экранах) -->
    <p class="cal__legend">
      <span class="cal__legend-item"><span class="cal__chip cal__chip--plan">план</span> плановая дата</span>
      <span class="cal__legend-item"><span class="cal__chip cal__chip--fact">факт</span> выполнено</span>
      <template
        v-for="(c, type) in OP_TYPE_COLORS"
        :key="type"
      >
        <span class="cal__legend-item cal__legend-item--type">
          <span
            class="cal__chip"
            :style="{ background: c.bg, borderColor: c.border, color: '#263238' }"
          >{{ c.label }}</span>
        </span>
      </template>
      <span class="cal__muted">{{ monthTitle }}: планов {{ loading ? '…' : planCount }}, фактов {{ loading ? '…' : factCount }}</span>
    </p>

    <p
      v-if="failed"
      class="cal__state cal__state--error"
    >
      Не удалось загрузить календарь работ
    </p>

    <!-- #186: месяц шире 500 операций — показаны первые 500 -->
    <p
      v-if="calTruncated"
      class="cal__state"
    >
      Работ в месяце очень много — показаны первые {{ CAL_MAX }} операций
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
                  <!-- #763/#192: номер дня — выбор дня; работы — ссылки в
                       карточку поля, в чипе тип + номер поля -->
                  <div
                    class="cal__day"
                    :data-date="date"
                  >
                    <button
                      type="button"
                      class="cal__day-num-btn"
                      :aria-label="`Работы за ${date}`"
                      @click="selectedDay = selectedDay === date ? null : date"
                    >
                      {{ Number(date.slice(8)) }}
                    </button>
                    <span class="cal__chips">
                      <Skeleton
                        v-if="loading"
                        width="2.4rem"
                        height="0.65rem"
                        pill
                      />
                      <template v-else>
                        <RouterLink
                          v-for="(e, j) in entriesOf(date).slice(0, 4)"
                          :key="j"
                          class="cal__chip"
                          :class="[
                            e.kind === 'plan' ? 'cal__chip--plan' : 'cal__chip--fact',
                            `cal__chip--type-${e.op.operation_type}`,
                          ]"
                          :style="chipStyle(e.op.operation_type, e.kind === 'plan')"
                          :data-kind="e.kind"
                          :data-field="e.op.field_id"
                          :title="chipTitle(e.op)"
                          :to="{ name: 'field-card', params: { fieldId: e.op.field_id } }"
                        >
                          {{ OP_LABELS[e.op.operation_type] }} ·
                          {{ shortFieldNumber(fieldName(e.op.field_id)) }}
                        </RouterLink>
                        <span
                          v-if="entriesOf(date).length > 4"
                          class="cal__chip cal__chip--more"
                        >+{{ entriesOf(date).length - 4 }}</span>
                      </template>
                    </span>
                  </div>
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
              :class="[
                e.kind === 'plan' ? 'cal__chip--plan' : 'cal__chip--fact',
                `cal__chip--type-${e.op.operation_type}`,
              ]"
              :style="chipStyle(e.op.operation_type, e.kind === 'plan')"
            >{{ e.kind === 'plan' ? 'план' : 'факт' }}</span>
            <RouterLink
              class="cal__field-link"
              :to="{ name: 'field-card', params: { fieldId: e.op.field_id } }"
            >
              {{ fieldName(e.op.field_id) }}
            </RouterLink>
            <span>
              — {{ OP_LABELS[e.op.operation_type] }}{{ inputText(e.op) }}{{
                costText(e.op, e.kind)
              }}<template v-if="e.kind === 'fact' && e.op.performer"> · {{ e.op.performer }}</template>{{
                machinesText(e.op)
              }}
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
  gap: 0.5rem 0.9rem;
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
  /* #192: чипы — ссылки в карточку поля */
  text-decoration: none;
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

/* #790/#203: цвета типов — инлайн через chipStyle (utils/field);
   базовые рамки план/факт остаются, класс типа — для e2e/селекторов */

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
  padding: 0.25rem;
  text-align: left;
  font: inherit;
  box-sizing: border-box;
}

/* #192: номер дня — кнопка выбора дня (чипы-работы — ссылки в карточку) */
.cal__day-num-btn {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  align-self: flex-start;
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
