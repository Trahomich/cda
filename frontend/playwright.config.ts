import { defineConfig, devices } from '@playwright/test'

// E2E-проверка офлайн-сценария 10-mobile-pwa §3.4.
// Запускается против dev-сервера (мок-транспорт по ADR-07): офлайн-очередь
// и синхронизация не зависят от Service Worker, installability проверена в #21.
// PW_PORT — переопределить порт (полезно, когда 5173 занят чужим процессом).
const PW_PORT = Number(process.env.PW_PORT ?? 5173)

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: `http://localhost:${PW_PORT}`,
    trace: 'off',
  },
  webServer: {
    command: 'npm run dev',
    port: PW_PORT,
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
