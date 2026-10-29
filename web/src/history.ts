import type { AssetHistory, Json, Op } from './api'
import { numericDelta } from './format'

/** One value in a field's sequence across builds. */
export interface SeqValue {
  label: string // build label, or the base build for the starting value
  value: Json
  absent: boolean // the field did not exist at this point
  op: Op | null // null for the starting value
}

export interface FieldSeries {
  field: string
  values: SeqValue[]
}

/**
 * Group every change of an asset by field_path (alphabetical). Each series starts with the first
 * change's `before` (absent when that change added the field), then one value per change's `after`
 * (absent when it removed the field), in history order.
 */
export function groupByField(history: AssetHistory['history']): FieldSeries[] {
  const map = new Map<string, SeqValue[]>()
  for (const h of history) {
    for (const c of h.changes) {
      let seq = map.get(c.field_path)
      if (!seq) {
        seq = [
          {
            label: h.build.prev_label ?? 'before',
            value: c.before,
            absent: c.op === 'added' && c.before === null,
            op: null,
          },
        ]
        map.set(c.field_path, seq)
      }
      seq.push({
        label: h.build.label,
        value: c.after,
        absent: c.op === 'removed' && c.after === null,
        op: c.op,
      })
    }
  }
  return [...map.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([field, values]) => ({ field, values }))
}

/** Delta between consecutive values, or null when either is not a number (or absent). */
export function stepDelta(prev: SeqValue, next: SeqValue) {
  if (prev.absent || next.absent) return null
  return numericDelta(prev.value, next.value)
}

export function numericValues(values: SeqValue[]): number[] {
  return values.flatMap((v) => (!v.absent && typeof v.value === 'number' && Number.isFinite(v.value) ? [v.value] : []))
}

export interface Pt {
  x: number
  y: number
}

/**
 * Scale values into a w x h box with `pad` padding; y grows downward (larger value = smaller y).
 * A single point sits at the horizontal centre, and all-equal values draw a flat line mid-height.
 */
export function scalePoints(values: number[], w: number, h: number, pad: number): Pt[] {
  const n = values.length
  if (n === 0) return []
  const min = Math.min(...values)
  const max = Math.max(...values)
  const innerW = w - 2 * pad
  const innerH = h - 2 * pad
  return values.map((v, i) => ({
    x: n === 1 ? w / 2 : pad + (i / (n - 1)) * innerW,
    y: max === min ? h / 2 : pad + (1 - (v - min) / (max - min)) * innerH,
  }))
}
