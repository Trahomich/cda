<script setup lang="ts">
// График временного ряда индекса (E-03, v2-fix-B; #151 — обобщение на все
// индексы API-06): ось Y — фиксированный вегетационный диапазон [0, 1] для
// NDVI (как раньше) или масштаб по фактическому диапазону выбранного индекса
// (значения + полоса min–max по сцене), сетка с делениями (значения у точек
// не подписываем — v2-fix-G, только подсказка <title> и строка «Последний
// снимок»), полоса min–max по сцене, последняя «чистая» точка выделена,
// облачные снимки (cloud_pct > 80, 07-ux §3.2) — пустые кружки ◌ вне линии,
// низкое доверие источника (trust < 0.9) — штрих под точкой.
// #544: если свежей сцены на «сегодня» нет (asOf), под строкой «Последний
// снимок» — явная подпись «нет данных на …, показано за …».
// Интерактивность (v2-fix-H): точки дат с PNG-снимком (selectable) кликабельны —
// событие select выбирает дату слоя мини-карты; выбранная точка выделена.
// SVG без зависимостей; доступность — role="img" + <title> у точек.
import { computed } from 'vue'
import type { NdviPoint, SceneIndex } from '@/api/types'

const props = defineProps<{
  points: NdviPoint[]
  /** Даты, у которых есть PNG-рендер (§7.3) — кликабельны на графике. */
  selectable?: string[]
  /** Выбранная дата слоя (выделяется на графике). */
  selected?: string
  /** Индекс ряда (#151): NDVI — ось [0, 1]; прочие — по данным сезона. */
  index?: SceneIndex
  /** Короткая подпись индекса (NDVI/NDRE/…); по умолчанию — из index. */
  label?: string
  /** Русское название индекса (как у слоёв мини-карты #130). */
  hint?: string
  /** «Сегодня» (ISO-дата) для подписи «нет данных на …» (#544). */
  asOf?: string
}>()

const emit = defineEmits<{ (e: 'select', date: string): void }>()

const W = 340
const H = 168
const PAD_L = 30
const PAD_R = 10
const PAD_T = 18
const PAD_B = 24

const CLOUD_LIMIT = 80
const TRUST_LIMIT = 0.9

const LABEL = computed(() => props.label ?? (props.index ?? 'ndvi').toUpperCase())

const isCloudy = (p: NdviPoint) => p.cloud_pct > CLOUD_LIMIT

const fmtVal = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
const fmtDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`

/** Масштаб оси Y (#151): NDVI — фиксированный вегетационный диапазон [0, 1]
 *  (прежнее поведение); прочие индексы — по фактическому диапазону данных
 *  (значения точек и полосы min–max) с запасом и круглыми делениями. */
const scale = computed(() => {
  if ((props.index ?? 'ndvi') === 'ndvi') {
    return { min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1] }
  }
  const vals: number[] = []
  for (const p of props.points) {
    vals.push(p.ndvi)
    if (p.index_min != null) vals.push(p.index_min)
    if (p.index_max != null) vals.push(p.index_max)
  }
  if (!vals.length) return { min: 0, max: 1, ticks: [0, 0.5, 1] }
  let lo = Math.min(...vals)
  let hi = Math.max(...vals)
  if (hi - lo < 0.15) {
    const mid = (hi + lo) / 2
    lo = mid - 0.1
    hi = mid + 0.1
  }
  const span = hi - lo
  const step = span <= 0.6 ? 0.1 : span <= 1.5 ? 0.2 : 0.5
  const min = Math.floor((lo - span * 0.1) / step) * step
  const max = Math.ceil((hi + span * 0.1) / step) * step
  const ticks: number[] = []
  for (let t = min; t <= max + 1e-9; t += step) ticks.push(+t.toFixed(2))
  return { min, max, ticks }
})

const yMin = computed(() => scale.value.min)
const yMax = computed(() => scale.value.max)

/** API-06 отдаёт сцены по убыванию (desc); график — время слева-направо,
 *  поэтому defensive-сортировка по возрастанию даты (#142). */
const sorted = computed(() => [...props.points].sort((a, b) => a.date.localeCompare(b.date)))

/** Шаг подписей дат: при плотном ряде подписываем не каждую дату. */
const dateStep = computed(() => Math.max(1, Math.ceil(sorted.value.length / 9)))

const x = (i: number, n: number) => PAD_L + (i * (W - PAD_L - PAD_R)) / (n - 1 || 1)
const y = (v: number) =>
  H - PAD_B - ((Math.min(yMax.value, Math.max(yMin.value, v)) - yMin.value) * (H - PAD_T - PAD_B)) / (yMax.value - yMin.value || 1)

/** Последняя «чистая» (не облачная) точка — текущее значение индекса. */
const lastClearIdx = computed(() => {
  for (let i = sorted.value.length - 1; i >= 0; i--) {
    if (!isCloudy(sorted.value[i]!)) return i
  }
  return -1
})

const layout = computed(() => {
  const pts = sorted.value
  const n = pts.length
  const clear = pts
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => !isCloudy(p))

  // Полоса min–max по сценам (там, где индексы есть): верх по index_max,
  // обратный ход по index_min; значение зажимаем в масштаб оси.
  const bandTop = pts.map((p, i) => `${x(i, n).toFixed(1)},${y(p.index_max ?? p.ndvi).toFixed(1)}`)
  const bandBottom = [...pts]
    .reverse()
    .map((p, ri) => {
      const i = n - 1 - ri
      return `${x(i, n).toFixed(1)},${y(p.index_min ?? p.ndvi).toFixed(1)}`
    })

  return {
    line: clear.map(({ p, i }) => `${x(i, n).toFixed(1)},${y(p.ndvi).toFixed(1)}`).join(' '),
    band: [...bandTop, ...bandBottom].join(' '),
    ticks: scale.value.ticks.map((t) => ({ v: t, y: +y(t).toFixed(1), label: fmtVal(t) })),
    dots: pts.map((p, i) => ({
      cx: +x(i, n).toFixed(1),
      cy: +y(p.ndvi).toFixed(1),
      r: i === lastClearIdx.value ? 4 : 3,
      cloudy: isCloudy(p),
      lowTrust: (p.trust ?? 1) < TRUST_LIMIT,
      last: i === lastClearIdx.value,
      val: fmtVal(p.ndvi),
      selectable: (props.selectable ?? []).includes(p.date),
      selected: props.selected != null && props.selected === p.date,
      date: p.date,
      // Короткая подпись DD.MM; первая точка — с годом (DD.MM.YY): сезон озимых
      // пересекает календарный год, без года подписи читаются перемешанными.
      dateShort:
        i === 0
          ? `${p.date.slice(8, 10)}.${p.date.slice(5, 7)}.${p.date.slice(2, 4)}`
          : `${p.date.slice(8, 10)}.${p.date.slice(5, 7)}`,
      cloudPct: p.cloud_pct,
      trust: p.trust,
    })),
  }
})

const lastPoint = computed(() => sorted.value[lastClearIdx.value] ?? null)

/** #544: сцены на «сегодня» нет — явная подпись, какой датой показано. */
const asOfNote = computed(() => {
  if (!props.asOf || !lastPoint.value || lastPoint.value.date === props.asOf) return ''
  return `нет данных на ${fmtDate(props.asOf)}, показано за ${fmtDate(lastPoint.value.date)}`
})
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
      :aria-label="`${LABEL}-таймлайн сезона`"
    >
      <!-- Сетка и ось Y (NDVI — [0, 1]; прочие — по диапазону данных, #151) -->
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

      <!-- Точки: облачные — ◌, последняя чистая — выделена; даты со снимком
           кликабельны (v2-fix-H) и обведены, выбранная — синим кольцом -->
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
          v-if="dot.selected"
          :cx="dot.cx"
          :cy="dot.cy"
          r="6.5"
          class="ndvi__ring--selected"
        />
        <circle
          :cx="dot.cx"
          :cy="dot.cy"
          :r="dot.r"
          :class="[
            dot.cloudy ? 'ndvi__dot--cloud' : 'ndvi__dot',
            dot.selectable && !dot.cloudy ? 'ndvi__dot--selectable' : '',
          ]"
        >
          <title>{{ dot.date }} · {{ LABEL }} {{ dot.val }} · облачность {{ dot.cloudPct }} %<template v-if="dot.trust != null"> · доверие {{ dot.trust }}</template></title>
        </circle>
        <!-- Невидимая зона клика поверх точки, чтобы не перекрывалась ею -->
        <circle
          v-if="dot.selectable"
          :cx="dot.cx"
          :cy="dot.cy"
          r="9"
          class="ndvi__hit"
          :data-date="dot.date"
          role="button"
          :aria-label="`Снимок ${dot.date}`"
          @click="emit('select', dot.date)"
        />
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
          v-if="i % dateStep === 0 || i === layout.dots.length - 1"
          :x="dot.cx"
          :y="H - 8"
          class="ndvi__date"
          text-anchor="middle"
        >{{ dot.dateShort }}</text>
      </g>
    </svg>
    <div class="ndvi__legend">
      <span><i class="ndvi__lg-dot" /> {{ LABEL }}<template v-if="hint"> · {{ hint }}</template> (средн. по полю)</span>
      <span><i class="ndvi__lg-band" /> min–max</span>
      <span><i class="ndvi__lg-cloud" /> облачный снимок</span>
      <span v-if="selectable && selectable.length"><i class="ndvi__lg-select" /> клик — снимок этой даты</span>
    </div>
    <p
      v-if="lastPoint"
      class="ndvi__last"
    >
      Последний снимок: {{ fmtDate(lastPoint.date) }} · {{ LABEL }} {{ fmtVal(lastPoint.ndvi) }}
    </p>
    <!-- #544: сцены на «сегодня» нет — показано ближайшей доступной датой -->
    <p
      v-if="asOfNote"
      class="ndvi__asof"
    >
      {{ asOfNote }}
    </p>
  </div>
  <p
    v-else-if="(index ?? 'ndvi') === 'ndvi'"
    class="ndvi__empty"
  >
    Спутниковых снимков за сезон нет — график появится после первой ясной сцены
  </p>
  <p
    v-else
    class="ndvi__empty"
  >
    Нет данных по индексу {{ LABEL }} за сезон — история догружается
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

/* v2-fix-H: выбранная дата слоя — синее кольцо; кликабельные точки */
.ndvi__ring--selected {
  fill: none;
  stroke: #1565c0;
  stroke-width: 2;
}

.ndvi__hit {
  fill: transparent;
  cursor: pointer;
}

.ndvi__dot--selectable {
  stroke: #1b5e20;
  stroke-width: 1.5;
}

.ndvi__lg-select {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #2e7d32;
  border: 1.5px solid #1565c0;
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

/* #544: нейтральная подпись о фактической дате показа */
.ndvi__asof {
  margin: 0;
  font-size: 0.75rem;
  color: #757575;
}

.ndvi__empty {
  margin: 0;
  font-size: 0.85rem;
  color: #757575;
}
</style>
