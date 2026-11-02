/** Presentation helpers for the consumption statistics (spec 015). */

export type TrendClass = 'increase' | 'decrease' | 'neutral'

/** Above +10 % an increase, below -10 % a decrease (FR-007); no value is neutral. */
export function trendClass(pct: number | null | undefined): TrendClass {
  if (pct == null) return 'neutral'
  if (pct > 10) return 'increase'
  if (pct < -10) return 'decrease'
  return 'neutral'
}

/** "+12,5 %" / "-33,3 %"; empty string when there is no value. */
export function formatTrend(pct: number | null | undefined): string {
  if (pct == null) return ''
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toLocaleString('de-DE', { maximumFractionDigits: 1 })} %`
}

/** Same rounding the dashboard uses for consumption figures. */
export function formatAmount(val: number): string {
  if (val >= 1000) return val.toLocaleString('de-DE', { maximumFractionDigits: 0 })
  if (val >= 10) return val.toLocaleString('de-DE', { maximumFractionDigits: 1 })
  return val.toLocaleString('de-DE', { maximumFractionDigits: 3 })
}
