<script setup lang="ts">
// E-13 «Журнал операций» (сценарий SC-03, шаг 6): фильтры поле/даты/тип,
// статусы отклонений — строки с deviation_flag подсвечены, rationale
// (REQ-F-013, RULE-AGRO-11) = сравнение факт. дозы с планом внесения.
// Данные: GET /operations (API-18, пагинация limit/offset), планы — API-15.
// #168 (фидбек #646): фильтр сезона (по умолчанию текущий — не весь архив),
// явные чипы план/факт (пары ±5 дней рядом, план без факта — «ожидается»),
// итоги сезона сверху; при выбранном поле суммы те же, что в экономике
// карточки (API-62).
import { computed, onMounted, ref } from 'vue'
import { getFieldEconomics, getFields, getOperations, getTreatmentPlans } from '@/api'
import type {
  Field,
  FieldEconomics,
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

/* ── Сезон (#168): по умолчанию текущий (год самой свежей операции), ──
 *  выбор проставляет окно дат; «Все сезоны» — без окна. */
const season = ref<number | 'all'>('all')
const seasons = ref<number[]>([])
let seasonsKnown = false

/** Экономика сезона выбранного поля (API-62) — те же суммы, что в карточке. */
const econ = ref<FieldEconomics | null>(null)

const OP_LABELS: Record<string, string> = {
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

/* ── План/факт (#168): план и факт одной строки, даты обе, если разошлись ── */

const fmtD = (iso: string) => iso.split('-').reverse().join('.')

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(a) - Date.parse(b)) / 86_400_000)
}

/** План, сопоставленный факту: то же поле и препарат, даты в ±5 дней. */
function planOf(op: FieldOperation): TreatmentPlan | null {
  if (op.input_id == null) return null
  return (
    plans.value.find(
      (p) =>
        p.field_id === op.field_id &&
        p.input_id === op.input_id &&
        Math.abs(daysBetween(op.date, p.planned_date)) <= 5,
    ) ?? null
  )
}

/** Планы сезона/поля без выполненного факта — отдельные строки «ожидается». */
const unmatchedPlans = computed(() => {
  if (typeof season.value !== 'number') return []
  return plans.value.filter((p) => {
    if (fieldId.value && p.field_id !== fieldId.value) return false
    if (p.planned_date < `${season.value}-01-01` || p.planned_date > `${season.value}-12-31`) {
      return false
    }
    return !items.value.some(
      (o) =>
        o.field_id === p.field_id &&
        o.input_id === p.input_id &&
        Math.abs(daysBetween(o.date, p.planned_date)) <= 5,
    )
  })
})

function fmtRub(v: number): string {
  return `${Math.round(v).toLocaleString('ru-RU')} ₽`
}

/** Стоимость стороны (план/факт) типа операции из экономики поля (API-62);
 *  null — стоимости нет (не задана) или экономика не загружена. */
function econSideCost(type: string, side: 'plan' | 'fact'): number | null {
  const row = econ.value?.operations.find((o) => o.type === type)
  return row?.[side]?.cost_rub ?? null
}

/** Отклонение факт−план из экономики поля: для итогов журнала (#650). */
const econDiff = computed(() =>
  econ.value ? econ.value.fact_cost_rub - econ.value.plan_cost_rub : null,
)

const shownText = computed(
  () => `Показано ${filtered.value.length} из ${total.value}${filtered.value.length !== items.value.length ? ' (после фильтра по типу)' : ''}`,
)

/** Выбор сезона: проставить окно дат (календарный год) и перезагрузить. */
function onSeasonChange(): void {
  if (typeof season.value === 'number') {
    dateFrom.value = `${season.value}-01-01`
    dateTo.value = `${season.value}-12-31`
  } else {
    dateFrom.value = ''
    dateTo.value = ''
  }
  void load(true)
}

/** Итоги сезона: N операций; при выбранном поле — план Σ/факт Σ (API-62). */
async function loadEcon(): Promise<void> {
  if (!fieldId.value || typeof season.value !== 'number') {
    econ.value = null
    return
  }
  try {
    econ.value = await getFieldEconomics(fieldId.value, season.value)
  } catch {
    econ.value = null
  }
}

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
    // Сезоны (#168): список лет архива — один раз, по первому ответу;
    // по умолчанию текущий сезон (год самой свежей операции), не весь архив.
    if (!seasonsKnown && reset) {
      const years = new Set<number>([
        ...page.items.map((o) => Number(o.date.slice(0, 4))),
        ...plans.value.map((p) => p.year),
      ])
      years.delete(NaN)
      seasons.value = [...years].sort((a, b) => b - a)
      const latest = seasons.value[0]
      if (latest != null && dateFrom.value === '' && dateTo.value === '') {
        season.value = latest
        dateFrom.value = `${latest}-01-01`
        dateTo.value = `${latest}-12-31`
        seasonsKnown = true
        // Перезагрузка уже с окном текущего сезона.
        loading.value = false
        await load(true)
        return
      }
      seasonsKnown = true
    }
    void loadEcon()
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
        <span>Сезон</span>
        <select
          v-model.number="season"
          class="jrn__input"
          @change="onSeasonChange"
        >
          <option value="all">
            Все сезоны
          </option>
          <option
            v-for="y in seasons"
            :key="y"
            :value="y"
          >
            {{ y }}
          </option>
        </select>
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
      <!-- Итоги сезона (#168): N операций; при выбранном поле суммы те же,
           что в экономике карточки (API-62) -->
      <p class="jrn__totals">
        <template v-if="typeof season === 'number'">Сезон {{ season }} · </template>
        операций: <strong>{{ total }}</strong>
        <template v-if="econ">
          · план Σ <strong>{{ fmtRub(econ.plan_cost_rub) }}</strong>
          · факт Σ <strong>{{ fmtRub(econ.fact_cost_rub) }}</strong>
          · отклонение
          <strong :class="econDiff != null && econDiff > 0 ? 'jrn__bad' : 'jrn__good'">{{
            econDiff != null && econDiff > 0 ? '+' : ''
          }}{{ fmtRub(Math.abs(econDiff ?? 0)) }}</strong>
          <span
            v-if="econ.fact_cost_rub === 0 && econ.operations.some((o) => o.plan != null)"
            class="jrn__muted"
          >(работы не начаты)</span>
        </template>
        <span
          v-else-if="fieldId && typeof season === 'number'"
          class="jrn__muted"
        >· суммы сезона загружаются…</span>
        <span
          v-else
          class="jrn__muted"
        >· выберите поле — покажу суммы план/факт сезона</span>
      </p>
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
                Статус
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
              <td>
                <!-- #168: факт — зелёный чип; рядом дата плана, если разошлись -->
                <span class="jrn__chip jrn__chip--fact">факт</span>
                <span
                  v-if="planOf(op) && planOf(op)!.planned_date !== op.date"
                  class="jrn__muted"
                >план {{ fmtD(planOf(op)!.planned_date) }}</span>
              </td>
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
            <!-- #168: планы сезона без выполненного факта — «ожидается» -->
            <tr
              v-for="p in unmatchedPlans"
              :key="`plan-${p.plan_id}`"
              class="jrn__row--plan"
            >
              <td>{{ p.planned_date }}</td>
              <td>{{ fieldName(p.field_id) }}</td>
              <td>{{ OP_LABELS[p.operation_type] ?? p.operation_type }}</td>
              <td>
                <span class="jrn__chip jrn__chip--plan">план · ожидается</span>
              </td>
              <td class="jrn__muted">
                {{ p.input_name }} · {{ p.dose.toLocaleString('ru-RU') }} {{ p.unit === 'l_ha' ? 'л/га' : 'кг/га' }}
              </td>
              <td class="jrn__muted">—</td>
              <td class="jrn__muted">—</td>
              <td class="jrn__muted">—</td>
              <td class="jrn__muted">—</td>
            </tr>
            <tr v-if="filtered.length === 0 && unmatchedPlans.length === 0">
              <td
                colspan="9"
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
}

.jrn__footer {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.jrn__more {
  min-height: 44px;
  background: #fff;
  border: 1px solid #2e7d32;
  color: #2e7d32;
  font-weight: 600;
  border-radius: 6px;
  padding: 0 1rem;
  cursor: pointer;
}
</style>
