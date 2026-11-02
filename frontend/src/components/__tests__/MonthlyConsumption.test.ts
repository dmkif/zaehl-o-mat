import { describe, expect, it, vi, beforeEach } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de'

const apiFetch = vi.fn()
vi.mock('@/utils/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }))

import MonthlyConsumption from '../MonthlyConsumption.vue'

const payload = {
  scope: { kind: 'meter', id: 'm1' },
  series: [{
    meter_type: 'water', unit: 'm³',
    months: [
      { month: '2026-08', value: null, state: 'no_data', estimated: false, previous_year: null, delta_pct: null },
      { month: '2026-09', value: 12.5, state: 'complete', estimated: false, previous_year: { value: 10, state: 'complete' }, delta_pct: 25 },
      { month: '2026-10', value: 3, state: 'partial', estimated: true, previous_year: { value: 9, state: 'complete' }, delta_pct: null },
    ],
  }],
}

async function mount(props: Record<string, unknown>) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(MonthlyConsumption, props)
  app.component('apexchart', defineComponent({ props: ['series'], setup: () => () => h('div', { class: 'fake-chart' }) }))
  app.use(createPinia())
  app.use(createI18n({ legacy: false, locale: 'de', messages: { de } }))
  app.mount(el)
  await new Promise((r) => setTimeout(r, 0))
  await nextTick()
  return el
}

describe('MonthlyConsumption (spec 015)', () => {
  beforeEach(() => apiFetch.mockReset())

  it('requests the meter scope and renders rows newest first', async () => {
    apiFetch.mockResolvedValue({ ok: true, json: async () => payload })
    const el = await mount({ meterId: 'm1' })
    expect(apiFetch.mock.calls[0][0]).toBe('/api/dashboard/monthly?meter_id=m1&months=12')
    const rows = [...el.querySelectorAll('tbody tr')].map((r) => r.getAttribute('data-testid'))
    expect(rows).toEqual(['row-2026-10', 'row-2026-09', 'row-2026-08'])
  })

  it('shows "keine Daten" for a gap month, never 0', async () => {
    apiFetch.mockResolvedValue({ ok: true, json: async () => payload })
    const el = await mount({ meterId: 'm1' })
    const gap = el.querySelector('[data-testid=row-2026-08]')!.textContent!
    expect(gap).toContain('keine Daten')
    expect(gap).not.toMatch(/\b0\b/)
  })

  it('marks partial and estimated months and shows the previous year with percent', async () => {
    apiFetch.mockResolvedValue({ ok: true, json: async () => payload })
    const el = await mount({ meterId: 'm1' })
    const oct = el.querySelector('[data-testid=row-2026-10]')!.textContent!
    expect(oct).toContain('*')
    expect(oct).toContain('~')
    const sep = el.querySelector('[data-testid=row-2026-09]')!.textContent!
    expect(sep).toContain('+25 %')
  })

  it('uses the property scope when given a property', async () => {
    apiFetch.mockResolvedValue({ ok: true, json: async () => ({ scope: { kind: 'property', id: 'p' }, series: [] }) })
    await mount({ propertyId: 'p', months: 6 })
    expect(apiFetch.mock.calls[0][0]).toBe('/api/dashboard/monthly?property_id=p&months=6')
  })

  it('shows an error message when the request fails', async () => {
    apiFetch.mockResolvedValue({ ok: false, json: async () => ({}) })
    const el = await mount({ meterId: 'm1' })
    expect(el.querySelector('[role=alert]')).not.toBeNull()
  })
})
