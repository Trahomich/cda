import { defineConfig, devices } from '@playwright/test'

// E2E-проверка офлайн-сценария 10-mobile-pwa §3.4.
// Запускается против dev-сервера (мок-транспорт по ADR-07): офлайн-очередь
// и синхронизация не зависят от Service Worker, installability проверена в #21.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'off',
  },
  webServer: {
    command: 'npm run dev',
    port: 5173,
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
