import { test as base, expect, Page } from '@playwright/test'
import { Api } from './api'
import { Guard } from './guard'
import { Persona, setIdp } from './personas'

export { expect }

export interface Fixtures {
  /** Fails the test for CSP violations, http:// requests, unexpected API errors (auto fixture) */
  guard: Guard
  api: Api
  /** Sign in through the real SSO flow (login button -> fake IdP -> callback -> exchange) */
  signIn: (persona: Persona, page?: Page) => Promise<void>
  /** Properties created through `api` are deleted after the test */
  track: (propertyId: string) => string
}

export const test = base.extend<Fixtures>({
  guard: [
    async ({ context }, use) => {
      const g = new Guard(context)
      await use(g)
      g.assertClean()
    },
    { auto: true },
  ],

  api: async ({}, use) => {
    const api = await Api.superadmin()
    await use(api)
    await api.dispose()
  },

  signIn: async ({ page }, use) => {
    await use(async (persona, p = page) => {
      await setIdp(persona)
      await p.goto('/login')
      await p.getByRole('button', { name: 'Mit SSO anmelden' }).click()
      await p.waitForURL('**/dashboard')
      await expect(p.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    })
  },

  track: async ({ api }, use) => {
    const ids: string[] = []
    await use((id) => (ids.push(id), id))
    for (const id of ids) await api.deleteProperty(id).catch(() => undefined)
  },
})
