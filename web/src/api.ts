// Typed mirror of docs/API.md. Keep in sync with the contract.

export interface Counts {
  added: number
  removed: number
  modified: number
  moved: number
  property_changes: number
  removed_values: number
}

export interface BuildSummary {
  label: string
  prev_label: string | null
  steam_buildid: string | null
  released_at: string | null // YYYY-MM-DD
  ingested_at: string
  totals: Counts
}

export interface Area {
  id: string
  title: string
  counts: Counts
}

export interface TypeCount {
  type: string
  assets: number
}

export interface BuildDetail extends BuildSummary {
  areas: Area[]
  types: TypeCount[]
}

export type Status = 'added' | 'removed' | 'modified' | 'moved'
export const STATUSES: Status[] = ['modified', 'moved', 'added', 'removed']

export type Op = 'added' | 'removed' | 'changed' | 'resequenced'

export type Json = number | string | boolean | null | Json[] | { [k: string]: Json }

export interface Change {
  field_path: string
  op: Op
  before: Json
  after: Json
}

export interface Noise {
  renamed_keys: number
  ref_shifts: number
  dup_keys: number
  swapped_keys: number
  type_reinterpret: number
}

export interface AssetItem {
  path: string
  name: string
  type: string
  area: string
  status: Status
  old_path: string | null
  paired_by: string | null
  exports: unknown
  note: string | null
  noise: Noise
  changes: Change[]
}

export interface AssetPage {
  total: number
  items: AssetItem[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, { signal, headers: { Accept: 'application/json' } })
  } catch (e) {
    if (signal?.aborted) throw e
    throw new ApiError(0, 'Could not reach the API.')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export const listBuilds = (s?: AbortSignal) => getJson<BuildSummary[]>('/api/builds', s)
export const getBuild = (label: string, s?: AbortSignal) =>
  getJson<BuildDetail>(`/api/builds/${encodeURIComponent(label)}`, s)
export const getAssets = (label: string, query: string, s?: AbortSignal) =>
  getJson<AssetPage>(`/api/builds/${encodeURIComponent(label)}/assets?${query}`, s)

export const PAGE_SIZE = 50
