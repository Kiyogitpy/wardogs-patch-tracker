import { fmtNumber } from '../format'
import { scalePoints } from '../history'

const W = 120
const H = 32
const PAD = 5

/** Tiny trend line for >= 2 numeric points. The last point is marked with a larger filled dot. */
export default function Sparkline({ values, name }: { values: number[]; name: string }) {
  if (values.length < 2) return null
  const pts = scalePoints(values, W, H, PAD)
  const last = pts[pts.length - 1]
  const label = `Trend of ${name}: ${values.map(fmtNumber).join(', then ')}`
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={label}>
      <polyline className="spark-line" points={pts.map((p) => `${p.x},${p.y}`).join(' ')} />
      {pts.slice(0, -1).map((p, i) => (
        <circle key={i} className="spark-dot" cx={p.x} cy={p.y} r={2.5} />
      ))}
      <circle className="spark-last" cx={last.x} cy={last.y} r={4} />
    </svg>
  )
}
