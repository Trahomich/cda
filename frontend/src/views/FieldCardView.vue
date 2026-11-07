<script setup lang="ts">
// E-03 «Поле 360°» (docs/07-ux §3.2): карточка поля — контур, история
// культур, предшественник ✓/нарушение, NDVI-таймлайн (облачные точки ◌),
// агрохимия, погода сейчас+7 сут (осадки, GDD), планы/журнал.
// Данные: API-05/06/07 + API-18 (журнал поля).
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  getFieldCard,
  getFieldCropCycles,
  getFieldNdvi,
  getFieldWeather,
  getOperations,
  getTreatmentPlans,
} from '@/api'
import type {
  CropCycleHistoryItem,
  FieldCard,
  FieldCycle,
  FieldOperation,
  FieldWeather,
  NdviPoint,
  TreatmentPlan,
} from '@/api/types'
import CropHistoryStrip from '@/components/CropHistoryStrip.vue'
import NdviTimeline from '@/components/NdviTimeline.vue'

const route = useRoute()
const fieldId = route.params.fieldId as string

const card = ref<FieldCard | null>(null)
const cropHistory = ref<CropCycleHistoryItem[]>([])
const ndvi = ref<NdviPoint[]>([])
const weather = ref<FieldWeather | null>(null)
const operations = ref<FieldOperation[]>([])

/** Вкладки карточки (v2): «Обзор» — блоки 360°, «Работы» — план+факт поля. */
const activeTab = ref<'overview' | 'works'>('overview')
/** Вкладка «Работы»: планы API-15 (field_id) + журнал API-18 (field_id). */
const fieldPlans = ref<TreatmentPlan[]>([])
const fieldOperations = ref<FieldOperation[]>([])

/** Лента «Работы»: план и факт одной хронологией, свежие сверху. */
const works = computed(() => {
  const entries: Array<{
    key: string
    kind: 'plan' | 'fact'
    date: string
    plan?: TreatmentPlan
    op?: FieldOperation
  }> = [
    ...fieldPlans.value
      .filter((p) => p.status !== 'cancelled')
      .map((p) => ({ key: `plan-${p.plan_id}`, kind: 'plan' as const, date: p.planned_date, plan: p })),
    ...fieldOperations.value.map((o) => ({
      key: `fact-${o.operation_id}`,
      kind: 'fact' as const,
      date: o.date,
      op: o,
    })),
  ]
  return entries.sort((a, b) => b.date.localeCompare(a.date))
})

const PLAN_STATUS_LABELS: Record<string, string> = {
  draft: 'черновик',
  approved: 'согласован',
  in_progress: 'выполняется',
  completed: 'выполнен',
  cancelled: 'отменён',
}

const OP_LABELS: Record<string, string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seeding: 'посев',
  harvesting: 'уборка',
  tillage: 'обработка почвы',
  other: 'прочее',
}

/** Текущий сезон: item истории севооборота (API-49) со статусом in_progress;
 *  cycles из API-08 содержат только планируемые годы (2027+ в моке),
 *  поэтому текущий год ищем не по реальной дате, а по статусу цикла. */
const currentSeason = computed(
  () =>
    cropHistory.value.find((c) => c.status === 'in_progress') ??
    cropHistory.value.find((c) => c.year === new Date().getFullYear()) ??
    null,
)

const currentCycle = computed(() => {
  const season = currentSeason.value
  const bySeason = season ? card.value?.cycles.find((c) => c.year === season.year) : undefined
  // Поля seasons у API-08 и API-49 совпадают (year/culture_name/kind).
  return bySeason ?? (season as FieldCycle | null) ?? card.value?.cycles[0] ?? null
})

/** Цвет контура (п.5, v2-fix-A): color_hex культуры текущего цикла из
 *  истории севооборота (API-49 отдаёт цвет из справочника API-48);
 *  без культуры/цвета — прежний зелёный. */
const contourColor = computed(
  () => currentSeason.value?.color_hex ?? cropHistory.value.find((c) => c.year === currentCycle.value?.year)?.color_hex ?? null,
)

/** Урожайность по году (из API-05 cycles) — в подсказку ленты API-49. */
const cycleYields = computed(() => {
  const out: Record<number, number> = {}
  for (const c of card.value?.cycles ?? []) {
    if (c.yield_c_ha) out[c.year] = c.yield_c_ha
  }
  return out
})

const lastSatelliteDate = computed(() => {
  const clear = ndvi.value.filter((p) => p.cloud_pct <= 80)
  return clear.length ? clear[clear.length - 1].date : ''
})

/** Контур в координатах SVG viewBox 200×120 (нормализация lon/lat в рамку). */
const contourPoints = computed(() => {
  const ring = card.value?.field.contour.coordinates[0]
  if (!ring || ring.length < 3) return ''
  const lons = ring.map((p) => p[0])
  const lats = ring.map((p) => p[1])
  const minLon = Math.min(...lons)
  const maxLon = Math.max(...lons)
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)
  const sx = 180 / (maxLon - minLon || 1)
  const sy = 100 / (maxLat - minLat || 1)
  const s = Math.min(sx, sy)
  return ring
    .map(([lon, lat]) => {
      const x = 10 + (lon - minLon) * s
      const y = 110 - (lat - minLat) * s
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

/** Загрузка по блокам (v2-fix-E): каждый блок карточки рендерится и
 *  заполняется независимо — структура страницы видна сразу, вместо общего
 *  гейта «Загрузка карточки поля…» скелетоны внутри блоков. Ошибка ядра
 *  (API-05-композит) — сообщение, ошибки блока — внутри блока. */
const coreLoading = ref(true)
const coreFailed = ref(false)
const historyLoading = ref(true)
const ndviLoading = ref(true)
const weatherLoading = ref(true)
const weatherFailed = ref(false)
const journalLoading = ref(true)
const worksLoading = ref(true)

async function loadCore() {
  try {
    card.value = await getFieldCard(fieldId)
  } catch {
    coreFailed.value = true // тост с problem+json уже показал API-клиент
  } finally {
    coreLoading.value = false
  }
}

async function loadHistory() {
  try {
    cropHistory.value = await getFieldCropCycles(fieldId)
  } catch {
    cropHistory.value = []
  } finally {
    historyLoading.value = false
  }
}

async function loadNdvi() {
  try {
    ndvi.value = await getFieldNdvi(fieldId)
  } catch {
    ndvi.value = []
  } finally {
    ndviLoading.value = false
  }
}

async function loadWeather() {
  try {
    weather.value = await getFieldWeather(fieldId)
  } catch {
    weatherFailed.value = true
  } finally {
    weatherLoading.value = false
  }
}

async function loadJournal() {
  try {
    operations.value = (await getOperations({ field_id: fieldId, limit: 5 })).items
  } catch {
    operations.value = []
  } finally {
    journalLoading.value = false
  }
}

async function loadWorks() {
  try {
    // Вкладка «Работы» — планы API-15 (field_id) + полный журнал поля API-18.
    const [wplans, wops] = await Promise.all([
      getTreatmentPlans({ field_id: fieldId }),
      getOperations({ field_id: fieldId, limit: 100 }),
    ])
    fieldPlans.value = wplans.items
    fieldOperations.value = wops.items
  } catch {
    fieldPlans.value = []
    fieldOperations.value = []
  } finally {
    worksLoading.value = false
  }
}

function load() {
  void loadCore()
  void loadHistory()
  void loadNdvi()
  void loadWeather()
  void loadJournal()
  void loadWorks()
}

onMounted(load)

function fmtNum(v: number, digits = 1): string {
  return v.toLocaleString('ru-RU', { maximumFractionDigits: digits })
}
</script>

<template>
  <section class="card360">
    <header class="card360__header">
      <RouterLink
        class="card360__back"
        :to="{ name: 'fields-map' }"
      >
        ‹ Поля
      </RouterLink>
      <h1 class="card360__title">
        <template v-if="card">
          {{ card.field.name }}
          <span class="card360__area">· {{ fmtNum(card.field.area_ga, 2) }} га</span>
        </template>
        <Skeleton
          v-else
          width="11rem"
          height="1.3rem"
          pill
        />
      </h1>
      <button
        type="button"
        class="card360__edit"
        disabled
        title="Правка поля (API-03) — вне экранов MVP-15"
      >
        Изменить ✎
      </button>
    </header>

    <p
      v-if="coreFailed"
      class="card360__state card360__state--error"
    >
      Не удалось загрузить карточку поля
    </p>

    <template v-else>
      <div
        class="card360__tabs"
        role="tablist"
        aria-label="Разделы карточки поля"
      >
        <button
          type="button"
          class="card360__tab"
          :class="{ 'card360__tab--active': activeTab === 'overview' }"
          role="tab"
          :aria-selected="activeTab === 'overview'"
          @click="activeTab = 'overview'"
        >
          Обзор
        </button>
        <button
          type="button"
          class="card360__tab card360__tab--works"
          :class="{ 'card360__tab--active': activeTab === 'works' }"
          role="tab"
          :aria-selected="activeTab === 'works'"
          @click="activeTab = 'works'"
        >
          Работы
        </button>
      </div>

      <!-- Обзор: блоки «Поле 360°» -->
      <div
        v-show="activeTab === 'overview'"
        class="card360__grid"
      >
        <!-- Контур + история культур -->
        <div class="card360__block card360__block--map">
          <h2 class="card360__block-title">
            Контур поля
          </h2>
          <Skeleton
            v-if="!card"
            :lines="3"
          />
          <svg
            v-else
            class="card360__contour"
            viewBox="0 0 200 120"
            role="img"
            aria-label="Контур поля"
          >
            <polygon
              :points="contourPoints"
              class="card360__polygon"
              :style="contourColor ? { fill: contourColor } : undefined"
            />
          </svg>
          <h2 class="card360__block-title">
            Севооборот по годам
          </h2>
          <Skeleton
            v-if="historyLoading"
            height="2.2rem"
            pill
          />
          <CropHistoryStrip
            v-else
            :items="cropHistory"
            :yields="cycleYields"
          />
          <p class="card360__muted">
            Цвет полосы — культура справочника (API-48); пунктир — план, обводка —
            текущий год.
          </p>
          <p
            v-if="currentCycle && card.rotation_check"
            class="card360__check"
            :class="{ 'card360__check--warn': !card.rotation_check.ok }"
          >
            Культура {{ currentCycle.year }}: {{ currentCycle.culture_name }} —
            <template v-if="card.rotation_check.ok">
              ✓ {{ card.rotation_check.message }}
            </template>
            <template v-else>
              ⚠ {{ card.rotation_check.message }}
            </template>
          </p>
        </div>

        <!-- NDVI -->
        <div class="card360__block">
          <h2 class="card360__block-title">
            NDVI-таймлайн (сезон {{ currentCycle?.year ?? '—' }})
          </h2>
          <!-- Пустое состояние («снимков нет») рендерит сам компонент -->
          <NdviTimeline :points="ndvi" />
          <p
            v-if="lastSatelliteDate"
            class="card360__muted"
          >
            Дата последнего снимка: {{ lastSatelliteDate }}
          </p>
        </div>

        <!-- Агрохимия -->
        <div class="card360__block">
          <h2 class="card360__block-title">
            Агрохимия
          </h2>
          <p
            v-if="card.agrochem"
            class="card360__agrochem"
          >
            Проба от {{ card.agrochem.sample_date }}:<br>
            pH {{ fmtNum(card.agrochem.ph) }} · гумус {{ fmtNum(card.agrochem.humus_pct) }} %<br>
            P₂O₅ {{ card.agrochem.p2o5_mg100 }} · K₂O {{ card.agrochem.k2o_mg100 }} мг/100 г<br>
            N {{ fmtNum(card.agrochem.n_mg100) }} мг/100 г
          </p>
          <p
            v-else
            class="card360__muted"
          >
            Данных агрохимии нет (импорт — API-30)
          </p>
        </div>

        <!-- Погода -->
        <div class="card360__block">
          <h2 class="card360__block-title">
            Погода (сейчас + 7 сут)
          </h2>
          <p
            v-if="weather"
            class="card360__weather"
          >
            Сейчас: {{ fmtNum(weather.now.temp_c) }} °C,
            ветер {{ fmtNum(weather.now.wind_ms) }} м/с,
            влажность {{ weather.now.humidity_pct }} %<br>
            Осадки за 7 сут: {{ fmtNum(weather.summary.precip_7d_mm) }} мм ·
            GDD: {{ weather.summary.gdd }} °C·сут
          </p>
          <table
            v-if="weather"
            class="card360__forecast"
          >
            <thead>
              <tr>
                <th scope="col">
                  Дата
                </th>
                <th scope="col">
                  °C
                </th>
                <th scope="col">
                  Осадки, мм
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="day in weather.forecast"
                :key="day.date"
              >
                <td>{{ day.date.slice(5) }}</td>
                <td>{{ day.temp_min_c }}…{{ day.temp_max_c }}</td>
                <td>{{ fmtNum(day.precip_mm) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Планы и журнал -->
        <div class="card360__block card360__block--plans">
          <h2 class="card360__block-title">
            Планы и журнал {{ currentCycle?.year ?? '' }}
          </h2>
          <ul class="card360__plans">
            <li
              v-for="plan in card.treatment_plans"
              :key="plan.plan_id"
            >
              • {{ plan.input_name }} — {{ fmtNum(plan.dose, 2) }}
              {{ plan.unit === 'l_ha' ? 'л/га' : plan.unit === 'kg_ha' ? 'кг/га' : 'кг д.в./га' }},
              {{ plan.planned_date }} — {{ PLAN_STATUS_LABELS[plan.status] ?? plan.status }}
            </li>
            <li
              v-if="card.treatment_plans.length === 0"
              class="card360__muted"
            >
              Планов внесения нет
            </li>
          </ul>
          <h3 class="card360__block-title">
            Журнал (факты)
          </h3>
          <ul class="card360__plans">
            <li
              v-for="op in operations"
              :key="op.operation_id"
            >
              ✓ {{ op.date }} · {{ op.operation_type }} ·
              {{ fmtNum(op.actual_area_ga, 2) }} га
              <span v-if="op.performer">· {{ op.performer }}</span>
            </li>
            <li
              v-if="operations.length === 0"
              class="card360__muted"
            >
              Фактов работ пока нет
            </li>
          </ul>
        </div>
      </div>

      <!-- Работы: планы (API-15 field_id) + журнал (API-18 field_id) одной
           хронологией, свежие сверху (v2, docs/06 §7.2). -->
      <div
        v-if="activeTab === 'works'"
        class="card360__works"
      >
        <h2 class="card360__block-title">
          Работы по полю (план и факт)
        </h2>
        <ul class="card360__works-list">
          <li
            v-for="entry in works"
            :key="entry.key"
            class="card360__works-item"
            :class="entry.kind === 'plan' ? 'card360__works-item--plan' : 'card360__works-item--fact'"
          >
            <span
              class="card360__works-chip"
              :class="entry.kind === 'plan' ? 'card360__works-chip--plan' : 'card360__works-chip--fact'"
            >{{ entry.kind === 'plan' ? 'план' : 'факт' }}</span>
            <span class="card360__works-date">{{ entry.date }}</span>
            <template v-if="entry.kind === 'plan' && entry.plan">
              <span>
                {{ entry.plan.operation_type === 'spraying'
                  ? 'опрыскивание' : entry.plan.operation_type === 'fertilizing'
                    ? 'внесение удобрений' : 'протравливание семян' }} ·
                {{ entry.plan.input_name }} ·
                {{ fmtNum(entry.plan.dose, 2) }}
                {{ entry.plan.unit === 'l_ha' ? 'л/га' : entry.plan.unit === 'kg_ha' ? 'кг/га' : 'кг д.в./га' }}
              </span>
              <span class="card360__works-status">{{ PLAN_STATUS_LABELS[entry.plan.status] ?? entry.plan.status }}</span>
            </template>
            <template v-else-if="entry.op">
              <span>
                {{ OP_LABELS[entry.op.operation_type] }} ·
                {{ fmtNum(entry.op.actual_area_ga, 2) }} га<span v-if="entry.op.actual_dose != null"> ·
                  доза {{ fmtNum(entry.op.actual_dose, 2) }}</span>
              </span>
              <span
                v-if="entry.op.performer"
                class="card360__works-status"
              >{{ entry.op.performer }}</span>
            </template>
          </li>
          <li
            v-if="works.length === 0"
            class="card360__muted"
          >
            Работ по полю пока нет
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>

<style scoped>
.card360__tabs {
  display: flex;
  gap: 0.4rem;
}

.card360__tab {
  background: #fff;
  border: 2px solid #c8e6c9;
  border-radius: 8px 8px 0 0;
  padding: 0.45rem 1rem;
  font-size: 1rem;
  min-height: 44px;
  cursor: pointer;
  color: #1b5e20;
}

.card360__tab--active {
  background: #e8f5e9;
  font-weight: 700;
}

.card360__works {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.card360__works-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.card360__works-item {
  display: flex;
  gap: 0.6rem;
  align-items: baseline;
  flex-wrap: wrap;
  font-size: 0.98rem;
}

.card360__works-chip {
  border-radius: 6px;
  padding: 0.05rem 0.45rem;
  font-size: 0.8rem;
}

.card360__works-chip--plan {
  border: 1.5px dashed #2e7d32;
  color: #1b5e20;
  background: #f1f8f1;
}

.card360__works-chip--fact {
  background: #2e7d32;
  color: #fff;
}

.card360__works-date {
  font-variant-numeric: tabular-nums;
  color: #424242;
}

.card360__works-status {
  color: #757575;
  font-size: 0.9rem;
}

.card360 {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1rem;
}

.card360__header {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.card360__back {
  color: #1b5e20;
  font-weight: 600;
  text-decoration: none;
  min-height: 48px;
  display: inline-flex;
  align-items: center;
}

.card360__back:hover {
  text-decoration: underline;
}

.card360__title {
  font-size: 1.25rem;
  margin: 0;
  flex: 1;
}

.card360__area {
  font-weight: 400;
  color: #424242;
}

.card360__edit {
  background: #fff;
  color: #2e7d32;
  border: 2px solid #2e7d32;
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: not-allowed;
  opacity: 0.6;
}

.card360__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 0.9rem;
  align-items: start;
}

.card360__block {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.card360__block-title {
  font-size: 1rem;
  margin: 0.2rem 0 0.5rem;
  color: #1b5e20;
}

.card360__polygon {
  width: 100%;
  height: auto;
  max-height: 180px;
}

.card360__polygon polygon {
  fill: #a5d6a7;
  stroke: #2e7d32;
  stroke-width: 1.5;
}

.card360__check {
  margin: 0.6rem 0 0;
  padding: 0.5rem 0.6rem;
  border-radius: 8px;
  background: #e8f5e9;
}

.card360__check--warn {
  background: #fff8e1;
}

.card360__muted {
  color: #616161;
  margin: 0.2rem 0;
}

.card360__agrochem,
.card360__weather {
  line-height: 1.6;
}

.card360__forecast {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
}

.card360__forecast th,
.card360__forecast td {
  text-align: left;
  padding: 0.3rem 0.4rem;
  border-bottom: 1px solid #e0e0e0;
}

.card360__plans {
  list-style: none;
  margin: 0 0 0.4rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.card360__state {
  color: #424242;
  padding: 1rem;
}

.card360__state--error {
  color: #b00020;
  font-weight: 600;
}
</style>
