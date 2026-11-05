<script setup lang="ts">
// Мини-график NDVI-таймлайна (E-03): линия по «чистым» точкам, облачные
// снимки (cloud_pct > 80, 07-ux §3.2) показываются пустыми кружками ◌ и в
// линию не входят. SVG, без зависимостей; доступность — role="img".
import { computed } from 'vue'
import type { NdviPoint } from '@/api/types'

const props = defineProps<{ points: NdviPoint[] }>()

const W = 320
const H = 90
const PAD = 6

const isCloudy = (p: NdviPoint) => p.cloud_pct > 80

/** Диапазон дат по всем точкам (ось X равномерна по индексу). */
const layout = computed(() => {
  const n = props.points.length
  const values = props.points.filter((p) => !isCloudy(p)).map((p) => p.ndvi)
  const minV = Math.min(...values, 0)
  const maxV = Math.max(...values, 0.9)
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (n - 1 || 1)
  const y = (v: number) => H - PAD - ((v - minV) * (H - 2 * PAD)) / (maxV - minV || 1)
  const clear = props.points.map((p, i) => ({ p, i })).filter(({ p }) => !isCloudy(p))
  return {
    line: clear.map(({ p, i }) => `${x(i).toFixed(1)},${y(p.ndvi).toFixed(1)}`).join(' '),
    dots: props.points.map((p, i) => ({
      cx: +x(i).toFixed(1),
      cy: +y(isCloudy(p) ? minV + (maxV - minV) * 0.08 : p.ndvi).toFixed(1),
      cloudy: isCloudy(p),
      date: p.date,
      ndvi: p.ndvi,
      cloudPct: p.cloud_pct,
    })),
  }
})
</script>

<template>
  <div class="ndvi">
    <svg
      class="ndvi__svg"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      aria-label="NDVI-таймлайн сезона"
    >
      <polyline
        :points="layout.line"
        class="ndvi__line"
      />
      <circle
        v-for="dot in layout.dots"
        :key="dot.date"
        :cx="dot.cx"
        :cy="dot.cy"
        r="3"
        :class="dot.cloudy ? 'ndvi__dot--cloud' : 'ndvi__dot'"
      >
        <title>{{ dot.date }} · NDVI {{ dot.ndvi }} · облачность {{ dot.cloudPct }} %</title>
      </circle>
    </svg>
    <div class="ndvi__axis">
      <span>{{ points[0]?.date }}</span>
      <span>{{ points[Math.floor(points.length / 2)]?.date }}</span>
      <span>{{ points[points.length - 1]?.date }}</span>
    </div>
  </div>
</template>

<style scoped>
.ndvi {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.ndvi__svg {
  width: 100%;
  height: auto;
}

.ndvi__line {
  fill: none;
  stroke: #2e7d32;
  stroke-width: 2;
}

.ndvi__dot {
  fill: #2e7d32;
}

.ndvi__dot--cloud {
  fill: #fff;
  stroke: #9e9e9e;
  stroke-dasharray: 2 2;
}

.ndvi__axis {
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
  color: #757575;
}
</style>
