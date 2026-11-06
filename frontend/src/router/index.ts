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
        {
          // E-10 «Погода и погодные окна» (07-ux §2, SC-03): факт, прогноз,
          // окна операций RULE-AGRO-09.
          path: 'weather',
          name: 'weather',
          component: () => import('@/views/WeatherView.vue'),
          meta: { title: 'Погода' },
        },
        {
          // «Календарь работ» (v2, docs/06 §7.2): месячная сетка план+факт —
          // фронт-композиция API-18 (операции) и API-15 (планы внесения).
          path: 'calendar',
          name: 'work-calendar',
          component: () => import('@/views/WorkCalendarView.vue'),
          meta: { title: 'Календарь работ' },
        },
        {
          // E-13 «Журнал операций» (состав MVP 08 §1): API-18, отклонения RULE-AGRO-11.
          path: 'operations',
          name: 'operations-journal',
          component: () => import('@/views/OperationsJournalView.vue'),
          meta: { title: 'Журнал операций' },
        },
        {
          // E-18 «Техника и задания» (состав MVP 08 §1): API-19/20, read-only.
          path: 'machinery',
          name: 'machinery',
          component: () => import('@/views/MachineryView.vue'),
          meta: { title: 'Техника' },
        },
        {
          // E-15 «Справочники» (07-ux §2): культуры, препараты.
          path: 'references',
          name: 'references',
          component: () => import('@/views/ReferencesView.vue'),
          meta: { title: 'Справочники' },
        },
        {
          // E-14 «Источники данных» (07-ux §5): руководитель/админ, API-28.
          path: 'sources',
          name: 'sources',
          component: () => import('@/views/SourcesView.vue'),
          meta: { title: 'Источники данных', roles: ['manager', 'admin'] },
        },
        {
          // E-16 «Пользователи и права» (07-ux §5): админ/руководитель.
          path: 'users',
          name: 'users',
          component: () => import('@/views/UsersView.vue'),
          meta: { title: 'Пользователи и права', roles: ['manager', 'admin'] },
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
  // /login при живой сессии — сразу в приложение (без цикла редиректов, #58).
  if (to.name === 'login' && auth.isAuthenticated) {
    return { name: 'fields-map' }
  }
  // Ограничение по ролям (meta.roles, матрица прав 07-ux §5).
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.user && !roles.includes(auth.user.role)) {
    return { name: 'fields-map' }
  }
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? 'ЦДА'
  document.title = `${title} · ЦДА`
})

export default router
