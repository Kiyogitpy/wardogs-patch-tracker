import type { Counts, Status } from '../api'

export const STATUS_META: Record<Status, { glyph: string; label: string }> = {
  added: { glyph: '+', label: 'added' },
  removed: { glyph: '−', label: 'removed' },
  modified: { glyph: '~', label: 'modified' },
  moved: { glyph: '→', label: 'moved' },
}

export function StatusBadge({ status }: { status: Status }) {
  const m = STATUS_META[status]
  return (
    <span className={`badge badge-${status}`}>
      <span aria-hidden="true">{m.glyph}</span> {m.label}
    </span>
  )
}

const nf = new Intl.NumberFormat('en-US')

/** Totals as a definition list: label + number, color plus glyph. */
export function StatList({ counts, className = '' }: { counts: Counts; className?: string }) {
  const rows: [Status | 'props', string, number][] = [
    ['added', 'Added', counts.added],
    ['removed', 'Removed', counts.removed],
    ['modified', 'Modified', counts.modified],
    ['moved', 'Moved', counts.moved],
    ['props', 'Property changes', counts.property_changes],
  ]
  return (
    <dl className={`stats ${className}`}>
      {rows.map(([k, label, n]) => (
        <div key={k} className={`stat stat-${k}`}>
          <dt>
            {k !== 'props' && <span aria-hidden="true">{STATUS_META[k].glyph} </span>}
            {label}
          </dt>
          <dd>{nf.format(n)}</dd>
        </div>
      ))}
    </dl>
  )
}

export { nf }
