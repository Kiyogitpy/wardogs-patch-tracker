import { useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { getAssetHistory, type AssetHistory as History, type HistoryEntry } from '../api'
import { ChangesTable } from '../components/AssetCard'
import { Empty, ErrorBox, Loading, NotFound } from '../components/State'
import { StatusBadge } from '../components/Stats'
import Sparkline from '../components/Sparkline'
import ValueView from '../components/ValueView'
import { numberPair } from '../format'
import { groupByField, numericValues, stepDelta, type FieldSeries } from '../history'
import { useRemote, useTitle, useUrlText } from '../hooks'
import { assetApiQuery, assetToParams, parseAsset, type AssetState } from '../query'

const NOISE_LABELS: Record<string, string> = {
  renamed_keys: 'renamed keys',
  ref_shifts: 'reference shifts',
  dup_keys: 'duplicate keys',
  swapped_keys: 'swapped keys',
  type_reinterpret: 'type reinterpretations',
}

function noiseText(n: HistoryEntry['noise']): string {
  return Object.entries(n)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => `${v} ${NOISE_LABELS[k] ?? k}`)
    .join(', ')
}

function FieldCard({ series }: { series: FieldSeries }) {
  const nums = numericValues(series.values)
  return (
    <article className="card field-card" aria-label={series.field}>
      <header className="field-head">
        <h3 className="field-name">{series.field}</h3>
        <Sparkline values={nums} name={series.field} />
      </header>
      <ol className="seq">
        {series.values.map((v, i) => {
          const prev = i > 0 ? series.values[i - 1] : null
          const d = prev ? stepDelta(prev, v) : null
          const pair =
            prev && !prev.absent && !v.absent && typeof prev.value === 'number' && typeof v.value === 'number'
              ? numberPair(prev.value, v.value)
              : null
          return (
            <li key={i} className={i === 0 ? 'seq-start' : ''}>
              <span className="seq-build mono">
                {i === 0 && v.label !== 'before' ? 'before ' : ''}
                {v.label}
              </span>
              <span className="seq-val">
                <ValueView value={v.value} numText={pair?.[1]} absent={v.absent} />
              </span>
              {v.op && v.op !== 'changed' && <span className={`op op-${v.op}`}>{v.op}</span>}
              {d && (
                <span className={`seq-delta ${d.delta.startsWith('+') ? 'up' : d.delta.startsWith('−') ? 'down' : ''}`}>
                  {d.delta}
                  {d.pct && <span className="pct"> ({d.pct})</span>}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </article>
  )
}

function BuildSection({ h, name, filtered }: { h: HistoryEntry; name: string; filtered: boolean }) {
  const noiseTotal = Object.values(h.noise).reduce((a, b) => a + b, 0)
  return (
    <section className="card build-sec" aria-label={`Build ${h.build.label}`}>
      <header className="card-head">
        <h3>
          <Link to={`/builds/${encodeURIComponent(h.build.label)}`}>{h.build.label}</Link>
        </h3>
        <StatusBadge status={h.status} />
        <span className="chip">{h.area}</span>
        {h.build.released_at && <time className="muted mono" dateTime={h.build.released_at}>{h.build.released_at}</time>}
      </header>
      {h.note && <p className="note">{h.note}</p>}
      {noiseTotal > 0 && <p className="note">Serialization noise: {noiseText(h.noise)}.</p>}
      {h.changes.length > 0 ? (
        <ChangesTable name={`${name} in ${h.build.label}`} changes={h.changes} />
      ) : (
        <p className="muted none">{filtered ? 'no matching changes' : 'no field changes in this build'}</p>
      )}
    </section>
  )
}

export default function AssetHistory() {
  const [sp, setSp] = useSearchParams()
  const state = parseAsset(sp)
  const navigate = useNavigate()
  const location = useLocation()
  const movedState = (location.state as { movedFrom?: string } | null)?.movedFrom ?? null

  const patch = (p: Partial<AssetState>, replace = false) => setSp(assetToParams({ ...state, ...p }), { replace })
  const [fieldText, setFieldText] = useUrlText(state.field, (field) => patch({ field }, true))

  const query = assetApiQuery(state)
  const res = useRemote((s) => getAssetHistory(query, s), `asset:${query}`)

  // Keep showing the last loaded asset while a field filter reloads, so the input keeps focus.
  const last = useRef<{ path: string; data: History } | null>(null)
  if (res.state === 'ok') last.current = { path: state.path, data: res.data }
  const data = res.state === 'ok' ? res.data : last.current?.path === state.path ? last.current.data : null

  // A former path resolves to the current asset: replace the URL, remember where we came from.
  const resolvedPath = res.state === 'ok' ? res.data.path : null
  useEffect(() => {
    if (resolvedPath && resolvedPath !== state.path) {
      navigate(`/asset?${assetToParams({ ...state, path: resolvedPath })}`, {
        replace: true,
        state: { movedFrom: state.path },
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedPath])

  useTitle(data?.name ?? 'Asset')

  if (!state.path) return <NotFound what="asset" />
  if (res.state === 'error' && res.error.status === 404) return <NotFound what="asset" />
  if (!data) return res.state === 'error' ? <ErrorBox error={res.error} onRetry={res.retry} /> : <Loading what="asset history" />

  const movedFrom = movedState ?? (data.path !== state.path ? state.path : null)
  const filtered = state.field !== ''
  const series = groupByField(data.history)

  return (
    <>
      <p className="crumbs">
        <Link to="/search">&larr; Search assets</Link>
      </p>
      <h1 className="page-title mono">{data.name}</h1>
      <p className="meta">
        <span className="chip">{data.type}</span>
        <span>
          {data.history.length} build{data.history.length === 1 ? '' : 's'}
        </span>
      </p>
      <p className="path">{data.path}</p>
      {movedFrom && (
        <p className="path old">
          <span className="path-label">moved from</span> {movedFrom}
        </p>
      )}
      {data.aliases.length > 0 && (
        <section aria-labelledby="aliases-h" className="aliases">
          <h2 id="aliases-h" className="sub-title">
            Former paths
          </h2>
          <ul>
            {data.aliases.map((a) => (
              <li key={`${a.build}:${a.old_path}`}>
                <span className="path">{a.old_path}</span>{' '}
                <span className="muted">
                  moved in{' '}
                  <Link to={`/builds/${encodeURIComponent(a.build)}`} className="mono">
                    {a.build}
                  </Link>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="viewbar">
        <div role="group" aria-label="View" className="seg">
          <button
            type="button"
            className="btn"
            aria-pressed={state.view === 'field'}
            onClick={() => patch({ view: 'field' })}
          >
            By field
          </button>
          <button
            type="button"
            className="btn"
            aria-pressed={state.view === 'build'}
            onClick={() => patch({ view: 'build' })}
          >
            By build
          </button>
        </div>
        <form className="filters inline" role="search" aria-label="Filter fields" onSubmit={(e) => e.preventDefault()}>
          <label>
            <span>Field path</span>
            <input
              type="search"
              value={fieldText}
              onChange={(e) => setFieldText(e.target.value)}
              placeholder="e.g. BaseDamage"
            />
          </label>
        </form>
      </div>

      <div aria-live="polite">{res.state === 'loading' && <Loading what="asset history" />}</div>
      {res.state === 'error' && <ErrorBox error={res.error} onRetry={res.retry} />}

      {state.view === 'field' ? (
        series.length === 0 ? (
          <Empty>{filtered ? 'No field matches this filter.' : 'No field-level changes recorded for this asset.'}</Empty>
        ) : (
          <div className="cards">
            {series.map((s) => (
              <FieldCard key={s.field} series={s} />
            ))}
          </div>
        )
      ) : data.history.length === 0 ? (
        <Empty>This asset is in no ingested build.</Empty>
      ) : (
        <div className="cards">
          {data.history.map((h) => (
            <BuildSection key={h.build.label} h={h} name={data.name} filtered={filtered} />
          ))}
        </div>
      )}
    </>
  )
}
