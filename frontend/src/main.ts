import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { apiClient } from './api/client'
import { useAuthStore } from './stores/auth'
import { useToastStore } from './stores/toast'
import { useOfflineStore } from './stores/offline'
import { registerSW } from 'virtual:pwa-register'
import './assets/main.css'
import 'leaflet/dist/leaflet.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// Единый показ ошибок API (RFC 7807 problem+json) — компонент AppToast.
const toast = useToastStore()
apiClient.setErrorHandler((error) => {
  toast.push('error', error.problem?.title ?? 'Ошибка', error.userMessage)
})

// Восстановление сессии до первого перехода роутера.
const auth = useAuthStore()
auth.restoreSession()

// 401 (истёкший/невалидный JWT, баг #59): сброс сессии и переход на /login
// с возвратом на текущий экран после повторного входа (как в guard роутера).
apiClient.setUnauthorizedHandler(() => {
  const current = router.currentRoute.value
  auth.logout()
  if (current.name !== 'login') {
    void router.push({
      name: 'login',
      query: current.meta.public ? {} : { redirect: current.fullPath },
    })
  }
})

// Офлайн-режим: слушатели сети + автосинхронизация очереди при 'online'.
const offline = useOfflineStore()
offline.init()

app.use(router)
app.mount('#app')

// Service Worker (vite-plugin-pwa): autoUpdate — «activate on next launch».
registerSW({ immediate: true })
