import { describe, expect, it, vi, beforeEach } from 'vitest'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de'

const apiFetch = vi.fn()
vi.mock('@/utils/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }))

import YearlySummary from '../YearlySummary.vue'

async function mount(props: Record<string, unknown>) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(YearlySummary, props)
  app.use(createPinia())
  app.use(createI18n({ legacy: false, locale: 'de', messages: { de } }))
  app.mount(el)
  await new Promise((r) => setTimeout(r, 0))
  return el
}

const series = (over: Record<string, unknown>) => ({
  ok: true,
  json: async () => ({ series: [{ meter_type: 'oil', unit: 'L', year_to_date: 612, previous_year_same_period: 540, delta_pct: 13.3, cost: 640, cost_complete: true, ...over }] }),
})

describe('YearlySummary (spec 015)', () => {
  beforeEach(() => apiFetch.mockReset())

  it('shows year to date, previous year, percent and cost', async () => {
    apiFetch.mockResolvedValue(series({}))
    const el = await mount({ propertyId: 'p' })
    expect(apiFetch.mock.calls[0][0]).toBe('/api/dashboard/yearly?property_id=p')
    const row = el.querySelector('[data-testid=year-oil]')!.textContent!
    expect(row).toContain('612')
    expect(row).toContain('+13,3 %')
    expect(row).toContain('640,00')
  })

  it('shows dashes, not zero, when data or price is missing', async () => {
    apiFetch.mockResolvedValue(series({ year_to_date: null, previous_year_same_period: null, delta_pct: null, cost: null, cost_complete: false }))
    const el = await mount({})
    const row = el.querySelector('[data-testid=year-oil]')!.textContent!
    expect(row.match(/–/g)!.length).toBe(4)
    expect(row).not.toMatch(/\b0\b/)
  })

  it('flags an incomplete cost', async () => {
    apiFetch.mockResolvedValue(series({ cost_complete: false }))
    const el = await mount({})
    expect(el.querySelector('[data-testid=year-oil]')!.textContent).toContain('*')
  })

  it('shows an error when the request fails', async () => {
    apiFetch.mockResolvedValue({ ok: false, json: async () => ({}) })
    const el = await mount({})
    expect(el.querySelector('[role=alert]')).not.toBeNull()
  })
})
