import { useRef } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { getAssets, getBuild, PAGE_SIZE, STATUSES } from '../api'
import AssetCard from '../components/AssetCard'
import { Empty, ErrorBox, Loading, NotFound } from '../components/State'
import { nf, STATUS_META, StatLedger } from '../components/Stats'
import { BuildMeta } from './Timeline'
import { useRemote, useTitle, useUrlText } from '../hooks'
import { apiQuery, changesToParams, EMPTY_CHANGES, EMPTY_FILTERS, filtersToParams, parseFilters, type Filters } from '../query'
import { totalAssets } from '../format'

export default function BuildDiff() {
  const { label = '' } = useParams()
  const [sp, setSp] = useSearchParams()
  const filters = parseFilters(sp)
  const topRef = useRef<HTMLHeadingElement>(null)

  const patch = (p: Partial<Filters>, replace = false) => {
    const next = { ...filters, page: 1, ...p }
    setSp(filtersToParams(next), { replace })
  }
  const [qText, setQText] = useUrlText(filters.q, (q) => patch({ q }, true))
  const [fieldText, setFieldText] = useUrlText(filters.field, (field) => patch({ field }, true))

  const build = useRemote((s) => getBuild(label, s), `build:${label}`)
  const query = apiQuery(filters)
  const assets = useRemote((s) => getAssets(label, query, s), `assets:${label}?${query}`)

  useTitle(label)

  if (build.state === 'error' && build.error.status === 404) return <NotFound what="build" />
  if (build.state === 'loading') return <Loading what="build" />
  if (build.state === 'error') return <ErrorBox what="the build" error={build.error} onRetry={build.retry} />
  const b = build.data

  const pages = assets.state === 'ok' ? Math.max(1, Math.ceil(assets.data.total / PAGE_SIZE)) : 1
  const areaLink = (area: string) => `?${filtersToParams({ ...filters, area, page: 1 }).toString()}`
  const hasFilters = filters.area || filters.status || filters.type || filters.q || filters.field
  const goPage = (page: number) => {
    setSp(filtersToParams({ ...filters, page }))
    topRef.current?.scrollIntoView({ block: 'start' })
  }

  return (
    <>
      <p className="crumbs">
        <Link to="/">All builds</Link>
      </p>
      <header className="build-head">
        <div className="rail">
          <h1 className="crate">{b.label}</h1>
        </div>
        <div className="build-body">
          <BuildMeta b={b} />
          <StatLedger counts={b.totals} />
        </div>
      </header>

      <a className="skip-inline" href="#filters" onClick={(e) => { e.preventDefault(); document.getElementById("filters")?.querySelector("select")?.focus() }}>
        Skip to filters
      </a>
      <nav className="areas" aria-label="Areas">
        <ul>
          <li>
            <Link to={areaLink('')} aria-current={filters.area === '' ? 'page' : undefined}>
              All areas <span className="count">{nf.format(totalAssets(b.totals))}</span>
            </Link>
          </li>
          {b.areas.map((a) => (
            <li key={a.id}>
              <Link to={areaLink(a.id)} aria-current={filters.area === a.id ? 'page' : undefined}>
                {a.title} <span className="count">{nf.format(totalAssets(a.counts))}</span>
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      <form id="filters" className="filters" role="search" aria-label="Filter assets" onSubmit={(e) => e.preventDefault()}>
        <label>
          <span>Status</span>
          <select value={filters.status} onChange={(e) => patch({ status: e.target.value as Filters['status'] })}>
            <option value="">Any status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_META[s].glyph} {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Type</span>
          <select value={filters.type} onChange={(e) => patch({ type: e.target.value })}>
            <option value="">Any type</option>
            {filters.type && !b.types.some((t) => t.type === filters.type) && (
              <option value={filters.type}>{filters.type}</option>
            )}
            {b.types.map((t) => (
              <option key={t.type} value={t.type}>
                {t.type} ({nf.format(t.assets)})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Name or path</span>
          <input type="search" value={qText} onChange={(e) => setQText(e.target.value)} placeholder="e.g. AK74" />
        </label>
        <label>
          <span>Field path</span>
          <input
            type="search"
            value={fieldText}
            onChange={(e) => setFieldText(e.target.value)}
            placeholder="e.g. BaseDamage"
          />
        </label>
        {fieldText.trim().length >= 2 && (
          <Link
            className="side-link"
            to={`/changes?${changesToParams({ ...EMPTY_CHANGES, field: fieldText.trim(), area: filters.area })}`}
          >
            Search all builds
          </Link>
        )}
        {hasFilters && (
          <Link className="btn clear" to={`?${filtersToParams(EMPTY_FILTERS)}`} onClick={() => { setQText(''); setFieldText('') }}>
            Clear filters
          </Link>
        )}
      </form>

      <h2 className="results-title" ref={topRef} tabIndex={-1}>
        {assets.state === 'ok' ? (
          <>
            {nf.format(assets.data.total)} asset{assets.data.total === 1 ? '' : 's'}
            {assets.data.total > PAGE_SIZE && assets.data.items.length > 0 && (
              <span className="muted">
                {' '}
                page {filters.page} of {pages}
              </span>
            )}
          </>
        ) : (
          'Assets'
        )}
      </h2>
      <div aria-live="polite">
        {assets.state === 'loading' && <Loading what="assets" />}
        {assets.state === 'error' && <ErrorBox what="the assets" error={assets.error} onRetry={assets.retry} />}
        {assets.state === 'ok' && assets.data.items.length === 0 && assets.data.total > 0 && (
          <Empty>
            Page {filters.page} is past the end: there {pages === 1 ? 'is' : 'are'} {pages} page{pages === 1 ? '' : 's'} of
            results.{' '}
            <Link to={`?${filtersToParams({ ...filters, page: pages })}`}>Go to the last page ({pages})</Link>
          </Empty>
        )}
        {assets.state === 'ok' && assets.data.items.length === 0 && assets.data.total === 0 && (
          <Empty>
            {hasFilters ? (
              <>
                No assets match these filters.{' '}
                <Link
                  to={`?${filtersToParams(EMPTY_FILTERS)}`}
                  onClick={() => {
                    setQText('')
                    setFieldText('')
                  }}
                >
                  Clear filters
                </Link>
              </>
            ) : (
              'This build has no changed assets.'
            )}
          </Empty>
        )}
      </div>
      {assets.state === 'ok' && assets.data.items.length > 0 && (
        <>
          <div className="assets">
            {assets.data.items.map((a) => (
              <AssetCard key={a.path} asset={a} />
            ))}
          </div>
          {pages > 1 && (
            <nav className="pager" aria-label="Pagination">
              <button type="button" className="btn" disabled={filters.page <= 1} onClick={() => goPage(filters.page - 1)}>
                Previous
              </button>
              <span>
                Page {filters.page} of {pages}
              </span>
              <button type="button" className="btn" disabled={filters.page >= pages} onClick={() => goPage(filters.page + 1)}>
                Next
              </button>
            </nav>
          )}
        </>
      )}
    </>
  )
}
