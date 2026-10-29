import { describe, expect, it } from 'vitest'
import { apiQuery, EMPTY_FILTERS, filtersToParams, parseFilters, type Filters } from './query'

describe('URL query state', () => {
  it('round-trips every filter', () => {
    const f: Filters = { area: 'weapons-ammo', status: 'modified', type: 'WeaponData', q: 'AK 74&x', field: 'Damage', page: 3 }
    const sp = filtersToParams(f)
    expect(parseFilters(new URLSearchParams(sp.toString()))).toEqual(f)
  })
  it('omits defaults', () => {
    expect(filtersToParams(EMPTY_FILTERS).toString()).toBe('')
    expect(filtersToParams({ ...EMPTY_FILTERS, q: 'a' }).toString()).toBe('q=a')
  })
  it('sanitises bad input', () => {
    const f = parseFilters(new URLSearchParams('status=bogus&page=-4'))
    expect(f.status).toBe('')
    expect(f.page).toBe(1)
    expect(parseFilters(new URLSearchParams('page=abc')).page).toBe(1)
  })
  it('maps page to limit/offset', () => {
    const q = new URLSearchParams(apiQuery({ ...EMPTY_FILTERS, page: 3, status: 'added' }))
    expect(q.get('limit')).toBe('50')
    expect(q.get('offset')).toBe('100')
    expect(q.get('status')).toBe('added')
    expect(q.has('page')).toBe(false)
  })
})
