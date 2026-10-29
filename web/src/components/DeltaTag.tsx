import type { Json } from '../api'
import { deltaText, numericDelta } from '../format'

/** The yellow notched tag for any number-to-number change. Renders nothing for other values. */
export default function DeltaTag({ before, after, size }: { before: Json; after: Json; size?: 'xl' }) {
  const d = numericDelta(before, after)
  if (!d) return null
  return (
    <span className={`delta-tag${size ? ` delta-${size}` : ''}`}>
      <span className="sr-only">{d.delta.startsWith('−') ? 'decrease ' : 'increase '}</span>
      {deltaText(d)}
    </span>
  )
}
