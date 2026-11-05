<script setup lang="ts">
// Карта полей: Leaflet, слой контуров из GET /fields (API-01), попап поля —
// имя, площадь (га), культура сезона. Wireframe — 07-ux E-02 (карта полей).
import { onMounted, onUnmounted, ref } from 'vue'
import type { Map as LeafletMap, LayerGroup } from 'leaflet'
import L from 'leaflet'
import { getFields } from '@/api'
import type { Field } from '@/api/types'

const mapEl = ref<HTMLElement | null>(null)
const loading = ref(true)
const failed = ref(false)
const fieldCount = ref(0)

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
  const status = STATUS_LABELS[field.status]
  const area = field.area_ga.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
  return (
    `<div class="field-popup">` +
    `<strong>${field.name}</strong><br/>` +
    `Площадь: ${area} га<br/>` +
    `Культура сезона: ${culture}<br/>` +
    `Статус: ${status}` +
    `</div>`
  )
}

async function loadFields(layer: LayerGroup) {
  try {
    const page = await getFields({ status: 'active', limit: 100 })
    fieldCount.value = page.total
    L.geoJSON(page.items as unknown as GeoJSON.GeoJsonObject[], {
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
    failed.value = true // тост с problem+json уже показан клиентом
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
      <span v-if="loading" class="map-screen__state">Загрузка контуров…</span>
      <span v-else-if="failed" class="map-screen__state map-screen__state--error">
        Не удалось загрузить поля
      </span>
      <span v-else class="map-screen__state">Полей: {{ fieldCount }}</span>
    </div>
    <div ref="mapEl" class="map-screen__map" aria-label="Карта контуров полей хозяйства"></div>
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
  align-items: baseline;
  gap: 1rem;
  padding: 0.6rem 1rem;
  background: #fff;
  border-bottom: 1px solid #c8e6c9;
}

.map-screen__title {
  font-size: 1.15rem;
  margin: 0;
}

.map-screen__state {
  font-size: 1rem;
  color: #424242;
}

.map-screen__state--error {
  color: #b00020;
  font-weight: 600;
}

.map-screen__map {
  flex: 1;
  min-height: 320px;
  height: 60dvh;
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
