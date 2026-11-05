// Авторизация: Pinia-хранилище, токен в localStorage, POST /auth через API-слой.
import { defineStore } from 'pinia'
import { login as apiLogin } from '@/api'
import { apiClient } from '@/api/client'
import type { UserRole } from '@/api/types'

const TOKEN_KEY = 'cda.token'
const USER_KEY = 'cda.user'

export interface AuthUser {
  name: string
  role: UserRole
  farm_name: string
}

function readStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const token = localStorage.getItem(TOKEN_KEY)
    const user = readStoredUser()
    return {
      token,
      user,
      loading: false,
    }
  },
  getters: {
    isAuthenticated: (state) => state.token !== null && state.user !== null,
    /** Название роли по docs/07-ux.md §2. */
    roleTitle(): string {
      const titles: Record<UserRole, string> = {
        agronom: 'Агроном',
        manager: 'Руководитель',
        operator: 'Механизатор',
        livestock: 'Зоотехник/вет.',
        admin: 'Администратор',
      }
      return this.user ? titles[this.user.role] : ''
    },
  },
  actions: {
    restoreSession() {
      apiClient.setToken(this.token)
    },
    async login(loginName: string, password: string) {
      this.loading = true
      try {
        const res = await apiLogin(loginName, password)
        this.token = res.token
        this.user = res.user
        localStorage.setItem(TOKEN_KEY, res.token)
        localStorage.setItem(USER_KEY, JSON.stringify(res.user))
        apiClient.setToken(res.token)
      } finally {
        this.loading = false
      }
    },
    logout() {
      this.token = null
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      apiClient.setToken(null)
    },
  },
})
