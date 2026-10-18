import { describe, it, expect } from 'vitest'
import { permsFor } from '../propertyRole'

describe('permsFor (spec 014)', () => {
  it('admin: write and delete', () => {
    expect(permsFor('admin')).toEqual({ canWrite: true, canDelete: true })
  })
  it('manager: write, no delete', () => {
    expect(permsFor('manager')).toEqual({ canWrite: true, canDelete: false })
  })
  it('user: read only', () => {
    expect(permsFor('user')).toEqual({ canWrite: false, canDelete: false })
  })
  it('unknown / missing role: nothing', () => {
    for (const r of [undefined, null, '', 'root', 'ADMIN']) {
      expect(permsFor(r as any)).toEqual({ canWrite: false, canDelete: false })
    }
  })
})
