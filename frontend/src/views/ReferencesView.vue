<script setup lang="ts">
// E-15 «Справочники» (07-ux §2: агрономические справочники; REQ-F-006, 009):
// культуры (справочник ref_culture, API-48) и препараты/удобрения (API-14).
// Нормативы и цены — этап v1+ (docs/12-economics).
import { computed, onMounted, ref } from 'vue'
import { getAgroInputs, getCultureReferences, getRotationPlan } from '@/api'
import type { AgroInput, CultureReference, RotationPlan } from '@/api/types'

const tab = ref<'cultures' | 'inputs'>('cultures')
const cultures = ref<CultureReference[]>([])
const inputs = ref<AgroInput[]>([])
const plan = ref<RotationPlan | null>(null)
const loading = ref(true)
const failed = ref(false)

const KIND_LABELS: Record<CultureReference['kind'], string> = {
  main: 'основная',
  catch: 'промежуточная',
  fallow: 'пар',
}

const UNIT_LABELS: Record<AgroInput['unit'], string> = {
  l_ha: 'л/га',
  kg_ha: 'кг/га',
  kg_dv_ha: 'кг д.в./га',
}

function cropNames(input: AgroInput): string {
  if (input.approved_crops.length === 0) return 'без ограничений'
  return input.approved_crops
    .map((id) => cultures.value.find((c) => c.culture_id === id)?.name ?? '')
    .filter(Boolean)
    .join(', ')
}

/** Норма возврата на то же поле (RULE-AGRO-02) — из плана севооборота,
 *  если он есть; у справочника API-48 этого поля нет, показываем «—». */
function repeatYears(c: CultureReference): string {
  const years = plan.value?.cultures.find((p) => p.culture_id === c.culture_id)?.repeat_years
  return years && years > 0 ? String(years) : '—'
}

const filteredInputs = computed(() => inputs.value)

onMounted(async () => {
  try {
    const [rot, agi] = await Promise.all([getRotationPlan(), getAgroInputs()])
    plan.value = rot
    cultures.value = rot.cultures
    inputs.value = agi.items
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="ref">
    <header class="ref__header">
      <h1 class="ref__title">
        Справочники
      </h1>
      <nav class="ref__tabs">
        <button
          type="button"
          class="ref__tab"
          :class="{ 'ref__tab--active': tab === 'cultures' }"
          @click="tab = 'cultures'"
        >
          Культуры
        </button>
        <button
          type="button"
          class="ref__tab"
          :class="{ 'ref__tab--active': tab === 'inputs' }"
          @click="tab = 'inputs'"
        >
          Препараты и удобрения
        </button>
      </nav>
    </header>

    <p
      v-if="loading"
      class="ref__state"
    >
      Загрузка справочников…
    </p>
    <p
      v-else-if="failed"
      class="ref__state ref__state--error"
    >
      Не удалось загрузить справочники
    </p>

    <template v-else>
      <div
        v-if="tab === 'cultures'"
        class="ref__table-wrap"
      >
        <table class="ref__table">
          <thead>
            <tr>
              <th scope="col">
                Культура
              </th>
              <th scope="col">
                Тип
              </th>
              <th scope="col">
                Возврат на то же поле, лет
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in cultures"
              :key="c.culture_id"
            >
              <td>{{ c.name }}</td>
              <td>{{ KIND_LABELS[c.kind] }}</td>
              <td class="ref__num">
                {{ c.repeat_years === 0 ? '—' : c.repeat_years }}
              </td>
            </tr>
          </tbody>
        </table>
        <p class="ref__note">
          Возврат на то же поле — норма RULE-AGRO-02 (docs/03 §3.1); у паров и
          сидератов ограничений нет. Источник: план севооборота
          {{ plan ? `${plan.year_from}–${plan.year_to}` : '' }}.
        </p>
      </div>

      <div
        v-else
        class="ref__table-wrap"
      >
        <table class="ref__table">
          <thead>
            <tr>
              <th scope="col">
                Название
              </th>
              <th scope="col">
                Вид
              </th>
              <th scope="col">
                Состав / д.в.
              </th>
              <th scope="col">
                Ед. дозы
              </th>
              <th scope="col">
                Срок ожидания, дн.
              </th>
              <th scope="col">
                Допустимые культуры
              </th>
              <th scope="col">
                Лимит обработок/сезон
              </th>
              <th scope="col">
                Ограничения
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="i in filteredInputs"
              :key="i.input_id"
            >
              <td>{{ i.name }}</td>
              <td>{{ i.kind === 'ppp' ? 'СЗР' : 'удобрение' }}</td>
              <td>
                <template v-if="i.npk_content">
                  N{{ i.npk_content[0] }}-P{{ i.npk_content[1] }}-K{{ i.npk_content[2] }}
                </template>
                <template v-else-if="i.active_substance">
                  {{ i.active_substance }}<template v-if="i.concentration">
                    , {{ i.concentration }} %
                  </template>
                </template>
                <template v-else>
                  —
                </template>
              </td>
              <td>{{ UNIT_LABELS[i.unit] }}</td>
              <td class="ref__num">
                {{ i.waiting_days ?? '—' }}
              </td>
              <td>{{ cropNames(i) }}</td>
              <td class="ref__num">
                {{ i.max_treatments_per_season ?? '—' }}
              </td>
              <td>{{ i.restrictions ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="ref__note">
          Сроки ожидания и лимиты контролируются правилами RULE-AGRO-08/10 при
          планировании. Нормативы доз и цены — этап v1+ (docs/12-economics.md).
        </p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ref {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 88rem;
}

.ref__header {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.ref__title {
  font-size: 1.35rem;
  margin: 0;
}

.ref__tabs {
  display: flex;
  gap: 0.4rem;
}

.ref__tab {
  min-height: 44px;
  padding: 0 1rem;
  border: 1px solid #2e7d32;
  background: #fff;
  color: #2e7d32;
  font-weight: 600;
  border-radius: 6px;
  cursor: pointer;
}

.ref__tab--active {
  background: #2e7d32;
  color: #fff;
}

.ref__state {
  color: #555;
}

.ref__state--error {
  color: #b3261e;
}

.ref__table-wrap {
  overflow-x: auto;
}

.ref__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}

.ref__table th,
.ref__table td {
  border: 1px solid #d7e0d8;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.ref__table th {
  background: #eef4ef;
}

.ref__num {
  font-variant-numeric: tabular-nums;
}

.ref__note {
  margin: 0.5rem 0 0;
  color: #555;
  font-size: 0.9rem;
  max-width: 48rem;
}
</style>
