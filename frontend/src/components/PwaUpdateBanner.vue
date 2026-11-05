<script setup lang="ts">
// Баннер обновления PWA: «activate on next launch» (docs/10-mobile-pwa.md §5).
import { ref, onMounted } from 'vue'
import { registerSW } from 'virtual:pwa-register'

const needRefresh = ref(false)
let updateSW: ((reloadPage?: boolean) => Promise<void> | void) | undefined

onMounted(() => {
  updateSW = registerSW({
    onNeedRefresh() {
      needRefresh.value = true
    },
  })
})

function apply() {
  updateSW?.(true)
}
</script>

<template>
  <div v-if="needRefresh" class="update-banner">
    <span>Доступна новая версия — применится при перезапуске.</span>
    <button type="button" @click="apply">Обновить</button>
    <button type="button" class="update-banner__close" aria-label="Закрыть" @click="needRefresh = false">
      ✕
    </button>
  </div>
</template>

<style scoped>
.update-banner {
  position: fixed;
  bottom: 4.5rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1100;
  background: #1b5e20;
  color: #fff;
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  box-shadow: 0 4px 12px rgb(0 0 0 / 30%);
  font-size: 1rem;
}

.update-banner button {
  background: #fff;
  color: #1b5e20;
  border: none;
  border-radius: 6px;
  padding: 0.45rem 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: pointer;
}

.update-banner__close {
  background: transparent;
  color: #fff;
  padding: 0.45rem;
}
</style>
