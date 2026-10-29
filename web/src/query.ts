import { PAGE_SIZE, STATUSES, type Status } from './api'

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
