import { useRef } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getBuild, getChanges, listBuilds, PAGE_SIZE } from '../api'
import { Empty, ErrorBox, Loading } from '../components/State'
import Pager from '../components/Pager'
import { nf, StatusBadge } from '../components/Stats'
import ValueView from '../components/ValueView'
import { numberPair, numericDelta } from '../format'
import { useRemote, useTitle, useUrlText } from '../hooks'
import {
  changesApiQuery,
  changesToParams,
  EMPTY_CHANGES,
  MIN_FIELD_CHARS,
  OPS,
  parseChanges,
  type ChangesState,
} from '../query'

export default function Changes() {
  const [sp, setSp] = useSearchParams()
  const state = parseChanges(sp)
  const topRef = useRef<HTMLHeadingElement>(null)
  const patch = (p: Partial<ChangesState>, replace = false) =>
    setSp(changesToParams({ ...state, page: 1, ...p }), { replace })
  const [fieldText, setFieldText] = useUrlText(state.field, (field) => patch({ field }, true))

  const ready = state.field.trim().length >= MIN_FIELD_CHARS
  const query = changesApiQuery({ ...state, field: state.field.trim() })
  const res = useRemote((s) => (ready ? getChanges(query, s) : Promise.resolve(null)), `changes:${ready}:${query}`)

  const builds = useRemote((s) => listBuilds(s), 'builds')
  const buildList = builds.state === 'ok' ? builds.data : []
  // Areas come from the selected build, else from the newest one.
  const areaBuild = state.build || buildList[0]?.label || ''
  const detail = useRemote((s) => (areaBuild ? getBuild(areaBuild, s) : Promise.resolve(null)), `build:${areaBuild}`)
  const areas = detail.state === 'ok' && detail.data ? detail.data.areas : []

  useTitle(state.field ? `Changes: ${state.field}` : 'Changes')

  const data = res.state === 'ok' ? res.data : null
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE))
  const hasFilters = state.field || state.build || state.area || state.op
  const goPage = (page: number) => {
    setSp(changesToParams({ ...state, page }))
    topRef.current?.scrollIntoView({ block: 'start' })
  }

  return (
    <>
      <h1 className="page-title">Changes across builds</h1>
      <p className="lede">Every change to a field, in every build. Type part of a field path, such as BaseDamage.</p>

      <form className="filters" role="search" aria-label="Filter changes" onSubmit={(e) => e.preventDefault()}>
        <label>
          <span>Field path (min. {MIN_FIELD_CHARS} characters)</span>
          <input
            type="search"
            value={fieldText}
            onChange={(e) => setFieldText(e.target.value)}
            placeholder="e.g. BaseDamage"
          />
        </label>
        <label>
          <span>Build</span>
          <select value={state.build} onChange={(e) => patch({ build: e.target.value })}>
            <option value="">All builds</option>
            {state.build && !buildList.some((b) => b.label === state.build) && (
              <option value={state.build}>{state.build}</option>
            )}
            {buildList.map((b) => (
              <option key={b.label} value={b.label}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Area</span>
          <select value={state.area} onChange={(e) => patch({ area: e.target.value })}>
            <option value="">Any area</option>
            {state.area && !areas.some((a) => a.id === state.area) && <option value={state.area}>{state.area}</option>}
            {areas.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Op</span>
          <select value={state.op} onChange={(e) => patch({ op: e.target.value as ChangesState['op'] })}>
            <option value="">Any op</option>
            {OPS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        {hasFilters && (
          <Link className="btn clear" to={`?${changesToParams(EMPTY_CHANGES)}`} onClick={() => setFieldText('')}>
            Clear filters
          </Link>
        )}
      </form>

      <h2 className="results-title" ref={topRef} tabIndex={-1}>
        {data ? (
          <>
            {nf.format(data.total)} change{data.total === 1 ? '' : 's'}
            {data.total > PAGE_SIZE && (
              <span className="muted">
                {' '}
                &middot; page {state.page} of {pages}
              </span>
            )}
          </>
        ) : (
          'Changes'
        )}
      </h2>
      <div aria-live="polite">
        {!ready && <Empty>Enter at least {MIN_FIELD_CHARS} characters of a field path to search.</Empty>}
        {ready && res.state === 'loading' && <Loading what="changes" />}
        {ready && res.state === 'error' && <ErrorBox error={res.error} onRetry={res.retry} />}
        {ready && data && data.total === 0 && <Empty>No changes match these filters.</Empty>}
        {ready && data && data.total > 0 && data.items.length === 0 && (
          <Empty>
            Page {state.page} is past the end.{' '}
            <Link to={`?${changesToParams({ ...state, page: pages })}`}>Go to the last page ({pages})</Link>
          </Empty>
        )}
      </div>
      {ready && data && data.items.length > 0 && (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Field changes">
            <table className="changes results wide">
              <thead>
                <tr>
                  <th scope="col">Build</th>
                  <th scope="col">Asset</th>
                  <th scope="col">Field</th>
                  <th scope="col">Op</th>
                  <th scope="col">Before &rarr; after</th>
                  <th scope="col">Delta</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c, i) => {
                  const d = numericDelta(c.before, c.after)
                  const pair =
                    typeof c.before === 'number' && typeof c.after === 'number' ? numberPair(c.before, c.after) : null
                  return (
                    <tr key={`${c.build}:${c.asset.path}:${c.field_path}:${i}`}>
                      <th scope="row" className="nowrap">
                        <Link to={`/builds/${encodeURIComponent(c.build)}`} className="mono">
                          {c.build}
                        </Link>
                      </th>
                      <td>
                        <Link to={`/asset?${new URLSearchParams({ path: c.asset.path })}`} className="asset-link">
                          {c.asset.name}
                        </Link>{' '}
                        <StatusBadge status={c.status} />
                      </td>
                      <td className="fieldcell">{c.field_path}</td>
                      <td>
                        <span className={`op op-${c.op}`}>{c.op}</span>
                      </td>
                      <td>
                        <div className="ba">
                          <ValueView value={c.before} numText={pair?.[0]} absent={c.op === 'added' && c.before === null} />
                          <span aria-hidden="true">&rarr;</span>
                          <span className="sr-only">changed to</span>
                          <ValueView value={c.after} numText={pair?.[1]} absent={c.op === 'removed' && c.after === null} />
                        </div>
                      </td>
                      <td className="delta">
                        {d && (
                          <span className={d.delta.startsWith('+') ? 'up' : d.delta.startsWith('−') ? 'down' : ''}>
                            {d.delta}
                            {d.pct && <span className="pct"> ({d.pct})</span>}
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <Pager page={state.page} pages={pages} onPage={goPage} />
        </>
      )}
    </>
  )
}
