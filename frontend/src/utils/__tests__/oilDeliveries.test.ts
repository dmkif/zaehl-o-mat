import { describe, it, expect } from 'vitest'
import { parseDecimal, buildDeliveryPayload, toLocalInput, formatApiError } from '../oilDeliveries'

const base = { delivered_at: '2026-03-01T10:30', liters: '2000', total_price: '', price_per_liter: '', supplier: ' ', note: '' }

describe('parseDecimal', () => {
  it('handles comma, dot and thousands', () => {
    expect(parseDecimal('1.234,5')).toBe(1234.5)
    expect(parseDecimal('12.5')).toBe(12.5)
    expect(parseDecimal('2000')).toBe(2000)
  })
  it('rejects junk', () => {
    expect(parseDecimal('')).toBeNaN()
    expect(parseDecimal('1e5')).toBeNaN()
    expect(parseDecimal('-3')).toBeNaN()
    expect(parseDecimal('abc')).toBeNaN()
  })
})

describe('buildDeliveryPayload', () => {
  it('builds minimal payload with nulls', () => {
    const r = buildDeliveryPayload(base)
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.payload.liters).toBe(2000)
      expect(r.payload.supplier).toBeNull()
      expect(r.payload.total_price).toBeNull()
      expect(new Date(r.payload.delivered_at as string).getTime()).toBe(new Date('2026-03-01T10:30').getTime())
    }
  })
  it('passes both prices through', () => {
    const r = buildDeliveryPayload({ ...base, total_price: '1.800,00', price_per_liter: '0,9' })
    expect(r.ok && r.payload.total_price).toBe(1800)
    expect(r.ok && r.payload.price_per_liter).toBe(0.9)
  })
  it('flags errors', () => {
    expect(buildDeliveryPayload({ ...base, delivered_at: '' })).toEqual({ ok: false, error: 'date_required' })
    expect(buildDeliveryPayload({ ...base, liters: '0' })).toEqual({ ok: false, error: 'liters_invalid' })
    expect(buildDeliveryPayload({ ...base, total_price: 'x' })).toEqual({ ok: false, error: 'total_price_invalid' })
    expect(buildDeliveryPayload({ ...base, price_per_liter: 'x' })).toEqual({ ok: false, error: 'price_per_liter_invalid' })
  })
})

describe('toLocalInput', () => {
  it('round-trips local time', () => {
    expect(toLocalInput(new Date('2026-03-01T10:30').toISOString())).toBe('2026-03-01T10:30')
  })
  it('returns empty for bad input', () => {
    expect(toLocalInput('nope')).toBe('')
    expect(toLocalInput(null)).toBe('')
  })
})

describe('formatApiError', () => {
  it('passes strings', () => expect(formatApiError('bad', 'fb')).toBe('bad'))
  it('formats pydantic lists', () => {
    const s = formatApiError([{ loc: ['body', 'liters'], msg: 'must be > 0', type: 'x' }, { loc: ['body', 'note'], msg: 'too long' }], 'fb')
    expect(s).toBe('liters: must be > 0; note: too long')
  })
  it('formats objects without [object Object]', () => {
    expect(formatApiError({ msg: 'oops' }, 'fb')).toBe('oops')
    expect(formatApiError({ a: 1 }, 'fb')).toBe('{"a":1}')
    expect(formatApiError({ detail: [{ msg: 'deep' }] }, 'fb')).toBe('deep')
  })
  it('falls back', () => {
    expect(formatApiError(null, 'fb')).toBe('fb')
    expect(formatApiError([], 'fb')).toBe('fb')
  })
})
