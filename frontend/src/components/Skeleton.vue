<script setup lang="ts">
// Примитив «скелетон» (v2-fix-E): серые пульсирующие плейсхолдеры вместо
// текста «Загрузка…». Декоративный (aria-hidden), состояние загрузки
// дублируется текстом через aria-busy на контейнере страницы/блока.
withDefaults(
  defineProps<{
    /** Число строк-полос. */
    lines?: number
    /** Высота одной полосы. */
    height?: string
    /** Ширина первой полосы (последняя при lines>1 — 70 %, «хвост»). */
    width?: string
    /** Скруглить полосы «пилюлями» (чипы, аватары). */
    pill?: boolean
  }>(),
  { lines: 1, height: '0.9rem', width: '100%', pill: false },
)
</script>

<template>
  <div
    class="skeleton"
    aria-hidden="true"
  >
    <span
      v-for="i in lines"
      :key="i"
      class="skeleton__line"
      :class="{ 'skeleton__line--pill': pill }"
      :style="{
        height,
        width: lines > 1 && i === lines ? '70%' : width,
      }"
    />
  </div>
</template>

<style scoped>
.skeleton {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 100%;
}

.skeleton__line {
  display: inline-block;
  border-radius: 4px;
  background: linear-gradient(90deg, #eceff1 25%, #f5f7f8 45%, #eceff1 65%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.2s ease-in-out infinite;
}

.skeleton__line--pill {
  border-radius: 999px;
}

@keyframes skeleton-shimmer {
  0% {
    background-position: 150% 0;
  }

  100% {
    background-position: -50% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton__line {
    animation: none;
  }
}
</style>
