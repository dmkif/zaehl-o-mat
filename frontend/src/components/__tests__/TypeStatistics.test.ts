import { describe, expect, it } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de'
import TypeStatistics from '../TypeStatistics.vue'

async function render(props: Record<string, unknown>) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(TypeStatistics, props)
  app.use(createI18n({ legacy: false, locale: 'de', messages: { de } }))
  app.mount(el)
  await nextTick()
  return el
}

const water = { unit: 'm³', meter_count: 2, yearly: 0.8, recent_30d: 1.1, trend_pct: 37.5 }

describe('TypeStatistics (spec 015)', () => {
  it('shows yearly, 30-day rate and trend per meter type', async () => {
    const el = await render({ types: { water } })
    const row = el.querySelector('[data-testid=stat-water]')!.textContent!
    expect(row).toContain('Wasser')
    expect(row).toContain('0,8')
    expect(row).toContain('1,1')
    expect(row).toContain('+37,5 %')
  })

  it('shows a missing rate as a dash, never 0', async () => {
    const el = await render({ types: { water: { ...water, yearly: null, recent_30d: null, trend_pct: null } } })
    const row = el.querySelector('[data-testid=stat-water]')!.textContent!
    expect(row.match(/–/g)!.length).toBe(3)
    expect(row).not.toMatch(/\b0\b/)
  })

  it('overall: shows the number of properties and the mean per property', async () => {
    const el = await render({
      overall: true,
      types: { water: { ...water, properties_used: 2, mean_per_property: { yearly: 0.4, recent_30d: 0.55 } } },
    })
    expect(el.querySelector('[data-testid=stat-water]')!.textContent).toContain('aus 2 Objekten')
    expect(el.querySelector('[data-testid=mean-water]')!.textContent).toContain('0,4')
  })

  it('no mean row when the server sends none', async () => {
    const el = await render({ overall: true, types: { oil: { unit: 'L', yearly: 3, recent_30d: 3, trend_pct: 0, properties_used: 1 } } })
    expect(el.querySelector('[data-testid=mean-oil]')).toBeNull()
  })
})
