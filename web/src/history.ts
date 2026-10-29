import type { AssetHistory, Json, Op } from './api'
import { numericDelta } from './format'

/** One value in a field's sequence across builds. */
export interface SeqValue {
  label: string | null // build label; for the starting value, the previous build (null: there is none)
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
            label: h.build.prev_label,
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

/** Heading for the first value of a sequence: "Before" when no build precedes it, else "Before <label>". */
export function startLabel(label: string | null): string {
  return label === null ? 'Before' : `Before ${label}`
}

/** One entry per value, in order; null where the value is absent or not a finite number (a gap in the trend). */
export function numericValues(values: SeqValue[]): (number | null)[] {
  return values.map((v) => (!v.absent && typeof v.value === 'number' && Number.isFinite(v.value) ? v.value : null))
}

/** Split into runs of consecutive points, breaking at nulls. */
export function segments<T>(pts: (T | null)[]): T[][] {
  const out: T[][] = []
  let cur: T[] = []
  for (const p of pts) {
    if (p === null) {
      if (cur.length) out.push(cur)
      cur = []
    } else cur.push(p)
  }
  if (cur.length) out.push(cur)
  return out
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

/**
 * Like scalePoints, but nulls stay null (they still take up an x slot, so gaps look like gaps).
 * Scaling uses only the numeric values.
 */
export function scaleSeries(values: (number | null)[], w: number, h: number, pad: number): (Pt | null)[] {
  const nums = values.filter((v): v is number => v !== null)
  if (nums.length === 0) return values.map(() => null)
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const n = values.length
  const innerW = w - 2 * pad
  const innerH = h - 2 * pad
  return values.map((v, i) =>
    v === null
      ? null
      : {
          x: n === 1 ? w / 2 : pad + (i / (n - 1)) * innerW,
          y: max === min ? h / 2 : pad + (1 - (v - min) / (max - min)) * innerH,
        },
  )
}
