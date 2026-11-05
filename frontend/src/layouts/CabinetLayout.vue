<script setup lang="ts">
// Кабинетный layout: десктопные «кабинетные» экраны (07-ux §1.1, 10-pwa §1).
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

// Ссылки кабинета; restricted — роли с доступом (матрица прав 07-ux §5).
const NAV_ITEMS = [
  { label: 'Поля', to: { name: 'fields-map' } },
  { label: 'Севооборот', to: { name: 'rotation-plan' } },
  { label: 'Планы СЗР', to: { name: 'treatment-plans' } },
  { label: 'Погода', to: { name: 'weather' } },
  { label: 'Журнал', to: { name: 'operations-journal' } },
  { label: 'Техника', to: { name: 'machinery' } },
  { label: 'Справочники', to: { name: 'references' } },
  { label: 'Источники', to: { name: 'sources' }, roles: ['manager', 'admin'] },
  { label: 'Права', to: { name: 'users' }, roles: ['manager', 'admin'] },
] as const

/** Ссылки, доступные роли текущего пользователя (07-ux §5). */
const visibleNav = () =>
  NAV_ITEMS.filter((item) => !('roles' in item) || (auth.user && item.roles.includes(auth.user.role)))

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="cabinet">
    <header class="cabinet__header">
      <span class="cabinet__brand">ЦДА · {{ auth.user?.farm_name ?? 'Хозяйство' }}</span>
      <span class="cabinet__role">[роль: {{ auth.roleTitle }}]</span>
      <span class="cabinet__spacer" />
      <RouterLink
        class="cabinet__nav"
        :to="{ name: 'fields-map' }"
      >
        Поля
      </RouterLink>
      <RouterLink
        class="cabinet__nav"
        :to="{ name: 'rotation-plan' }"
      >
        Севооборот
      </RouterLink>
      <RouterLink
        class="cabinet__nav"
        :to="{ name: 'treatment-plans' }"
      >
        Планы СЗР
      </RouterLink>
      <RouterLink
        class="cabinet__nav"
        :to="{ name: 'field-map' }"
      >
        Полевой режим
      </RouterLink>
      <button
        type="button"
        class="cabinet__logout"
        @click="logout"
      >
        Выйти
      </button>
    </header>
    <main class="cabinet__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.cabinet {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

.cabinet__header {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: #2e7d32;
  color: #fff;
  padding: 0.6rem 1rem;
  font-size: 1.05rem;
  flex-wrap: wrap;
}

.cabinet__brand {
  font-weight: 700;
}

.cabinet__role {
  opacity: 0.9;
}

.cabinet__spacer {
  flex: 1;
}

.cabinet__nav {
  color: #fff;
  text-decoration: none;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  min-height: 48px;
  display: inline-flex;
  align-items: center;
}

.cabinet__nav.router-link-active {
  background: rgb(255 255 255 / 20%);
}

.cabinet__logout {
  background: #fff;
  color: #2e7d32;
  font-weight: 600;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 0.9rem;
  font-size: 1rem;
  min-height: 48px;
  cursor: pointer;
}

.cabinet__main {
  flex: 1;
  display: flex;
  flex-direction: column;
}
</style>
