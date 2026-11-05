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
        {
          // E-03 «Поле 360°» (07-ux §3.2): карточка поля из списка/карты.
          path: 'fields/:fieldId',
          name: 'field-card',
          component: () => import('@/views/FieldCardView.vue'),
          meta: { title: 'Карточка поля' },
        },
        {
          // E-04 «План севооборота» (07-ux §3.3): матрица поле × год.
          path: 'rotation',
          name: 'rotation-plan',
          component: () => import('@/views/RotationPlanView.vue'),
          meta: { title: 'План севооборота' },
        },
        {
          // E-05 «Планы удобрений и СЗР» (07-ux §2): список планов + создание.
          path: 'treatment-plans',
          name: 'treatment-plans',
          component: () => import('@/views/TreatmentPlansView.vue'),
          meta: { title: 'Планы удобрений и СЗР' },
        },
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
        {
          path: 'operations',
          name: 'field-operations',
          component: () => import('@/views/OperationEntryView.vue'),
          meta: { title: 'Журнал работ' },
        },
        {
          path: 'queue',
          name: 'field-queue',
          component: () => import('@/views/OfflineQueueView.vue'),
          meta: { title: 'Офлайн-очередь' },
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
