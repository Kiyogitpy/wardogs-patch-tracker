import { Link } from 'react-router'
import { listBuilds, type BuildSummary } from '../api'
import { Empty, ErrorBox, Loading } from '../components/State'
import { StatList } from '../components/Stats'
import { useRemote, useTitle } from '../hooks'

function BuildEntry({ b }: { b: BuildSummary }) {
  return (
    <li className="entry">
      <div className="entry-main">
        <h2>
          <Link to={`/builds/${encodeURIComponent(b.label)}`}>{b.label}</Link>
        </h2>
        <p className="meta">
          <span>{b.released_at ? <time dateTime={b.released_at}>{b.released_at}</time> : 'release date unknown'}</span>
          <span>Steam build {b.steam_buildid ?? 'unknown'}</span>
          <span>{b.prev_label ? <>diffed against <b>{b.prev_label}</b></> : 'no previous build'}</span>
        </p>
        <StatList counts={b.totals} />
      </div>
      <Link className="btn btn-primary" to={`/builds/${encodeURIComponent(b.label)}`}>
        View diff <span className="sr-only">for {b.label}</span> &rarr;
      </Link>
    </li>
  )
}

export default function Timeline() {
  useTitle('')
  const r = useRemote((s) => listBuilds(s), 'builds')
  return (
    <>
      <h1 className="page-title">Build timeline</h1>
      <p className="lede">Every ingested build, newest first, with what changed compared to the build before it.</p>
      {r.state === 'loading' && <Loading what="builds" />}
      {r.state === 'error' && <ErrorBox error={r.error} />}
      {r.state === 'ok' &&
        (r.data.length === 0 ? (
          <Empty>No builds have been ingested yet.</Empty>
        ) : (
          <ol className="timeline">
            {r.data.map((b) => (
              <BuildEntry key={b.label} b={b} />
            ))}
          </ol>
        ))}
    </>
  )
}
