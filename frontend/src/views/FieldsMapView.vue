<script setup lang="ts">
// Экран E-02 «Карта полей» (docs/07-ux §2): Leaflet-карта с контурами полей
// из GET /fields?with_geometry=true (API-01), попап поля — имя, площадь (га),
// культура сезона. Панель «Список» — таблица полей с фильтрами (вкладки).
import { onMounted, onUnmounted, ref } from 'vue'
import type { Map as LeafletMap, LayerGroup } from 'leaflet'
import L from 'leaflet'
import { getFields } from '@/api'
import type { Field } from '@/api/types'
import FieldsListPanel from '@/components/FieldsListPanel.vue'

const mapEl = ref<HTMLElement | null>(null)
const loading = ref(true)
const failed = ref(false)
const fieldCount = ref(0)
const tab = ref<'map' | 'list'>('map')

let map: LeafletMap | null = null
let contoursLayer: LayerGroup | null = null

const STATUS_LABELS: Record<Field['status'], string> = {
  active: 'действует',
  merged: 'объединено',
  archived: 'в архиве',
}

const STATUS_COLORS: Record<Field['status'], string> = {
  active: '#2e7d32',
  merged: '#f9a825',
  archived: '#757575',
}

function popupHtml(field: Field): string {
  const cycle = field.current_cycle
  const culture = cycle ? `${cycle.culture_name} (${cycle.year})` : 'нет посева'
  const area = field.area_ga.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
  return (
    `<div class="field-popup">` +
    `<strong>${field.name}</strong><br/>` +
    `Площадь: ${area} га<br/>` +
    `Культура сезона: ${culture}<br/>` +
    `Статус: ${STATUS_LABELS[field.status]}` +
    `</div>`
  )
}

async function loadFields(layer: LayerGroup) {
  try {
    // Карта запрашивает контуры: with_geometry=true (docs/06 §5, API-01).
    const page = await getFields({ status: 'active', with_geometry: true, limit: 100 })
    fieldCount.value = page.total
    const features = page.items
      .filter((field) => field.contour)
      .map((field) => ({
        type: 'Feature' as const,
        properties: field,
        geometry: field.contour,
      }))
    L.geoJSON(features as unknown as GeoJSON.Feature[], {
      style: (feature) => ({
        color: STATUS_COLORS[(feature?.properties?.status as Field['status']) ?? 'active'],
        weight: 2,
        fillOpacity: 0.25,
      }),
      onEachFeature: (feature, featureLayer) => {
        featureLayer.bindPopup(popupHtml(feature.properties as Field))
      },
    }).addTo(layer)
    const bounds = layer.getBounds()
    if (bounds.isValid()) map?.fitBounds(bounds.pad(0.15))
  } catch {
    failed.value = true // тост с problem+json уже показан API-клиентом
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (!mapEl.value) return
  map = L.map(mapEl.value, { zoomControl: true }).setView([48.731, 42.0], 12)
  // Тайлы OSM (провайдер офлайн-тайлов выбирается до MVP, 10-mobile-pwa §7).
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; участники OpenStreetMap',
  }).addTo(map)
  contoursLayer = L.layerGroup().addTo(map)
  void loadFields(contoursLayer)
})

onUnmounted(() => {
  map?.remove()
  map = null
})
</script>

<template>
  <section class="map-screen">
    <div class="map-screen__toolbar">
      <h1 class="map-screen__title">Карта полей</h1>
      <div class="map-screen__tabs" role="tablist" aria-label="Вид: карта или список">
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
      <span v-if="tab === 'map' && loading" class="map-screen__state">Загрузка контуров…</span>
      <span
        v-else-if="tab === 'map' && failed"
        class="map-screen__state map-screen__state--error"
      >
        Не удалось загрузить поля
      </span>
      <span v-else-if="tab === 'map'" class="map-screen__state">Полей: {{ fieldCount }}</span>
    </div>

    <div v-show="tab === 'map'" class="map-screen__map-wrap">
      <div ref="mapEl" class="map-screen__map" aria-label="Карта контуров полей хозяйства"></div>
    </div>
    <div v-if="tab === 'list'" class="map-screen__list">
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
}

.map-screen__map {
  flex: 1;
  min-height: 320px;
  height: 60dvh;
}

.map-screen__list {
  flex: 1;
  overflow-y: auto;
}

@media (min-width: 900px) {
  .map-screen__map {
    height: auto;
  }
}
</style>

<style>
/* Стили попапа Leaflet — глобальные (динамический HTML попапа). */
.field-popup {
  font-size: 1rem;
  line-height: 1.45;
}
</style>
