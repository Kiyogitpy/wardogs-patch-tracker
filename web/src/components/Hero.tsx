import { Link } from 'react-router'
import { getChanges, type BuildSummary } from '../api'
import { numberPair } from '../format'
import { pickHeroLines, type HeroLine } from '../hero'
import { useRemote } from '../hooks'
import { FieldPath } from './AssetCard'
import { Arrow } from './BeforeAfter'
import DeltaTag from './DeltaTag'
import Odometer from './Odometer'
import { ErrorBox } from './State'
import { StatLedger } from './Stats'

async function loadHero(label: string, signal: AbortSignal): Promise<HeroLine[]> {
  const q = (field: string) => new URLSearchParams({ field, build: label, limit: '200' }).toString()
  const [base, other] = await Promise.all([getChanges(q('BaseDamage'), signal), getChanges(q('Damage'), signal)])
  return pickHeroLines(base.items, other.items)
}

const allDamage = (label: string) => `/changes?${new URLSearchParams({ field: 'Damage', build: label })}`

function Note({ l }: { l: HeroLine }) {
  const [b, a] = numberPair(l.before, l.after)
  return (
    <li className="note-line">
      <span className="note-bar hazard" aria-hidden="true" />
      <div className="note-what">
        <Link to={`/asset?${new URLSearchParams({ path: l.assetPath })}`} className="note-asset">
          {l.asset}
        </Link>
        <span className="note-field" title={l.fieldPath}>
          {l.field}
        </span>
      </div>
      <div className="note-ba">
        <span className="ba-b mono">{b}</span>
        <Arrow />
        <span className="ba-a mono">{a}</span>
        <DeltaTag before={l.before} after={l.after} />
      </div>
    </li>
  )
}

/** Width in characters of the display line, so CSS can shrink the type to fit narrow screens. */
const fit = (...parts: string[]) => parts.reduce((n, p) => n + p.length, 0) + 1

export default function Hero({ latest }: { latest: BuildSummary }) {
  const r = useRemote((s) => loadHero(latest.label, s), `hero:${latest.label}`)

  if (r.state === 'loading') {
    return (
      <section className="hero" aria-busy="true" aria-label="Biggest damage change">
        <span className="hero-bar hazard" aria-hidden="true" />
        <p className="state" role="status">
          <span className="spinner" aria-hidden="true" /> Loading the biggest damage change&hellip;
        </p>
      </section>
    )
  }
  if (r.state === 'error') {
    return (
      <section className="hero" aria-label="Biggest damage change">
        <span className="hero-bar hazard" aria-hidden="true" />
        <ErrorBox what="the biggest damage change" error={r.error} onRetry={r.retry} />
      </section>
    )
  }

  const [top, ...rest] = r.data
  if (!top) {
    return (
      <section className="hero" aria-label="Latest build">
        <span className="hero-bar bar-added" aria-hidden="true" />
        <h1 className="hero-kicker">Latest build, no numeric damage changes</h1>
        <p className="hero-num" style={{ ['--n' as string]: fit(latest.label) }}>
          {latest.label}
        </p>
        <StatLedger counts={latest.totals} />
        <p className="hero-links">
          <Link to={`/builds/${encodeURIComponent(latest.label)}`}>Open the full diff</Link>
        </p>
      </section>
    )
  }
  const [b, a] = numberPair(top.before, top.after)
  return (
    <section className="hero" aria-label="Biggest damage change">
      <span className="hero-bar hazard" aria-hidden="true" />
      <h1 className="hero-kicker">
        Biggest damage change in{' '}
        <Link to={`/builds/${encodeURIComponent(top.build)}`} className="mono">
          {top.build}
        </Link>
      </h1>
      <p className="hero-asset">
        <Link to={`/asset?${new URLSearchParams({ path: top.assetPath })}`}>{top.asset}</Link>
        <span className="hero-field"> {top.field}</span>
      </p>
      <p className="hero-path path">
        <FieldPath path={top.fieldPath} />
      </p>
      <p className="hero-num" style={{ ['--n' as string]: fit(b, a) }} aria-label={`${b} to ${a}`}>
        <span className="hero-b" aria-hidden="true">
          {b}
        </span>
        <svg className="hero-arrow" viewBox="0 0 10 12" aria-hidden="true" focusable="false">
          <path d="M0 0 10 6 0 12Z" />
        </svg>
        <span className="hero-a" aria-hidden="true">
          <Odometer from={b} to={a} />
        </span>
        <DeltaTag before={top.before} after={top.after} size="xl" />
      </p>
      {rest.length > 0 && (
        <ol className="notes" aria-label="More damage changes">
          {rest.map((l) => (
            <Note key={l.key} l={l} />
          ))}
        </ol>
      )}
      <p className="hero-links">
        <Link to={allDamage(top.build)} className="btn btn-primary">
          All damage changes
        </Link>
      </p>
    </section>
  )
}
