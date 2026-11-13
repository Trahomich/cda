<script setup lang="ts">
// График истории погоды (фидбек #391 п.2, #134; полировка #149; #194):
// режим «Воздух» — осадки столбцами, tmax/tmin плавными кривыми;
// режим «Почва» (#767) — температура почвы (левая ось, °C) и влажность
// почвы (правая ось, 0–100 %). Подход как NdviTimeline (чистый SVG).
import { computed, ref } from 'vue'
import type { WeatherHistoryDay } from '@/api/types'
import { continuousSegments, smoothPath } from './chartCurve'

const props = defineProps<{ days: WeatherHistoryDay[] }>()

const W = 720
const H = 220
const PAD = { top: 12, right: 44, bottom: 24, left: 30 }
const plotW = W - PAD.left - PAD.right
const plotH = H - PAD.top - PAD.bottom

/** Режим графика (#194): воздух (осадки+температуры) или почва (T+%). */
const mode = ref<'air' | 'soil'>('air')

const temps = computed(() =>
  props.days.flatMap((d) => [d.temp_max_c, d.temp_min_c]).filter((t): t is number => t != null),
)

const tMin = computed(() => (temps.value.length ? Math.min(...temps.value, 0) : 0))
const tMax = computed(() => (temps.value.length ? Math.max(...temps.value, 1) : 1))
const pMax = computed(() =>
  Math.max(5, ...props.days.map((d) => d.precip_mm)),
)

/** Круглые деления оси температур, как в NdviTimeline. */
const tTicks = computed(() => {
  const lo = Math.ceil(tMin.value / 5) * 5
  const hi = Math.floor(tMax.value / 5) * 5
  const out: number[] = []
  for (let t = lo; t <= hi; t += 5) out.push(t)
  return out
})

function x(i: number): number {
  return props.days.length > 1
    ? PAD.left + (i / (props.days.length - 1)) * plotW
    : PAD.left
}

function yT(v: number): number {
  const span = tMax.value - tMin.value || 1
  return PAD.top + (1 - (v - tMin.value) / span) * plotH
}

function yP(v: number): number {
  return PAD.top + (1 - v / (pMax.value * 1.1)) * plotH
}

/** Плавные кривые температур: отрезки непрерывных данных (null разрывает). */
const tmaxPaths = computed(() =>
  continuousSegments(props.days, (d) => d.temp_max_c, x, yT).map((seg) => smoothPath(seg)),
)
const tminPaths = computed(() =>
  continuousSegments(props.days, (d) => d.temp_min_c, x, yT).map((seg) => smoothPath(seg)),
)

/* ── Линия многолетней нормы осадков (#782/#201): пунктир как у СЭТ
 *  (GddChart norm_cum); null (мало истории) — не рисуем и не показываем. ── */
const normPaths = computed(() =>
  continuousSegments(props.days, (d) => d.norm_mm ?? null, x, yP).map((seg) => smoothPath(seg)),
)
const hasNorm = computed(() => props.days.some((d) => d.norm_mm != null))

/* ── Режим «Почва» (#767/#194): T почвы — левая ось (°C), влажность —
 *  правая ось (0–100 %); null-значения разрывают линии. ── */

const soilVals = computed(() =>
  props.days.flatMap((d) => [d.soil_temp_c ?? null]).filter((v): v is number => v != null),
)
const soilTMin = computed(() => (soilVals.value.length ? Math.min(...soilVals.value, 0) : 0))
const soilTMax = computed(() => (soilVals.value.length ? Math.max(...soilVals.value, 1) : 1))

const soilTTicks = computed(() => {
  const lo = Math.ceil(soilTMin.value / 5) * 5
  const hi = Math.floor(soilTMax.value / 5) * 5
  const out: number[] = []
  for (let t = lo; t <= hi; t += 5) out.push(t)
  return out
})

function yST(v: number): number {
  const span = soilTMax.value - soilTMin.value || 1
  return PAD.top + (1 - (v - soilTMin.value) / span) * plotH
}

/** Влажность почвы: правая ось 0–100 %. */
const soilMTicks = [0, 25, 50, 75, 100]

function ySM(v: number): number {
  return PAD.top + (1 - v / 100) * plotH
}

const soilTPaths = computed(() =>
  continuousSegments(props.days, (d) => d.soil_temp_c ?? null, x, yST).map((seg) => smoothPath(seg)),
)
const soilMPaths = computed(() =>
  continuousSegments(props.days, (d) => d.soil_moisture_pct ?? null, x, ySM).map((seg) => smoothPath(seg)),
)

/** Есть ли почвенные данные за период (иначе режим «Почва» недоступен). */
const soilHasData = computed(() =>
  props.days.some((d) => d.soil_temp_c != null || d.soil_moisture_pct != null),
)

/** Столбец осадков: ширина по числу дней, не больше 8 px. */
const barW = computed(() =>
  Math.min(8, Math.max(1.5, plotW / Math.max(props.days.length, 1) - 1)),
)

/** Метки месяцев: первая дата каждого месяца. */
const monthTicks = computed(() => {
  const out: Array<{ i: number; label: string }> = []
  let prev = ''
  props.days.forEach((d, i) => {
    const m = d.date.slice(0, 7)
    if (m !== prev) {
      const dt = new Date(d.date)
      out.push({
        i,
        label: dt.toLocaleString('ru-RU', { month: 'short' }),
      })
      prev = m
    }
  })
  return out
})
</script>

<template>
  <div
    v-if="days.length"
    class="whist"
  >
    <!-- #194: режим графика — воздух (осадки+температуры) или почва (T+%) -->
    <div
      class="whist__modes"
      role="tablist"
      aria-label="Показатели истории"
    >
      <button
        type="button"
        class="whist__mode"
        :class="{ 'whist__mode--active': mode === 'air' }"
        role="tab"
        :aria-selected="mode === 'air'"
        @click="mode = 'air'"
      >
        Воздух
      </button>
      <button
        type="button"
        class="whist__mode"
        :class="{ 'whist__mode--active': mode === 'soil' }"
        role="tab"
        :aria-selected="mode === 'soil'"
        :disabled="!soilHasData"
        :title="soilHasData ? 'Температура и влажность почвы' : 'нет данных о почве за период'"
        @click="mode = 'soil'"
      >
        Почва
      </button>
    </div>

    <!-- Режим «Воздух»: осадки столбцами, tmax/tmin кривыми -->
    <svg
      v-if="mode === 'air'"
      class="whist__svg"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      aria-label="История погоды: осадки и температура"
    >
      <!-- сетка и ось температур -->
      <g
        v-for="t in tTicks"
        :key="t"
      >
        <line
          :x1="PAD.left"
          :x2="W - PAD.right"
          :y1="yT(t)"
          :y2="yT(t)"
          class="whist__grid"
        />
        <text
          :x="PAD.left - 4"
          :y="yT(t) + 2.5"
          class="whist__tick"
          text-anchor="end"
        >{{ t }}°</text>
      </g>

      <!-- осадки (столбцы) -->
      <rect
        v-for="(d, i) in days"
        :key="d.date"
        class="whist__bar"
        :x="x(i) - barW / 2"
        :y="yP(d.precip_mm)"
        :width="barW"
        :height="Math.max(0, PAD.top + plotH - yP(d.precip_mm))"
      >
        <title>{{ d.date }}: {{ d.precip_mm.toLocaleString('ru-RU') }} мм</title>
      </rect>

      <!-- температуры: плавные кривые, нули (null) разрывают линию -->
      <path
        v-for="(p, i) in tmaxPaths"
        :key="`max${i}`"
        class="whist__line whist__line--max"
        :d="p"
      />
      <path
        v-for="(p, i) in tminPaths"
        :key="`min${i}`"
        class="whist__line whist__line--min"
        :d="p"
      />

      <!-- #201: многолетняя норма осадков — пунктир, как у СЭТ -->
      <path
        v-for="(p, i) in normPaths"
        :key="`norm${i}`"
        class="whist__line whist__line--norm"
        :d="p"
      />

      <!-- месяцы по X -->
      <text
        v-for="m in monthTicks"
        :key="`m${m.i}`"
        :x="x(m.i)"
        :y="H - 8"
        class="whist__tick"
        text-anchor="middle"
      >{{ m.label }}</text>
    </svg>

    <!-- Режим «Почва» (#767): T почвы — левая ось, влажность — правая -->
    <svg
      v-else
      class="whist__svg"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      aria-label="История погоды: температура и влажность почвы"
    >
      <g
        v-for="t in soilTTicks"
        :key="`st${t}`"
      >
        <line
          :x1="PAD.left"
          :x2="W - PAD.right"
          :y1="yST(t)"
          :y2="yST(t)"
          class="whist__grid"
        />
        <text
          :x="PAD.left - 4"
          :y="yST(t) + 2.5"
          class="whist__tick"
          text-anchor="end"
        >{{ t }}°</text>
      </g>
      <!-- правая ось: влажность почвы, % -->
      <text
        v-for="t in soilMTicks"
        :key="`sm${t}`"
        :x="W - PAD.right + 4"
        :y="ySM(t) + 2.5"
        class="whist__tick whist__tick--sm"
        text-anchor="start"
      >{{ t }}%</text>

      <path
        v-for="(p, i) in soilTPaths"
        :key="`st${i}`"
        class="whist__line whist__line--soil-t"
        :d="p"
      />
      <path
        v-for="(p, i) in soilMPaths"
        :key="`sm${i}`"
        class="whist__line whist__line--soil-m"
        :d="p"
      />

      <text
        v-for="m in monthTicks"
        :key="`m${m.i}`"
        :x="x(m.i)"
        :y="H - 8"
        class="whist__tick"
        text-anchor="middle"
      >{{ m.label }}</text>
    </svg>

    <!-- легенда под графиком (HTML, как у NdviTimeline) -->
    <div
      v-if="mode === 'air'"
      class="whist__legend"
    >
      <span><i class="whist__lg-bar" /> осадки, мм (макс {{ Math.round(pMax) }})</span>
      <span><i class="whist__lg-line whist__lg-line--max" /> t дн., °C</span>
      <span><i class="whist__lg-line whist__lg-line--min" /> t ночи, °C</span>
      <span v-if="hasNorm"><i class="whist__lg-line whist__lg-line--norm" /> норма осадков, мм</span>
    </div>
    <div
      v-else
      class="whist__legend"
    >
      <span><i class="whist__lg-line whist__lg-line--soil-t" /> T почвы, °C</span>
      <span><i class="whist__lg-line whist__lg-line--soil-m" /> влажность почвы, %</span>
    </div>
  </div>
</template>

<style scoped>
.whist {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.whist__svg {
  width: 100%;
  height: auto;
  display: block;
}

/* стиль — эталон NdviTimeline: тонкая светлая сетка, серые мелкие подписи */
.whist__grid {
  stroke: #e0e0e0;
  stroke-width: 1;
}

.whist__tick {
  font-size: 9px;
  fill: #9e9e9e;
}

.whist__bar {
  fill: #4d9fd6;
  fill-opacity: 0.75;
}

.whist__line {
  fill: none;
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.whist__line--max {
  stroke: #d84315;
}

.whist__line--min {
  stroke: #1e88e5;
}

/* #201: норма осадков — пунктир, стиль как norm_cum у СЭТ (GddChart) */
.whist__line--norm {
  stroke: #888;
  stroke-dasharray: 5 4;
  stroke-width: 1.8;
}

.whist__lg-line--norm {
  border-top: 2px dashed #888;
}

/* #194: линии почвы — T (коричневая) и влажность (бирюзовая) */
.whist__line--soil-t {
  stroke: #8d6e63;
}

.whist__line--soil-m {
  stroke: #00897b;
}

.whist__tick--sm {
  fill: #00897b;
}

.whist__modes {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.whist__mode {
  border: 1px solid #9db59e;
  border-radius: 999px;
  background: #fff;
  padding: 0.25rem 0.85rem;
  min-height: 34px;
  font-size: 0.9rem;
  cursor: pointer;
}

.whist__mode--active {
  background: #2e7d32;
  border-color: #2e7d32;
  color: #fff;
  font-weight: 600;
}

.whist__mode:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.whist__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.9rem;
  font-size: 0.75rem;
  color: #757575;
}

.whist__legend i {
  display: inline-block;
  vertical-align: middle;
  margin-right: 0.25rem;
}

.whist__lg-bar {
  width: 10px;
  height: 8px;
  border-radius: 2px;
  background: rgba(77, 159, 214, 0.75);
}

.whist__lg-line {
  width: 14px;
  height: 0;
  border-top: 2px solid;
}

.whist__lg-line--max {
  border-color: #d84315;
}

.whist__lg-line--min {
  border-color: #1e88e5;
}

.whist__lg-line--soil-t {
  border-color: #8d6e63;
}

.whist__lg-line--soil-m {
  border-color: #00897b;
}
</style>
