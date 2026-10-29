import { useEffect, useId, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { createSubscription, type ApiError, type NewSubscription } from '../api'
import { isWebhookUrl, loadWebhook, loginHref, saveWebhook } from '../account'
import { useMe } from '../me'

type Target = { kind: 'asset'; asset_path: string } | { kind: 'area' | 'field'; target: string }

/** A "Watch" button that opens an inline form for the Discord webhook URL. `what` is the plain-words subject. */
export default function Watch({ target, what }: { target: Target; what: string }) {
  const { me } = useMe()
  const { pathname, search } = useLocation()
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const id = useId()

  // A different thing to watch (another area or field) starts from a closed form.
  const key = JSON.stringify(target)
  useEffect(() => {
    setOpen(false)
    setMsg(null)
  }, [key])

  if (me.state === 'loading') {
    return (
      <button type="button" className="btn" disabled>
        Watch
      </button>
    )
  }
  if (me.state === 'out') {
    return (
      <a className="btn" href={loginHref(pathname + search)}>
        Watch
      </a>
    )
  }

  const close = () => {
    setOpen(false)
    btn.current?.focus()
  }
  const openForm = () => {
    setUrl(loadWebhook())
    setMsg(null)
    setOpen(true)
    // The input mounts on the next render.
    setTimeout(() => input.current?.focus(), 0)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const webhook_url = url.trim()
    if (!isWebhookUrl(webhook_url)) {
      setMsg({
        ok: false,
        text: 'That is not a Discord webhook URL. It looks like https://discord.com/api/webhooks/123456/token.',
      })
      input.current?.focus()
      return
    }
    setBusy(true)
    setMsg(null)
    try {
      await createSubscription({ ...target, webhook_url } as NewSubscription)
      saveWebhook(webhook_url)
      setMsg({ ok: true, text: `Watching ${what}. Discord will get a message when a new patch changes it.` })
      setOpen(false)
    } catch (err) {
      setMsg({ ok: false, text: (err as ApiError).message })
      input.current?.focus()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="watch">
      <button
        type="button"
        className="btn"
        ref={btn}
        aria-expanded={open}
        aria-controls={`${id}-form`}
        onClick={() => (open ? close() : openForm())}
      >
        Watch
      </button>
      {open && (
        <form
          id={`${id}-form`}
          className="filters watch-form"
          aria-label={`Watch ${what}`}
          onSubmit={submit}
          onKeyDown={(e) => e.key === 'Escape' && close()}
        >
          <label>
            <span>Discord webhook URL for {what}</span>
            <input
              ref={input}
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://discord.com/api/webhooks/…"
              autoComplete="off"
              spellCheck={false}
              aria-invalid={msg && !msg.ok ? true : undefined}
              aria-describedby={`${id}-hint`}
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : 'Start watching'}
          </button>
          <button type="button" className="btn" onClick={close}>
            Cancel
          </button>
          <p id={`${id}-hint`} className="watch-hint">
            Need one? <Link to="/account#webhook-help">How to create a webhook</Link>. Keep it private: anyone with it can
            post in your channel.
          </p>
        </form>
      )}
      <p className={`watch-msg${msg ? (msg.ok ? ' ok' : ' bad') : ''}`} role="status" aria-live="polite">
        {msg && (
          <>
            <span className="msg-glyph" aria-hidden="true">
              {msg.ok ? '✓' : '!'}
            </span>
            <span>
              {msg.ok ? '' : 'Could not watch. '}
              {msg.text}{' '}
              {msg.ok && <Link to="/account">Manage your watches</Link>}
            </span>
          </>
        )}
      </p>
    </div>
  )
}
