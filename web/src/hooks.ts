import { useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

export type Remote<T> =
  | { state: 'loading' }
  | { state: 'ok'; data: T }
  | { state: 'error'; error: ApiError; retry: () => void }

/** Fetch whenever `key` changes; stale responses are aborted. */
export function useRemote<T>(load: (signal: AbortSignal) => Promise<T>, key: string): Remote<T> {
  const [result, setResult] = useState<{ key: string; value: Remote<T> } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const retry = () => {
    setResult(null)
    setAttempt((n) => n + 1)
  }
  useEffect(() => {
    const ctl = new AbortController()
    load(ctl.signal).then(
      (data) => setResult({ key, value: { state: 'ok', data } }),
      (e) => {
        if (ctl.signal.aborted) return
        const error = e instanceof ApiError ? e : new ApiError(0, String(e))
        setResult({ key, value: { state: 'error', error, retry } })
      },
    )
    return () => ctl.abort()
    // `load` is recreated each render; `key` identifies the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt])
  return result && result.key === key ? result.value : { state: 'loading' }
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

export function useTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · Wardogs Patch Tracker` : 'Wardogs Patch Tracker'
  }, [title])
}

/** A text input whose debounced value is written to the URL; follows external URL changes (back button). */
export function useUrlText(urlValue: string, commit: (v: string) => void) {
  const [text, setText] = useState(urlValue)
  const debounced = useDebounced(text)
  const lastCommitted = useRef(urlValue)
  useEffect(() => {
    if (debounced !== lastCommitted.current) {
      lastCommitted.current = debounced
      commit(debounced)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])
  useEffect(() => {
    if (urlValue !== lastCommitted.current) {
      lastCommitted.current = urlValue
      setText(urlValue)
    }
  }, [urlValue])
  return [text, setText] as const
}
