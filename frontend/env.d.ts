/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Базовый URL REST API (docs/06-data-api.md §5, префикс /api/v1). */
  readonly VITE_API_BASE?: string
  /** 'true' — использовать мок-транспорт вместо реальных запросов (dev-режим). */
  readonly VITE_USE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// Типы для vite-plugin-pwa (виртуальный модуль virtual:pwa-register).
declare module 'virtual:pwa-register' {
  export function registerSW(options?: {
    immediate?: boolean
    onNeedRefresh?: () => void
    onOfflineReady?: () => void
    onRegisteredSW?: (url: string, r?: ServiceWorkerRegistration) => void
  }): (reloadPage?: boolean) => Promise<void> | void
}
