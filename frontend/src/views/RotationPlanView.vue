<script setup lang="ts">
// E-04 «План севооборота» (docs/07-ux §3.3): матрица поле × год, редактор
// клетки (API-11 POST /crop-cycles, запретные предшественники — 422 c
// rule_id), блок «ПРОВЕРКА ПЛАНА» с нарушениями RULE-AGRO-01/02, кнопки
// «Почему?» (rationale) и «Принять ⚠», согласование руководителем (API-10).
// Согласование и правка — по матрице прав 07-ux §5: агроном Ч/Р, руководитель Ч/С.
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { approveRotationPlan, createCropCycle, getRotationPlan } from '@/api'
import type { Culture, RotationPlan } from '@/api/types'

const auth = useAuthStore()

const plan = ref<RotationPlan | null>(null)
const loading = ref(true)
const failed = ref(false)
const saving = ref(false)

/** Клетка в редактировании: `field_id:year`. */
const editing = ref<string | null>(null)
const editCulture = ref('')

/** Принятые агрономом предупреждения (локально, до перезагрузки плана). */
const accepted = ref(new Set<string>())

const canApprove = computed(() => auth.user?.role === 'manager' || auth.user?.role === 'admin')
const canEdit = computed(() => plan.value?.status === 'draft')

const fields = computed(() => {
  if (!plan.value) return []
  const map = new Map<string, { field_id: string; field_name: string; area_ga: number }>()
  for (const cell of plan.value.cells) {
    map.set(cell.field_id, {
      field_id: cell.field_id,
      field_name: cell.field_name,
      area_ga: cell.area_ga,
    })
  }
  return [...map.values()].sort((a, b) => a.field_name.localeCompare(b.field_name, 'ru'))
})

const years = computed(() => {
  if (!plan.value) return []
  const out: number[] = []
  for (let y = plan.value.year_from; y <= plan.value.year_to; y++) out.push(y)
  return out
})

function cellOf(fieldId: string, year: number) {
  return plan.value?.cells.find((c) => c.field_id === fieldId && c.year === year) ?? null
}

/** Нарушение клетки — для маркера ⚠/✕ в матрице. */
function violationOf(fieldId: string, year: number) {
  return (
    plan.value?.violations.find(
      (v) => v.field_id === fieldId && v.year === year && !accepted.value.has(v.violation_id),
    ) ?? null
  )
}

const openViolations = computed(
  () => plan.value?.violations.filter((v) => !accepted.value.has(v.violation_id)) ?? [],
)

/** Развёрнутые rationale (кнопка «Почему?»). */
const explained = ref(new Set<string>())

/** Короткое имя культуры для клетки (без «(сидерат)»). */
function shortName(culture: Culture): string {
  return culture.name.replace(' (сидерат)', '')
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    plan.value = await getRotationPlan()
    accepted.value = new Set()
    explained.value = new Set()
  } catch {
    failed.value = true // тост с problem+json уже показал API-клиент
  } finally {
    loading.value = false
  }
}

function startEdit(fieldId: string, year: number) {
  if (!canEdit.value || saving.value) return
  const cell = cellOf(fieldId, year)
  editing.value = `${fieldId}:${year}`
  editCulture.value = cell?.cycle?.culture_id ?? ''
}

async function saveEdit(fieldId: string, year: number) {
  if (!editCulture.value) {
    editing.value = null
    return
  }
  saving.value = true
  try {
    // API-11: 422 (rule_id=RULE-AGRO-01) отдаётся problem+json — текст
    // покажет API-клиент в тосте, план не меняется. kind — вид выбранной
    // культуры (обязателен по схеме backend, согласуется с RULE-AGRO-03).
    const culture = plan.value?.cultures.find((c) => c.culture_id === editCulture.value)
    await createCropCycle({
      field_id: fieldId,
      year,
      culture_id: editCulture.value,
      kind: culture?.kind ?? 'main',
    })
    editing.value = null
    await load()
  } catch {
    // ошибка уже в тосте; редактирование оставляем открытым
  } finally {
    saving.value = false
  }
}

async function approve() {
  if (!plan.value || !plan.value.draft_plan_ids.length) return
  saving.value = true
  try {
    // API-08 отдаёт по-полевые планы: согласуем каждый черновик (API-10).
    for (const planId of plan.value.draft_plan_ids) {
      await approveRotationPlan(planId)
    }
    await load()
  } catch {
    // тост уже показан
  } finally {
    saving.value = false
  }
}

function accept(violationId: string) {
  accepted.value = new Set([...accepted.value, violationId])
}

function toggleExplain(violationId: string) {
  explained.value = explained.value.has(violationId)
    ? new Set([...explained.value].filter((id) => id !== violationId))
    : new Set([...explained.value, violationId])
}

onMounted(load)
</script>

<template>
  <section class="rotation">
    <header class="rotation__header">
      <h1 class="rotation__title">
        План севооборота · {{ plan?.farm_name || 'демо-хозяйство' }} ·
        горизонт {{ plan?.year_from }}–{{ plan?.year_to }}
        <span
          v-if="plan"
          class="rotation__status"
          :class="{ 'rotation__status--approved': plan.status === 'approved' }"
        >{{ plan.status === 'approved' ? `согласован (${plan.approved_by})` : 'черновик' }}</span>
      </h1>
      <span class="rotation__spacer" />
      <button
        type="button"
        class="rotation__btn"
        :disabled="loading"
        @click="load"
      >
        Проверить план
      </button>
      <button
        v-if="canApprove && plan?.status === 'draft'"
        type="button"
        class="rotation__btn rotation__btn--primary"
        :disabled="saving"
        @click="approve"
      >
        Согласовать ▸
      </button>
    </header>

    <p
      v-if="loading"
      class="rotation__state"
    >
      Загрузка плана…
    </p>
    <p
      v-else-if="failed || !plan"
      class="rotation__state rotation__state--error"
    >
      Не удалось загрузить план севооборота
    </p>

    <template v-else>
      <div class="rotation__table-wrap">
        <table class="rotation__matrix">
          <thead>
            <tr>
              <th scope="col">
                Поле
              </th>
              <th
                v-for="year in years"
                :key="year"
                scope="col"
              >
                {{ year }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="field in fields"
              :key="field.field_id"
            >
              <th scope="row">
                {{ field.field_name }}
                <span class="rotation__area">{{ field.area_ga }} га</span>
              </th>
              <td
                v-for="year in years"
                :key="`${field.field_id}:${year}`"
                class="rotation__cell"
              >
                <template v-if="editing === `${field.field_id}:${year}`">
                  <select
                    v-model="editCulture"
                    class="rotation__select"
                    :aria-label="`Культура ${field.field_name}, ${year}`"
                    @change="saveEdit(field.field_id, year)"
                    @keyup.esc="editing = null"
                  >
                    <option value="">
                      —
                    </option>
                    <option
                      v-for="culture in plan.cultures"
                      :key="culture.culture_id"
                      :value="culture.culture_id"
                    >
                      {{ shortName(culture) }}
                    </option>
                  </select>
                </template>
                <button
                  v-else
                  type="button"
                  class="rotation__culture"
                  :class="{
                    'rotation__culture--warn': violationOf(field.field_id, year)?.severity === 'warning',
                    'rotation__culture--editable': canEdit,
                  }"
                  :disabled="!canEdit"
                  :title="canEdit ? 'Изменить культуру' : 'План закрыт для правки'"
                  @click="startEdit(field.field_id, year)"
                >
                  {{ cellOf(field.field_id, year)?.cycle?.culture_name ?? '—' }}
                  <span v-if="violationOf(field.field_id, year)">⚠</span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="rotation__check">
        <h2 class="rotation__check-title">
          ПРОВЕРКА ПЛАНА · нарушений: {{ openViolations.length }}
        </h2>
        <p
          v-if="openViolations.length === 0"
          class="rotation__ok"
        >
          ✓ Нарушений севооборота нет
        </p>
        <ul class="rotation__violations">
          <li
            v-for="v in openViolations"
            :key="v.violation_id"
            class="rotation__violation"
          >
            <p class="rotation__violation-message">
              <span :class="v.severity === 'error' ? 'rotation__err' : 'rotation__warn'">
                {{ v.severity === 'error' ? '✕' : '⚠' }}
              </span>
              {{ v.message }}
            </p>
            <p
              v-if="explained.has(v.violation_id)"
              class="rotation__rationale"
            >
              {{ v.rationale }}
            </p>
            <div class="rotation__violation-actions">
              <button
                type="button"
                class="rotation__link"
                @click="toggleExplain(v.violation_id)"
              >
                {{ explained.has(v.violation_id) ? 'Скрыть' : 'Почему?' }}
              </button>
              <button
                v-if="v.severity === 'warning'"
                type="button"
                class="rotation__link"
                @click="accept(v.violation_id)"
              >
                Принять ⚠
              </button>
            </div>
          </li>
        </ul>
        <p
          v-if="plan.status === 'draft'"
          class="rotation__hint"
        >
          Запрещённые предшественники блокируются при вводе (RULE-AGRO-01);
          предупреждения о возврате культуры — RULE-AGRO-02.
        </p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.rotation {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1rem;
}

.rotation__header {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  flex-wrap: wrap;
}

.rotation__title {
  font-size: 1.2rem;
  margin: 0;
}

.rotation__status {
  font-size: 0.9rem;
  font-weight: 600;
  color: #8d6e00;
  background: #fff8e1;
  border-radius: 6px;
  padding: 0.15rem 0.5rem;
}

.rotation__status--approved {
  color: #1b5e20;
  background: #e8f5e9;
}

.rotation__spacer {
  flex: 1;
}

.rotation__btn {
  background: #fff;
  color: #2e7d32;
  border: 2px solid #2e7d32;
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.rotation__btn--primary {
  background: #2e7d32;
  color: #fff;
}

.rotation__btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.rotation__table-wrap {
  overflow-x: auto;
}

.rotation__matrix {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
}

.rotation__matrix th,
.rotation__matrix td {
  border: 1px solid #c8e6c9;
  padding: 0.3rem 0.5rem;
  text-align: left;
  font-size: 1rem;
}

.rotation__matrix thead th {
  background: #e8f5e9;
  color: #1b5e20;
}

.rotation__matrix tbody th {
  background: #f9fbe7;
  white-space: nowrap;
}

.rotation__area {
  color: #757575;
  font-weight: 400;
  margin-left: 0.3rem;
}

.rotation__culture {
  background: none;
  border: none;
  padding: 0.45rem 0.4rem;
  font: inherit;
  cursor: default;
  width: 100%;
  text-align: left;
  min-height: 48px;
}

.rotation__culture--editable {
  cursor: pointer;
  border-radius: 6px;
}

.rotation__culture--editable:hover {
  background: #f1f8e9;
}

.rotation__culture--warn {
  background: #fff8e1;
}

.rotation__select {
  font: inherit;
  padding: 0.4rem;
  border: 2px solid #2e7d32;
  border-radius: 6px;
  width: 100%;
  min-height: 48px;
  background: #fff;
}

.rotation__check {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.rotation__check-title {
  font-size: 1rem;
  color: #1b5e20;
  margin: 0 0 0.5rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.rotation__ok {
  color: #1b5e20;
  font-weight: 600;
}

.rotation__violations {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.rotation__violation {
  border-bottom: 1px dashed #e0e0e0;
  padding-bottom: 0.4rem;
}

.rotation__violation-message {
  margin: 0;
}

.rotation__warn {
  color: #b28704;
  font-weight: 700;
}

.rotation__err {
  color: #b00020;
  font-weight: 700;
}

.rotation__rationale {
  margin: 0.3rem 0 0;
  color: #424242;
  background: #f5f5f5;
  border-radius: 8px;
  padding: 0.5rem 0.7rem;
}

.rotation__violation-actions {
  display: flex;
  gap: 1rem;
  margin-top: 0.3rem;
}

.rotation__link {
  background: none;
  border: none;
  color: #1b5e20;
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
  padding: 0.3rem 0;
  font-size: 1rem;
}

.rotation__hint {
  color: #757575;
  font-size: 0.9rem;
  margin: 0.6rem 0 0;
}

.rotation__state {
  color: #424242;
  padding: 1rem;
}

.rotation__state--error {
  color: #b00020;
  font-weight: 600;
}
</style>
