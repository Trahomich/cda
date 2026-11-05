import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// Конфигурация клиента ЦДА (ADR-06: Vue 3 + Vite + TS; PWA — vite-plugin-pwa,
// manifest по docs/10-mobile-pwa.md §2: name, иконки 192/512, standalone).
export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate', // «activate on next launch» — 10-mobile-pwa §5
      includeAssets: ['offline.html'],
      manifest: {
        name: 'ЦДА — Цифровой двойник агронома',
        short_name: 'ЦДА',
        description:
          'Мобильный клиент ИС «Цифровой двойник агронома»: поля, работы, погода, офлайн-очередь.',
        lang: 'ru',
        dir: 'ltr',
        display: 'standalone',
        orientation: 'any',
        start_url: '/',
        scope: '/',
        background_color: '#ffffff',
        theme_color: '#2e7d32',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/icons/icon-maskable-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        navigateFallback: 'index.html', // офлайн-старт SPA-оболочки
        navigateFallbackDenylist: [/^\/offline\.html$/],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: true,
  },
  preview: {
    port: 4173,
    host: true,
  },
})
