import { describe, expect, it } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de'
import TrendLine from '../TrendLine.vue'

async function render(meter: Record<string, unknown>) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(TrendLine, { meter })
  app.use(createI18n({ legacy: false, locale: 'de', messages: { de } }))
  app.mount(el)
  await nextTick()
  return el
}

describe('TrendLine (spec 015)', () => {
  it('shows 30-day rate, trend and rate since last reading', async () => {
    const el = await render({
      unit: 'L', avg_daily_consumption_30d: 8, trend_30d_vs_365d_pct: 90.5, daily_consumption_last_interval: 7.5,
    })
    expect(el.textContent).toContain('30 Tage')
    expect(el.textContent).toContain('+90,5 %')
    expect(el.textContent).toContain('seit letzter Ablesung')
    expect(el.querySelector('[data-testid=trend-pct]')!.className).toContain('text-red-600')
  })

  it('marks a decrease green and a small change neutral', async () => {
    const down = await render({ unit: 'L', avg_daily_consumption_30d: 1, trend_30d_vs_365d_pct: -40 })
    expect(down.querySelector('[data-testid=trend-pct]')!.className).toContain('text-green-600')
    const flat = await render({ unit: 'L', avg_daily_consumption_30d: 1, trend_30d_vs_365d_pct: 4 })
    const cls = flat.querySelector('[data-testid=trend-pct]')!.className
    expect(cls).not.toContain('text-red-600')
    expect(cls).not.toContain('text-green-600')
  })

  it('renders nothing without data (never a 0)', async () => {
    const el = await render({
      unit: 'L', avg_daily_consumption_30d: null, trend_30d_vs_365d_pct: null, daily_consumption_last_interval: null,
    })
    expect(el.querySelector('[data-testid=trend-line]')).toBeNull()
    expect(el.textContent).not.toContain('0')
  })

  it('hides the trend when only the rate exists', async () => {
    const el = await render({ unit: 'm³', avg_daily_consumption_30d: 0.2, trend_30d_vs_365d_pct: null })
    expect(el.querySelector('[data-testid=trend-pct]')).toBeNull()
    expect(el.textContent).toContain('30 Tage')
  })

  it('shows the unusual marker only when the server says so', async () => {
    const on = await render({ unit: 'm³', avg_daily_consumption_30d: 1, unusually_high: true })
    expect(on.querySelector('[data-testid=unusually-high]')).not.toBeNull()
    const off = await render({ unit: 'm³', avg_daily_consumption_30d: 1, unusually_high: false })
    expect(off.querySelector('[data-testid=unusually-high]')).toBeNull()
    const oil = await render({ unit: 'L', avg_daily_consumption_30d: 9, trend_30d_vs_365d_pct: 300 })
    expect(oil.querySelector('[data-testid=unusually-high]')).toBeNull()
  })
})
