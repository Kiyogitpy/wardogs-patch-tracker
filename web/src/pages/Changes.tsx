import { useRef } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getBuild, getChanges, listBuilds, PAGE_SIZE } from '../api'
import Watch from '../components/Watch'
import { Empty, ErrorBox, Loading } from '../components/State'
import Pager from '../components/Pager'
import { FieldPath } from '../components/AssetCard'
import BeforeAfter from '../components/BeforeAfter'
import { nf, OP_BAR, StatusBadge } from '../components/Stats'
import { readableAsset } from '../hero'
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

      {ready && state.field.trim().length <= 200 && (
        <Watch target={{ kind: 'field', target: state.field.trim() }} what={`the field ${state.field.trim()}`} />
      )}

      <h2 className="results-title" ref={topRef} tabIndex={-1}>
        {data ? (
          <>
            {nf.format(data.total)} change{data.total === 1 ? '' : 's'}
            {data.total > PAGE_SIZE && (
              <span className="muted">
                {' '}
                page {state.page} of {pages}
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
        {ready && res.state === 'error' && <ErrorBox what="the changes" error={res.error} onRetry={res.retry} />}
        {ready && data && data.total === 0 && <Empty>
            No changes match these filters. <Link to={`?${changesToParams(EMPTY_CHANGES)}`} onClick={() => setFieldText('')}>Clear filters</Link>
          </Empty>}
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
            <table className="rows stack results wide">
              <thead>
                <tr>
                  <th scope="col">Asset</th>
                  <th scope="col">Field</th>
                  <th scope="col">Before to after</th>
                  <th scope="col">Build</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c, i) => (
                  <tr key={`${c.build}:${c.asset.path}:${c.field_path}:${i}`} className={`mark ${OP_BAR[c.op]}`}>
                    <th scope="row" className="c-asset">
                      <Link to={`/asset?${new URLSearchParams({ path: c.asset.path })}`} className="asset-link">
                        {readableAsset(c.asset.name)}
                      </Link>{' '}
                      <StatusBadge status={c.status} />
                    </th>
                    <td className="c-field field">
                      <FieldPath path={c.field_path} />
                    </td>
                    <td className="c-change">
                      <BeforeAfter c={c} />
                    </td>
                    <td className="c-build">
                      <Link to={`/builds/${encodeURIComponent(c.build)}`} className="mono">
                        {c.build}
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
