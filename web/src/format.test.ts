import { describe, expect, it } from 'vitest'
import { inlineText, isLong, numericDelta } from './format'

describe('numericDelta', () => {
  it('shows signed delta and percent', () => {
    expect(numericDelta(60, 45)).toEqual({ delta: '−15', pct: '−25%' })
    expect(numericDelta(40, 50)).toEqual({ delta: '+10', pct: '+25%' })
  })
  it('keeps one decimal for small percentages and trims float noise', () => {
    expect(numericDelta(100, 102.5)).toEqual({ delta: '+2.5', pct: '+2.5%' })
    expect(numericDelta(0.1, 0.3)).toEqual({ delta: '+0.2', pct: '+200%' })
  })
  it('has no percent when before is 0 and nothing for non-numbers', () => {
    expect(numericDelta(0, 5)).toEqual({ delta: '+5', pct: null })
    expect(numericDelta(5, 5)).toEqual({ delta: '0', pct: '0%' })
    expect(numericDelta('a', 1)).toBeNull()
    expect(numericDelta(null, 1)).toBeNull()
    expect(numericDelta(true, 1)).toBeNull()
  })
  it('uses the magnitude of a negative before', () => {
    expect(numericDelta(-10, -5)).toEqual({ delta: '+5', pct: '+50%' })
  })
})

describe('inlineText', () => {
  it('renders null visibly', () => {
    expect(inlineText(null)).toBe('null')
    expect(inlineText('')).toBe('""')
    expect(inlineText(false)).toBe('false')
    expect(inlineText(1.5)).toBe('1.5')
  })
  it('summarises containers', () => {
    expect(inlineText([1, 2, 3])).toBe('[3 items]')
    expect(inlineText([1])).toBe('[1 item]')
    expect(inlineText({ a: 1, b: 2 })).toBe('{2 keys}')
  })
})

describe('isLong', () => {
  it('keeps scalars and empty containers inline', () => {
    expect(isLong(1)).toBe(false)
    expect(isLong(null)).toBe(false)
    expect(isLong('short')).toBe(false)
    expect(isLong([])).toBe(false)
  })
  it('collapses long arrays, objects and strings', () => {
    expect(isLong(Array.from({ length: 20 }, (_, i) => `Id.Item.${i}`))).toBe(true)
    expect(isLong({ a: { b: 1 } })).toBe(true)
    expect(isLong('x'.repeat(121))).toBe(true)
  })
})
