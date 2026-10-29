import type { ChangeHit } from './api'

/** Player-readable asset name: drop type prefixes and suffixes, split words. Display only. */
export function readableAsset(name: string): string {
  const base = name
    .replace(/\.json$/i, '')
    .replace(/^(DA|DT|BP|WBP|ABP|PDA|AEB|AE|B)_/, '')
    .replace(/_(ProjectileData|ProjectileStats|Stats|Data)$/, '')
  const words = humanize(base)
  return words || name
}

function humanize(s: string): string {
  return s
    .replace(/_+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/(\d)([A-Z][a-z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
}

function sentence(s: string): string {
  const t = humanize(s.replace(/\[(\d+)\]/g, ' $1')).toLowerCase()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

/** Short readable field name: the last path segment, with its parent when the segment alone is too terse. */
export function readableField(fieldPath: string): string {
  const parts = fieldPath.split(/\.|::/).filter(Boolean)
  const last = parts[parts.length - 1] ?? fieldPath
  if (last.replace(/\[\d+\]/g, '').length <= 6 && parts.length > 1) {
    return sentence(`${parts[parts.length - 2]} ${last}`)
  }
  return sentence(last)
}

export interface HeroLine {
  key: string
  build: string
  assetPath: string
  asset: string // readable
  fieldPath: string
  field: string // readable
  before: number
  after: number
  pct: number // signed relative change, fraction
}

function toLine(c: ChangeHit): HeroLine | null {
  const { before, after } = c
  if (c.op !== 'changed' || typeof before !== 'number' || typeof after !== 'number') return null
  if (!Number.isFinite(before) || !Number.isFinite(after) || before === 0) return null
  return {
    key: `${c.asset.path}|${c.field_path}`,
    build: c.build,
    assetPath: c.asset.path,
    asset: readableAsset(c.asset.name),
    fieldPath: c.field_path,
    field: readableField(c.field_path),
    before,
    after,
    pct: (after - before) / Math.abs(before),
  }
}

const byBiggest = (a: HeroLine, b: HeroLine) => Math.abs(b.pct) - Math.abs(a.pct)

export const HERO_MAX_LINES = 8

/**
 * Hero line first (biggest relative BaseDamage change), then the rest of the BaseDamage changes, then other
 * numeric changes whose field path contains "Damage" (curve tangents are skipped as noise). No duplicates.
 */
export function pickHeroLines(baseDamage: ChangeHit[], otherDamage: ChangeHit[], max = HERO_MAX_LINES): HeroLine[] {
  const base = baseDamage.flatMap((c) => toLine(c) ?? []).sort(byBiggest)
  const other = otherDamage
    .filter((c) => c.field_path.includes('Damage') && !c.field_path.includes('Tangent'))
    .flatMap((c) => toLine(c) ?? [])
    .sort(byBiggest)
  const seen = new Set<string>()
  const out: HeroLine[] = []
  for (const l of [...base, ...other]) {
    if (seen.has(l.key)) continue
    seen.add(l.key)
    out.push(l)
    if (out.length >= max) break
  }
  return out
}
