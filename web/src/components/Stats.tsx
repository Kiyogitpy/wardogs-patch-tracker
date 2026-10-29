import type { Counts, Op, Status } from '../api'

export const STATUS_META: Record<Status, { glyph: string; label: string }> = {
  added: { glyph: '+', label: 'added' },
  removed: { glyph: '−', label: 'removed' },
  modified: { glyph: '~', label: 'modified' },
  moved: { glyph: '→', label: 'moved' },
}

export const OP_META: Record<Op, { glyph: string; label: string }> = {
  added: { glyph: '+', label: 'added' },
  removed: { glyph: '−', label: 'removed' },
  changed: { glyph: '~', label: 'changed' },
  resequenced: { glyph: '⇅', label: 'resequenced' },
}

/** The marking a row gets: changed values are hazard-striped, the rest are solid bars. */
export const OP_BAR: Record<Op, string> = {
  changed: 'hazard',
  added: 'bar-added',
  removed: 'bar-removed',
  resequenced: 'bar-moved',
}

export function StatusBadge({ status }: { status: Status }) {
  const m = STATUS_META[status]
  return (
    <span className={`badge badge-${status}`}>
      <span aria-hidden="true">{m.glyph}</span> {m.label}
    </span>
  )
}

export function OpBadge({ op }: { op: Op }) {
  const m = OP_META[op]
  return (
    <span className={`badge op-${op}`}>
      <span aria-hidden="true">{m.glyph}</span> {m.label}
    </span>
  )
}

const nf = new Intl.NumberFormat('en-US')

/** Totals as a ledger: a marking bar, glyph and word, then the number at display size. */
export function StatLedger({ counts }: { counts: Counts }) {
  const rows: [string, string, string, number][] = [
    ['modified', 'hazard', 'Modified', counts.modified],
    ['added', 'bar-added', 'Added', counts.added],
    ['removed', 'bar-removed', 'Removed', counts.removed],
    ['moved', 'bar-moved', 'Moved', counts.moved],
    ['props', 'hazard', 'Property changes', counts.property_changes],
  ]
  return (
    <dl className="ledger">
      {rows.map(([k, bar, label, n]) => (
        <div key={k} className="led">
          <span className={`led-bar ${bar}`} aria-hidden="true" />
          <dt>
            {k !== 'props' && <span aria-hidden="true">{STATUS_META[k as Status].glyph} </span>}
            {label}
          </dt>
          <dd className="mono">{nf.format(n)}</dd>
        </div>
      ))}
    </dl>
  )
}

export { nf }
