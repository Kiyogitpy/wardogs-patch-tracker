import { useEffect, useState } from 'react'

// The roll plays once per page load, not on every visit to the timeline.
let played = false

const DIGITS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
const isDigit = (c: string | undefined): c is string => c !== undefined && c >= '0' && c <= '9'

function prefersReducedMotion(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Shows `to`. On first load the digits roll from `from` (about 900 ms, ease-out, staggered right to left).
 * With reduced motion, or after the first play, it is plain text.
 */
export default function Odometer({ from, to }: { from: string; to: string }) {
  const [animate] = useState(() => !played && !prefersReducedMotion())
  const [go, setGo] = useState(false)
  useEffect(() => {
    if (!animate) return
    // Two frames so the start position is painted before the transition begins.
    let id2 = 0
    const id1 = requestAnimationFrame(() => {
      id2 = requestAnimationFrame(() => {
        played = true
        setGo(true)
      })
    })
    return () => {
      cancelAnimationFrame(id1)
      cancelAnimationFrame(id2)
    }
  }, [animate])

  if (!animate) return <span className="odo">{to}</span>

  const chars = [...to]
  const src = [...from]
  const off = chars.length - src.length // align the two numbers on their right edge
  const increasing = Number.parseFloat(to) >= Number.parseFloat(from)
  return (
    <span className={`odo odo-live${go ? ' odo-go' : ''}`} role="text" aria-label={to}>
      {chars.map((ch, i) => {
        if (!isDigit(ch)) {
          return (
            <span key={i} aria-hidden="true">
              {ch}
            </span>
          )
        }
        const f = src[i - off]
        const d0 = isDigit(f) ? Number(f) : 0
        const d1 = Number(ch)
        // Rolling up runs through a full turn; rolling down starts a turn in.
        const start = increasing ? d0 : d0 + 10
        const end = increasing ? d1 + 10 : d1
        const rank = chars.length - 1 - i // rightmost digit starts first
        return (
          <span key={i} className="odo-col" aria-hidden="true" style={{ ['--rank' as string]: rank }}>
            <span className="odo-strip" style={{ transform: `translateY(calc(${-(go ? end : start)} * var(--cell)))` }}>
              {DIGITS.map((d, k) => (
                <span key={k}>{d}</span>
              ))}
            </span>
          </span>
        )
      })}
    </span>
  )
}
