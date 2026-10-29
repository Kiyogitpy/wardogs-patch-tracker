import { describe, expect, it } from 'vitest'
import type { ChangeHit, Json } from './api'
import { pickHeroLines, readableAsset, readableField } from './hero'

const hit = (name: string, field: string, before: Json, after: Json, op: ChangeHit['op'] = 'changed'): ChangeHit => ({
  build: 'b1',
  released_at: null,
  asset: { path: `Game/${name}.json`, name, type: 'T' },
  area: 'a',
  status: 'modified',
  field_path: field,
  op,
  before,
  after,
})

describe('readableAsset', () => {
  it('strips type prefixes and suffixes and splits words', () => {
    expect(readableAsset('DA_308Winchester_ProjectileData')).toBe('308 Winchester')
    expect(readableAsset('DA_50Cal_ProjectileData')).toBe('50 Cal')
    expect(readableAsset('DA_762x54mm_ProjectileData')).toBe('762x54mm')
    expect(readableAsset('DA_CombatBow_Stats')).toBe('Combat Bow')
    expect(readableAsset('B_TargetDummy')).toBe('Target Dummy')
    expect(readableAsset('DT_GlobalDamage_ArmorData')).toBe('Global Damage Armor Data')
  })
  it('falls back to the full name when nothing is left', () => {
    expect(readableAsset('DA_')).toBe('DA_')
    expect(readableAsset('')).toBe('')
  })
})

describe('readableField', () => {
  it('humanizes the last segment', () => {
    expect(readableField('Properties.ProjectileStats.DamageValues.BaseDamage')).toBe('Base damage')
  })
  it('adds the parent for terse segments and removes indices', () => {
    expect(readableField('Rows.Door.DamageModificationEntries[9].OutModification.Amount')).toBe('Out modification amount')
    expect(readableField('A.Keys[3].Value')).toBe('Keys 3 value')
  })
})

describe('pickHeroLines', () => {
  const base = [
    hit('A', 'X.BaseDamage', 60, 45), // -25%
    hit('B', 'X.BaseDamage', 100, 90), // -10%
    hit('C', 'X.BaseDamage', 10, 15), // +50%
    hit('D', 'X.BaseDamage', 0, 5), // before 0: dropped
    hit('E', 'X.BaseDamage', 'a', 'b'), // not numeric
    hit('F', 'X.BaseDamage', null, 5, 'added'), // not changed
  ]
  it('sorts BaseDamage by absolute percent, biggest first', () => {
    expect(pickHeroLines(base, []).map((l) => l.asset)).toEqual(['C', 'A', 'B'])
  })
  it('appends other damage changes after BaseDamage, without duplicates', () => {
    const other = [
      hit('A', 'X.BaseDamage', 60, 45),
      hit('Z', 'Y.FalloffDamage', 1, 5), // +400%
      hit('Z', 'Y.ArriveTangent.Damage', 1, 9), // tangent noise
      hit('W', 'Y.Armor', 1, 9), // not damage
    ]
    const lines = pickHeroLines(base, other)
    expect(lines.map((l) => l.asset)).toEqual(['C', 'A', 'B', 'Z'])
    expect(lines[3].field).toBe('Falloff damage')
  })
  it('caps the list', () => {
    const many = Array.from({ length: 20 }, (_, i) => hit(`N${i}`, 'X.BaseDamage', 10, 11 + i))
    expect(pickHeroLines(many, [])).toHaveLength(8)
    expect(pickHeroLines(many, [], 3)).toHaveLength(3)
  })
  it('is empty with no numeric damage change', () => {
    expect(pickHeroLines([], [])).toEqual([])
  })
})
