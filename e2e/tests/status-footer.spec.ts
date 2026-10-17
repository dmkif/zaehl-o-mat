import { test, expect } from '../lib/fixtures'
import { PERSONAS } from '../lib/personas'

// The footer reads /api/health (anonymous, status only) for the backend light and /api/health/details
// (sign-in required) for OCR / LLM. A backend that does not offer the details endpoint, or a frontend that still
// reads the anonymous endpoint, leaves the lights red/grey: that is what this test catches.
test.describe('status footer', () => {
  const light = (page: import('@playwright/test').Page, label: string) =>
    page.locator('span.inline-flex', { hasText: label }).first().locator('.status-dot')

  test('signed-in admin: backend, OCR and LLM green, versions shown', async ({ page, signIn, guard }) => {
    const details = page.waitForResponse((r) => r.url().endsWith('/api/health/details'))
    await signIn(PERSONAS.admin)
    const res = await details
    expect(res.status()).toBe(200)
    expect(res.request().headers()['authorization']).toMatch(/^Bearer /)
    expect(await res.json()).toMatchObject({ status: 'ok', db: true, scheduler: true, ocr: true, llm: true })

    await expect(light(page, 'Backend')).toHaveClass(/bg-green-500/)
    await expect(light(page, 'OCR')).toHaveClass(/bg-green-500/)
    await expect(light(page, 'LLM')).toHaveClass(/bg-green-500/)
    await expect(page.getByText(/Backend v\d+\.\d+/)).toBeVisible() // version is admin only
    expect(guard.apiResponses.some((r) => r.url.endsWith('/api/health') && r.status === 200)).toBe(true)
  })

  test('signed-in plain user: lights green, but no version or model name', async ({ page, signIn }) => {
    await signIn(PERSONAS.user)
    await expect(light(page, 'OCR')).toHaveClass(/bg-green-500/)
    await expect(light(page, 'LLM')).toHaveClass(/bg-green-500/)
    await expect(light(page, 'Backend')).toHaveClass(/bg-green-500/)
    await expect(page.getByText(/Backend v\d/)).toHaveCount(0)
    await expect(page.getByText(/Frontend v\d/)).toBeVisible()
  })

  test('the anonymous health endpoint says nothing but the status', async ({ page, guard }) => {
    const res = await page.request.get('/api/health')
    expect(res.status()).toBe(200)
    expect(await res.json()).toEqual({ status: 'ok' })
    guard.expect(401, /\/api\/health\/details/)
    expect((await page.request.get('/api/health/details')).status()).toBe(401)
  })
})
