import { describe, it, expect } from 'vitest'
import { trendClass, formatTrend, formatAmount } from '../consumptionStats'

describe('trendClass (spec 015, FR-007)', () => {
  it('more than +10 % is an increase', () => {
    expect(trendClass(10.1)).toBe('increase')
    expect(trendClass(300)).toBe('increase')
  })
  it('less than -10 % is a decrease', () => {
    expect(trendClass(-10.1)).toBe('decrease')
  })
  it('within the band, at the edges and without a value is neutral', () => {
    for (const v of [0, 5, -5, 10, -10, null, undefined]) {
      expect(trendClass(v as any)).toBe('neutral')
    }
  })
})

describe('formatTrend', () => {
  it('shows the sign and a German decimal comma', () => {
    expect(formatTrend(12.5)).toBe('+12,5 %')
    expect(formatTrend(-33.3)).toBe('-33,3 %')
    expect(formatTrend(0)).toBe('0 %')
  })
  it('is empty without a value', () => {
    expect(formatTrend(null)).toBe('')
    expect(formatTrend(undefined)).toBe('')
  })
})

describe('formatAmount', () => {
  it('uses fewer decimals for bigger numbers', () => {
    expect(formatAmount(1234.5)).toBe('1.235')
    expect(formatAmount(12.34)).toBe('12,3')
    expect(formatAmount(0.1234)).toBe('0,123')
  })
})

import { monthLabel, chartValue } from '../consumptionStats'

describe('monthly helpers', () => {
  it('formats the month in German', () => {
    expect(monthLabel('2026-10')).toMatch(/Okt/)
    expect(monthLabel('2026-10')).toContain('2026')
  })
  it('keeps gaps as null, never 0', () => {
    expect(chartValue(null)).toBeNull()
    expect(chartValue(0)).toBe(0)
    expect(chartValue(12.5)).toBe(12.5)
  })
})

import { formatCost } from '../consumptionStats'

describe('formatCost', () => {
  it('formats euros and shows a missing cost as a dash', () => {
    expect(formatCost(1234.5)).toContain('1.234,50')
    expect(formatCost(1234.5)).toContain('€')
    expect(formatCost(null)).toBe('–')
    expect(formatCost(0)).toContain('0,00')
  })
})
