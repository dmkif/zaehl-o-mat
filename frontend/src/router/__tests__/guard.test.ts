import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const me = vi.hoisted(() => ({ role: 'admin', ok: true }))

// apiFetch itself imports the router; the guard only needs /auth/me.
vi.mock('@/utils/api', () => ({
  apiFetch: vi.fn(async () => ({
    ok: me.ok,
    status: me.ok ? 200 : 401,
    json: async () => ({ id: '1', username: 'u', email: 'u@x.invalid', role: me.role }),
  })),
}))

describe('router guard on a page load with a stored session (e2e finding: reload of /admin)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    me.role = 'admin'
    me.ok = true
    vi.resetModules()
  })

  async function load(path: string) {
    const { default: router } = await import('../index')
    await router.push(path)
    await router.isReady()
    return router.currentRoute.value.path
  }

  it('keeps an admin on /admin although the role arrives asynchronously', async () => {
    sessionStorage.setItem('jwt', 'tok')
    expect(await load('/admin')).toBe('/admin')
  })

  it('sends a non-admin from /admin to the dashboard', async () => {
    me.role = 'user'
    sessionStorage.setItem('jwt', 'tok')
    expect(await load('/admin')).toBe('/dashboard')
  })

  it('sends a revoked session to the login', async () => {
    me.ok = false
    sessionStorage.setItem('jwt', 'revoked')
    expect(await load('/dashboard')).toBe('/login')
  })

  it('sends an anonymous visitor to the login', async () => {
    expect(await load('/properties')).toBe('/login')
  })
})
