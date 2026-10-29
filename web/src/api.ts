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
  exports: number | null
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

/** The API's `detail` as an ApiError; a 422 validation list is joined into one sentence. */
async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (Array.isArray(body?.detail)) {
      detail = body.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join('; ') || detail
    }
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, detail || `HTTP ${res.status}`)
}

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, { signal, headers: { Accept: 'application/json' } })
  } catch (e) {
    if (signal?.aborted) throw e
    throw new ApiError(0, 'Could not reach the API.')
  }
  if (!res.ok) throw await errorFrom(res)
  return (await res.json()) as T
}

export const listBuilds = (s?: AbortSignal) => getJson<BuildSummary[]>('/api/builds', s)
export const getBuild = (label: string, s?: AbortSignal) =>
  getJson<BuildDetail>(`/api/builds/${encodeURIComponent(label)}`, s)
export const getAssets = (label: string, query: string, s?: AbortSignal) =>
  getJson<AssetPage>(`/api/builds/${encodeURIComponent(label)}/assets?${query}`, s)

export interface AssetHit {
  path: string
  name: string
  type: string
  builds: number
  last: { label: string; status: Status; area: string }
}

export interface AssetSearchPage {
  total: number
  items: AssetHit[]
}

export interface HistoryEntry {
  build: { label: string; prev_label: string | null; released_at: string | null }
  area: string
  status: Status
  paired_by: string | null
  exports: number | null
  note: string | null
  noise: Noise
  changes: Change[]
}

export interface AssetHistory {
  path: string
  name: string
  type: string
  aliases: { old_path: string; build: string }[]
  history: HistoryEntry[]
}

export interface ChangeHit {
  build: string
  released_at: string | null
  asset: { path: string; name: string; type: string }
  area: string
  status: Status
  field_path: string
  op: Op
  before: Json
  after: Json
}

export interface ChangesPage {
  total: number
  items: ChangeHit[]
}

export const searchAssets = (query: string, s?: AbortSignal) => getJson<AssetSearchPage>(`/api/assets?${query}`, s)
export const getAssetHistory = (query: string, s?: AbortSignal) => getJson<AssetHistory>(`/api/asset?${query}`, s)
export const getChanges = (query: string, s?: AbortSignal) => getJson<ChangesPage>(`/api/changes?${query}`, s)

export const PAGE_SIZE = 50

/* ---- accounts and subscriptions (step 4) ---- */

export interface Me {
  discord_id: string
  username: string
  avatar_url: string | null
}

export type SubKind = 'asset' | 'area' | 'field'

export interface Subscription {
  id: number
  kind: SubKind
  target: string | null
  asset: { path: string; name: string } | null
  webhook: string // masked by the server
  created_at: string
  disabled_at: string | null
  last_delivery: { build: string; status: string; updated_at: string } | null
}

export type NewSubscription =
  | { kind: 'asset'; asset_path: string; webhook_url: string }
  | { kind: 'area' | 'field'; target: string; webhook_url: string }

/** 401 means signed out, not an error. */
export async function getMe(signal?: AbortSignal): Promise<Me | null> {
  try {
    return await getJson<Me>('/api/me', signal)
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null
    throw e
  }
}

async function send(method: 'POST' | 'DELETE', url: string, body?: unknown): Promise<Response> {
  let res: Response
  try {
    res = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Could not reach the API.')
  }
  if (!res.ok) throw await errorFrom(res)
  return res
}

export const logout = async () => void (await send('POST', '/api/auth/logout'))
export const listSubscriptions = (s?: AbortSignal) => getJson<Subscription[]>('/api/subscriptions', s)
export const createSubscription = async (b: NewSubscription) =>
  (await (await send('POST', '/api/subscriptions', b)).json()) as Subscription
export const deleteSubscription = async (id: number) => void (await send('DELETE', `/api/subscriptions/${id}`))
