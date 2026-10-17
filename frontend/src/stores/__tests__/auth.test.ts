import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// apiFetch pulls in the router (and thus every view); the store only needs fetch.
vi.mock('@/utils/api', () => ({
  apiFetch: vi.fn(async () => ({ ok: false, status: 401 })),
}))

import { useAuthStore } from '../auth'

describe('auth store token handling (F-042)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('keeps the JWT in sessionStorage only, never localStorage', () => {
    const auth = useAuthStore()
    auth.setToken('abc')
    expect(sessionStorage.getItem('jwt')).toBe('abc')
    expect(localStorage.getItem('jwt')).toBeNull()
  })

  it('logout removes the token from memory and sessionStorage', () => {
    const auth = useAuthStore()
    auth.setToken('abc')
    auth.logout()
    expect(auth.token).toBeNull()
    expect(auth.isLoggedIn).toBe(false)
    expect(sessionStorage.getItem('jwt')).toBeNull()
  })

  it('drops a stored token the server rejects (401 on /me)', async () => {
    sessionStorage.setItem('jwt', 'stale')
    const auth = useAuthStore()
    auth.tryRestoreSession()
    await vi.waitFor(() => expect(sessionStorage.getItem('jwt')).toBeNull())
    expect(auth.isLoggedIn).toBe(false)
  })
})
