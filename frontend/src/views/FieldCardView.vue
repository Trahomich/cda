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
} from '@/api'
import type {
  CropCycleHistoryItem,
  FieldCard,
  FieldOperation,
  FieldWeather,
  NdviPoint,
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
const loading = ref(true)
const failed = ref(false)

const PLAN_STATUS_LABELS: Record<string, string> = {
  draft: 'черновик',
  approved: 'согласован',
  in_progress: 'выполняется',
  completed: 'выполнен',
  cancelled: 'отменён',
}

const currentCycle = computed(() => {
  const year = new Date().getFullYear()
  return card.value?.cycles.find((c) => c.year === year) ?? card.value?.cycles[0] ?? null
})

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

async function load() {
  loading.value = true
  failed.value = false
  try {
    // Карточка догружает NDVI и погоду отдельными эндпоинтами (API-06/07),
    // журнал поля — API-18 (фильтр field_id, последние записи).
    const [c, h, n, w, ops] = await Promise.all([
      getFieldCard(fieldId),
      getFieldCropCycles(fieldId),
      getFieldNdvi(fieldId),
      getFieldWeather(fieldId),
      getOperations({ field_id: fieldId, limit: 5 }),
    ])
    card.value = c
    cropHistory.value = h
    ndvi.value = n
    weather.value = w
    operations.value = ops.items
  } catch {
    failed.value = true // тост с problem+json уже показал API-клиент
  } finally {
    loading.value = false
  }
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
        {{ card ? card.field.name : 'Поле' }}
        <span
          v-if="card"
          class="card360__area"
        >· {{ fmtNum(card.field.area_ga, 2) }} га</span>
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
      v-if="loading"
      class="card360__state"
    >
      Загрузка карточки поля…
    </p>
    <p
      v-else-if="failed || !card"
      class="card360__state card360__state--error"
    >
      Не удалось загрузить карточку поля
    </p>

    <div
      v-else
      class="card360__grid"
    >
      <!-- Контур + история культур -->
      <div class="card360__block card360__block--map">
        <h2 class="card360__block-title">
          Контур поля
        </h2>
        <svg
          class="card360__contour"
          viewBox="0 0 200 120"
          role="img"
          aria-label="Контур поля"
        >
          <polygon
            :points="contourPoints"
            class="card360__polygon"
          />
        </svg>
        <h2 class="card360__block-title">
          Севооборот по годам
        </h2>
        <CropHistoryStrip
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
        <NdviTimeline
          v-if="ndvi.length"
          :points="ndvi"
        />
        <p
          v-else
          class="card360__muted"
        >
          Спутниковых снимков за сезон нет
        </p>
        <p
          v-if="lastSatelliteDate"
          class="card360__muted"
        >
          Дата последнего снимка: {{ lastSatelliteDate }} · ◌ — облачные точки
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
  </section>
</template>

<style scoped>
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
