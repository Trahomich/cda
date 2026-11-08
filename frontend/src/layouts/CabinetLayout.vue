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
  { label: 'Календарь', to: { name: 'work-calendar' } },
  { label: 'Журнал', to: { name: 'operations-journal' } },
  { label: 'Техника', to: { name: 'machinery' } },
  { label: 'Справочники', to: { name: 'references' } },
  { label: 'Источники', to: { name: 'sources' }, roles: ['manager', 'admin'] },
  { label: 'Права', to: { name: 'users' }, roles: ['manager', 'admin'] },
] as const

/** Разделы «почти готовы» (фидбек #398, #136): лёгкая точка-индикация
 * в меню. Когда остальные разделы дозреют — очистить массив одним
 * движением, индикация исчезнет. */
const READY_SECTIONS: ReadonlySet<string> = new Set(['fields-map', 'work-calendar', 'weather'])

/** У пункта меню индикатор «почти готово»? */
function isReady(item: (typeof NAV_ITEMS)[number]): boolean {
  return READY_SECTIONS.has(String(item.to.name))
}

/** Ссылки, доступные роли текущего пользователя (07-ux §5). */
const visibleNav = () =>
  NAV_ITEMS.filter(
    (item) => !('roles' in item) || (auth.user && (item.roles as readonly string[]).includes(auth.user.role)),
  )

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
        v-for="item in visibleNav()"
        :key="item.label"
        class="cabinet__nav"
        :class="{ 'cabinet__nav--ready': isReady(item) }"
        :to="item.to"
      >
        {{ item.label }}
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
  gap: 0.35rem;
  background: #2e7d32;
  color: #fff;
  padding: 0.4rem 0.75rem;
  font-size: 0.95rem;
  flex-wrap: wrap;
}

.cabinet__brand {
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 16rem;
}

.cabinet__role {
  opacity: 0.9;
  white-space: nowrap;
}

/* На узких экранах роль убираем — компактность шапки (v2-fix-J) */
@media (max-width: 1280px) {
  .cabinet__role {
    display: none;
  }
}

/* Совсем узкие экраны: ещё компактнее (v2-fix-J) */
@media (max-width: 1100px) {
  .cabinet__header {
    gap: 0.25rem;
    padding: 0.35rem 0.5rem;
  }

  .cabinet__nav {
    padding: 0.3rem 0.45rem;
    font-size: 0.88rem;
  }

  .cabinet__brand {
    max-width: 10rem;
  }
}

.cabinet__spacer {
  flex: 1;
}

.cabinet__nav {
  color: #fff;
  text-decoration: none;
  padding: 0.35rem 0.6rem;
  border-radius: 6px;
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  font-size: 0.92rem;
  white-space: nowrap;
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
  padding: 0.35rem 0.7rem;
  font-size: 0.92rem;
  min-height: 40px;
  cursor: pointer;
  white-space: nowrap;
}

.cabinet__main {
  flex: 1;
  display: flex;
  flex-direction: column;
}
</style>
