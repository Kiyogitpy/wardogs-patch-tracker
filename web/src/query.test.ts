import { describe, expect, it } from 'vitest'
import {
  apiQuery, assetApiQuery, assetToParams, changesApiQuery, changesToParams, EMPTY_CHANGES, EMPTY_FILTERS, filtersToParams,
  parseAsset, parseChanges, parseFilters, parseSearch, searchApiQuery, searchToParams,
  type AssetState, type ChangesState, type Filters,
} from './query'

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

describe('step 3 URL state', () => {
  it('round-trips search', () => {
    const s = { q: 'AK 74&x', page: 2 }
    expect(parseSearch(new URLSearchParams(searchToParams(s).toString()))).toEqual(s)
    expect(searchToParams({ q: '', page: 1 }).toString()).toBe('')
    expect(new URLSearchParams(searchApiQuery({ q: 'a', page: 3 })).get('offset')).toBe('100')
  })
  it('round-trips the asset page and keeps the field out of nothing', () => {
    const s: AssetState = { path: 'A/B c.json', field: 'Dmg', view: 'build' }
    expect(parseAsset(new URLSearchParams(assetToParams(s).toString()))).toEqual(s)
    const d = parseAsset(new URLSearchParams(assetToParams({ path: 'p', field: '', view: 'field' })))
    expect(d).toEqual({ path: 'p', field: '', view: 'field' })
    expect(assetToParams({ path: 'p', field: '', view: 'field' }).toString()).toBe('path=p')
    expect(parseAsset(new URLSearchParams('path=p&view=zzz')).view).toBe('field')
    expect(assetApiQuery(s)).toBe('path=A%2FB+c.json&field=Dmg')
  })
  it('round-trips changes and sanitises', () => {
    const s: ChangesState = { field: 'Damage', build: 's2-cl1', area: 'weapons-ammo', op: 'changed', page: 4 }
    expect(parseChanges(new URLSearchParams(changesToParams(s).toString()))).toEqual(s)
    expect(parseChanges(new URLSearchParams('op=nope&page=0')).op).toBe('')
    expect(changesToParams(EMPTY_CHANGES).toString()).toBe('')
    const q = new URLSearchParams(changesApiQuery(s))
    expect([q.get('limit'), q.get('offset'), q.has('page')]).toEqual(['50', '150', false])
  })
})
