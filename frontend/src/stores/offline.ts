// Состояние офлайн-режима: сеть, очередь, синхронизация (10-mobile-pwa §3).
import { defineStore } from 'pinia'
import {
  listQueue,
  syncQueue,
  type SyncResult,
} from '@/services/offline'
import type { QueueRecord } from '@/services/db'
import { useToastStore } from './toast'

export const useOfflineStore = defineStore('offline', {
  state: () => ({
    online: typeof navigator === 'undefined' ? true : navigator.onLine,
    queue: [] as QueueRecord[],
    syncing: false,
  }),
  getters: {
    pendingCount: (state) => state.queue.filter((r) => r.status !== 'sending').length,
  },
  actions: {
    init() {
      window.addEventListener('online', () => {
        this.online = true
        void this.autoSync()
      })
      window.addEventListener('offline', () => {
        this.online = false
      })
      void this.refresh()
    },
    async refresh() {
      this.queue = await listQueue()
    },
    /** Автосинхронизация при появлении сети (Android Background Sync — вне MVP). */
    async autoSync() {
      if (!this.online) return
      await this.runSync(false)
    },
    /** Ручная кнопка «Синхронизировать» (обязательна для iOS, 10-pwa §2). */
    async runSync(notify = true): Promise<SyncResult> {
      if (this.syncing || !this.online) {
        return { sent: 0, rejected: 0, remaining: this.queue.length }
      }
      this.syncing = true
      try {
        const result = await syncQueue()
        await this.refresh()
        if (notify) {
          const toast = useToastStore()
          toast.push(
            result.rejected > 0 ? 'error' : 'info',
            'Синхронизация завершена',
            `Отправлено: ${result.sent}. Отклонено сервером: ${result.rejected}. В очереди: ${result.remaining}.`,
          )
        }
        return result
      } finally {
        this.syncing = false
      }
    },
  },
})
