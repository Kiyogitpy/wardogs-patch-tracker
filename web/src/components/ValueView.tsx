import { useId, useState } from 'react'
import type { Json } from '../api'
import { inlineText, isLong, pretty } from '../format'

/** One before/after value. Long containers collapse behind an expand button. */
export default function ValueView({ value, absent, numText }: { value: Json; absent?: boolean; numText?: string }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  if (absent) return <span className="val val-absent" aria-label="not present">&mdash;</span>
  if (!isLong(value)) {
    return <span className={`val ${value === null ? 'val-null' : ''}`}>{numText ?? inlineText(value)}</span>
  }
  const text = typeof value === 'string' ? value : pretty(value)
  return (
    <div className="val val-long">
      <button type="button" className="expand" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>{' '}
        {typeof value === 'string' ? `${value.length} chars` : inlineText(value)}
        <span className="sr-only">{open ? ', collapse' : ', expand'}</span>
      </button>
      {open ? (
        <pre id={id}>{text}</pre>
      ) : (
        <span id={id} className="preview">
          {text.replace(/\s+/g, ' ').slice(0, 60)}&hellip;
        </span>
      )}
    </div>
  )
}
