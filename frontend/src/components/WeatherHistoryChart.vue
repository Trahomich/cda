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
    <svg
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

    <!-- легенда под графиком (HTML, как у NdviTimeline) -->
    <div class="whist__legend">
      <span><i class="whist__lg-bar" /> осадки, мм (макс {{ Math.round(pMax) }})</span>
      <span><i class="whist__lg-line whist__lg-line--max" /> t дн., °C</span>
      <span><i class="whist__lg-line whist__lg-line--min" /> t ночи, °C</span>
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
</style>
