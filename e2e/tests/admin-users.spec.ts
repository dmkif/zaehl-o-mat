import { test, expect } from '../lib/fixtures'
import { PERSONAS, setIdp } from '../lib/personas'

test.describe('administration: users', () => {
  test('admin changes a role and the change survives a reload', async ({ page, signIn, api }) => {
    await signIn(PERSONAS.admin)
    await page.goto('/admin')
    const row = page.getByRole('row', { name: /rig-nogroup/ })
    await expect(row).toBeVisible()
    await expect(row.getByRole('combobox')).toHaveValue('user')
    const patched = page.waitForResponse((r) => r.request().method() === 'PATCH' && /\/role$/.test(r.url()))
    await row.getByRole('combobox').selectOption('manager')
    expect((await patched).status()).toBe(200)
    await page.reload()
    await expect(page.getByRole('row', { name: /rig-nogroup/ }).getByRole('combobox')).toHaveValue('manager')
    // restore for the other tests (and prove the way back works)
    const u = (await api.users()).find((x) => x.username === 'rig-nogroup')!
    await api.call('patch', `/api/admin/users/${u.id}/role`, { role: 'user' })
  })

  test('admin deletes a user (confirm dialog); the superadmin cannot be deleted from the UI', async ({ page, signIn, context }) => {
    // a throw-away account that signs in through SSO
    const other = await context.newPage()
    await signIn({ ...PERSONAS.newcomer, sub: 'rig-e2e-sub-throwaway', username: 'rig-e2e-throwaway', email: 'rig-e2e-throwaway@rig.invalid' }, other)
    await other.getByRole('button', { name: 'Abmelden' }).click()
    await other.close()

    await signIn(PERSONAS.admin)
    await page.goto('/admin')
    const row = page.getByRole('row', { name: /rig-e2e-throwaway/ })
    await expect(row).toBeVisible()
    await expect(page.getByRole('row', { name: /rig-superadmin/ }).getByRole('button', { name: 'Löschen' })).toHaveCount(0)

    page.once('dialog', (d) => {
      expect(d.message()).toContain('rig-e2e-throwaway')
      void d.accept()
    })
    const deleted = page.waitForResponse((r) => r.request().method() === 'DELETE' && /\/api\/admin\/users\//.test(r.url()))
    await row.getByRole('button', { name: 'Löschen' }).click()
    expect((await deleted).status()).toBe(204)
    await expect(page.getByRole('row', { name: /rig-e2e-throwaway/ })).toHaveCount(0)
    await setIdp(PERSONAS.admin)
  })
})
