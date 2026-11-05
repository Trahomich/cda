<script setup lang="ts">
// Полевой (мобильный) layout: нижняя навигация по E-01 wireframe (07-ux §3.1).
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useOfflineStore } from '@/stores/offline'

const auth = useAuthStore()
const router = useRouter()
const offline = useOfflineStore()

// Бейдж уведомлений — заглушка (экран E-13 в другой задаче).
const alertsBadge = ref(0)

const nav = computed(() => [
  { label: 'Дашборд', route: { name: 'field-map' }, disabled: true },
  { label: 'Поля', route: { name: 'field-map' }, disabled: false },
  { label: 'Работы', route: { name: 'field-operations' }, disabled: false },
  { label: 'Животные', route: { name: 'field-map' }, disabled: true },
  { label: 'Ещё…', route: { name: 'field-queue' }, disabled: false },
])

function go(item: (typeof nav.value)[number]) {
  if (!item.disabled) router.push(item.route)
}

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="field">
    <header class="field__header">
      <span class="field__brand">ЦДА · {{ auth.user?.farm_name ?? 'Хозяйство' }}</span>
      <span class="field__role">[роль: {{ auth.roleTitle }}]</span>
      <span class="field__spacer" />
      <RouterLink
        v-if="offline.pendingCount > 0 || !offline.online"
        class="field__offline"
        :class="{ 'field__offline--down': !offline.online }"
        :to="{ name: 'field-queue' }"
      >
        {{ offline.online ? '⛁' : 'ОФЛАЙН' }} ⛁ {{ offline.pendingCount }}
      </RouterLink>
      <span
        v-if="alertsBadge > 0"
        class="field__badge"
        aria-label="Уведомления"
      >
        ({{ alertsBadge }})
      </span>
      <button
        type="button"
        class="field__logout"
        @click="logout"
      >
        Выйти
      </button>
    </header>
    <main class="field__main">
      <RouterView />
    </main>
    <nav
      class="field__nav"
      aria-label="Основная навигация"
    >
      <button
        v-for="item in nav"
        :key="item.label"
        type="button"
        class="field__nav-btn"
        :disabled="item.disabled"
        @click="go(item)"
      >
        {{ item.label }}
      </button>
    </nav>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

.field__header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #2e7d32;
  color: #fff;
  padding: 0.5rem 0.75rem;
  font-size: 1rem;
  position: sticky;
  top: 0;
  z-index: 500;
}

.field__brand {
  font-weight: 700;
}

.field__spacer {
  flex: 1;
}

.field__badge {
  background: #ffd600;
  color: #000;
  font-weight: 700;
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
}

.field__offline {
  color: #1b5e20;
  background: #fff;
  font-weight: 700;
  border-radius: 999px;
  padding: 0.25rem 0.7rem;
  font-size: 0.95rem;
  text-decoration: none;
  min-height: 48px;
  display: inline-flex;
  align-items: center;
}

.field__offline--down {
  background: #ff9800;
  color: #000;
}

.field__logout {
  background: transparent;
  color: #fff;
  border: 1px solid rgb(255 255 255 / 60%);
  border-radius: 6px;
  padding: 0.4rem 0.7rem;
  font-size: 1rem;
  min-height: 48px;
  cursor: pointer;
}

.field__main {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.field__nav {
  display: flex;
  background: #fff;
  border-top: 1px solid #c8e6c9;
  position: sticky;
  bottom: 0;
  z-index: 500;
}

.field__nav-btn {
  flex: 1;
  background: transparent;
  border: none;
  border-right: 1px solid #e8f5e9;
  padding: 0.7rem 0.25rem;
  font-size: 1rem;
  color: #1b1b1b;
  min-height: 48px;
  cursor: pointer;
}

.field__nav-btn:disabled {
  color: #9e9e9e;
  cursor: default;
}
</style>
