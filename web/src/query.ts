import { PAGE_SIZE, STATUSES, type Op, type Status } from './api'

export interface Filters {
  area: string
  status: Status | ''
  type: string
  q: string
  field: string
  page: number // 1-based
}

export const EMPTY_FILTERS: Filters = { area: '', status: '', type: '', q: '', field: '', page: 1 }

export function parseFilters(sp: URLSearchParams): Filters {
  const status = sp.get('status') ?? ''
  const page = Number.parseInt(sp.get('page') ?? '1', 10)
  return {
    area: sp.get('area') ?? '',
    status: (STATUSES as string[]).includes(status) ? (status as Status) : '',
    type: sp.get('type') ?? '',
    q: sp.get('q') ?? '',
    field: sp.get('field') ?? '',
    page: Number.isFinite(page) && page > 1 ? page : 1,
  }
}

/** Only non-default values, so URLs stay short and canonical. */
export function filtersToParams(f: Filters): URLSearchParams {
  const sp = new URLSearchParams()
  if (f.area) sp.set('area', f.area)
  if (f.status) sp.set('status', f.status)
  if (f.type) sp.set('type', f.type)
  if (f.q) sp.set('q', f.q)
  if (f.field) sp.set('field', f.field)
  if (f.page > 1) sp.set('page', String(f.page))
  return sp
}

/** The API query string for a filter set. */
export function apiQuery(f: Filters): string {
  const sp = filtersToParams({ ...f, page: 1 })
  sp.set('limit', String(PAGE_SIZE))
  sp.set('offset', String((f.page - 1) * PAGE_SIZE))
  return sp.toString()
}

function parsePage(sp: URLSearchParams): number {
  const page = Number.parseInt(sp.get('page') ?? '1', 10)
  return Number.isFinite(page) && page > 1 ? page : 1
}

function pageParams(sp: URLSearchParams, page: number): string {
  sp.set('limit', String(PAGE_SIZE))
  sp.set('offset', String((page - 1) * PAGE_SIZE))
  return sp.toString()
}

/* ---- /search ---- */
export interface SearchState {
  q: string
  page: number
}

export function parseSearch(sp: URLSearchParams): SearchState {
  return { q: sp.get('q') ?? '', page: parsePage(sp) }
}

export function searchToParams(s: SearchState): URLSearchParams {
  const sp = new URLSearchParams()
  if (s.q) sp.set('q', s.q)
  if (s.page > 1) sp.set('page', String(s.page))
  return sp
}

export function searchApiQuery(s: SearchState): string {
  const sp = new URLSearchParams()
  if (s.q) sp.set('q', s.q)
  return pageParams(sp, s.page)
}

/* ---- /asset ---- */
export type AssetView = 'field' | 'build'

export interface AssetState {
  path: string
  field: string
  view: AssetView
}

export function parseAsset(sp: URLSearchParams): AssetState {
  return {
    path: sp.get('path') ?? '',
    field: sp.get('field') ?? '',
    view: sp.get('view') === 'build' ? 'build' : 'field',
  }
}

export function assetToParams(s: AssetState): URLSearchParams {
  const sp = new URLSearchParams()
  sp.set('path', s.path)
  if (s.field) sp.set('field', s.field)
  if (s.view === 'build') sp.set('view', 'build')
  return sp
}

/** The API query: path and field only; the view is a client concern. */
export function assetApiQuery(s: AssetState): string {
  const sp = new URLSearchParams({ path: s.path })
  if (s.field) sp.set('field', s.field)
  return sp.toString()
}

/* ---- /changes ---- */
export const OPS: Op[] = ['added', 'removed', 'changed', 'resequenced']
export const MIN_FIELD_CHARS = 2

export interface ChangesState {
  field: string
  build: string
  area: string
  op: Op | ''
  page: number
}

export const EMPTY_CHANGES: ChangesState = { field: '', build: '', area: '', op: '', page: 1 }

export function parseChanges(sp: URLSearchParams): ChangesState {
  const op = sp.get('op') ?? ''
  return {
    field: sp.get('field') ?? '',
    build: sp.get('build') ?? '',
    area: sp.get('area') ?? '',
    op: (OPS as string[]).includes(op) ? (op as Op) : '',
    page: parsePage(sp),
  }
}

export function changesToParams(s: ChangesState): URLSearchParams {
  const sp = new URLSearchParams()
  if (s.field) sp.set('field', s.field)
  if (s.build) sp.set('build', s.build)
  if (s.area) sp.set('area', s.area)
  if (s.op) sp.set('op', s.op)
  if (s.page > 1) sp.set('page', String(s.page))
  return sp
}

export function changesApiQuery(s: ChangesState): string {
  const sp = changesToParams({ ...s, page: 1 })
  return pageParams(sp, s.page)
}
