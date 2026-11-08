<script setup lang="ts">
// График истории погоды (фидбек #391 п.2, #134): осадки — столбцами,
// tmax/tmin — линиями. Подход как NdviTimeline (чистый SVG, без библиотек).
import { computed } from 'vue'
import type { WeatherHistoryDay } from '@/api/types'

const props = defineProps<{ days: WeatherHistoryDay[] }>()

const W = 720
const H = 240
const PAD = { top: 16, right: 44, bottom: 26, left: 44 }
const plotW = W - PAD.left - PAD.right
const plotH = H - PAD.top - PAD.bottom

const temps = computed(() =>
  props.days.flatMap((d) => [d.temp_max_c, d.temp_min_c]).filter((t): t is number => t != null),
)

const tMin = computed(() => (temps.value.length ? Math.min(...temps.value, 0) : 0))
const tMax = computed(() => (temps.value.length ? Math.max(...temps.value, 1) : 1))
const pMax = computed(() =>
  Math.max(5, ...props.days.map((d) => d.precip_mm)),
)

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

const tmaxPath = computed(() =>
  props.days
    .map((d, i) => (d.temp_max_c == null ? null : `${x(i)},${yT(d.temp_max_c)}`))
    .filter((p): p is string => p != null)
    .join(' '),
)
const tminPath = computed(() =>
  props.days
    .map((d, i) => (d.temp_min_c == null ? null : `${x(i)},${yT(d.temp_min_c)}`))
    .filter((p): p is string => p != null)
    .join(' '),
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

const MONTHS_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
void MONTHS_RU
</script>

<template>
  <svg
    class="whist"
    :viewBox="`0 0 ${W} ${H}`"
    role="img"
    aria-label="История погоды: осадки и температура"
  >
    <!-- сетка и ось температур -->
    <line
      v-for="t in [tMin, (tMin + tMax) / 2, tMax]"
      :key="t"
      :x1="PAD.left"
      :x2="W - PAD.right"
      :y1="yT(t)"
      :y2="yT(t)"
      class="whist__grid"
    />
    <text
      v-for="t in [tMin, (tMin + tMax) / 2, tMax]"
      :key="`l${t}`"
      :x="PAD.left - 6"
      :y="yT(t) + 4"
      class="whist__tick"
      text-anchor="end"
    >{{ Math.round(t) }}°</text>

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

    <!-- температуры -->
    <polyline
      class="whist__line whist__line--max"
      :points="tmaxPath"
    />
    <polyline
      class="whist__line whist__line--min"
      :points="tminPath"
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

    <!-- легенда -->
    <g
      class="whist__legend"
      transform="translate(8, 4)"
    >
      <rect
        x="0"
        y="-7"
        width="10"
        height="8"
        class="whist__bar"
      />
      <text
        x="14"
        y="0"
        class="whist__tick"
      >осадки, мм (макс {{ Math.round(pMax) }})</text>
      <line
        x1="150"
        y1="-3"
        x2="172"
        y2="-3"
        class="whist__line whist__line--max"
      />
      <text
        x="176"
        y="0"
        class="whist__tick"
      >t дн., °C</text>
      <line
        x1="234"
        y1="-3"
        x2="256"
        y2="-3"
        class="whist__line whist__line--min"
      />
      <text
        x="260"
        y="0"
        class="whist__tick"
      >t ночи, °C</text>
    </g>
  </svg>
</template>

<style scoped>
.whist {
  width: 100%;
  height: auto;
  background: #fff;
  border: 1px solid #d7e0d8;
  border-radius: 10px;
}

.whist__grid {
  stroke: #e4ebe5;
  stroke-width: 1;
}

.whist__tick {
  font-size: 11px;
  fill: #66756a;
}

.whist__bar {
  fill: #4d9fd6;
  fill-opacity: 0.75;
}

.whist__line {
  fill: none;
  stroke-width: 2;
  stroke-linejoin: round;
}

.whist__line--max {
  stroke: #d84315;
}

.whist__line--min {
  stroke: #1e88e5;
}
</style>
