import type { Change } from '../api'
import { numberPair } from '../format'
import DeltaTag from './DeltaTag'
import { OpBadge } from './Stats'
import ValueView from './ValueView'

export function Arrow() {
  return (
    <>
      <svg className="arrow" viewBox="0 0 10 12" aria-hidden="true" focusable="false">
        <path d="M0 0 10 6 0 12Z" />
      </svg>
      <span className="sr-only">changed to</span>
    </>
  )
}

/** Op badge, before ▶ after, delta tag. The before value is quiet, the after value is loud. */
export default function BeforeAfter({ c }: { c: Pick<Change, 'op' | 'before' | 'after'> }) {
  const pair = typeof c.before === 'number' && typeof c.after === 'number' ? numberPair(c.before, c.after) : null
  return (
    <div className="ba">
      <OpBadge op={c.op} />
      <span className="ba-b">
        <ValueView value={c.before} numText={pair?.[0]} absent={c.op === 'added' && c.before === null} />
      </span>
      <Arrow />
      <span className="ba-a">
        <ValueView value={c.after} numText={pair?.[1]} absent={c.op === 'removed' && c.after === null} />
      </span>
      <DeltaTag before={c.before} after={c.after} />
    </div>
  )
}
