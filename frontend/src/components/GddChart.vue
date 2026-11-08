<script setup lang="ts">
// Накопительная кривая GDD с 1 января (фидбек #391 п.4, #134; полировка #149
// п.1–3): факт + пунктирная линия многолетней нормы (GET /weather/gdd, #132).
// Кривые сглажены (Catmull-Rom без искажения данных), стиль — эталон
// NdviTimeline.vue: сетка, серые подписи, HTML-легенда под графиком.
import { computed } from 'vue'
import type { GddNormPoint, GddPoint } from '@/api/types'
import { smoothPath } from './chartCurve'

const props = defineProps<{
  items: GddPoint[]
  norm?: GddNormPoint[] | null
}>()

const W = 720
const H = 190
const PAD = { top: 12, right: 12, bottom: 24, left: 38 }
const plotW = W - PAD.left - PAD.right
const plotH = H - PAD.top - PAD.bottom

const maxCum = computed(() => {
  const vals = [
    ...props.items.map((p) => p.gdd_cum),
    ...(props.norm ?? []).map((p) => p.norm_cum),
  ]
  return Math.max(100, ...vals)
})

function x(i: number, n: number): number {
  return n > 1 ? PAD.left + (i / (n - 1)) * plotW : PAD.left
}

function y(v: number): number {
  return PAD.top + (1 - v / (maxCum.value * 1.05)) * plotH
}

/** Плавная кривая факта; точки данных кривая проходит точно. */
const cumPath = computed(() => {
  const pts = props.items.map((p, i) => ({ x: x(i, props.items.length), y: y(p.gdd_cum) }))
  return smoothPath(pts)
})

const normPath = computed(() => {
  const norm = props.norm ?? []
  if (!norm.length) return ''
  return smoothPath(norm.map((p, i) => ({ x: x(i, norm.length), y: y(p.norm_cum) })))
})

const gridVals = computed(() =>
  [0, 0.5, 1].map((k) => Math.round((maxCum.value * k) / 50) * 50),
)

const lastDate = computed(() => props.items[props.items.length - 1]?.date)

/** Метки месяцев (фиксированная сетка месяцев года). */
const monthTicks = computed(() => {
  const names = ['янв', 'мар', 'май', 'июл', 'сен', 'ноя']
  return names.map((label, k) => ({
    px: PAD.left + (plotW * k) / (names.length - 1),
    label,
  }))
})
</script>

<template>
  <div class="gdd">
    <svg
      class="gdd__svg"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      aria-label="Накопленная сумма эффективных температур с 1 января"
    >
      <g
        v-for="v in gridVals"
        :key="v"
      >
        <line
          :x1="PAD.left"
          :x2="W - PAD.right"
          :y1="y(v)"
          :y2="y(v)"
          class="gdd__grid"
        />
        <text
          :x="PAD.left - 4"
          :y="y(v) + 2.5"
          class="gdd__tick"
          text-anchor="end"
        >{{ v.toLocaleString('ru-RU') }}</text>
      </g>

      <path
        v-if="normPath"
        class="gdd__line gdd__line--norm"
        :d="normPath"
      />
      <path
        class="gdd__line gdd__line--fact"
        :d="cumPath"
      />

      <text
        v-for="m in monthTicks"
        :key="m.label"
        :x="m.px"
        :y="H - 8"
        class="gdd__tick"
        text-anchor="middle"
      >{{ m.label }}</text>
    </svg>

    <!-- легенда под графиком (HTML, как у NdviTimeline) -->
    <div class="gdd__legend">
      <span><i class="gdd__lg-fact" /> факт<template v-if="lastDate"> (на {{ lastDate }})</template></span>
      <span v-if="normPath"><i class="gdd__lg-norm" /> норма (многолетняя)</span>
    </div>
  </div>
</template>

<style scoped>
.gdd {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.gdd__svg {
  width: 100%;
  height: auto;
  display: block;
}

/* стиль — эталон NdviTimeline: тонкая светлая сетка, серые мелкие подписи */
.gdd__grid {
  stroke: #e0e0e0;
  stroke-width: 1;
}

.gdd__tick {
  font-size: 9px;
  fill: #9e9e9e;
}

.gdd__line {
  fill: none;
  stroke-width: 2.2;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.gdd__line--fact {
  stroke: #2e7d32;
}

.gdd__line--norm {
  stroke: #888;
  stroke-dasharray: 5 4;
  stroke-width: 1.8;
}

.gdd__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.9rem;
  font-size: 0.75rem;
  color: #757575;
}

.gdd__legend i {
  display: inline-block;
  vertical-align: middle;
  margin-right: 0.25rem;
}

.gdd__lg-fact {
  width: 14px;
  height: 0;
  border-top: 2.2px solid #2e7d32;
}

.gdd__lg-norm {
  width: 14px;
  height: 0;
  border-top: 2px dashed #888;
}
</style>
