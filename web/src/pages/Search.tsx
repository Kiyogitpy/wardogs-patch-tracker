import { useRef } from 'react'
import { Link, useSearchParams } from 'react-router'
import { PAGE_SIZE, searchAssets } from '../api'
import { Empty, ErrorBox, Loading } from '../components/State'
import Pager from '../components/Pager'
import { nf, StatusBadge } from '../components/Stats'
import { useRemote, useTitle } from '../hooks'
import { readableAsset } from '../hero'
import { parseSearch, searchApiQuery, searchToParams } from '../query'

export default function Search() {
  const [sp, setSp] = useSearchParams()
  const state = parseSearch(sp)
  const topRef = useRef<HTMLHeadingElement>(null)
  const query = searchApiQuery(state)
  const res = useRemote((s) => searchAssets(query, s), `search:${query}`)
  useTitle(state.q ? `Search: ${state.q}` : 'Search')

  const pages = res.state === 'ok' ? Math.max(1, Math.ceil(res.data.total / PAGE_SIZE)) : 1
  const goPage = (page: number) => {
    setSp(searchToParams({ ...state, page }))
    topRef.current?.scrollIntoView({ block: 'start' })
  }

  return (
    <>
      <h1 className="page-title">Search assets</h1>
      <p className="lede">
        {state.q ? (
          <>
            Assets whose name or path (current or former) contains <b className="mono">{state.q}</b>.
          </>
        ) : (
          'Every asset that appears in a build diff. Type in the search box above to narrow it down.'
        )}
      </p>
      <h2 className="results-title" ref={topRef} tabIndex={-1}>
        {res.state === 'ok' ? (
          <>
            {nf.format(res.data.total)} asset{res.data.total === 1 ? '' : 's'}
            {res.data.total > PAGE_SIZE && (
              <span className="muted">
                {' '}
                page {state.page} of {pages}
              </span>
            )}
          </>
        ) : (
          'Results'
        )}
      </h2>
      <div aria-live="polite">
        {res.state === 'loading' && <Loading what="assets" />}
        {res.state === 'error' && <ErrorBox what="the assets" error={res.error} onRetry={res.retry} />}
        {res.state === 'ok' && res.data.total === 0 && (
          <Empty>
            {state.q ? (
              <>
                No assets match &ldquo;{state.q}&rdquo;. <Link to="/search">Clear search</Link>
              </>
            ) : (
              'No assets have been ingested yet.'
            )}
          </Empty>
        )}
        {res.state === 'ok' && res.data.total > 0 && res.data.items.length === 0 && (
          <Empty>
            Page {state.page} is past the end.{' '}
            <Link to={`?${searchToParams({ ...state, page: pages })}`}>Go to the last page ({pages})</Link>
          </Empty>
        )}
      </div>
      {res.state === 'ok' && res.data.items.length > 0 && (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Search results">
            <table className="rows stack results">
              <thead>
                <tr>
                  <th scope="col">Asset</th>
                  <th scope="col">Type</th>
                  <th scope="col">Builds</th>
                  <th scope="col">Latest change</th>
                </tr>
              </thead>
              <tbody>
                {res.data.items.map((a) => (
                  <tr key={a.path}>
                    <th scope="row" className="c-asset">
                      <Link to={`/asset?${new URLSearchParams({ path: a.path })}`} className="asset-link">
                        {readableAsset(a.name)}
                      </Link>
                      <span className="path">{a.path}</span>
                    </th>
                    <td className="c-type">
                      <span className="tag">{a.type}</span>
                    </td>
                    <td className="c-builds">
                      <span className="mono">{a.builds}</span> build{a.builds === 1 ? '' : 's'}
                    </td>
                    <td className="c-last">
                      <StatusBadge status={a.last.status} /> in{' '}
                      <Link to={`/builds/${encodeURIComponent(a.last.label)}`} className="mono">
                        {a.last.label}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={state.page} pages={pages} onPage={goPage} />
        </>
      )}
    </>
  )
}
