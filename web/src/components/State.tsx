import { Link } from 'react-router'
import type { ApiError } from '../api'
import { useTitle } from '../hooks'

export function Loading({ what }: { what: string }) {
  return (
    <p className="state" role="status">
      <span className="spinner" aria-hidden="true" /> Loading {what}&hellip;
    </p>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="state state-empty">{children}</p>
}

export function ErrorBox({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Something went wrong.</strong> {error.message}
      {error.status ? ` (HTTP ${error.status})` : ''}
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn">
          Retry
        </button>
      )}
    </div>
  )
}

export function NotFound({ what = 'page' }: { what?: string }) {
  useTitle('Not found')
  return (
    <div className="state state-404">
      <p className="big404" aria-hidden="true">
        404
      </p>
      <h1>That {what} does not exist</h1>
      <p>
        <Link to="/">Back to the build timeline</Link>
      </p>
    </div>
  )
}
