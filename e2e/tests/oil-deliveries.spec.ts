import { test, expect } from '../lib/fixtures'
import { PERSONAS } from '../lib/personas'

// Tank levels of the reference tank (specs/005-oil-deliveries): 8 readings, 164 days, 1374 -> 816 L.
// Dates are shifted relative to today (the consumption window is the last 365 days): last reading 34 days ago.
const LEVELS: [number, number][] = [
  [198, 1374], [178, 1353], [158, 1365], [136, 1289], [114, 1207], [90, 1074], [63, 945], [34, 816],
]

const deliveryCards = (page: import('@playwright/test').Page) =>
  page.locator('div.rounded-xl.shadow-sm', { hasText: / L/ })

test.describe('oil deliveries', () => {
  test('consumption tile and range from tank readings (~3.4 L/day, ~240 days)', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e oil tile')).id)
    await api.oilTank(prop, 'e2e tank tile', LEVELS)
    await signIn(PERSONAS.admin)

    const card = page.locator('a', { hasText: 'e2e tank tile' })
    await expect(card).toContainText('816')
    // tile: average per day over the known period, 558 L / 164 d
    await expect(card).toContainText(/∅ 3,[3-5]\d* L\/Tag/)
    // range = level / rate, about 240 days; the property section renders "Reichweite: N Tage"
    const range = page.getByText(/Reichweite: \d+ Tage/)
    await expect(range.first()).toBeVisible()
    const days = (await range.allTextContents()).map((t) => Number(t.match(/(\d+) Tage/)![1]))
    expect(days.some((d) => d >= 225 && d <= 250), `Reichweite values ${days}`).toBe(true)
    await expect(page.getByText('Vermutliche Heizöl-Lieferung')).toHaveCount(0) // noise rises are not deliveries
  })

  test('create, edit and delete a delivery', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e oil crud')).id)
    await signIn(PERSONAS.admin)
    await page.goto(`/properties/${prop}/oil-deliveries`)
    await expect(page.getByText('Noch keine Lieferungen erfasst.')).toBeVisible()

    // create
    await page.getByRole('button', { name: '+ Lieferung erfassen' }).click()
    const dialog = page.locator('form')
    await dialog.getByLabel('Lieferzeitpunkt').fill('2026-10-01T10:30')
    await dialog.getByLabel('Menge (Liter)').fill('1500')
    await dialog.getByLabel('Gesamtpreis').fill('1650')
    await dialog.getByLabel('Lieferant').fill('E2E Heizöl GmbH')
    await dialog.getByLabel('Notiz').fill('first delivery')
    await dialog.getByRole('button', { name: 'Speichern' }).click()
    await expect(dialog).toHaveCount(0)
    const cards = deliveryCards(page)
    await expect(cards).toHaveCount(1)
    await expect(cards.first()).toContainText('1.500 L')
    await expect(cards.first()).toContainText('1.650 €')
    await expect(cards.first()).toContainText('1,1 €/L') // derived price per litre
    await expect(cards.first()).toContainText('E2E Heizöl GmbH')

    // edit
    await page.getByTitle('Lieferung bearbeiten').click()
    await expect(dialog.getByLabel('Menge (Liter)')).toHaveValue(/^1500(\.0+)?$/)
    await dialog.getByLabel('Menge (Liter)').fill('1600')
    await dialog.getByLabel('Gesamtpreis').fill('1760')
    await dialog.getByRole('button', { name: 'Speichern' }).click()
    await expect(cards.first()).toContainText('1.600 L')
    await expect(cards.first()).toContainText('1.760 €')

    // invalid input is refused in the form, nothing is sent
    await page.getByTitle('Lieferung bearbeiten').click()
    await dialog.getByLabel('Menge (Liter)').fill('0')
    await dialog.getByRole('button', { name: 'Speichern' }).click()
    await expect(dialog.getByRole('alert')).toContainText('Die Menge muss größer als 0 sein.')
    await dialog.getByRole('button', { name: 'Abbrechen' }).click()

    // delete (confirm dialog)
    await page.getByTitle('Löschen').click()
    const confirm = page.locator('div.fixed', { hasText: 'Lieferung löschen?' })
    await expect(confirm).toContainText('1.600 L')
    await confirm.getByRole('button', { name: 'Löschen', exact: true }).click()
    await expect(page.getByText('Noch keine Lieferungen erfasst.')).toBeVisible()
  })

  test('probable delivery: notice on the dashboard, confirm creates the delivery from the prefill', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e oil probable')).id)
    // 700 -> 1600 L: a rise of 900 L between two readings is a refill, not noise
    await api.oilTank(prop, 'e2e tank probable', [[50, 1000], [40, 850], [30, 700], [10, 1600]])
    await signIn(PERSONAS.admin)

    const notice = page.getByRole('status').filter({ hasText: 'Vermutliche Heizöl-Lieferung' })
    await expect(notice).toHaveCount(1)
    await expect(notice).toContainText(/ca\. 9\d\d Liter/)
    await notice.getByRole('button', { name: 'Als Lieferung erfassen' }).click()

    await expect(page).toHaveURL(new RegExp(`/properties/${prop}/oil-deliveries$`)) // prefill query dropped again
    const form = page.locator('form')
    await expect(form.getByLabel('Menge (Liter)')).toHaveValue(/^9\d\d$/)
    await expect(form.getByLabel('Lieferzeitpunkt')).not.toHaveValue('')
    await form.getByRole('button', { name: 'Speichern' }).click()
    await expect(deliveryCards(page)).toHaveCount(1)

    // with the delivery recorded the interval is known: no notice any more, consumption is computed
    await page.getByRole('link', { name: 'Dashboard' }).first().click()
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    await expect(page.locator('a', { hasText: 'e2e tank probable' })).toContainText('∅')
    await expect(page.getByText('Vermutliche Heizöl-Lieferung')).toHaveCount(0)
  })

  test('probable delivery: dismissed as a reading error it stays gone', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e oil dismiss')).id)
    await api.oilTank(prop, 'e2e tank dismiss', [[50, 1000], [40, 850], [30, 700], [10, 1600]])
    await signIn(PERSONAS.admin)

    const notice = page.getByRole('status').filter({ hasText: 'Vermutliche Heizöl-Lieferung' })
    await expect(notice).toHaveCount(1)
    await notice.getByRole('button', { name: 'Als Lesefehler verwerfen' }).click()
    await expect(notice).toHaveCount(0)
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    await expect(page.locator('a', { hasText: 'e2e tank dismiss' })).toBeVisible()
    await expect(page.getByText('Vermutliche Heizöl-Lieferung')).toHaveCount(0)
  })

  test('a plain user sees the notice but has no buttons to act on it', async ({ page, api, track, signIn }) => {
    const prop = track((await api.createProperty('e2e oil readonly')).id)
    await api.oilTank(prop, 'e2e tank readonly', [[50, 1000], [40, 850], [30, 700], [10, 1600]])
    await api.addMember(prop, PERSONAS.user.username, 'user')
    await signIn(PERSONAS.user)
    await expect(page.getByRole('status').filter({ hasText: 'Vermutliche Heizöl-Lieferung' })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'Als Lieferung erfassen' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Als Lesefehler verwerfen' })).toHaveCount(0)
  })
})
