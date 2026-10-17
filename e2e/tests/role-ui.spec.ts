import { test, expect } from '../lib/fixtures'
import { PERSONAS } from '../lib/personas'

// What each role gets to see. The SPA decides by the global role (isAdmin / isManager); the backend decides by the
// property membership. Both are set up consistently here: manager = property manager, user = property user.
test.describe('role dependent UI', () => {
  let propertyId: string

  test.beforeEach(async ({ api, track }) => {
    propertyId = track((await api.createProperty('e2e roles')).id)
    await api.createMeter(propertyId, { meter_type: 'water', unit: 'm³', name: 'e2e roles meter', serial_number: 'E2E-R-1' })
    await api.call('post', `/api/properties/${propertyId}/oil-deliveries/`, { delivered_at: new Date().toISOString(), liters: 1000 })
    await api.addMember(propertyId, PERSONAS.manager.username, 'manager')
    await api.addMember(propertyId, PERSONAS.user.username, 'user')
  })

  test('admin: administration, create, edit and delete everywhere', async ({ page, signIn }) => {
    await signIn(PERSONAS.admin)
    await expect(page.locator('header nav').getByRole('link', { name: 'Administration' })).toBeVisible()
    await page.goto('/admin')
    await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'rig-user-a', exact: true })).toBeVisible()

    await page.goto('/properties')
    await expect(page.getByRole('button', { name: '+ Objekt hinzufügen' })).toBeVisible()
    await expect(page.getByTitle('Objekt löschen').first()).toBeVisible()

    await page.goto(`/properties/${propertyId}/meters`)
    await expect(page.getByRole('button', { name: '+ Zähler hinzufügen' })).toBeVisible()
    await expect(page.getByTitle('Bearbeiten').first()).toBeVisible()
    await expect(page.getByTitle('Zähler löschen').first()).toBeVisible()

    await page.goto(`/properties/${propertyId}/oil-deliveries`)
    await expect(page.getByRole('button', { name: '+ Lieferung erfassen' })).toBeVisible()
    await expect(page.getByTitle('Lieferung bearbeiten')).toBeVisible()
    await expect(page.getByTitle('Löschen')).toBeVisible()
  })

  test('manager: may add and edit but not delete, no administration', async ({ page, signIn }) => {
    await signIn(PERSONAS.manager)
    await expect(page.locator('header nav').getByRole('link', { name: 'Administration' })).toHaveCount(0)
    await page.goto('/admin') // guarded route
    await expect(page).toHaveURL(/\/dashboard$/)

    await page.goto('/properties')
    await expect(page.getByText('e2e roles')).toBeVisible()
    await expect(page.getByRole('button', { name: '+ Objekt hinzufügen' })).toHaveCount(0)
    await expect(page.getByTitle('Objekt löschen')).toHaveCount(0)

    await page.goto(`/properties/${propertyId}/meters`)
    await expect(page.getByText('e2e roles meter')).toBeVisible()
    await expect(page.getByRole('button', { name: '+ Zähler hinzufügen' })).toBeVisible()
    await expect(page.getByTitle('Bearbeiten').first()).toBeVisible()
    await expect(page.getByTitle('Zähler löschen')).toHaveCount(0)

    await page.goto(`/properties/${propertyId}/oil-deliveries`)
    await expect(page.getByRole('button', { name: '+ Lieferung erfassen' })).toBeVisible()
    await expect(page.getByTitle('Lieferung bearbeiten')).toBeVisible()
    await expect(page.getByTitle('Löschen')).toHaveCount(0)
  })

  test('plain user: read only, sees only the properties they belong to', async ({ page, signIn }) => {
    await signIn(PERSONAS.user)
    await expect(page.locator('header nav').getByRole('link', { name: 'Administration' })).toHaveCount(0)
    await page.goto('/admin')
    await expect(page).toHaveURL(/\/dashboard$/)

    await page.goto('/properties')
    await expect(page.getByText('e2e roles')).toBeVisible()
    await expect(page.getByText('prop-a')).toBeVisible()
    await expect(page.getByText('prop-b')).toHaveCount(0) // other tenant
    await expect(page.getByRole('button', { name: '+ Objekt hinzufügen' })).toHaveCount(0)

    await page.goto(`/properties/${propertyId}/meters`)
    await expect(page.getByText('e2e roles meter')).toBeVisible()
    await expect(page.getByRole('button', { name: '+ Zähler hinzufügen' })).toHaveCount(0)
    await expect(page.getByTitle('Bearbeiten')).toHaveCount(0)
    await expect(page.getByTitle('Zähler löschen')).toHaveCount(0)

    await page.goto(`/properties/${propertyId}/oil-deliveries`)
    await expect(page.getByText('1.000 L')).toBeVisible()
    await expect(page.getByRole('button', { name: '+ Lieferung erfassen' })).toHaveCount(0)
    await expect(page.getByTitle('Lieferung bearbeiten')).toHaveCount(0)
    await expect(page.getByTitle('Löschen')).toHaveCount(0)
  })

  test('plain user cannot open a foreign property (other tenant)', async ({ page, signIn, guard }) => {
    const { seed } = await import('../lib/rig')
    const foreign = seed().properties.prop_b.id as string
    await signIn(PERSONAS.user)
    guard.expect(403, /\/api\/properties\//)
    guard.expect(404, /\/api\/properties\//)
    await page.goto(`/properties/${foreign}/oil-deliveries`)
    await expect(page.getByText('Property not found')).toBeVisible() // same answer as for a property that does not exist
  })
})
