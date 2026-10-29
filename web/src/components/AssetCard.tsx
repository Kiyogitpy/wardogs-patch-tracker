import { Link } from 'react-router'
import type { AssetItem, Change } from '../api'
import { readableAsset } from '../hero'
import BeforeAfter from './BeforeAfter'
import { StatusBadge } from './Stats'

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

/** Break a field path after each dot or bracket so long paths wrap cleanly. */
export function FieldPath({ path }: { path: string }) {
  return (
    <>
      {path.split(/(?<=[.[])/).map((part, i) => (
        <span key={i}>
          {part}
          <wbr />
        </span>
      ))}
    </>
  )
}

export function ChangesTable({ name, changes }: { name: string; changes: Change[] }) {
  if (changes.length === 0) return null
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={`Changes to ${name}`}>
      <table className="rows stack">
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Before to after</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c, i) => (
            <tr key={`${c.field_path}:${i}`}>
              <th scope="row" className="field">
                <FieldPath path={c.field_path} />
              </th>
              <td className="c-change">
                <BeforeAfter c={c} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function statusBar(status: AssetItem['status']): string {
  return status === 'modified' ? 'hazard' : `bar-${status}`
}

export default function AssetCard({ asset }: { asset: AssetItem }) {
  const noiseTotal = Object.values(asset.noise).reduce((a, b) => a + b, 0)
  const noiseOnly = asset.status === 'modified' && asset.changes.length === 0 && noiseTotal > 0
  return (
    <article className="asset" aria-label={asset.name}>
      <span className={`asset-bar ${statusBar(asset.status)}`} aria-hidden="true" />
      <header className="asset-head">
        <h3>
          <Link to={`/asset?${new URLSearchParams({ path: asset.path })}`}>{readableAsset(asset.name)}</Link>
        </h3>
        <StatusBadge status={asset.status} />
        <span className="tag">{asset.type}</span>
        {noiseOnly && (
          <span className="tag" title={noiseSummary(asset.noise)}>
            noise only
          </span>
        )}
      </header>
      <p className="path">{asset.path}</p>
      {asset.old_path && (
        <p className="path old">
          <span className="path-label">Moved from</span> {asset.old_path}
        </p>
      )}
      {asset.note && <p className="note">{asset.note}</p>}
      {noiseOnly && (
        <p className="note">
          Only serialization noise changed here: {noiseSummary(asset.noise)}. No game values differ.
        </p>
      )}
      <ChangesTable name={asset.name} changes={asset.changes} />
    </article>
  )
}
