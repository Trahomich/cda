<script setup lang="ts">
// Лайтбокс карты дифвнесения (#141, фидбек #447): клик по превью карты —
// полноэкранный просмотр PNG с зумом (колесо/кнопки) и панорамированием
// (перетаскивание); закрытие — ✕, Esc, клик по фону.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  url: string
  caption?: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const scale = ref(1)
const tx = ref(0)
const ty = ref(0)
let dragging = false
let startX = 0
let startY = 0

const MIN_SCALE = 0.5
const MAX_SCALE = 8

const transform = computed(() => `translate(${tx.value}px, ${ty.value}px) scale(${scale.value})`)

function zoomBy(delta: number): void {
  scale.value = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale.value * delta))
}

function onWheel(e: WheelEvent): void {
  e.preventDefault()
  zoomBy(e.deltaY < 0 ? 1.2 : 1 / 1.2)
}

function onPointerDown(e: PointerEvent): void {
  dragging = true
  startX = e.clientX - tx.value
  startY = e.clientY - ty.value
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent): void {
  if (!dragging) return
  tx.value = e.clientX - startX
  ty.value = e.clientY - startY
}

function onPointerUp(): void {
  dragging = false
}

function reset(): void {
  scale.value = 1
  tx.value = 0
  ty.value = 0
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div
      class="vra-lb"
      role="dialog"
      aria-modal="true"
      aria-label="Карта зон дифференцированного внесения"
      @click.self="emit('close')"
      @wheel="onWheel"
    >
      <div class="vra-lb__bar">
        <span class="vra-lb__caption">{{ caption ?? 'Карта дифвнесения' }}</span>
        <span class="vra-lb__tools">
          <button
            type="button"
            class="vra-lb__btn"
            aria-label="Уменьшить"
            @click="zoomBy(1 / 1.3)"
          >−</button>
          <span class="vra-lb__scale">{{ Math.round(scale * 100) }} %</span>
          <button
            type="button"
            class="vra-lb__btn"
            aria-label="Увеличить"
            @click="zoomBy(1.3)"
          >+</button>
          <button
            type="button"
            class="vra-lb__btn vra-lb__btn--reset"
            @click="reset"
          >1:1</button>
        </span>
        <button
          type="button"
          class="vra-lb__btn vra-lb__btn--close"
          aria-label="Закрыть"
          @click="emit('close')"
        >✕</button>
      </div>
      <div
        class="vra-lb__stage"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <img
          :src="url"
          class="vra-lb__img"
          :style="{ transform }"
          alt="Карта зон дифференцированного внесения"
          draggable="false"
        >
      </div>
      <p class="vra-lb__hint">
        Колесо — зум, перетаскивание — панорама, Esc — закрыть
      </p>
    </div>
  </Teleport>
</template>

<style scoped>
.vra-lb {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(0, 0, 0, 0.82);
  display: flex;
  flex-direction: column;
}

.vra-lb__bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
}

.vra-lb__caption {
  flex: 1;
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vra-lb__tools {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.vra-lb__btn {
  min-width: 36px;
  min-height: 36px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 1rem;
  cursor: pointer;
}

.vra-lb__btn:hover {
  background: rgba(255, 255, 255, 0.25);
}

.vra-lb__scale {
  font-variant-numeric: tabular-nums;
  min-width: 3.2rem;
  text-align: center;
  font-size: 0.9rem;
}

.vra-lb__stage {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  touch-action: none;
  cursor: grab;
}

.vra-lb__stage:active {
  cursor: grabbing;
}

.vra-lb__img {
  max-width: min(92vw, 1200px);
  max-height: 78vh;
  transform-origin: center;
  user-select: none;
  transition: transform 0.05s linear;
}

.vra-lb__hint {
  margin: 0;
  padding: 0.4rem 0.75rem;
  color: rgba(255, 255, 255, 0.7);
  font-size: 0.85rem;
  text-align: center;
}
</style>
