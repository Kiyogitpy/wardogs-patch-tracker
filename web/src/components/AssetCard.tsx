import type { AssetItem, Change } from '../api'
import { numberPair, numericDelta } from '../format'
import { StatusBadge } from './Stats'
import ValueView from './ValueView'

const NOISE_LABELS: Record<string, string> = {
  renamed_keys: 'renamed keys',
  ref_shifts: 'reference shifts',
  dup_keys: 'duplicate keys',
  swapped_keys: 'swapped keys',
  type_reinterpret: 'type reinterpretations',
}

const NOISE_SINGULAR: Record<string, string> = {
  renamed_keys: 'renamed key',
  ref_shifts: 'reference shift',
  dup_keys: 'duplicate key',
  swapped_keys: 'swapped key',
  type_reinterpret: 'type reinterpretation',
}

function noiseSummary(noise: AssetItem['noise']): string {
  return Object.entries(noise)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${n === 1 ? (NOISE_SINGULAR[k] ?? k) : (NOISE_LABELS[k] ?? k)}`)
    .join(', ')
}

function ChangeRow({ c }: { c: Change }) {
  const d = numericDelta(c.before, c.after)
  const pair = typeof c.before === 'number' && typeof c.after === 'number' ? numberPair(c.before, c.after) : null
  return (
    <tr>
      <th scope="row" className="field">
        {c.field_path.split(/(?<=[.\[])/).map((part, i) => (
          <span key={i}>
            {part}
            <wbr />
          </span>
        ))}
      </th>
      <td>
        <span className={`op op-${c.op}`}>{c.op}</span>
      </td>
      <td>
        <ValueView value={c.before} numText={pair?.[0]} absent={c.op === 'added' && c.before === null} />
      </td>
      <td>
        <ValueView value={c.after} numText={pair?.[1]} absent={c.op === 'removed' && c.after === null} />
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
}

export default function AssetCard({ asset }: { asset: AssetItem }) {
  const noiseTotal = Object.values(asset.noise).reduce((a, b) => a + b, 0)
  const noiseOnly = asset.status === 'modified' && asset.changes.length === 0 && noiseTotal > 0
  return (
    <article className="card" aria-label={asset.name}>
      <header className="card-head">
        <h3>{asset.name}</h3>
        <StatusBadge status={asset.status} />
        <span className="chip">{asset.type}</span>
        {noiseOnly && (
          <span className="chip chip-noise" title={noiseSummary(asset.noise)}>
            noise only
          </span>
        )}
      </header>
      <p className="path">{asset.path}</p>
      {asset.old_path && (
        <p className="path old">
          <span className="path-label">moved from</span> {asset.old_path}
        </p>
      )}
      {asset.note && <p className="note">{asset.note}</p>}
      {noiseOnly && (
        <p className="note">
          Only serialization noise changed here: {noiseSummary(asset.noise)}. No game values differ.
        </p>
      )}
      {asset.changes.length > 0 && (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={`Changes to ${asset.name}`}>
          <table className="changes">
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Op</th>
                <th scope="col">Before</th>
                <th scope="col">After</th>
                <th scope="col">Delta</th>
              </tr>
            </thead>
            <tbody>
              {asset.changes.map((c, i) => (
                <ChangeRow key={`${c.field_path}:${i}`} c={c} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  )
}
