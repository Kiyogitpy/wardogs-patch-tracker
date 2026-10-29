import { Link } from 'react-router'
import { listBuilds, type BuildSummary } from '../api'
import Hero from '../components/Hero'
import { Empty, ErrorBox, Loading } from '../components/State'
import { StatLedger } from '../components/Stats'
import { useRemote, useTitle } from '../hooks'

export function BuildMeta({ b }: { b: BuildSummary }) {
  return (
    <dl className="meta">
      <div>
        <dt>Released</dt>
        <dd>{b.released_at ? <time dateTime={b.released_at}>{b.released_at}</time> : 'date unknown'}</dd>
      </div>
      <div>
        <dt>Steam build</dt>
        <dd className="mono">{b.steam_buildid ?? 'unknown'}</dd>
      </div>
      <div>
        <dt>Compared with</dt>
        <dd>
          {b.prev_label ? (
            <Link to={`/builds/${encodeURIComponent(b.prev_label)}`} className="mono">
              {b.prev_label}
            </Link>
          ) : (
            'no earlier build'
          )}
        </dd>
      </div>
    </dl>
  )
}

function BuildEntry({ b }: { b: BuildSummary }) {
  return (
    <li className="build">
      <div className="rail">
        <Link to={`/builds/${encodeURIComponent(b.label)}`} className="crate" aria-label={`Build ${b.label}`}>
          {b.label}
        </Link>
      </div>
      <div className="build-body">
        <BuildMeta b={b} />
        <StatLedger counts={b.totals} />
        <p>
          <Link className="btn btn-primary" to={`/builds/${encodeURIComponent(b.label)}`}>
            Open diff <span className="sr-only">for {b.label}</span>
          </Link>
        </p>
      </div>
    </li>
  )
}

export default function Timeline() {
  useTitle('')
  const r = useRemote((s) => listBuilds(s), 'builds')
  return (
    <>
      {r.state === 'loading' && <Loading what="builds" />}
      {r.state === 'error' && <ErrorBox what="the builds" error={r.error} onRetry={r.retry} />}
      {r.state === 'ok' &&
        (r.data.length === 0 ? (
          <Empty>No builds have been ingested yet.</Empty>
        ) : (
          <>
            <Hero latest={r.data[0]} />
            <h2 className="section-title">All builds</h2>
            <ol className="timeline">
              {r.data.map((b) => (
                <BuildEntry key={b.label} b={b} />
              ))}
            </ol>
          </>
        ))}
    </>
  )
}
