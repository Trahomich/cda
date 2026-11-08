<script setup lang="ts">
// Накопительная кривая GDD с 1 января (фидбек #391 п.4, #134): факт +
// пунктирная линия многолетней нормы при её наличии (GET /weather/gdd, #132).
import { computed } from 'vue'
import type { GddNormPoint, GddPoint } from '@/api/types'

const props = defineProps<{
  items: GddPoint[]
  norm?: GddNormPoint[] | null
}>()

const W = 720
const H = 200
const PAD = { top: 14, right: 52, bottom: 24, left: 52 }
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

const cumPath = computed(() =>
  props.items.map((p, i) => `${x(i, props.items.length)},${y(p.gdd_cum)}`).join(' '),
)

const normPath = computed(() => {
  const norm = props.norm ?? []
  return norm.length ? norm.map((p, i) => `${x(i, norm.length)},${y(p.norm_cum)}`).join(' ') : ''
})

const gridVals = computed(() =>
  [0, 0.5, 1].map((k) => Math.round((maxCum.value * k) / 50) * 50),
)

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
  <svg
    class="gdd"
    :viewBox="`0 0 ${W} ${H}`"
    role="img"
    aria-label="Накопленная сумма эффективных температур с 1 января"
  >
    <line
      v-for="v in gridVals"
      :key="v"
      :x1="PAD.left"
      :x2="W - PAD.right"
      :y1="y(v)"
      :y2="y(v)"
      class="gdd__grid"
    />
    <text
      v-for="v in gridVals"
      :key="`l${v}`"
      :x="PAD.left - 6"
      :y="y(v) + 4"
      class="gdd__tick"
      text-anchor="end"
    >{{ v.toLocaleString('ru-RU') }}</text>

    <polyline
      v-if="normPath"
      class="gdd__line gdd__line--norm"
      :points="normPath"
    />
    <polyline
      class="gdd__line gdd__line--fact"
      :points="cumPath"
    />

    <text
      v-for="m in monthTicks"
      :key="m.label"
      :x="m.px"
      :y="H - 6"
      class="gdd__tick"
      text-anchor="middle"
    >{{ m.label }}</text>

    <g
      transform="translate(8, 4)"
    >
      <line
        x1="0"
        y1="-3"
        x2="22"
        y2="-3"
        class="gdd__line gdd__line--fact"
      />
      <text
        x="26"
        y="0"
        class="gdd__tick"
      >{{ items.at(-1)?.date }} — факт</text>
      <line
        v-if="normPath"
        x1="110"
        y1="-3"
        x2="132"
        y2="-3"
        class="gdd__line gdd__line--norm"
      />
      <text
        v-if="normPath"
        x="136"
        y="0"
        class="gdd__tick"
      >норма (многолетняя)</text>
    </g>
  </svg>
</template>

<style scoped>
.gdd {
  width: 100%;
  height: auto;
  background: #fff;
  border: 1px solid #d7e0d8;
  border-radius: 10px;
}

.gdd__grid {
  stroke: #e4ebe5;
  stroke-width: 1;
}

.gdd__tick {
  font-size: 11px;
  fill: #66756a;
}

.gdd__line {
  fill: none;
  stroke-width: 2.2;
  stroke-linejoin: round;
}

.gdd__line--fact {
  stroke: #2e7d32;
}

.gdd__line--norm {
  stroke: #888;
  stroke-dasharray: 5 4;
  stroke-width: 1.8;
}
</style>
