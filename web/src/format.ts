import type { Json } from './api'

const MINUS = '−'

function plain(n: number, digits: number): string {
  if (!Number.isFinite(n)) return String(n)
  // toLocaleString never uses exponent notation, unlike String() for 1e-7 or 1e21.
  return n.toLocaleString('en-US', { useGrouping: false, maximumSignificantDigits: digits })
}

/** Trim float noise (0.1+0.2) to 6 significant digits, without exponent notation. */
export function fmtNumber(n: number): string {
  return plain(n, 6)
}

/**
 * Display text for a before/after pair of numbers. If 6 digits would make two
 * different values look identical, both are shown at full precision.
 */
export function numberPair(before: number, after: number): [string, string] {
  const b = fmtNumber(before)
  const a = fmtNumber(after)
  if (b === a && before !== after) return [plain(before, 17), plain(after, 17)]
  return [b, a]
}

function signed(n: number, text: string): string {
  if (n > 0) return `+${text}`
  if (n < 0) return `${MINUS}${text.replace('-', '')}`
  return text
}

export interface Delta {
  delta: string // "−15"
  pct: string | null // "−25%"; null when before is 0
}

/** Delta and percent for number -> number changes; null for anything else. */
export function numericDelta(before: Json, after: Json): Delta | null {
  if (typeof before !== 'number' || typeof after !== 'number') return null
  if (!Number.isFinite(before) || !Number.isFinite(after)) return null
  const d = Number((after - before).toPrecision(12))
  const delta = signed(d, fmtNumber(d))
  if (before === 0) return { delta, pct: null }
  const p = (d / Math.abs(before)) * 100
  const rounded = Math.abs(p) < 10 ? Number(p.toFixed(1)) : Math.round(p)
  if (rounded === 0 && d !== 0) return { delta, pct: `${p > 0 ? '+' : MINUS}<0.1%` }
  return { delta, pct: `${signed(rounded, String(rounded))}%` }
}

export const COLLAPSE_CHARS = 80
export const COLLAPSE_LINES = 3

export function pretty(v: Json): string {
  return JSON.stringify(v, null, 2)
}

/** Arrays/objects whose pretty form is long, and long strings, get an expand control. */
export function isLong(v: Json): boolean {
  if (typeof v === 'string') return v.length > 120 || v.includes('\n')
  if (v === null || typeof v !== 'object') return false
  const text = pretty(v)
  return text.length > COLLAPSE_CHARS || text.split('\n').length > COLLAPSE_LINES
}

/** Inline text for a scalar, or a short summary for a collapsed container. */
export function inlineText(v: Json): string {
  if (v === null) return 'null'
  if (typeof v === 'number') return fmtNumber(v)
  if (typeof v === 'string') return v === '' ? '""' : v
  if (typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return `[${v.length} item${v.length === 1 ? '' : 's'}]`
  const n = Object.keys(v).length
  return `{${n} key${n === 1 ? '' : 's'}}`
}

export function totalAssets(c: { added: number; removed: number; modified: number; moved: number }): number {
  return c.added + c.removed + c.modified + c.moved
}

/** Text of a delta tag: "−15 · −25%", or just "−15" when there is no percent. */
export function deltaText(d: Delta): string {
  return d.pct ? `${d.delta} · ${d.pct}` : d.delta
}
