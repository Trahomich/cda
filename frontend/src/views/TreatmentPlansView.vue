<script setup lang="ts">
// E-05 «Планы удобрений и СЗР» — на ЕДИНОМ источнике §7.10 (#175): плановое
// внесение = плановая операция (API-18 ?plan=true, типы spraying/fertilizing/
// seed_treatment). Список: поле · тип · препарат/доза · плановая дата ·
// стоимость плана · переход в карточку. Создание — POST /operations
// (createOperationPlan); approve-флоу API-16 устарел вместе с сущностью.
import { computed, onMounted, ref } from 'vue'
import { createOperationPlan, getAgroInputs, getFields, getOperations } from '@/api'
import type { AgroInput, Field, FieldOperation, OperationType } from '@/api/types'
import Skeleton from '@/components/Skeleton.vue'

const fields = ref<Field[]>([])
const inputs = ref<AgroInput[]>([])
/** Плановые операции внесения (API-18 ?plan=true, §7.10). */
const plans = ref<FieldOperation[]>([])
const loading = ref(true)
const failed = ref(false)
const busy = ref(false)
const created = ref<string | null>(null)
const createError = ref<string | null>(null)

const OP_LABELS: Record<OperationType, string> = {
  spraying: 'Опрыскивание (СЗР)',
  fertilizing: 'Внесение удобрений',
  seed_treatment: 'Протравливание семян',
  seeding: 'Посев',
  harvesting: 'Уборка',
  tillage: 'Обработка почвы',
  other: 'Прочее',
}

/** Типы планов внесения (§7.10): всё, что с расходником. */
const PLAN_TYPES: OperationType[] = ['spraying', 'fertilizing', 'seed_treatment']

/** Форма создания плана (POST /operations, #531). */
const form = ref({
  field_id: '',
  operation_type: 'spraying' as OperationType,
  input_id: '',
  input_dose: '' as string | number,
  planned_date: '',
})

const filteredInputs = computed(() =>
  inputs.value.filter((i) =>
    form.value.operation_type === 'fertilizing' ? i.kind === 'fertilizer' : i.kind === 'ppp',
  ),
)

const fieldName = (id: string) => fields.value.find((f) => f.field_id === id)?.name ?? id.slice(0, 8)

const currentCycle = (id: string) => {
  const c = fields.value.find((f) => f.field_id === id)?.current_cycle
  return c ? `${c.culture_name} (${c.year})` : '—'
}

function fmtDate(iso: string | null | undefined): string {
  return iso ? iso.split('-').reverse().join('.') : '—'
}

function fmtRub(v: number | null | undefined): string {
  return v != null ? `${Math.round(v).toLocaleString('ru-RU')} ₽` : '—'
}

/** Доза с единицей препарата (кг/га · л/га · кг д.в./га). */
function doseText(op: FieldOperation): string {
  if (!op.input_name) return '—'
  if (op.input_dose == null) return op.input_name
  const input = inputs.value.find((i) => i.name === op.input_name)
  const unit = input?.unit === 'l_ha' ? 'л/га' : input?.unit === 'kg_dv_ha' ? 'кг д.в./га' : 'кг/га'
  return `${op.input_name} · ${Number(op.input_dose).toLocaleString('ru-RU')} ${unit}`
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const [f, page] = await Promise.all([
      getFields({ status: 'active', limit: 100 }),
      getOperations({ plan: true, limit: 100 }),
    ])
    fields.value = f.items
    // В списке планов внесения — только типы с расходником (§7.10).
    plans.value = page.items.filter((o) => PLAN_TYPES.includes(o.operation_type))
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

async function submitPlan() {
  createError.value = null
  created.value = null
  busy.value = true
  try {
    const op = await createOperationPlan({
      field_id: form.value.field_id,
      operation_type: form.value.operation_type,
      planned_date: form.value.planned_date,
      input_id: form.value.input_id || null,
      input_dose: form.value.input_dose === '' ? null : Number(form.value.input_dose),
    })
    created.value = `План создан: ${OP_LABELS[op.operation_type]} · ${fmtDate(op.planned_date)} (${fmtRub(op.cost_plan_rub)})`
    form.value.input_id = ''
    form.value.input_dose = ''
    await load()
  } catch {
    createError.value = 'Не удалось создать план — проверьте поля (нарушение правил покажет тост)'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    const page = await getAgroInputs()
    inputs.value = page.items
  } catch {
    /* создание недоступно, список работает */
  }
  await load()
})
</script>

<template>
  <section class="tp">
    <header class="tp__header">
      <h1 class="tp__title">
        Планы удобрений и СЗР
      </h1>
      <p class="tp__note">
        Плановое внесение — плановая операция (§7.10): выполняется и считается в экономике
        вместе с остальными работами поля.
      </p>
    </header>

    <!-- Создание плана: POST /operations (единая операция) -->
    <form
      class="tp__form"
      @submit.prevent="submitPlan"
    >
      <label class="tp__field">
        <span>Поле</span>
        <select
          v-model="form.field_id"
          class="tp__input"
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
            {{ f.name }}
          </option>
        </select>
      </label>
      <label class="tp__field">
        <span>Тип</span>
        <select
          v-model="form.operation_type"
          class="tp__input"
        >
          <option
            v-for="t in PLAN_TYPES"
            :key="t"
            :value="t"
          >
            {{ OP_LABELS[t] }}
          </option>
        </select>
      </label>
      <label class="tp__field">
        <span>Препарат / удобрение</span>
        <select
          v-model="form.input_id"
          class="tp__input"
        >
          <option value="">
            Без расходника
          </option>
          <option
            v-for="i in filteredInputs"
            :key="i.input_id"
            :value="i.input_id"
          >
            {{ i.name }}
          </option>
        </select>
      </label>
      <label class="tp__field">
        <span>Доза, кг(л)/га</span>
        <input
          v-model="form.input_dose"
          class="tp__input"
          type="number"
          inputmode="decimal"
          min="0"
          step="0.1"
        >
      </label>
      <label class="tp__field">
        <span>Плановая дата</span>
        <input
          v-model="form.planned_date"
          class="tp__input"
          type="date"
          required
        >
      </label>
      <button
        type="submit"
        class="tp__submit"
        :disabled="busy || !form.field_id || !form.planned_date"
      >
        {{ busy ? 'Создаём…' : 'Создать план' }}
      </button>
    </form>
    <p
      v-if="created"
      class="tp__ok"
      role="status"
    >
      {{ created }}
    </p>
    <p
      v-if="createError"
      class="tp__err"
      role="alert"
    >
      {{ createError }}
    </p>

    <Skeleton
      v-if="loading"
      :lines="4"
    />
    <p
      v-else-if="failed"
      class="tp__state tp__state--error"
    >
      Не удалось загрузить планы
    </p>
    <p
      v-else-if="!plans.length"
      class="tp__state"
    >
      Плановых операций внесения пока нет — создайте первый выше
    </p>

    <div
      v-else
      class="tp__table-wrap"
    >
      <table class="tp__table">
        <thead>
          <tr>
            <th scope="col">
              Плановая дата
            </th>
            <th scope="col">
              Тип
            </th>
            <th scope="col">
              Поле · культура сезона
            </th>
            <th scope="col">
              Препарат · доза
            </th>
            <th scope="col">
              Стоимость плана
            </th>
            <th scope="col">
              Факт
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="op in plans"
            :key="op.operation_id"
          >
            <td class="tp__num">
              <RouterLink
                class="tp__field-link"
                :to="{ name: 'field-card', params: { fieldId: op.field_id } }"
              >
                {{ fmtDate(op.planned_date) }}
              </RouterLink>
            </td>
            <td>{{ OP_LABELS[op.operation_type] }}</td>
            <td>{{ fieldName(op.field_id) }} · {{ currentCycle(op.field_id) }}</td>
            <td>{{ doseText(op) }}</td>
            <td class="tp__num">
              {{ fmtRub(op.cost_plan_rub) }}
            </td>
            <td>
              <span
                v-if="op.fact_date"
                class="tp__chip tp__chip--done"
              >выполнено {{ fmtDate(op.fact_date) }}</span>
              <span
                v-else
                class="tp__chip"
              >ожидается</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.tp {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  max-width: 78rem;
}

.tp__title {
  font-size: 1.35rem;
  margin: 0;
}

.tp__note {
  margin: 0.2rem 0 0;
  color: #55685a;
  font-size: 0.9rem;
}

.tp__form {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
  align-items: flex-end;
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.7rem 0.9rem;
  background: #f7faf7;
}

.tp__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.82rem;
  color: #55685a;
}

.tp__input {
  min-height: 40px;
  border: 1px solid #9db59e;
  border-radius: 8px;
  padding: 0.2rem 0.5rem;
  font-size: 0.95rem;
  min-width: 8.5rem;
}

.tp__submit {
  min-height: 42px;
  padding: 0.25rem 1.1rem;
  border-radius: 8px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: #fff;
  font-size: 0.95rem;
  cursor: pointer;
}

.tp__submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tp__ok {
  margin: 0;
  color: #1b5e20;
  background: #e2f2e4;
  border-radius: 8px;
  padding: 0.4rem 0.7rem;
}

.tp__err {
  margin: 0;
  color: #b3261e;
  font-size: 0.9rem;
}

.tp__state {
  color: #555;
}

.tp__state--error {
  color: #b3261e;
}

.tp__table-wrap {
  overflow-x: auto;
}

.tp__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
  font-size: 0.92rem;
}

.tp__table th,
.tp__table td {
  border: 1px solid #d7e0d8;
  padding: 0.4rem 0.55rem;
  text-align: left;
  white-space: nowrap;
}

.tp__table th {
  background: #eef4ef;
}

.tp__num {
  font-variant-numeric: tabular-nums;
}

.tp__field-link {
  color: #2e7d32;
  font-weight: 600;
  text-decoration: none;
}

.tp__field-link:hover {
  text-decoration: underline;
}

.tp__chip {
  border-radius: 999px;
  padding: 0.1rem 0.55rem;
  font-size: 0.8rem;
  background: #eceff1;
  color: #546e7a;
  font-weight: 600;
}

.tp__chip--done {
  background: #e2f2e4;
  color: #1b5e20;
}
</style>
