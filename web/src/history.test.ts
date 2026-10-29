import { describe, expect, it } from 'vitest'
import type { AssetHistory, Change } from './api'
import { groupByField, numericValues, scalePoints, stepDelta } from './history'

const noise = { renamed_keys: 0, ref_shifts: 0, dup_keys: 0, swapped_keys: 0, type_reinterpret: 0 }
const entry = (label: string, prev: string | null, changes: Change[]): AssetHistory['history'][number] => ({
  build: { label, prev_label: prev, released_at: null },
  area: 'a',
  status: 'modified',
  paired_by: null,
  exports: null,
  note: null,
  noise,
  changes,
})

const history = [
  entry('b1', 'b0', [
    { field_path: 'Dmg', op: 'changed', before: 60, after: 45 },
    { field_path: 'Only', op: 'changed', before: 'x', after: 'y' },
  ]),
  entry('b2', 'b1', [
    { field_path: 'Dmg', op: 'changed', before: 45, after: 30 },
    { field_path: 'New', op: 'added', before: null, after: 5 },
  ]),
  entry('b3', 'b2', [{ field_path: 'New', op: 'removed', before: 5, after: null }]),
]

describe('groupByField', () => {
  const series = groupByField(history)
  const by = (f: string) => series.find((s) => s.field === f)!

  it('sorts fields and builds a before/after sequence', () => {
    expect(series.map((s) => s.field)).toEqual(['Dmg', 'New', 'Only'])
    expect(by('Dmg').values.map((v) => [v.label, v.value])).toEqual([
      ['b0', 60],
      ['b1', 45],
      ['b2', 30],
    ])
  })
  it('keeps a field that appears in one build', () => {
    expect(by('Only').values.map((v) => [v.label, v.value])).toEqual([
      ['b0', 'x'],
      ['b1', 'y'],
    ])
  })
  it('marks the start absent for an added op with null before', () => {
    const v = by('New').values
    expect(v[0].absent).toBe(true)
    expect(v[1]).toMatchObject({ label: 'b2', value: 5, absent: false, op: 'added' })
  })
  it('marks the end absent for a removed op', () => {
    const v = by('New').values
    expect(v).toHaveLength(3)
    expect(v[2]).toMatchObject({ label: 'b3', value: null, absent: true, op: 'removed' })
  })
  it('has no delta across an absent value, and a delta between numbers', () => {
    const v = by('New').values
    expect(stepDelta(v[0], v[1])).toBeNull()
    expect(stepDelta(v[1], v[2])).toBeNull()
    expect(stepDelta(by('Dmg').values[0], by('Dmg').values[1])).toEqual({ delta: '−15', pct: '−25%' })
    expect(numericValues(v)).toEqual([5])
  })
  it('is empty for a history without changes', () => {
    expect(groupByField([entry('b1', null, [])])).toEqual([])
  })
  it('labels the start "before" without a previous build', () => {
    expect(groupByField([entry('b1', null, [{ field_path: 'F', op: 'changed', before: 1, after: 2 }])])[0].values[0].label).toBe('before')
  })
})

describe('scalePoints', () => {
  it('maps min to the bottom and max to the top', () => {
    expect(scalePoints([0, 10], 100, 20, 2)).toEqual([
      { x: 2, y: 18 },
      { x: 98, y: 2 },
    ])
  })
  it('draws all-equal values flat at mid height', () => {
    expect(scalePoints([5, 5, 5], 100, 20, 2).map((p) => p.y)).toEqual([10, 10, 10])
  })
  it('centres a single point and handles none', () => {
    expect(scalePoints([7], 100, 20, 2)).toEqual([{ x: 50, y: 10 }])
    expect(scalePoints([], 100, 20, 2)).toEqual([])
  })
  it('handles negatives', () => {
    const p = scalePoints([-10, 0, 10], 100, 20, 0)
    expect(p.map((q) => q.y)).toEqual([20, 10, 0])
    expect(p.map((q) => q.x)).toEqual([0, 50, 100])
  })
})
