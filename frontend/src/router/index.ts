import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

// Роутер с двумя layout'ами (docs/10-mobile-pwa.md §1):
//  - кабинетный (CabinetLayout): десктопные «кабинетные» экраны;
//  - полевой (FieldLayout): мобильный PWA-режим, нижняя навигация (E-01 wireframe).
// Экраны каркаса: карта полей и список полей (задача #21); остальные — заглушки.
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/CabinetLayout.vue'),
      children: [
        { path: '', redirect: { name: 'fields-map' } },
        {
          path: 'fields/map',
          name: 'fields-map',
          component: () => import('@/views/FieldsMapView.vue'),
          meta: { title: 'Карта полей' },
        },
        // Список полей — вкладка экрана карты (решение в обсуждении #21).
        { path: 'fields', redirect: { name: 'fields-map' } },
      ],
    },
    {
      path: '/f',
      component: () => import('@/layouts/FieldLayout.vue'),
      children: [
        { path: '', redirect: { name: 'field-map' } },
        {
          path: 'map',
          name: 'field-map',
          component: () => import('@/views/FieldsMapView.vue'),
          meta: { title: 'Карта полей' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: { name: 'fields-map' } },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? 'ЦДА'
  document.title = `${title} · ЦДА`
})

export default router
