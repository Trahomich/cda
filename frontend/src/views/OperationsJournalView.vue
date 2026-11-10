<script setup lang="ts">
// E-13 «Журнал операций» — на ЕДИНОМ источнике §7.10 (#175): одна таблица
// операций с планом и фактом в строке (API-18 ?plan/?fact), фильтры
// сезон/поле/тип/план-факт; итоги сезона сверху — API-62 по полю либо
// API-62b по хозяйству (те же числа, что в экономике карточки поля).
import { computed, onMounted, ref, watch } from 'vue'
import {
  getFieldEconomics,
  getEconomicsSummary,
  getFields,
  getMachines,
  getOperations,
  patchOperation,
} from '@/api'
import type { Field, FieldOperation, Machine, OperationType } from '@/api/types'
import Skeleton from '@/components/Skeleton.vue'

const PAGE_SIZE = 50

const fields = ref<Field[]>([])
const items = ref<FieldOperation[]>([])
const total = ref(0)
const loading = ref(true)
const failed = ref(false)
const busy = ref(false)

/** Сезон: null — все сезоны; default — сезон самой свежей операции. */
const season = ref<number | null>(null)
const seasons = ref<number[]>([])
const fieldId = ref('')
const opType = ref<OperationType | 'all'>('all')
const planFact = ref<'all' | 'plan' | 'fact'>('all')

/** Итоги сезона (план Σ / факт Σ / отклонение). */
const planTotal = ref<number | null>(null)
const factTotal = ref<number | null>(null)

const OP_LABELS: Record<OperationType, string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seed_treatment: 'протравливание семян',
  seeding: 'посев',
  harvesting: 'уборка',
  tillage: 'обработка почвы',
  other: 'прочее',
}

const OP_OPTIONS: Array<{ value: OperationType | 'all'; label: string }> = [
  { value: 'all', label: 'Все типы' },
  ...Object.entries(OP_LABELS).map(([value, label]) => ({ value: value as OperationType, label })),
]

const fieldName = (id: string) => fields.value.find((f) => f.field_id === id)?.name ?? id.slice(0, 8)

function fmtDate(iso: string | null | undefined): string {
  return iso ? iso.split('-').reverse().join('.') : '—'
}

/** Сводки — в тыс ₽ (#707/#180): «1 234 тыс ₽»; до 10 тыс — один знак. */
function fmtThousand(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${(v / 1000).toLocaleString('ru-RU', {
    maximumFractionDigits: v < 10_000 ? 1 : 0,
  })} тыс ₽`
}

/** Статус строки: план (ожидается) / факт (выполнено). */
function rowStatus(op: FieldOperation): { label: string; cls: string } {
  if (op.fact_date) return { label: 'факт', cls: 'jrn__chip--fact' }
  return { label: 'план · ожидается', cls: 'jrn__chip--plan' }
}

/** Расходник: имя + доза (кг/га или л/га) либо расход всего у факта. */
function inputText(op: FieldOperation): string {
  if (!op.input_name) return '—'
  const dose = op.input_dose != null ? ` · ${op.input_dose.toLocaleString('ru-RU')} кг(л)/га` : ''
  const amount =
    op.fact_date && op.input_amount_total != null
      ? ` · всего ${op.input_amount_total.toLocaleString('ru-RU')}`
      : ''
  return op.input_name + dose + amount
}

/** Техника кратко: «МТЗ-1221 +2» с полным списком в title. */
function machinesText(op: FieldOperation): { text: string; title: string } {
  const list = op.machines ?? []
  if (!list.length) return { text: '—', title: '' }
  const first = list[0]!
  const rest = list.length > 1 ? ` +${list.length - 1}` : ''
  const title = list
    .map(
      (m) =>
        `${m.machine_name}${m.trailer_name ? ` + ${m.trailer_name}` : ''}${
          m.operator ? ` · ${m.operator}` : ''
        }${m.hours != null ? ` · ${m.hours} ч` : ''}`,
    )
    .join('; ')
  return { text: `${first.machine_name}${rest}`, title }
}

/** Отклонение факт/план по стоимости (грубая метрика итогов). */
const deviation = computed(() => {
  if (planTotal.value == null || factTotal.value == null || !planTotal.value) return null
  return Math.round(((factTotal.value - planTotal.value) / planTotal.value) * 100)
})

const seasonLabel = computed(() => (season.value ? `Сезон ${season.value}` : 'Все сезоны'))

/* ── Редактирование операции из журнала (#184): план — план-поля,
 *  факт — факт-поля (дата, расход, стоимость, исполнитель, техника). ── */

const machinesRef = ref<Machine[]>([])
const editing = ref<FieldOperation | null>(null)
const editForm = ref({
  date: '',
  input_dose: null as number | null,
  input_amount_total: null as number | null,
  cost_mode: 'unit' as 'unit' | 'total',
  cost_value: null as number | null,
  performer: '',
})
const editMachines = ref<
  Array<{ machine_id: string; trailer_id: string; operator: string; hours: number | null }>
>([])

function startEdit(op: FieldOperation): void {
  editing.value = op
  const isFact = !!op.fact_date
  const cost = isFact ? op.cost_fact_rub : op.cost_plan_rub
  editForm.value = {
    date: (isFact ? op.fact_date : op.planned_date) ?? op.date,
    input_dose: op.input_dose ?? null,
    input_amount_total: op.input_amount_total ?? null,
    cost_mode: 'unit',
    cost_value: cost != null ? Math.round(cost / (op.actual_area_ga || 1)) : null,
    performer: op.performer ?? '',
  }
  editMachines.value = (op.machines ?? []).map((m) => ({
    machine_id: m.machine_id,
    trailer_id: m.trailer_id ?? '',
    operator: m.operator ?? '',
    hours: m.hours ?? null,
  }))
  if (!editMachines.value.length) {
    editMachines.value = [{ machine_id: '', trailer_id: '', operator: '', hours: null }]
  }
}

async function submitEdit(): Promise<void> {
  const op = editing.value
  if (!op || !editForm.value.date) return
  const machines = editMachines.value
    .filter((m) => m.machine_id)
    .map((m) => ({
      machine_id: m.machine_id,
      trailer_id: m.trailer_id || null,
      operator: m.operator || null,
      hours: m.hours,
    }))
  try {
    if (op.fact_date) {
      await patchOperation(op.operation_id, {
        fact_date: editForm.value.date,
        input_dose: editForm.value.input_dose,
        input_amount_total: editForm.value.input_amount_total,
        cost_fact_mode: editForm.value.cost_mode,
        cost_fact_value: editForm.value.cost_value,
        performer: editForm.value.performer || null,
        machines,
      })
    } else {
      await patchOperation(op.operation_id, {
        planned_date: editForm.value.date,
        input_dose: editForm.value.input_dose,
        cost_plan_mode: editForm.value.cost_mode,
        cost_plan_value: editForm.value.cost_value,
        machines,
      })
    }
    editing.value = null
    await load(true)
  } catch {
    /* тост уже показан API-клиентом */
  }
}

async function loadTotals(): Promise<void> {
  planTotal.value = null
  factTotal.value = null
  if (!season.value) return
  try {
    if (fieldId.value) {
      const econ = await getFieldEconomics(fieldId.value, season.value)
      planTotal.value = econ.plan_cost_rub
      factTotal.value = econ.fact_cost_rub
    } else {
      const summary = await getEconomicsSummary(season.value)
      planTotal.value = summary.plan_cost_rub
      factTotal.value = summary.fact_cost_rub
    }
  } catch {
    /* итоги вторичны — список работает */
  }
}

async function load(reset = true) {
  loading.value = true
  failed.value = false
  try {
    if (reset) items.value = []
    const page = await getOperations({
      field_id: fieldId.value || undefined,
      year: season.value ?? undefined,
      type: opType.value === 'all' ? undefined : opType.value,
      plan: planFact.value === 'plan' ? true : undefined,
      fact: planFact.value === 'fact' ? true : undefined,
      limit: PAGE_SIZE,
      offset: reset ? 0 : items.value.length,
    })
    items.value = reset ? page.items : [...items.value, ...page.items]
    total.value = page.total
    // Список сезонов — по первому ответу (без фильтра года), desc.
    if (reset && !seasons.value.length) {
      const years = new Set<number>()
      for (const op of page.items) {
        const d = op.planned_date ?? op.date
        years.add(Number(d.slice(0, 4)))
      }
      seasons.value = [...years].sort((a, b) => b - a)
      if (season.value === null && seasons.value.length) season.value = seasons.value[0]!
    }
    void loadTotals()
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
    const f = await getFields({ status: 'all', limit: 100 })
    fields.value = f.items
  } catch {
    /* фильтр по полю необязателен */
  }
  try {
    machinesRef.value = (await getMachines()).items
  } catch {
    /* форма правки без справочника техники */
  }
  await load(true)
})

watch([season, fieldId, opType, planFact], () => void load(true))
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
        <span>Сезон</span>
        <select
          v-model.number="season"
          class="jrn__input"
        >
          <option :value="null">
            Все сезоны
          </option>
          <option
            v-for="s in seasons"
            :key="s"
            :value="s"
          >
            {{ s }}
          </option>
        </select>
      </label>
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
      <label class="jrn__field">
        <span>Показ</span>
        <select
          v-model="planFact"
          class="jrn__input"
        >
          <option value="all">
            план + факт
          </option>
          <option value="plan">
            только планы
          </option>
          <option value="fact">
            только факты
          </option>
        </select>
      </label>
    </form>

    <div
      v-if="season && planTotal != null"
      class="jrn__totals"
    >
      <span>{{ seasonLabel }} · операций: {{ total }}</span>
      <span class="jrn__total">
        План: <strong>{{ fmtThousand(planTotal) }}</strong>
      </span>
      <span class="jrn__total">
        Факт: <strong>{{ fmtThousand(factTotal) }}</strong>
      </span>
      <span
        v-if="deviation != null"
        class="jrn__deviation"
        :class="deviation > 0 ? 'jrn__deviation--over' : deviation < 0 ? 'jrn__deviation--under' : ''"
      >
        {{ deviation > 0 ? '+' : '' }}{{ deviation }} %
      </span>
    </div>

    <Skeleton
      v-if="loading && !items.length"
      :lines="5"
    />
    <p
      v-else-if="failed"
      class="jrn__state jrn__state--error"
    >
      Не удалось загрузить журнал
    </p>
    <p
      v-else-if="!items.length"
      class="jrn__state"
    >
      Операций по выбранным фильтрам нет
    </p>

    <div
      v-else
      class="jrn__table-wrap"
    >
      <table class="jrn__table">
        <thead>
          <tr>
            <th scope="col">
              Статус
            </th>
            <th scope="col">
              Даты план / факт
            </th>
            <th scope="col">
              Тип
            </th>
            <th scope="col">
              Поле
            </th>
            <th scope="col">
              Расходник · расход
            </th>
            <th scope="col">
              Стоимость план / факт
            </th>
            <th scope="col">
              Исполнитель
            </th>
            <th scope="col">
              Подтв.
            </th>
            <th scope="col">
              Техника
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="op in items"
            :key="op.operation_id"
          >
            <td>
              <span
                class="jrn__chip"
                :class="rowStatus(op).cls"
              >{{ rowStatus(op).label }}</span>
            </td>
            <td class="jrn__num">
              {{ fmtDate(op.planned_date) }} / {{ fmtDate(op.fact_date) }}
            </td>
            <td>{{ OP_LABELS[op.operation_type] ?? op.operation_type }}</td>
            <td>{{ fieldName(op.field_id) }}</td>
            <td>{{ inputText(op) }}</td>
            <td class="jrn__num">
              {{ fmtThousand(op.cost_plan_rub) }} / {{ fmtThousand(op.cost_fact_rub) }}
            </td>
            <td>{{ op.performer ?? '—' }}</td>
            <td>{{ op.confirmed_at ? '✓' : '—' }}</td>
            <td :title="machinesText(op).title">
              {{ machinesText(op).text }}
            </td>
          </tr>
        </tbody>
      </table>
      <p
        v-if="hasMore"
        class="jrn__more"
      >
        <button
          type="button"
          :disabled="busy"
          @click="loadMore"
        >
          {{ busy ? 'Загрузка…' : `Показать ещё (${items.length} из ${total})` }}
        </button>
      </p>
    </div>
  </section>
</template>

<style scoped>
.jrn {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  max-width: 82rem;
}

.jrn__header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.jrn__title {
  font-size: 1.35rem;
  margin: 0;
}

.jrn__filters {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.jrn__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
  color: #55685a;
}

.jrn__input {
  min-height: 40px;
  border: 1px solid #9db59e;
  border-radius: 8px;
  padding: 0.2rem 0.5rem;
  font-size: 0.95rem;
  min-width: 9rem;
}

.jrn__totals {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  background: #f1f6f1;
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.5rem 0.9rem;
  font-size: 0.95rem;
}

.jrn__deviation--over {
  color: #b3261e;
  font-weight: 600;
}

.jrn__deviation--under {
  color: #1b5e20;
  font-weight: 600;
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
  font-size: 0.92rem;
}

.jrn__table th,
.jrn__table td {
  border: 1px solid #d7e0d8;
  padding: 0.4rem 0.55rem;
  text-align: left;
  white-space: nowrap;
}

.jrn__table th {
  background: #eef4ef;
}

.jrn__num {
  font-variant-numeric: tabular-nums;
}

.jrn__chip {
  border-radius: 999px;
  padding: 0.1rem 0.55rem;
  font-size: 0.8rem;
  font-weight: 600;
  white-space: nowrap;
}

.jrn__chip--fact {
  background: #e2f2e4;
  color: #1b5e20;
}

.jrn__chip--plan {
  background: #eceff1;
  color: #546e7a;
}

.jrn__more {
  margin: 0.5rem 0 0;
}

.jrn__more button {
  min-height: 40px;
  padding: 0.2rem 1rem;
  border: 1px solid #9db59e;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}
</style>
