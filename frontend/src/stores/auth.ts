import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '@/utils/api'

interface AuthUser {
  id: string
  username: string
  email: string
  role: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin' || user.value?.role === 'superadmin')
  const isManager = computed(() => isAdmin.value || user.value?.role === 'manager')

  function setToken(t: string) {
    token.value = t
    sessionStorage.setItem('jwt', t)
  }

  // Restoring is a one-shot promise the router guard can wait for: a reload of /admin must not be decided
  // before /auth/me told us the role (otherwise an admin is bounced to the dashboard).
  let restoring: Promise<void> | null = null

  function tryRestoreSession(): Promise<void> {
    if (!restoring) {
      const stored = sessionStorage.getItem('jwt')
      if (stored) {
        token.value = stored
        restoring = fetchMe()
      } else {
        restoring = Promise.resolve()
      }
    }
    return restoring
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      const res = await apiFetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token.value}` },
      })
      if (res.ok) {
        user.value = await res.json()
      } else {
        logout()
      }
    } catch {
      logout()
    }
  }

  function logout() {
    token.value = null
    user.value = null
    sessionStorage.removeItem('jwt')
  }

  return { token, user, isLoggedIn, isAdmin, isManager, setToken, tryRestoreSession, fetchMe, logout }
})
