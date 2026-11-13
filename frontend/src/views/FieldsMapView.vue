<script setup lang="ts">
// Экран E-02 «Карта полей» (docs/07-ux §2): Leaflet-карта с контурами полей
// из GET /fields?with_geometry=true (API-01). Заливка контура — цвет культуры
// текущего цикла (API-48 /references/cultures, color_hex; без культуры — серый),
// в центроиде — краткий номер поля (часть имени до первого «/»), поверх карты —
// складная легенда культур (#55, замечания пользователя №3–4). Панель «Список» —
// таблица полей с фильтрами (вкладки).
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { Map as LeafletMap } from 'leaflet'
import L from 'leaflet'
import { getCultureReferences, getFields, getWeatherMap } from '@/api'
import type { CultureReference, Field, WeatherMapMetric } from '@/api/types'
import { shortFieldNumber } from '@/utils/field'
import FieldsListPanel from '@/components/FieldsListPanel.vue'
import Skeleton from '@/components/Skeleton.vue'

const router = useRouter()
const mapEl = ref<HTMLElement | null>(null)
const loading = ref(true)
const failed = ref(false)
const fieldCount = ref(0)
const tab = ref<'map' | 'list'>('map')

let map: LeafletMap | null = null
let contoursLayer: L.FeatureGroup | null = null
/** GeoJSON-слой полей (для restyle: eachLayer FeatureGroup не рекурсивен). */
let fieldsGeoLayer: L.GeoJSON | null = null
/** У карты уже есть «её» вид: восстановленный из localStorage или fitBounds.
 * Пока false и идёт загрузка — оверлей «Загружаем поля…» вместо серой канвы. */
const mapViewReady = ref(false)

const STATUS_LABELS: Record<Field['status'], string> = {
  active: 'действует',
  merged: 'объединено',
  archived: 'в архиве',
}

/** Цвет обводки — статус поля (информация о статусе сохраняется на карте). */
const STATUS_COLORS: Record<Field['status'], string> = {
  active: '#2e7d32',
  merged: '#f9a825',
  archived: '#757575',
}

/** Культура без цвета в справочнике / поле без посева (#55). */
const NO_CULTURE_COLOR = '#9e9e9e'
const NO_CULTURE_LABEL = 'Без посева'

/** Зум, с которого подписи номеров полей постоянны (ниже — скрыты: сливаются). */
const LABEL_MIN_ZOOM = 12

/* shortFieldNumber — общий хелпер в @/utils/field (#192). */

/* ── Ретентивный вьюпорт карты (#136, фидбек #437): последний вид карты
 * запоминается и восстанавливается при открытии — пользователь никогда
 * не видит произвольную точку России. Без сохранённого вида карта
 * получает его от fitBounds по полям, до того — оверлей «Загружаем». ── */

const MAP_VIEW_KEY = 'cda.map.view'

interface SavedMapView {
  center: [number, number]
  zoom: number
}

/** Сохранённый вид карты; null — нет или битый (тогда старт по полям). */
function loadMapView(): SavedMapView | null {
  try {
    const raw = localStorage.getItem(MAP_VIEW_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<SavedMapView>
    const [lat, lng] = Array.isArray(v.center) ? v.center : []
    if (
      typeof lat === 'number' && Number.isFinite(lat) &&
      typeof lng === 'number' && Number.isFinite(lng) &&
      typeof v.zoom === 'number' && v.zoom >= 1 && v.zoom <= 19
    ) {
      return { center: [lat, lng], zoom: v.zoom }
    }
  } catch {
    // нет доступа к localStorage / битый JSON — не критично
  }
  return null
}

/** Запомнить текущий вид карты (moveend покрывает и зум). */
function saveMapView(): void {
  if (!map) return
  try {
    const c = map.getCenter()
    localStorage.setItem(
      MAP_VIEW_KEY,
      JSON.stringify({ center: [c.lat, c.lng], zoom: map.getZoom() } satisfies SavedMapView),
    )
  } catch {
    // приватный режим и т.п. — вид просто не запомнится
  }
}

/** Культуры → цвет из справочника (API-48). */
const cultureColors = ref(new Map<string, string>())
const legendOpen = ref(true)

interface LegendEntry {
  name: string
  color: string
}

/** Легенда: уникальные культуры на карте (по текущим циклам полей). */
const legend = ref<LegendEntry[]>([])

function cultureColorOf(field: Field): { name: string; color: string } {
  const cycle = field.current_cycle
  if (!cycle) return { name: NO_CULTURE_LABEL, color: NO_CULTURE_COLOR }
  const color = cultureColors.value.get(cycle.culture_name) ?? NO_CULTURE_COLOR
  return { name: cycle.culture_name, color }
}

function popupHtml(field: Field): string {
  const cycle = field.current_cycle
  const culture = cycle ? `${cycle.culture_name} (${cycle.year})` : 'нет посева'
  const area = field.area_ga.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
  // В режиме погоды — строка со значением метрики поля (API-61, #143).
  const weatherLine = weatherMode.value
    ? `<br/>${metricLabel()}: <strong>${weatherValueText(field)}</strong>`
    : ''
  return (
    `<div class="field-popup">` +
    // Заголовок балуна (название поля) — кликабельный: открывает карточку
    // поля (фидбек #368): отдельная ссылка «Открыть карточку поля» убрана.
    `<a href="/fields/${field.field_id}" data-field-id="${field.field_id}" class="field-popup__title">${field.name}</a><br/>` +
    `Площадь: ${area} га<br/>` +
    `Культура сезона: ${culture}` +
    weatherLine +
    `<br/>Статус: ${STATUS_LABELS[field.status]}` +
    `</div>`
  )
}

/* ── Режим погодной карты (API-61, §7.8, #143): заливка полей по метрике ── */

const WEATHER_METRICS: Array<{
  value: WeatherMapMetric
  label: string
  unit: string
  from: string
  to: string
  digits: number
}> = [
  { value: 'precip_7d', label: 'Осадки 7 сут', unit: 'мм', from: '#e3f2fd', to: '#1565c0', digits: 1 },
  { value: 'precip_14d', label: 'Осадки 14 сут', unit: 'мм', from: '#e3f2fd', to: '#0d47a1', digits: 1 },
  { value: 't_now', label: 'Температура', unit: '°C', from: '#90caf9', to: '#e53935', digits: 1 },
  { value: 'gdd_ytd', label: 'СЭТ с 1 января', unit: '°C·сут', from: '#f0f4c3', to: '#2e7d32', digits: 0 },
]

const weatherMode = ref(false)
const weatherMetric = ref<WeatherMapMetric>('precip_7d')
const weatherValues = ref(new Map<string, number | null>())
const weatherLoading = ref(false)
/** Метрика недоступна (до деплоя API-61): режим не включаем, подсказка. */
const weatherFailed = ref(false)
const weatherUpdated = ref<string | null>(null)

const weatherMetricMeta = computed(
  () => WEATHER_METRICS.find((m) => m.value === weatherMetric.value)!,
)

const weatherStats = computed(() => {
  const vals = [...weatherValues.value.values()].filter((v): v is number => v != null)
  if (!vals.length) return { min: 0, max: 0 }
  return { min: Math.min(...vals), max: Math.max(...vals) }
})

function metricLabel(): string {
  return weatherMetricMeta.value.label
}

function weatherValueText(field: Field): string {
  const v = weatherValues.value.get(field.field_id)
  if (v === undefined) return '…'
  if (v === null) return 'нет данных'
  return `${v.toLocaleString('ru-RU', { maximumFractionDigits: weatherMetricMeta.value.digits })} ${weatherMetricMeta.value.unit}`
}

/** Интерполяция цвета from→to по позиции значения в [min, max]. */
function weatherColor(v: number): string {
  const { from, to } = weatherMetricMeta.value
  const { min, max } = weatherStats.value
  const t = max > min ? (v - min) / (max - min) : 0.5
  const hx = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  const [r1, g1, b1] = hx(from)
  const [r2, g2, b2] = hx(to)
  const mix = (a: number, b: number) => Math.round(a + (b - a) * t)
  return `#${[mix(r1, r2), mix(g1, g2), mix(b1, b2)]
    .map((c) => c.toString(16).padStart(2, '0'))
    .join('')}`
}

/** Стиль контура: культуры (обычный) или метрика погоды (#143). */
function styleFor(field: Field | undefined): L.PathOptions {
  const base: L.PathOptions = {
    color: STATUS_COLORS[field?.status ?? 'active'],
    weight: 1.5,
    fillColor: field ? cultureColorOf(field).color : NO_CULTURE_COLOR,
    fillOpacity: 0.45,
  }
  if (!field || !weatherMode.value) return base
  const v = weatherValues.value.get(field.field_id)
  if (v === undefined) return base // значения ещё грузятся — прежняя заливка
  if (v === null) {
    // Нет наблюдений: нейтральная штриховка (§7.8), подпись в балуне.
    return { ...base, fillColor: '#eeeeee', fillOpacity: 0.55, dashArray: '4 3' }
  }
  return { color: '#37474f', weight: 1.5, fillColor: weatherColor(v), fillOpacity: 0.7 }
}

/** Перекрасить контуры и балуны (смена режима/метрики/данных). */
function restyleLayer(): void {
  fieldsGeoLayer?.eachLayer((layer) => {
    const feature = (layer as L.Layer & { feature?: GeoJSON.Feature }).feature
    const field = feature?.properties as Field | undefined
    ;(layer as L.Path).setStyle(styleFor(field))
    const withPopup = layer as L.Layer & { setPopupContent?: (html: string) => unknown }
    if (field && withPopup.setPopupContent) {
      withPopup.setPopupContent(popupHtml(field))
    }
  })
}

async function loadWeatherMap(): Promise<void> {
  weatherLoading.value = true
  weatherFailed.value = false
  try {
    const data = await getWeatherMap(weatherMetric.value)
    weatherValues.value = new Map(data.items.map((it) => [it.field_id, it.value]))
    weatherUpdated.value = data.items[0]?.updated_at ?? null
  } catch {
    weatherValues.value = new Map()
    weatherFailed.value = true
    weatherMode.value = false
  } finally {
    weatherLoading.value = false
    restyleLayer()
  }
}

watch(weatherMode, (on) => {
  if (on) void loadWeatherMap()
  else restyleLayer()
})
watch(weatherMetric, () => {
  if (weatherMode.value) void loadWeatherMap()
})

/** Переход в карточку поля (пункт 1, v2-fix-A): тот же роут, что у строки списка. */
function openFieldCard(field: Field): void {
  void router.push({ name: 'field-card', params: { fieldId: field.field_id } })
}

/** Скрытие/показ подписей номеров по зуму (класс на контейнере карты). */
function syncLabelZoom(): void {
  if (!map) return
  const el = map.getContainer()
  if (map.getZoom() >= LABEL_MIN_ZOOM) el.classList.add('map-labels-on')
  else el.classList.remove('map-labels-on')
}

async function loadCultures(): Promise<void> {
  try {
    // Справочник мал и почти не меняется: ошибка загрузки не критична —
    // поля останутся серыми, карта работает.
    const cultures: CultureReference[] = await getCultureReferences()
    const colors = new Map<string, string>()
    for (const c of cultures) if (c.color_hex) colors.set(c.name, c.color_hex)
    cultureColors.value = colors
  } catch {
    /* серые поля + тост от API-клиента */
  }
}

async function loadFields(layer: L.FeatureGroup) {
  try {
    // Карта запрашивает контуры: with_geometry=true (docs/06 §5, API-01).
    // 90 полей пилота умещаются в limit=100; на случай роста — постранично.
    const page = await getFields({ status: 'active', with_geometry: true, limit: 100 })
    let items = [...page.items]
    while (items.length < page.total) {
      const next = await getFields({
        status: 'active',
        with_geometry: true,
        limit: 100,
        offset: items.length,
      })
      items = [...items, ...next.items]
    }
    fieldCount.value = page.total
    const features = items
      .filter((field) => field.contour)
      .map((field) => ({
        type: 'Feature' as const,
        properties: field,
        geometry: field.contour,
      }))
    const legendMap = new Map<string, string>()
    fieldsGeoLayer = L.geoJSON(features as unknown as GeoJSON.Feature[], {
      style: (feature) => styleFor(feature?.properties as Field | undefined),
      onEachFeature: (feature, featureLayer) => {
        const field = feature.properties as Field
        featureLayer.bindPopup(popupHtml(field))
        // Краткий номер поля в центроиде (#55): постоянная подпись.
        // Пункт 1 (v2-fix-A): подпись интерактивна — клик открывает карточку
        // поля (тот же роут, что клик по строке списка).
        featureLayer.bindTooltip(shortFieldNumber(field.name), {
          permanent: true,
          direction: 'center',
          className: 'field-label',
          interactive: true,
          opacity: 1,
        })
        featureLayer.getTooltip()?.on('click', () => openFieldCard(field))
        const { name, color } = cultureColorOf(field)
        legendMap.set(name, color)
      },
    }).addTo(layer)
    legend.value = [...legendMap.entries()]
      .map(([name, color]) => ({ name, color }))
      .sort((a, b) => a.name.localeCompare(b.name, 'ru'))
    const bounds = layer.getBounds()
    // Вьюпорт уже восстанавливали (сохранённый вид) или подгоняли ранее —
    // не дёргаем карту заново (ретентивная навигация, #437)
    if (!mapViewReady.value && bounds.isValid()) {
      map?.fitBounds(bounds.pad(0.15))
      mapViewReady.value = true
    }
  } catch {
    failed.value = true // тост с problem+json уже показан API-клиентом
  } finally {
    loading.value = false
  }
}

const legendTitle = computed(() =>
  legendOpen.value ? 'Легенда: культуры' : 'Культуры',
)

onMounted(async () => {
  if (!mapEl.value) return
  // На узких экранах легенда по умолчанию сложена (E-01: место под картой важнее).
  legendOpen.value = window.innerWidth >= 600
  // Без сохранённого вида НЕ задаём произвольную точку: вид даст fitBounds
  // по полям (до того — серая канва под оверлеем «Загружаем поля…»)
  map = L.map(mapEl.value, { zoomControl: true })
  const savedView = loadMapView()
  if (savedView) {
    map.setView(savedView.center, savedView.zoom)
    mapViewReady.value = true
  }
  map.on('moveend', saveMapView)
  // Тайлы OSM (провайдер офлайн-тайлов выбирается до MVP, 10-mobile-pwa §7).
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; участники OpenStreetMap',
  }).addTo(map)
  map.on('zoomend', syncLabelZoom)
  // Заголовок балуна (название поля) открывает карточку поля (#368):
  // SPA-переход без перезагрузки страницы.
  map.on('popupopen', (e) => {
    const link = e.popup
      .getElement()
      ?.querySelector<HTMLAnchorElement>('.field-popup__title')
    if (link) {
      L.DomEvent.on(link, 'click', (ev) => {
        L.DomEvent.preventDefault(ev)
        const id = link.dataset.fieldId
        if (id) void router.push({ name: 'field-card', params: { fieldId: id } })
      })
    }
  })
  contoursLayer = L.featureGroup().addTo(map)
  // Сначала справочник культур, потом поля: заливка читает cultureColors в момент
  // построения geoJSON-слоя (на живом API геометрия полей тяжелее справочника,
  // при параллельной загрузке поля успели бы отрисоваться серыми — ревью #55).
  await loadCultures()
  await loadFields(contoursLayer)
  syncLabelZoom()
})

onUnmounted(() => {
  map?.remove()
  map = null
})
</script>

<template>
  <section class="map-screen">
    <div class="map-screen__toolbar">
      <h1 class="map-screen__title">
        Карта полей
      </h1>
      <div
        class="map-screen__tabs"
        role="tablist"
        aria-label="Вид: карта или список"
      >
        <button
          type="button"
          role="tab"
          :aria-selected="tab === 'map'"
          class="map-screen__tab"
          :class="{ 'map-screen__tab--active': tab === 'map' }"
          @click="tab = 'map'"
        >
          Карта
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="tab === 'list'"
          class="map-screen__tab"
          :class="{ 'map-screen__tab--active': tab === 'list' }"
          @click="tab = 'list'"
        >
          Список
        </button>
      </div>
      <span class="map-screen__spacer" />
      <span
        v-if="tab === 'map' && loading"
        class="map-screen__state"
      ><Skeleton
        width="7rem"
        height="0.9rem"
        pill
      /></span>
      <span
        v-else-if="tab === 'map' && failed"
        class="map-screen__state map-screen__state--error"
      >
        Не удалось загрузить поля
      </span>
      <span
        v-else-if="tab === 'map'"
        class="map-screen__state"
      >Полей: {{ fieldCount }}</span>
      <div
        v-if="tab === 'map'"
        class="map-screen__weather"
        role="group"
        aria-label="Режим заливки полей"
      >
        <button
          type="button"
          class="map-screen__wbtn"
          :class="{ 'map-screen__wbtn--active': !weatherMode }"
          :aria-pressed="!weatherMode"
          @click="weatherMode = false"
        >
          Культуры
        </button>
        <button
          type="button"
          class="map-screen__wbtn"
          :class="{ 'map-screen__wbtn--active': weatherMode }"
          :aria-pressed="weatherMode"
          :disabled="weatherFailed"
          @click="weatherMode = true"
        >
          Погода
        </button>
        <select
          v-if="weatherMode"
          v-model="weatherMetric"
          class="map-screen__wmetric"
          aria-label="Метрика погодной карты"
        >
          <option
            v-for="m in WEATHER_METRICS"
            :key="m.value"
            :value="m.value"
          >
            {{ m.label }}
          </option>
        </select>
      </div>
    </div>

    <div
      v-show="tab === 'map'"
      class="map-screen__map-wrap"
    >
      <div
        ref="mapEl"
        class="map-screen__map"
        aria-label="Карта контуров полей хозяйства"
      />
      <div
        v-if="loading && !mapViewReady"
        class="map-screen__loading-overlay"
        role="status"
        aria-live="polite"
      >
        Загружаем поля…
      </div>
      <div
        v-if="!loading && !failed && (weatherMode || legend.length)"
        class="map-legend"
        :class="{ 'map-legend--open': legendOpen }"
      >
        <button
          type="button"
          class="map-legend__toggle"
          :aria-expanded="legendOpen"
          @click="legendOpen = !legendOpen"
        >
          {{ weatherMode ? `Легенда: ${metricLabel()}` : legendTitle }}
          <span class="map-legend__chevron">▾</span>
        </button>
        <div
          v-if="legendOpen && weatherMode"
          class="map-legend__weather"
        >
          <div class="map-legend__scale">
            <span class="map-legend__scale-val">{{
              weatherStats.min.toLocaleString('ru-RU', { maximumFractionDigits: 1 })
            }} {{ weatherMetricMeta.unit }}</span>
            <span
              class="map-legend__bar"
              :style="{ background: `linear-gradient(90deg, ${weatherMetricMeta.from}, ${weatherMetricMeta.to})` }"
            />
            <span class="map-legend__scale-val">{{
              weatherStats.max.toLocaleString('ru-RU', { maximumFractionDigits: 1 })
            }} {{ weatherMetricMeta.unit }}</span>
          </div>
          <p class="map-legend__nodata">
            <span class="map-legend__swatch map-legend__swatch--nodata" />
            нет данных
            <template v-if="weatherUpdated">
              · по наблюдениям на {{ weatherUpdated.split('-').reverse().join('.') }}
            </template>
          </p>
        </div>
        <ul
          v-else-if="legendOpen"
          class="map-legend__list"
        >
          <li
            v-for="entry in legend"
            :key="entry.name"
            class="map-legend__item"
          >
            <span
              class="map-legend__swatch"
              :style="{ background: entry.color }"
              :aria-label="`Цвет культуры ${entry.name}`"
            />
            <span class="map-legend__name">{{ entry.name }}</span>
          </li>
        </ul>
      </div>
    </div>
    <div
      v-if="tab === 'list'"
      class="map-screen__list"
    >
      <FieldsListPanel />
    </div>
  </section>
</template>

<style scoped>
.map-screen {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.map-screen__toolbar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.6rem 1rem;
  background: #fff;
  border-bottom: 1px solid #c8e6c9;
  flex-wrap: wrap;
}

.map-screen__title {
  font-size: 1.15rem;
  margin: 0;
}

.map-screen__tabs {
  display: flex;
  border: 2px solid #2e7d32;
  border-radius: 8px;
  overflow: hidden;
}

.map-screen__tab {
  background: #fff;
  color: #2e7d32;
  border: none;
  padding: 0.55rem 1.1rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.map-screen__tab--active {
  background: #2e7d32;
  color: #fff;
}

.map-screen__spacer {
  flex: 1;
}

.map-screen__state {
  font-size: 1rem;
  color: #424242;
}

.map-screen__state--error {
  color: #b00020;
  font-weight: 600;
}

.map-screen__map-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  position: relative;
}

.map-screen__map {
  flex: 1;
  min-height: 320px;
  height: 60dvh;
}

/* Оверлей загрузки (#136/#437): пока у карты нет вьюпорта (нет
   сохранённого вида, поля ещё не пришли) — не показываем произвольную
   точку, а нейтральную плашку поверх серой канвы. */
.map-screen__loading-overlay {
  position: absolute;
  inset: 0;
  z-index: 700; /* выше тайлов, ниже легенды */
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e8f5e9;
  color: var(--cda-green-dark, #1b5e20);
  font-size: 1.05rem;
  pointer-events: none;
}

.map-screen__list {
  flex: 1;
  overflow-y: auto;
}

/* Легенда культур (#55): оверлей поверх карты, правый верхний угол. */
.map-legend {
  position: absolute;
  top: 0.75rem;
  right: 0.75rem;
  z-index: 800; /* выше тайлов и контуров, ниже попапов Leaflet (1000) */
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid #c8e6c9;
  border-radius: 8px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.18);
  max-width: min(16rem, 60vw);
}

.map-legend__toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  background: none;
  border: none;
  padding: 0.5rem 0.75rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: #1b5e20;
  cursor: pointer;
  min-height: 44px;
}

.map-legend__chevron {
  transition: transform 0.15s ease;
  font-size: 0.8em;
}

.map-legend--open .map-legend__chevron {
  transform: rotate(180deg);
}

/* ── Режим погодной карты (API-61, #143) ── */
.map-screen__weather {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.map-screen__wbtn {
  min-height: 36px;
  padding: 0.15rem 0.8rem;
  border: 1px solid #9db59e;
  border-radius: 999px;
  background: #fff;
  font-size: 0.9rem;
  cursor: pointer;
}

.map-screen__wbtn--active {
  background: #2e7d32;
  border-color: #2e7d32;
  color: #fff;
  font-weight: 600;
}

.map-screen__wbtn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.map-screen__wmetric {
  min-height: 36px;
  border: 1px solid #9db59e;
  border-radius: 8px;
  font-size: 0.9rem;
}

.map-legend__weather {
  padding: 0.35rem 0.75rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 13rem;
}

.map-legend__scale {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.map-legend__bar {
  flex: 1;
  height: 0.8rem;
  border-radius: 4px;
  border: 1px solid rgba(0, 0, 0, 0.15);
}

.map-legend__scale-val {
  font-size: 0.78rem;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.map-legend__nodata {
  margin: 0;
  font-size: 0.78rem;
  color: #55685a;
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.map-legend__swatch--nodata {
  background: repeating-linear-gradient(
    45deg,
    #eee,
    #eee 4px,
    #ccc 4px,
    #ccc 8px
  );
}

.map-legend__list {
  list-style: none;
  margin: 0;
  padding: 0.25rem 0.75rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  max-height: 40vh;
  overflow-y: auto;
}

.map-legend__item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
  color: #212121;
}

.map-legend__swatch {
  width: 1rem;
  height: 1rem;
  border-radius: 3px;
  border: 1px solid rgba(0, 0, 0, 0.35);
  flex: none;
}

.map-legend__name {
  overflow-wrap: anywhere;
}

@media (min-width: 900px) {
  .map-screen__map {
    height: auto;
  }
}
</style>

<style>
/* Стили попапа и подписей Leaflet — глобальные (динамический HTML). */
.field-popup {
  font-size: 1rem;
  line-height: 1.45;
}

/* Заголовок балуна — название поля: клик открывает карточку (#368),
   выглядит как ссылка (курсор-указатель, hover). */
.field-popup__title {
  font-weight: 700;
  font-size: 1.05rem;
  color: #1b5e20;
  text-decoration: none;
  cursor: pointer;
}

.field-popup__title:hover {
  text-decoration: underline;
}

/* Краткий номер поля в центроиде (#55): компактная постоянная подпись.
   v2-fix-A: подпись интерактивна — клик открывает карточку поля. */
.leaflet-tooltip.field-label {
  background: none;
  border: none;
  box-shadow: none;
  padding: 0.35rem 0.5rem;
  color: #10200f;
  font-size: 0.85rem;
  font-weight: 700;
  text-shadow:
    0 0 3px #fff,
    0 0 3px #fff,
    0 0 2px #fff;
  white-space: nowrap;
  cursor: pointer;
  border-radius: 4px;
}

.leaflet-tooltip.field-label:hover {
  color: #1b5e20;
  background: rgba(255, 255, 255, 0.55);
}

/* До LABEL_MIN_ZOOM подписи скрыты (сливаются на плотной карте 90 полей). */
.map-screen__map:not(.map-labels-on) .field-label {
  display: none;
}

.leaflet-tooltip.field-label::before {
  display: none; /* без хвостика: подпись в центре контура */
}
</style>
