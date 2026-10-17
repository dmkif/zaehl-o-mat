import { test, expect } from '../lib/fixtures'
import { PERSONAS } from '../lib/personas'

test.describe('logout and session', () => {
  test('a page reload keeps the session', async ({ page, signIn }) => {
    await signIn(PERSONAS.user)
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    await expect(page.locator('header').getByText(PERSONAS.user.username)).toBeVisible()
  })

  test('logout revokes the token server side and the SPA shows the login again', async ({ page, signIn, guard }) => {
    await signIn(PERSONAS.user)
    const token = await page.evaluate(() => sessionStorage.getItem('jwt'))
    expect(token).toBeTruthy()
    const auth = { Authorization: `Bearer ${token}` }
    expect((await page.request.get('/api/auth/me', { headers: auth })).status()).toBe(200)

    await page.getByRole('button', { name: 'Abmelden' }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('button', { name: 'Mit SSO anmelden' })).toBeVisible()
    expect(await page.evaluate(() => sessionStorage.getItem('jwt'))).toBeNull()

    // the old token is dead, not just forgotten by the browser
    expect((await page.request.get('/api/auth/me', { headers: auth })).status()).toBe(401)
    // a reload and a direct visit of a protected page show the login
    await page.reload()
    await expect(page.getByRole('button', { name: 'Mit SSO anmelden' })).toBeVisible()
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login$/)
    expect(guard.assertNoTokenInUrls()).toEqual([])
  })

  test('a session whose token was revoked elsewhere is sent to the login on the next API call', async ({ page, signIn, guard }) => {
    await signIn(PERSONAS.user)
    const token = await page.evaluate(() => sessionStorage.getItem('jwt'))
    await page.request.post('/api/auth/logout', { headers: { Authorization: `Bearer ${token}` } }) // "other device"
    guard.expect(401, /\/api\//)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Mit SSO anmelden' })).toBeVisible()
  })
})
