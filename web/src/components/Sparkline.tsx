import { fmtNumber } from '../format'
import { scaleSeries, segments, type Pt } from '../history'

const W = 160
const H = 40
const PAD = 7

/**
 * Trend line over a field's values. Absent and non-numeric values (null) break the line into separate
 * segments; a lone value between gaps is drawn as a dot. The last numeric point is larger.
 */
export default function Sparkline({
  values,
  labels,
  name,
}: {
  values: (number | null)[]
  labels: string[]
  name: string
}) {
  const nums = values.filter((v): v is number => v !== null)
  if (nums.length < 2) return null
  const pts = scaleSeries(values, W, H, PAD)
  const runs = segments(pts)
  const lastIdx = pts.map((p) => p !== null).lastIndexOf(true)
  const gaps = pts.some((p) => p === null)
  const label =
    `Trend of ${name}: ${nums.map(fmtNumber).join(', then ')}` + (gaps ? ' (with gaps where it was not a number)' : '')
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={label}>
      {runs
        .filter((r) => r.length > 1)
        .map((r, i) => (
          <polyline key={i} className="spark-line" points={r.map((p: Pt) => `${p.x},${p.y}`).join(' ')} />
        ))}
      {pts.map((p, i) =>
        p === null ? null : (
          <circle key={i} className={i === lastIdx ? 'spark-last' : 'spark-dot'} cx={p.x} cy={p.y} r={i === lastIdx ? 5 : 4}>
            <title>{`${labels[i] ?? ''}: ${fmtNumber(values[i] as number)}`}</title>
          </circle>
        ),
      )}
    </svg>
  )
}
