import { describe, it, expect } from 'vitest'
import { extractLoginCode } from '../authCode'

describe('extractLoginCode', () => {
  it('reads the code from the fragment', () => {
    expect(extractLoginCode('#code=abc_DEF-123')).toBe('abc_DEF-123')
  })
  it('works without leading hash and with other params', () => {
    expect(extractLoginCode('x=1&code=abc')).toBe('abc')
  })
  it('returns null for missing, empty or oversized codes', () => {
    expect(extractLoginCode('')).toBeNull()
    expect(extractLoginCode('#foo=bar')).toBeNull()
    expect(extractLoginCode('#code=')).toBeNull()
    expect(extractLoginCode('#code=' + 'a'.repeat(200))).toBeNull()
  })
  it('rejects characters outside the url-safe alphabet', () => {
    expect(extractLoginCode('#code=a b')).toBeNull()
    expect(extractLoginCode('#code=<script>')).toBeNull()
  })
})
