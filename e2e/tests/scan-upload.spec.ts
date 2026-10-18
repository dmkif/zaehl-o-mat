import path from 'node:path'
import { test, expect } from '../lib/fixtures'
import { PERSONAS } from '../lib/personas'

// Scan dialog: upload a (synthetic) photo -> crop -> serial crop (skipped) -> /api/ocr/scan -> prefilled value -> save.
// Uses blob: URLs for the preview/crop, which only works because the production CSP allows `blob:` in img-src and
// connect-src: a tightened CSP breaks this flow first.
// "Weiter" is disabled until the cropper has decoded the image (spec 014): one click, no retry workaround.
async function toSerialStep(page: import('@playwright/test').Page) {
  const weiter = page.getByRole('button', { name: 'Weiter' })
  await expect(weiter).toBeEnabled()
  await weiter.click()
  await expect(page.getByText('Schritt 2: Seriennummer (optional)')).toBeVisible()
}

test.describe('scan upload', () => {
  test('photo upload through the scan dialog prefills the reading (fake gateway) and saves it', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e scan')).id)
    const meter = (await api.createMeter(prop, { meter_type: 'water', unit: 'm³', name: 'e2e scan meter', serial_number: 'E2E-SCAN-1' })).id
    await signIn(PERSONAS.admin)
    await page.goto(`/properties/${prop}/meters/${meter}`)
    await expect(page.getByRole('heading', { name: 'Ablesung erfassen' })).toBeVisible()

    await page.locator('label', { hasText: 'Bild hochladen' }).locator('input[type=file]')
      .setInputFiles(path.resolve(__dirname, '../fixtures/meter.jpg'))
    await expect(page.getByText('Markiere den Zählerstand-Bereich')).toBeVisible()
    await expect(page.locator('.vue-advanced-cropper')).toBeVisible()
    await toSerialStep(page)

    const scan = page.waitForResponse((r) => r.url().endsWith('/api/ocr/scan') && r.request().method() === 'POST')
    await page.getByRole('button', { name: 'Überspringen' }).click()
    const res = await scan
    expect(res.status()).toBe(200)
    const body = await res.json()
    expect(body.detected_value).toBe('12345.6') // fake gateway answer

    const value = page.locator('input[inputmode=decimal]')
    await expect(value).toHaveValue('12345.6')
    await expect(page.getByRole('main').getByText('LLM', { exact: true })).toBeVisible()
    await expect(page.getByAltText('preview')).toBeVisible()

    await page.getByRole('button', { name: 'Ablesung speichern' }).click()
    const rows = page.locator('tbody tr')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('12345.6')
    // the stored photo can be fetched again with the session (rescan dialog shows it as a blob: image)
    await rows.first().getByTitle('Bild neu auswerten').click()
    await expect(page.locator('img.object-contain')).toBeVisible()
  })

  test('"Weiter" stays disabled with a hint until the image is decoded, then one click advances', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e scan gate')).id)
    const meter = (await api.createMeter(prop, { meter_type: 'water', unit: 'm³', name: 'e2e scan gate meter' })).id
    // Hold back the `load` event of images (the cropper waits for it) until the test releases them.
    await page.addInitScript(() => {
      const w = window as any
      w.__held = [] as Array<() => void>
      w.__hold = true
      const orig = HTMLImageElement.prototype.addEventListener
      HTMLImageElement.prototype.addEventListener = function (this: HTMLImageElement, type: string, fn: any, opts?: any) {
        if (type !== 'load' || typeof fn !== 'function') return orig.call(this, type, fn, opts)
        return orig.call(this, type, (ev: Event) => (w.__hold ? w.__held.push(() => fn.call(this, ev)) : fn.call(this, ev)), opts)
      } as typeof orig
    })
    await signIn(PERSONAS.admin)
    await page.goto(`/properties/${prop}/meters/${meter}`)
    await page.locator('label', { hasText: 'Bild hochladen' }).locator('input[type=file]')
      .setInputFiles(path.resolve(__dirname, '../fixtures/meter.jpg'))

    const weiter = page.getByRole('button', { name: 'Weiter' })
    await expect(page.getByText('Markiere den Zählerstand-Bereich')).toBeVisible()
    await expect(weiter).toBeDisabled()
    await expect(page.getByTestId('crop-loading')).toBeVisible()
    await page.waitForTimeout(500)
    await expect(weiter).toBeDisabled() // still held: a click would have been a silent no-op before

    await page.evaluate(() => { const w = window as any; w.__hold = false; w.__held.splice(0).forEach((f: () => void) => f()) })
    await expect(weiter).toBeEnabled()
    await expect(page.getByTestId('crop-loading')).toHaveCount(0)
    await weiter.click()
    await expect(page.getByText('Schritt 2: Seriennummer (optional)')).toBeVisible()
  })

  test('uploading the same photo twice is refused as a duplicate', async ({ page, api, track, signIn, guard }) => {
    const prop = track((await api.createProperty('e2e scan dup')).id)
    const meter = (await api.createMeter(prop, { meter_type: 'water', unit: 'm³', name: 'e2e scan dup meter' })).id
    await signIn(PERSONAS.admin)
    await page.goto(`/properties/${prop}/meters/${meter}`)
    const file = path.resolve(__dirname, '../fixtures/meter.jpg')
    for (let i = 0; i < 2; i++) {
      await page.locator('label', { hasText: 'Bild hochladen' }).locator('input[type=file]').setInputFiles(file)
      await toSerialStep(page)
      await page.getByRole('button', { name: 'Überspringen' }).click()
      await expect(page.locator('input[inputmode=decimal]')).not.toHaveValue('')
      await page.getByRole('button', { name: 'Ablesung speichern' }).click()
      if (i === 0) {
        await expect(page.locator('tbody tr')).toHaveCount(1)
      } else {
        guard.expect(409, /\/readings\//)
        // the browser encodes the same crop to the same bytes: same image hash -> 409 duplicate_image
        await expect(page.getByText(/Dieses Bild wurde bereits gespeichert/)).toBeVisible()
        await expect(page.locator('tbody tr')).toHaveCount(1)
      }
    }
  })
})
