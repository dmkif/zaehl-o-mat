import { test, expect } from '../lib/fixtures'
import { PERSONAS, setIdp } from '../lib/personas'
import { rig } from '../lib/rig'

test.describe('SSO sign-in', () => {
  test('full flow lands on the dashboard; no token in any URL, binder cookie cleared', async ({ page, context, signIn, guard }) => {
    await page.goto('/login')
    await expect(page.getByRole('button', { name: 'Mit SSO anmelden' })).toBeVisible()
    // the binder cookie travels with the login redirect only
    await signIn(PERSONAS.user)

    await expect(page.locator('header').getByText(PERSONAS.user.username)).toBeVisible()
    // the SPA route /auth/token dropped the single-use code from the address bar
    expect(page.url()).toBe(`${rig().proxyUrl}/dashboard`)
    expect(new URL(page.url()).hash).toBe('')
    expect(page.url()).not.toMatch(/token=|code=/)

    // JWT only lives in sessionStorage, never in a URL, localStorage or a cookie
    const stored = await page.evaluate(() => ({
      session: sessionStorage.getItem('jwt'),
      local: JSON.stringify({ ...localStorage }),
    }))
    expect(stored.session).toMatch(/^eyJ/)
    expect(stored.local).not.toContain(stored.session!)
    const cookies = await context.cookies()
    expect(cookies.map((c) => c.name)).not.toContain('oidc_binder')
    expect(cookies.some((c) => c.value.includes(stored.session!))).toBe(false)
    expect(guard.urls.some((u) => u.includes(stored.session!))).toBe(false)
    expect(guard.assertNoTokenInUrls()).toEqual([])
    // the redirect chain really ran over the HTTPS edge
    expect(guard.urls.some((u) => u.startsWith(`${rig().proxyUrl}/api/auth/callback?`))).toBe(true)
  })

  test('the callback URL and the login code are single use (second tab, replay)', async ({ page, context, signIn, guard }) => {
    const callbacks: string[] = []
    const codes: string[] = []
    page.on('request', (r) => r.url().includes('/api/auth/callback?') && callbacks.push(r.url()))
    page.on('framenavigated', (f) => {
      const m = f.url().match(/#code=([^&]+)/)
      if (m && !codes.includes(m[1])) codes.push(m[1]) // fires again for the router's history updates
    })
    await signIn(PERSONAS.user)
    expect(callbacks).toHaveLength(1)
    expect(codes).toHaveLength(1)

    // second tab of the same browser, same cookies: the state is consumed and the binder cookie is gone
    guard.expect(400, /\/api\/auth\/callback/)
    const second = await context.newPage()
    const res = await second.goto(callbacks[0])
    expect(res?.status()).toBe(400)
    await expect(second.locator('body')).toContainText('Invalid state')
    expect(await second.evaluate(() => sessionStorage.getItem('jwt'))).toBeNull()
    await second.close()

    // the login code cannot be exchanged a second time
    const replay = await page.request.post('/api/auth/exchange', { data: { code: codes[0] } })
    expect(replay.status()).toBe(400)
  })

  test('a user at the IdP without a permitted group is refused', async ({ page, guard }) => {
    guard.expect(403, /\/api\/auth\/callback/)
    await setIdp({ ...PERSONAS.newcomer, sub: 'rig-e2e-sub-nogroup', username: 'rig-e2e-nogroup', email: 'rig-e2e-nogroup@rig.invalid', groups: [] })
    await page.goto('/login')
    await page.getByRole('button', { name: 'Mit SSO anmelden' }).click()
    await page.waitForURL(/\/api\/auth\/callback/)
    await expect(page.locator('body')).toContainText('not a member of any permitted group')
    expect(await page.evaluate(() => sessionStorage.getItem('jwt'))).toBeNull()
  })
})
