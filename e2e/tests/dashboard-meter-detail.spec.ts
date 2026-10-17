import { test, expect } from '../lib/fixtures'
import { PERSONAS } from '../lib/personas'
import { rig, seed } from '../lib/rig'

test.describe('dashboard and meter detail', () => {
  test('dashboard shows the user\'s meters, a card opens the detail with its readings', async ({ page, signIn }) => {
    await signIn(PERSONAS.user)
    const s = seed()
    await expect(page.getByText('rig water a', { exact: true })).toBeVisible()
    await expect(page.getByText('rig water2 a', { exact: true })).toBeVisible()
    await expect(page.getByText('rig water b')).toHaveCount(0) // prop-b belongs to someone else
    await expect(page.getByText('Aktive Zähler')).toBeVisible()

    await page.getByText('rig water a', { exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/properties/${s.properties.prop_a.id}/meters/${s.meters.meter_a_water.id}$`))
    await expect(page.getByRole('heading', { name: 'Ablesungen' })).toBeVisible()
    // the 3 seeded readings 100 / 110 / 120 m³, newest first
    const rows = page.locator('tbody tr')
    await expect(rows).toHaveCount(3)
    await expect(rows.first()).toContainText('120')
    await expect(rows.last()).toContainText('100')
  })

  // REGRESSION (v0.6.0 -> v0.6.1): the SPA requests ".../readings?limit=200" WITHOUT the trailing slash, the backend
  // answers with a redirect to ".../readings/?limit=200". Behind the TLS-terminating proxy that Location was
  // "http://...", which the page CSP (connect-src 'self' blob:) blocked: an empty readings table. The API-level rig
  // never saw it, because it never went through a browser and an HTTPS edge.
  test('readings table loads through the trailing-slash redirect, which stays https', async ({ page, signIn }) => {
    const s = seed()
    await signIn(PERSONAS.user)
    const meter = s.meters.meter_a_water2.id
    const prop = s.properties.prop_a.id
    const listed = page.waitForResponse((r) => /\/readings\/\?limit=200/.test(r.url()) && r.status() === 200)
    await page.goto(`/properties/${prop}/meters/${meter}`)
    const final = await listed

    expect(final.url().startsWith(`${rig().proxyUrl}/`)).toBe(true) // https origin of the edge
    const first = final.request().redirectedFrom()
    expect(first, 'the SPA calls the slash-less URL, so a redirect must have happened').not.toBeNull()
    expect(first!.url()).toMatch(/\/readings\?limit=200$/)
    const redirect = await first!.response()
    expect(redirect!.status()).toBe(307)
    expect((await redirect!.allHeaders())['location']).toMatch(/^https:\/\//)

    const rows = page.locator('tbody tr')
    await expect(rows).toHaveCount(3)
    await expect(rows.first()).toContainText('5020') // water2: base 5000 + 2 * 10
  })

  test('the page itself is served with the production security headers', async ({ page, signIn }) => {
    await signIn(PERSONAS.user)
    const res = await page.goto('/dashboard')
    const h = await res!.allHeaders()
    expect(h['content-security-policy']).toContain("connect-src 'self' blob:")
    expect(h['x-frame-options']).toBe('DENY')
    expect(h['x-content-type-options']).toBe('nosniff')
    expect(h['strict-transport-security']).toMatch(/max-age=\d+/)
  })
})
