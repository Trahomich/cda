<script setup lang="ts">
// График NDVI-таймлайна (E-03, v2-fix-B): ось Y фиксирована [0, 1]
// (вегетационный диапазон NDVI), сетка с делениями, значения у точек,
// полоса min–max по сцене, последняя «чистая» точка выделена, облачные
// снимки (cloud_pct > 80, 07-ux §3.2) — пустые кружки ◌ вне линии,
// низкое доверие источника (trust < 0.9) — штрих под точкой.
// SVG без зависимостей; доступность — role="img" + <title> у точек.
import { computed } from 'vue'
import type { NdviPoint } from '@/api/types'

const props = defineProps<{ points: NdviPoint[] }>()

const W = 340
const H = 168
const PAD_L = 30
const PAD_R = 10
const PAD_T = 18
const PAD_B = 24

/** Ось Y — фиксированный вегетационный диапазон NDVI [0, 1]. */
const V_MIN = 0
const V_MAX = 1
const TICKS = [0, 0.25, 0.5, 0.75, 1]

const CLOUD_LIMIT = 80
const TRUST_LIMIT = 0.9

const isCloudy = (p: NdviPoint) => p.cloud_pct > CLOUD_LIMIT

const fmtVal = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
const fmtDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`

/** Шаг подписей дат: при плотном ряде подписываем не каждую дату. */
const dateStep = computed(() => Math.max(1, Math.ceil(props.points.length / 9)))

const x = (i: number, n: number) => PAD_L + (i * (W - PAD_L - PAD_R)) / (n - 1 || 1)
const y = (v: number) =>
  H - PAD_B - ((Math.min(V_MAX, Math.max(V_MIN, v)) - V_MIN) * (H - PAD_T - PAD_B)) / (V_MAX - V_MIN)

/** Последняя «чистая» (не облачная) точка — текущее значение NDVI. */
const lastClearIdx = computed(() => {
  for (let i = props.points.length - 1; i >= 0; i--) {
    if (!isCloudy(props.points[i]!)) return i
  }
  return -1
})

const layout = computed(() => {
  const n = props.points.length
  const clear = props.points
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => !isCloudy(p))

  // Полоса min–max по сценам (там, где индексы есть): верх по index_max,
  // обратный ход по index_min; значение зажимаем в [0, 1].
  const bandTop = props.points.map((p, i) => `${x(i, n).toFixed(1)},${y(p.index_max ?? p.ndvi).toFixed(1)}`)
  const bandBottom = [...props.points]
    .reverse()
    .map((p, ri) => {
      const i = n - 1 - ri
      return `${x(i, n).toFixed(1)},${y(p.index_min ?? p.ndvi).toFixed(1)}`
    })

  return {
    line: clear.map(({ p, i }) => `${x(i, n).toFixed(1)},${y(p.ndvi).toFixed(1)}`).join(' '),
    band: [...bandTop, ...bandBottom].join(' '),
    ticks: TICKS.map((t) => ({ v: t, y: +y(t).toFixed(1), label: fmtVal(t) })),
    dots: props.points.map((p, i) => ({
      cx: +x(i, n).toFixed(1),
      cy: +y(p.ndvi).toFixed(1),
      r: i === lastClearIdx.value ? 4 : 3,
      cloudy: isCloudy(p),
      lowTrust: (p.trust ?? 1) < TRUST_LIMIT,
      last: i === lastClearIdx.value,
      val: fmtVal(p.ndvi),
      date: p.date,
      dateShort: fmtDate(p.date),
      cloudPct: p.cloud_pct,
      trust: p.trust,
    })),
  }
})

const lastPoint = computed(() => props.points[lastClearIdx.value] ?? null)
</script>

<template>
  <div
    v-if="points.length"
    class="ndvi"
  >
    <svg
      class="ndvi__svg"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      aria-label="NDVI-таймлайн сезона"
    >
      <!-- Сетка и ось Y [0, 1] -->
      <g
        v-for="t in layout.ticks"
        :key="t.v"
      >
        <line
          :x1="PAD_L"
          :x2="W - PAD_R"
          :y1="t.y"
          :y2="t.y"
          class="ndvi__grid"
        />
        <text
          :x="PAD_L - 4"
          :y="t.y + 2.5"
          class="ndvi__tick"
          text-anchor="end"
        >{{ t.label }}</text>
      </g>

      <!-- Полоса min–max по сценам -->
      <polygon
        :points="layout.band"
        class="ndvi__band"
      />

      <!-- Линия по «чистым» точкам -->
      <polyline
        :points="layout.line"
        class="ndvi__line"
      />

      <!-- Точки: облачные — ◌, последняя чистая — выделена -->
      <g
        v-for="(dot, i) in layout.dots"
        :key="dot.date"
      >
        <circle
          v-if="dot.last"
          :cx="dot.cx"
          :cy="dot.cy"
          r="7"
          class="ndvi__ring"
        />
        <circle
          :cx="dot.cx"
          :cy="dot.cy"
          :r="dot.r"
          :class="dot.cloudy ? 'ndvi__dot--cloud' : 'ndvi__dot'"
        >
          <title>{{ dot.date }} · NDVI {{ dot.val }} · облачность {{ dot.cloudPct }} %<template v-if="dot.trust != null"> · доверие {{ dot.trust }}</template></title>
        </circle>
        <!-- Низкое доверие источника — штрих под точкой -->
        <line
          v-if="dot.lowTrust"
          :x1="dot.cx - 2.5"
          :x2="dot.cx + 2.5"
          :y1="dot.cy + 6"
          :y2="dot.cy + 6"
          class="ndvi__trust-mark"
        />
        <text
          v-if="i % dateStep === 0 || i === points.length - 1"
          :x="dot.cx"
          :y="H - 8"
          class="ndvi__date"
          text-anchor="middle"
        >{{ dot.dateShort }}</text>
      </g>
    </svg>
    <div class="ndvi__legend">
      <span><i class="ndvi__lg-dot" /> NDVI (средн. по полю)</span>
      <span><i class="ndvi__lg-band" /> min–max</span>
      <span><i class="ndvi__lg-cloud" /> облачный снимок</span>
    </div>
    <p
      v-if="lastPoint"
      class="ndvi__last"
    >
      Последний снимок: {{ fmtDate(lastPoint.date) }} · NDVI {{ fmtVal(lastPoint.ndvi) }}
    </p>
  </div>
  <p
    v-else
    class="ndvi__empty"
  >
    Спутниковых снимков за сезон нет — график появится после первой ясной сцены
  </p>
</template>

<style scoped>
.ndvi {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.ndvi__svg {
  width: 100%;
  height: auto;
}

.ndvi__grid {
  stroke: #e0e0e0;
  stroke-width: 1;
}

.ndvi__tick {
  font-size: 9px;
  fill: #9e9e9e;
}

.ndvi__band {
  fill: rgba(46, 125, 50, 0.14);
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

.ndvi__ring {
  fill: none;
  stroke: #2e7d32;
  stroke-opacity: 0.45;
  stroke-width: 1.5;
}

.ndvi__trust-mark {
  stroke: #ef6c00;
  stroke-width: 1.5;
}

.ndvi__date {
  font-size: 9px;
  fill: #9e9e9e;
}

.ndvi__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.9rem;
  font-size: 0.75rem;
  color: #757575;
}

.ndvi__legend i {
  display: inline-block;
  vertical-align: middle;
  margin-right: 0.25rem;
}

.ndvi__lg-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #2e7d32;
}

.ndvi__lg-band {
  width: 10px;
  height: 8px;
  background: rgba(46, 125, 50, 0.2);
  border: 1px solid rgba(46, 125, 50, 0.35);
}

.ndvi__lg-cloud {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1px dashed #9e9e9e;
  background: #fff;
}

.ndvi__last {
  margin: 0;
  font-size: 0.8rem;
  color: #2e7d32;
  font-weight: 600;
}

.ndvi__empty {
  margin: 0;
  font-size: 0.85rem;
  color: #757575;
}
</style>
