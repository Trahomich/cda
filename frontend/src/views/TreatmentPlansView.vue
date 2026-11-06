<script setup lang="ts">
// E-05 «План удобрений и СЗР» (docs/07-ux §2, сценарий SC-02): список планов
// внесения по статусам, создание из справочника препаратов (API-14/15;
// нарушения RULE-AGRO-07/08/10 — 422 c rule_id в тосте), согласование
// руководителем (API-16). Матрица прав 07-ux §5: агроном Ч/Р, руководитель Ч/С.
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  approveTreatmentPlan,
  createTreatmentPlan,
  getAgroInputs,
  getRotationPlan,
  getTreatmentPlans,
} from '@/api'
import type {
  AgroInput,
  PlanWeatherWindow,
  RotationPlan,
  TreatmentPlan,
  TreatmentPlanRequest,
  TreatmentPlanStatus,
} from '@/api/types'

const auth = useAuthStore()

const plans = ref<TreatmentPlan[]>([])
const inputs = ref<AgroInput[]>([])
const plan = ref<RotationPlan | null>(null)
const loading = ref(true)
const failed = ref(false)
const busy = ref(false)

const statusFilter = ref<TreatmentPlanStatus | 'all'>('all')

const STATUS_LABELS: Record<TreatmentPlanStatus, string> = {
  draft: 'черновик',
  approved: 'согласован',
  in_progress: 'выполняется',
  completed: 'выполнен',
  cancelled: 'отменён',
}

const STATUS_OPTIONS: Array<{ value: TreatmentPlanStatus | 'all'; label: string }> = [
  { value: 'all', label: 'Все статусы' },
  { value: 'draft', label: 'Черновики' },
  { value: 'approved', label: 'Согласованные' },
  { value: 'in_progress', label: 'Выполняются' },
  { value: 'completed', label: 'Выполненные' },
  { value: 'cancelled', label: 'Отменённые' },
]

const canApprove = computed(() => auth.user?.role === 'manager' || auth.user?.role === 'admin')
const canCreate = computed(() => auth.user?.role !== 'machine_operator')

const filtered = computed(() =>
  statusFilter.value === 'all'
    ? plans.value
    : plans.value.filter((p) => p.status === statusFilter.value),
)

/** Циклы для выбора: клетки плана севооборота с основной культурой. */
const cycles = computed(() => {
  if (!plan.value) return []
  return plan.value.cells
    .filter((c) => c.cycle && c.cycle.kind === 'main')
    .map((c) => ({
      cycle_id: c.cycle!.cycle_id,
      label: `${c.field_name} · ${c.year} · ${c.cycle!.culture_name}`,
    }))
})

/** Форма создания. */
const form = ref<{
  cycle_id: string
  input_id: string
  dose: string
  operation_type: TreatmentPlanRequest['operation_type']
  planned_date: string
  planned_phase: string
}>({
  cycle_id: '',
  input_id: '',
  dose: '',
  operation_type: 'spraying',
  planned_date: '2027-05-20',
  planned_phase: '',
})

const selectedInput = computed(() => inputs.value.find((i) => i.input_id === form.value.input_id) ?? null)

/** Подсказка ограничений выбранного препарата (07-ux §2: обоснование). */
const inputHint = computed(() => {
  const input = selectedInput.value
  if (!input) return ''
  const parts: string[] = []
  if (input.waiting_days !== null) parts.push(`срок ожидания ${input.waiting_days} дн.`)
  if (input.approved_crops.length > 0) {
    const names = input.approved_crops
      .map((id) => plan.value?.cultures.find((c) => c.culture_id === id)?.name ?? '')
      .filter(Boolean)
    parts.push(`культуры: ${names.join(', ')}`)
  }
  if (input.max_treatments_per_season !== null) parts.push(`макс. обработок за сезон: ${input.max_treatments_per_season}`)
  if (input.restrictions) parts.push(input.restrictions)
  return parts.join(' · ')
})

const UNIT_LABELS: Record<AgroInput['unit'], string> = {
  l_ha: 'л/га',
  kg_ha: 'кг/га',
  kg_dv_ha: 'кг д.в./га',
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    // Планы + справочник препаратов + план севооборота (источник циклов).
    const [tps, agi, rot] = await Promise.all([
      getTreatmentPlans(),
      getAgroInputs(),
      getRotationPlan(),
    ])
    plans.value = tps.items
    inputs.value = agi.items
    plan.value = rot
    if (!form.value.cycle_id && cycles.value.length) form.value.cycle_id = cycles.value[0].cycle_id
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.value.cycle_id || !form.value.input_id || form.value.dose === '') return
  busy.value = true
  try {
    // 422 (RULE-AGRO-07/08/10) придут problem+json — текст в тосте.
    // Для spraying ответ содержит weather_window (WEATHER-WINDOW, §7.2).
    const created = await createTreatmentPlan({
      cycle_id: form.value.cycle_id,
      input_id: form.value.input_id,
      dose: Number(form.value.dose),
      operation_type: form.value.operation_type,
      planned_date: form.value.planned_date,
      planned_phase: form.value.planned_phase || null,
    })
    windowResult.value = { input_name: created.input_name, window: created.weather_window ?? null }
    form.value.dose = ''
    form.value.planned_phase = ''
    await load()
  } catch {
    // тост уже показан
  } finally {
    busy.value = false
  }
}

/* ── WEATHER-WINDOW (docs/06 §7.2): подсказка для планов spraying ── */

/** Результат последнего создания плана spraying: окно на плановую дату. */
const windowResult = ref<{ input_name: string; window: PlanWeatherWindow | null } | null>(null)

/** Подставить предложенную дату в форму («перенести обработку в окно»). */
function applySuggestedDate(date: string) {
  form.value.planned_date = date
}

/* ── Диалог согласования (API-16): подтверждение + окно после approve ── */

const approveTarget = ref<TreatmentPlan | null>(null)
/** weather_window из ответа approve (для spraying), null — нет/не spraying. */
const approveWindow = ref<PlanWeatherWindow | null>(null)
const approveDone = ref(false)

function openApprove(p: TreatmentPlan) {
  approveTarget.value = p
  approveWindow.value = null
  approveDone.value = false
}

async function confirmApprove() {
  if (!approveTarget.value) return
  busy.value = true
  try {
    const approved = await approveTreatmentPlan(approveTarget.value.plan_id)
    approveWindow.value = approved.weather_window ?? null
    approveDone.value = true
    await load()
  } catch {
    // тост уже показан
  } finally {
    busy.value = false
  }
}

function closeApprove() {
  approveTarget.value = null
  approveWindow.value = null
  approveDone.value = false
}

async function approve(planId: string) {
  openApprove(plans.value.find((p) => p.plan_id === planId) ?? null)
}

onMounted(load)
</script>

<template>
  <section class="tp">
    <header class="tp__header">
      <h1 class="tp__title">
        Планы удобрений и СЗР
      </h1>
      <span class="tp__spacer" />
      <label
        class="tp__label"
        for="tp-status"
      >Статус</label>
      <select
        id="tp-status"
        v-model="statusFilter"
        class="tp__select"
      >
        <option
          v-for="opt in STATUS_OPTIONS"
          :key="opt.value"
          :value="opt.value"
        >
          {{ opt.label }}
        </option>
      </select>
    </header>

    <p
      v-if="loading"
      class="tp__state"
    >
      Загрузка планов…
    </p>
    <p
      v-else-if="failed"
      class="tp__state tp__state--error"
    >
      Не удалось загрузить планы внесения
    </p>

    <template v-else>
      <div class="tp__table-wrap">
        <table class="tp__table">
          <thead>
            <tr>
              <th scope="col">
                Поле · цикл
              </th>
              <th scope="col">
                Препарат
              </th>
              <th scope="col">
                Доза
              </th>
              <th scope="col">
                Дата / фаза
              </th>
              <th scope="col">
                Статус
              </th>
              <th
                v-if="canApprove"
                scope="col"
              >
                Действия
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in filtered"
              :key="p.plan_id"
            >
              <td>{{ p.field_name }} · {{ p.year }} · {{ p.culture_name }}</td>
              <td>{{ p.input_name }}</td>
              <td class="tp__num">
                {{ p.dose.toLocaleString('ru-RU') }} {{ UNIT_LABELS[p.unit] }}
              </td>
              <td>
                {{ p.planned_date }}
                <span
                  v-if="p.planned_phase"
                  class="tp__muted"
                >· {{ p.planned_phase }}</span>
              </td>
              <td>
                <span
                  class="tp__badge"
                  :class="`tp__badge--${p.status}`"
                >{{ STATUS_LABELS[p.status] }}</span>
              </td>
              <td v-if="canApprove">
                <button
                  v-if="p.status === 'draft'"
                  type="button"
                  class="tp__approve"
                  :disabled="busy"
                  @click="approve(p.plan_id)"
                >
                  Согласовать
                </button>
                <span
                  v-else
                  class="tp__muted"
                >—</span>
              </td>
            </tr>
            <tr v-if="filtered.length === 0">
              <td
                :colspan="canApprove ? 6 : 5"
                class="tp__state"
              >
                Планов не найдено
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <form
        v-if="canCreate"
        class="tp__form"
        @submit.prevent="submit"
      >
        <h2 class="tp__form-title">
          Новый план внесения
        </h2>
        <div class="tp__form-row">
          <label class="tp__field">
            <span>Цикл (поле · год · культура)</span>
            <select
              v-model="form.cycle_id"
              class="tp__input"
              required
            >
              <option
                v-for="c in cycles"
                :key="c.cycle_id"
                :value="c.cycle_id"
              >
                {{ c.label }}
              </option>
            </select>
          </label>
          <label class="tp__field">
            <span>Препарат / удобрение</span>
            <select
              v-model="form.input_id"
              class="tp__input"
              required
            >
              <option value="">
                — выберите —
              </option>
              <option
                v-for="input in inputs"
                :key="input.input_id"
                :value="input.input_id"
              >
                {{ input.name }} ({{ input.kind === 'ppp' ? 'СЗР' : 'удобрение' }})
              </option>
            </select>
          </label>
        </div>
        <p
          v-if="inputHint"
          class="tp__hint"
        >
          {{ inputHint }}
        </p>
        <div class="tp__form-row">
          <label class="tp__field">
            <span>Доза, {{ selectedInput ? UNIT_LABELS[selectedInput.unit] : 'ед./га' }}</span>
            <input
              v-model="form.dose"
              class="tp__input"
              type="number"
              step="0.01"
              min="0"
              required
            >
          </label>
          <label class="tp__field">
            <span>Тип операции</span>
            <select
              v-model="form.operation_type"
              class="tp__input"
            >
              <option value="spraying">
                опрыскивание
              </option>
              <option value="fertilizing">
                внесение удобрений
              </option>
              <option value="seed_treatment">
                протравливание семян
              </option>
            </select>
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
          <label class="tp__field">
            <span>Фаза (необязательно)</span>
            <input
              v-model="form.planned_phase"
              class="tp__input"
              type="text"
              placeholder="например, кущение"
            >
          </label>
        </div>
        <button
          type="submit"
          class="tp__submit"
          :disabled="busy"
        >
          Создать черновик
        </button>
        <p class="tp__muted">
          Черновик проверяется бизнес-правилами: доза (RULE-AGRO-07), срок
          ожидания до уборки (RULE-AGRO-08), допустимые культуры и лимит
          обработок (RULE-AGRO-10).
        </p>

        <!-- WEATHER-WINDOW (§7.2): подсказка по плану spraying после создания -->
        <div
          v-if="windowResult"
          class="tp__window"
          data-testid="tp-window"
        >
          <div class="tp__window-head">
            <span
              class="tp__badge"
              :class="windowResult.window
                ? (windowResult.window.ok ? 'tp__badge--approved' : 'tp__badge--badwindow')
                : 'tp__badge--draft'"
            >{{ windowResult.window
              ? (windowResult.window.ok ? '✓ окно подходит' : '⚠ окно не подходит')
              : 'окно не оценено' }}</span>
            <span class="tp__muted">погодное окно для «{{ windowResult.input_name }}»</span>
          </div>
          <p
            v-if="windowResult.window"
            class="tp__window-detail"
          >
            {{ windowResult.window.detail }}
          </p>
          <p
            v-else
            class="tp__muted"
          >
            Прогноза на плановую дату нет — окно не оценено (weather_window: null)
          </p>
          <div
            v-if="windowResult.window && windowResult.window.suggested_dates.length"
            class="tp__chips"
          >
            <span class="tp__muted">Подходящие даты:</span>
            <button
              v-for="d in windowResult.window.suggested_dates"
              :key="d"
              type="button"
              class="tp__chip"
              :title="`Подставить ${d} в форму нового плана`"
              @click="applySuggestedDate(d)"
            >
              {{ d }}
            </button>
          </div>
        </div>
      </form>
    </template>

    <!-- Диалог согласования (API-16): подтверждение + погодное окно -->
    <div
      v-if="approveTarget"
      class="tp__dialog-backdrop"
      @click.self="closeApprove"
    >
      <div
        class="tp__dialog"
        role="dialog"
        aria-label="Согласование плана внесения"
        data-testid="tp-approve-dialog"
      >
        <h2 class="tp__form-title">
          Согласование плана внесения
        </h2>
        <p v-if="!approveDone">
          {{ approveTarget.field_name }} · {{ approveTarget.year }} · {{ approveTarget.culture_name }}<br>
          <strong>{{ approveTarget.input_name }}</strong> —
          {{ approveTarget.dose.toLocaleString('ru-RU') }} {{ UNIT_LABELS[approveTarget.unit] }},
          {{ approveTarget.planned_date }}<br>
          <span class="tp__muted">Статус изменится: черновик → согласован.</span>
        </p>
        <template v-else>
          <p>
            План <strong>{{ approveTarget.input_name }}</strong>
            ({{ approveTarget.field_name }}, {{ approveTarget.planned_date }}) согласован.
          </p>
          <div
            v-if="approveTarget.operation_type === 'spraying'"
            class="tp__window"
            data-testid="tp-approve-window"
          >
            <div class="tp__window-head">
              <span
                class="tp__badge"
                :class="approveWindow
                  ? (approveWindow.ok ? 'tp__badge--approved' : 'tp__badge--badwindow')
                  : 'tp__badge--draft'"
              >{{ approveWindow
                ? (approveWindow.ok ? '✓ окно подходит' : '⚠ окно не подходит')
                : 'окно не оценено' }}</span>
              <span class="tp__muted">погодное окно (WEATHER-WINDOW)</span>
            </div>
            <p
              v-if="approveWindow"
              class="tp__window-detail"
            >
              {{ approveWindow.detail }}
            </p>
            <p
              v-else
              class="tp__muted"
            >
              Прогноза на плановую дату нет — окно не оценено
            </p>
            <div
              v-if="approveWindow && approveWindow.suggested_dates.length"
              class="tp__chips"
            >
              <span class="tp__muted">Подходящие даты:</span>
              <button
                v-for="d in approveWindow.suggested_dates"
                :key="d"
                type="button"
                class="tp__chip"
                :title="`Подставить ${d} в форму нового плана`"
                @click="applySuggestedDate(d)"
              >
                {{ d }}
              </button>
            </div>
          </div>
        </template>
        <div class="tp__dialog-actions">
          <button
            v-if="!approveDone"
            type="button"
            class="tp__approve"
            :disabled="busy"
            @click="confirmApprove"
          >
            Согласовать
          </button>
          <button
            type="button"
            class="tp__dialog-close"
            @click="closeApprove"
          >
            {{ approveDone ? 'Закрыть' : 'Отмена' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tp {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1rem;
}

.tp__header {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  flex-wrap: wrap;
}

.tp__title {
  font-size: 1.2rem;
  margin: 0;
}

.tp__spacer {
  flex: 1;
}

.tp__label {
  font-size: 1rem;
}

.tp__select,
.tp__input {
  font-size: 1rem;
  padding: 0.5rem 0.6rem;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  min-height: 48px;
  background: #fff;
  width: 100%;
  box-sizing: border-box;
}

.tp__table-wrap {
  overflow-x: auto;
}

.tp__table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  font-size: 1rem;
}

.tp__table th,
.tp__table td {
  text-align: left;
  padding: 0.55rem 0.7rem;
  border-bottom: 1px solid #e0e0e0;
}

.tp__table th {
  background: #e8f5e9;
  color: #1b5e20;
}

.tp__num {
  font-variant-numeric: tabular-nums;
}

.tp__badge {
  display: inline-block;
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.9rem;
  font-weight: 600;
  background: #eee;
}

.tp__badge--draft {
  background: #f5f5f5;
  color: #424242;
}

.tp__badge--approved {
  background: #e8f5e9;
  color: #1b5e20;
}

.tp__badge--in_progress {
  background: #e3f2fd;
  color: #0d47a1;
}

.tp__badge--completed {
  background: #eceff1;
  color: #263238;
}

.tp__badge--cancelled {
  background: #ffebee;
  color: #b00020;
}

.tp__badge--badwindow {
  background: #fff3e0;
  color: #a15c00;
}

/* WEATHER-WINDOW (§7.2): панель подсказки после создания/согласования */
.tp__window {
  border: 1px dashed #a5d6a7;
  border-radius: 8px;
  background: #f9fbe7;
  padding: 0.55rem 0.7rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.tp__window-head {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  flex-wrap: wrap;
}

.tp__window-detail {
  margin: 0;
  color: #424242;
  font-size: 0.95rem;
}

.tp__chips {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  flex-wrap: wrap;
}

.tp__chip {
  border: 1.5px solid #2e7d32;
  background: #fff;
  color: #1b5e20;
  border-radius: 999px;
  padding: 0.2rem 0.7rem;
  font-size: 0.9rem;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.tp__chip:hover {
  background: #e8f5e9;
}

/* Диалог согласования (API-16) */
.tp__dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 40%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: 1rem;
}

.tp__dialog {
  background: #fff;
  border-radius: 10px;
  padding: 1rem 1.2rem;
  max-width: 34rem;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.tp__dialog-actions {
  display: flex;
  gap: 0.6rem;
  justify-content: flex-end;
}

.tp__dialog-close {
  background: #fff;
  color: #424242;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  font-size: 0.95rem;
  min-height: 44px;
  cursor: pointer;
}

.tp__approve {
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  font-size: 0.95rem;
  font-weight: 600;
  min-height: 44px;
  cursor: pointer;
}

.tp__approve:disabled {
  opacity: 0.6;
}

.tp__form {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.9rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.tp__form-title {
  font-size: 1.05rem;
  margin: 0;
  color: #1b5e20;
}

.tp__form-row {
  display: flex;
  gap: 0.8rem;
  flex-wrap: wrap;
}

.tp__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  flex: 1;
  min-width: 200px;
  font-size: 0.95rem;
  color: #424242;
}

.tp__hint {
  margin: 0;
  color: #616161;
  background: #f9fbe7;
  border-radius: 8px;
  padding: 0.45rem 0.6rem;
  font-size: 0.95rem;
}

.tp__submit {
  align-self: flex-start;
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.6rem 1.2rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.tp__submit:disabled {
  opacity: 0.6;
}

.tp__muted {
  color: #757575;
  font-size: 0.9rem;
}

.tp__state {
  color: #424242;
  text-align: center;
  padding: 1rem 0.75rem;
}

.tp__state--error {
  color: #b00020;
  font-weight: 600;
}
</style>
