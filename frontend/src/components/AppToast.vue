<script setup lang="ts">
// Единый тост ошибок API: RFC 7807 problem+json (title + detail) из toast-стора.
import { useToastStore } from '@/stores/toast'

const store = useToastStore()
</script>

<template>
  <div
    class="toasts"
    role="status"
    aria-live="polite"
  >
    <div
      v-for="t in store.toasts"
      :key="t.id"
      class="toast"
      :class="`toast--${t.kind}`"
      @click="store.dismiss(t.id)"
    >
      <strong class="toast__title">{{ t.title }}</strong>
      <span class="toast__detail">{{ t.detail }}</span>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  /* Внизу справа, над нижней навигацией полевого режима (07-ux §3.1):
   * не перекрывает ни её, ни верхнее меню кабинета. */
  position: fixed;
  right: 0.75rem;
  bottom: calc(4.5rem + env(safe-area-inset-bottom, 0px));
  z-index: 1200;
  width: min(26rem, calc(100vw - 1.5rem));
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  background: #b00020;
  color: #fff;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  box-shadow: 0 4px 12px rgb(0 0 0 / 25%);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  cursor: pointer;
}

.toast--info {
  background: #2e7d32;
}

.toast__title {
  font-size: 1rem;
}

.toast__detail {
  font-size: 1rem;
}
</style>
