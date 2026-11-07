<script setup lang="ts">
// E-03 «Поле 360°» (docs/07-ux §3.2): карточка поля — контур, история
// культур, предшественник ✓/нарушение, NDVI-таймлайн (облачные точки ◌),
// агрохимия, погода сейчас+7 сут (осадки, GDD), планы/журнал.
// Данные: API-05/06/07 + API-18 (журнал поля).
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getFieldCard,
  getFieldCropCycles,
  getFieldNdvi,
  getFieldWeather,
  getNdviRender,
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
import type { NdviRender } from '@/api'
import CropHistoryStrip from '@/components/CropHistoryStrip.vue'
import NdviTimeline from '@/components/NdviTimeline.vue'
import Skeleton from '@/components/Skeleton.vue'

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

/** Рамка нормализации lon/lat → SVG viewBox 200×120 (для контура и слоя §7.3). */
const mapFrame = computed(() => {
  const ring = card.value?.field.contour.coordinates[0]
  if (!ring || ring.length < 3) return null
  const lons = ring.map((p) => p[0])
  const lats = ring.map((p) => p[1])
  const minLon = Math.min(...lons)
  const maxLon = Math.max(...lons)
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)
  const s = Math.min(180 / (maxLon - minLon || 1), 100 / (maxLat - minLat || 1))
  return { minLon, minLat, s }
})

/** Контур в координатах SVG viewBox 200×120 (нормализация lon/lat в рамку). */
const contourPoints = computed(() => {
  const ring = card.value?.field.contour.coordinates[0]
  const frame = mapFrame.value
  if (!ring || !frame) return ''
  const { minLon, minLat, s } = frame
  return ring
    .map(([lon, lat]) => {
      const x = 10 + (lon - minLon) * s
      const y = 110 - (lat - minLat) * s
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

/* ── Слой мини-карты (§7.3): NDVI-рендер / truecolor снимок / контур ── */

/** Режим слоя: 'ndvi' | 'truecolor' — PNG-оверлей на контуре, 'none' — контур. */
const layerMode = ref<'ndvi' | 'truecolor' | 'none'>('ndvi')
/** Даты со свежими рендерами (render_available у API-06, §7.3). */
const renderDates = computed(() => ndvi.value.filter((p) => p.render_available).map((p) => p.date))
/** Выбранная дата слоя; по умолчанию — последняя с рендером. */
const renderDate = ref('')
/** Загруженный PNG (blob URL + геопривязка из X-Field-Bbox/X-Image-Size). */
const layer = ref<NdviRender | null>(null)

watch(
  renderDates,
  (dates) => {
    if (!dates.length) {
      renderDate.value = ''
      return
    }
    if (!dates.includes(renderDate.value)) renderDate.value = dates[dates.length - 1]
  },
  { immediate: true },
)

let layerToken = 0

async function loadLayer() {
  const token = ++layerToken
  if (layerMode.value === 'none' || !renderDate.value) {
    const prev = layer.value
    layer.value = null
    if (prev) URL.revokeObjectURL(prev.url)
    return
  }
  const variant = layerMode.value
  const r = await getNdviRender(fieldId, renderDate.value, variant)
  if (token !== layerToken) {
    // Пользователь успел переключить режим/дату — этот ответ устарел.
    if (r) URL.revokeObjectURL(r.url)
    return
  }
  const prev = layer.value
  layer.value = r
  if (prev) URL.revokeObjectURL(prev.url)
  if (!r) {
    // Рендера нет (404) или заголовки битые — показываем просто контур.
    layerMode.value = 'none'
  }
}

watch([renderDate, layerMode], () => void loadLayer())

/** Клик по точке графика (v2-fix-H): даты без рендера не выбираем;
 *  если слой выключен («Контур»), включаем NDVI. */
function onDotSelect(date: string): void {
  if (!renderDates.value.includes(date)) return
  renderDate.value = date
  if (layerMode.value === 'none') layerMode.value = 'ndvi'
}

onUnmounted(() => {
  if (layer.value) URL.revokeObjectURL(layer.value.url)
})

/** Позиция PNG в SVG: bbox рендера (X-Field-Bbox) → координаты рамки мини-карты. */
const layerRect = computed(() => {
  const frame = mapFrame.value
  const r = layer.value
  if (!frame || !r) return null
  const [minLon, minLat, maxLon, maxLat] = r.bbox
  return {
    x: 10 + (minLon - frame.minLon) * frame.s,
    y: 110 - (maxLat - frame.minLat) * frame.s,
    width: (maxLon - minLon) * frame.s,
    height: (maxLat - minLat) * frame.s,
  }
})

/* ── Зум и панорамирование мини-карты (v2-fix-I): динамический viewBox
 *  рамки 200×120; зум 1×–8× (w от 200 до 25), рамку не покидаем. ── */
const contourSvg = ref<SVGSVGElement | null>(null)
const VB_FULL = { w: 200, h: 120 }
const ZOOM_MAX = 8
const vb = ref({ ...VB_FULL, x: 0, y: 0 })
const vbStr = computed(() => `${vb.value.x} ${vb.value.y} ${vb.value.w} ${vb.value.h}`)

function resetView(): void {
  vb.value = { ...VB_FULL, x: 0, y: 0 }
}

// Смена поля — исходный масштаб.
watch(
  () => card.value?.field.field_id,
  () => resetView(),
)

function clampView(): void {
  const v = vb.value
  v.w = Math.min(v.w, VB_FULL.w)
  v.h = v.w * (VB_FULL.h / VB_FULL.w)
  v.x = Math.min(Math.max(v.x, 0), VB_FULL.w - v.w)
  v.y = Math.min(Math.max(v.y, 0), VB_FULL.h - v.h)
}

/** Зум с сохранением точки (px, py) в координатах viewBox; factor > 1 — ближе. */
function zoomAt(px: number, py: number, factor: number): void {
  const v = { ...vb.value }
  const w = Math.min(Math.max(v.w / factor, VB_FULL.w / ZOOM_MAX), VB_FULL.w)
  const k = w / v.w
  vb.value = { x: px - (px - v.x) * k, y: py - (py - v.y) * k, w, h: w * (VB_FULL.h / VB_FULL.w) }
  clampView()
}

/** Координаты курсорного события в системе viewBox. */
function eventPoint(e: MouseEvent): { x: number; y: number } {
  const rect = contourSvg.value?.getBoundingClientRect()
  if (!rect) return { x: vb.value.x + vb.value.w / 2, y: vb.value.y + vb.value.h / 2 }
  return {
    x: vb.value.x + ((e.clientX - rect.left) / rect.width) * vb.value.w,
    y: vb.value.y + ((e.clientY - rect.top) / rect.height) * vb.value.h,
  }
}

/** Кнопки зума — к центру видимой рамки. */
function zoomBy(factor: number): void {
  zoomAt(vb.value.x + vb.value.w / 2, vb.value.y + vb.value.h / 2, factor)
}

function onWheel(e: WheelEvent): void {
  e.preventDefault()
  const p = eventPoint(e)
  zoomAt(p.x, p.y, e.deltaY < 0 ? 1.25 : 1 / 1.25)
}

/* Панорамирование перетаскиванием (Pointer Events + capture). */
const dragging = ref(false)
let dragStart: { sx: number; sy: number; vx: number; vy: number } | null = null

function onPanStart(e: PointerEvent): void {
  if (e.pointerType === 'mouse' && e.button !== 0) return
  dragging.value = true
  dragStart = { sx: e.clientX, sy: e.clientY, vx: vb.value.x, vy: vb.value.y }
  ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
}

function onPanMove(e: PointerEvent): void {
  const rect = contourSvg.value?.getBoundingClientRect()
  if (!dragging.value || !dragStart || !rect) return
  vb.value.x = dragStart.vx - ((e.clientX - dragStart.sx) / rect.width) * vb.value.w
  vb.value.y = dragStart.vy - ((e.clientY - dragStart.sy) / rect.height) * vb.value.h
  clampView()
}

function onPanEnd(): void {
  dragging.value = false
  dragStart = null
}

function fmtDate(iso: string): string {
  return iso.split('-').reverse().join('.')
}

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
          <div
            v-else
            class="card360__map-wrap"
          >
            <!-- Зум мини-карты (v2-fix-I): кнопки, колесо, перетаскивание -->
            <div
              class="card360__zoom"
              role="group"
              aria-label="Масштаб мини-карты"
            >
              <button
                type="button"
                class="card360__zoom-btn"
                title="Приблизить"
                aria-label="Приблизить"
                @click="zoomBy(1.6)"
              >
                +
              </button>
              <button
                type="button"
                class="card360__zoom-btn"
                title="Отдалить"
                aria-label="Отдалить"
                @click="zoomBy(1 / 1.6)"
              >
                −
              </button>
              <button
                type="button"
                class="card360__zoom-btn"
                title="Сбросить масштаб"
                aria-label="Сбросить масштаб"
                @click="resetView"
              >
                ⤢
              </button>
            </div>
            <svg
              ref="contourSvg"
              class="card360__contour"
              :class="{
                'card360__contour--layered': !!layerRect,
                'card360__contour--dragging': dragging,
              }"
              :viewBox="vbStr"
              role="img"
              aria-label="Контур поля (колесо мыши — масштаб, перетаскивание — сдвиг)"
              @wheel="onWheel"
              @pointerdown="onPanStart"
              @pointermove="onPanMove"
              @pointerup="onPanEnd"
              @pointercancel="onPanEnd"
            >
            <image
              v-if="layer && layerRect"
              class="card360__ndvi-layer"
              :href="layer.url"
              :x="layerRect.x"
              :y="layerRect.y"
              :width="layerRect.width"
              :height="layerRect.height"
              preserveAspectRatio="none"
            />
            <polygon
              :points="contourPoints"
              class="card360__polygon"
              :style="contourColor ? { fill: contourColor } : undefined"
            />
            </svg>
          </div>
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
            v-if="card && currentCycle && card.rotation_check"
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
          <Skeleton
            v-if="ndviLoading"
            :lines="3"
          />
          <NdviTimeline
            v-else
            :points="ndvi"
            :selectable="renderDates"
            :selected="renderDate"
            @select="onDotSelect"
          />
          <!-- §7.3: слой мини-карты — вариант рендера; дата выбирается
               кликом по точке графика (v2-fix-H) -->
          <div
            v-if="!ndviLoading && renderDates.length"
            class="card360__layers"
          >
            <div
              class="card360__layer-switch"
              role="group"
              aria-label="Слой мини-карты"
            >
              <button
                type="button"
                class="card360__layer-btn card360__layer-btn--ndvi"
                :class="{ 'card360__layer-btn--active': layerMode === 'ndvi' }"
                :aria-pressed="layerMode === 'ndvi'"
                @click="layerMode = 'ndvi'"
              >
                NDVI
              </button>
              <button
                type="button"
                class="card360__layer-btn card360__layer-btn--truecolor"
                :class="{ 'card360__layer-btn--active': layerMode === 'truecolor' }"
                :aria-pressed="layerMode === 'truecolor'"
                @click="layerMode = 'truecolor'"
              >
                Снимок
              </button>
              <button
                type="button"
                class="card360__layer-btn card360__layer-btn--contour"
                :class="{ 'card360__layer-btn--active': layerMode === 'none' }"
                :aria-pressed="layerMode === 'none'"
                @click="layerMode = 'none'"
              >
                Контур
              </button>
            </div>
            <span class="card360__layer-date-label">
              Снимок за {{ renderDate ? fmtDate(renderDate) : '—' }}
            </span>
          </div>
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
          <Skeleton
            v-if="!card"
            :lines="2"
          />
          <p
            v-else-if="card.agrochem"
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
          <Skeleton
            v-if="weatherLoading"
            :lines="4"
          />
          <p
            v-else-if="weatherFailed"
            class="card360__muted"
          >
            Не удалось загрузить погоду
          </p>
          <p
            v-else-if="weather"
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
          <Skeleton
            v-if="!card || journalLoading"
            :lines="4"
          />
          <template v-else>
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
          </template>
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
        <Skeleton
          v-if="worksLoading"
          :lines="5"
        />
        <ul
          v-else
          class="card360__works-list"
        >
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

/* Карта поля — на всю ширину карточки и крупнее (v2-fix-I) */
.card360__block--map {
  grid-column: 1 / -1;
}

.card360__map-wrap {
  position: relative;
}

.card360__zoom {
  position: absolute;
  top: 0.4rem;
  right: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 2px;
  z-index: 2;
}

.card360__zoom-btn {
  width: 1.8rem;
  height: 1.8rem;
  border: 1px solid #c8e6c9;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.92);
  color: #1b5e20;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.card360__zoom-btn:hover {
  background: #e8f5e9;
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

.card360__contour {
  width: 100%;
  height: auto;
  max-height: 300px;
  /* зум/пан (v2-fix-I): жесты перетаскивания не скроллят страницу */
  touch-action: none;
  cursor: grab;
}

.card360__contour--dragging {
  cursor: grabbing;
}

polygon.card360__polygon {
  fill: #a5d6a7;
  stroke: #2e7d32;
  stroke-width: 1.5;
  /* при зуме линия не толстеет (v2-fix-I) */
  vector-effect: non-scaling-stroke;
}

/* §7.3: PNG-слой под контуром; при активном слое контур stroke-only,
 *  чтобы заливка не перекрывала снимок (v2-fix-G) */
.card360__ndvi-layer {
  opacity: 0.95;
}

.card360__contour--layered polygon.card360__polygon {
  fill-opacity: 0;
}

.card360__layers {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: 0.6rem;
}

.card360__layer-switch {
  display: inline-flex;
  border: 1px solid #c8e6c9;
  border-radius: 999px;
  overflow: hidden;
}

.card360__layer-btn {
  border: none;
  background: #f1f8f1;
  color: #1b5e20;
  font-size: 0.85rem;
  padding: 0.35rem 0.75rem;
  cursor: pointer;
}

.card360__layer-btn:not(:last-child) {
  border-right: 1px solid #c8e6c9;
}

.card360__layer-btn--active {
  background: #2e7d32;
  color: #fff;
  font-weight: 600;
}

.card360__layer-date-label {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
  color: #33691e;
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
