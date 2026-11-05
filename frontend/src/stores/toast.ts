// Хранилище тостов problem+json: единая точка показа ошибок RFC 7807.
import { defineStore } from 'pinia'

export interface Toast {
  id: number
  title: string
  detail: string
  kind: 'error' | 'info'
}

let nextId = 1

export const useToastStore = defineStore('toast', {
  state: () => ({
    toasts: [] as Toast[],
  }),
  actions: {
    push(kind: Toast['kind'], title: string, detail: string, timeoutMs = 6000) {
      const id = nextId++
      this.toasts.push({ id, title, detail, kind })
      if (timeoutMs > 0) {
        setTimeout(() => this.dismiss(id), timeoutMs)
      }
    },
    error(title: string, detail: string) {
      this.push('error', title, detail)
    },
    dismiss(id: number) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
  },
})
