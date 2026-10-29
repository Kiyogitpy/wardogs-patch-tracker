import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { deleteSubscription, listBuilds, listSubscriptions, type ApiError, type Subscription } from '../api'
import { loginHref, webhookParts } from '../account'
import { Empty, ErrorBox, Loading } from '../components/State'
import { readableAsset } from '../hero'
import { useRemote, useTitle } from '../hooks'
import { useMe } from '../me'

const KIND_LABEL = { asset: 'Asset', area: 'Area', field: 'Field' } as const

const day = (iso: string) => iso.slice(0, 10)

function Help() {
  return (
    <section id="webhook-help" className="aliases" aria-labelledby="help-h">
      <h2 id="help-h" className="sub-title">
        How to create a Discord webhook
      </h2>
      <ol className="help-steps">
        <li>In Discord, open the settings of the server that should get the messages. You need permission to manage webhooks there.</li>
        <li>Go to Integrations, then Webhooks, then New Webhook.</li>
        <li>Choose the channel, then press Copy Webhook URL.</li>
        <li>Paste it into the Watch form on any asset, area or field. It looks like https://discord.com/api/webhooks/123456/token.</li>
      </ol>
      <p className="muted">
        We keep the URL to post patch messages and show only its ID and last four characters here. Anyone who has the
        full URL can post in your channel, so do not share it.
      </p>
    </section>
  )
}

function Target({ s, latest }: { s: Subscription; latest: string | null }) {
  if (s.kind === 'asset' && s.asset) {
    return (
      <Link to={`/asset?${new URLSearchParams({ path: s.asset.path })}`} className="asset-link">
        {readableAsset(s.asset.name)}
      </Link>
    )
  }
  const t = s.target ?? ''
  if (s.kind === 'field') {
    return (
      <Link to={`/changes?${new URLSearchParams({ field: t })}`} className="mono">
        {t}
      </Link>
    )
  }
  return latest ? (
    <Link to={`/builds/${encodeURIComponent(latest)}?${new URLSearchParams({ area: t })}`} className="mono">
      {t}
    </Link>
  ) : (
    <span className="mono">{t}</span>
  )
}

function Row({ s, latest, onGone }: { s: Subscription; latest: string | null; onGone: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const yes = useRef<HTMLButtonElement>(null)
  const del = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (confirm) yes.current?.focus()
  }, [confirm])

  const parts = webhookParts(s.webhook)
  const disabled = s.disabled_at !== null
  const d = s.last_delivery
  const keep = () => {
    setConfirm(false)
    del.current?.focus()
  }

  const remove = async () => {
    setBusy(true)
    setErr('')
    try {
      await deleteSubscription(s.id)
      onGone()
    } catch (e) {
      // 404 means it is already gone (or never was ours): the list is stale either way.
      if ((e as ApiError).status === 404) onGone()
      else setErr((e as ApiError).message)
      setBusy(false)
    }
  }

  return (
    <li className="asset sub">
      <span className={`asset-bar ${disabled ? 'bar-removed' : 'bar-added'}`} aria-hidden="true" />
      <header className="asset-head">
        <span className="tag">{KIND_LABEL[s.kind]}</span>
        <h3 className="sub-target">
          <Target s={s} latest={latest} />
        </h3>
        {disabled && <span className="badge badge-removed">✕ disabled</span>}
        {s.asset && <p className="path">{s.asset.path}</p>}
      </header>
      {disabled && (
        <p className="note">
          Disabled on {day(s.disabled_at!)}. Discord stopped accepting messages at this webhook (it was probably deleted
          or its channel is gone), so nothing is sent. Delete this watch and add a new one with a fresh webhook.
        </p>
      )}
      <dl className="sub-facts">
        <div>
          <dt>Webhook</dt>
          <dd className="mono">{parts ? `ID ${parts.id}, token ending ${parts.tail}` : s.webhook}</dd>
        </div>
        <div>
          <dt>Last delivery</dt>
          <dd>
            {d ? (
              <>
                <b className={`st-${d.status}`}>
                  {d.status === 'sent' ? '✓ ' : d.status === 'failed' ? '✕ ' : ''}
                  {d.status}
                </b>{' '}
                for{' '}
                <Link to={`/builds/${encodeURIComponent(d.build)}`} className="mono">
                  {d.build}
                </Link>
                , <time dateTime={d.updated_at}>{day(d.updated_at)}</time>
              </>
            ) : (
              <span className="muted">None yet. The next patch that changes this will send one.</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Added</dt>
          <dd>
            <time dateTime={s.created_at}>{day(s.created_at)}</time>
          </dd>
        </div>
      </dl>
      <div className="sub-actions">
        {confirm ? (
          <div role="group" aria-label="Confirm delete" onKeyDown={(e) => e.key === 'Escape' && keep()}>
            <span className="sub-ask">Delete this watch?</span>{' '}
            <button type="button" className="btn btn-danger" ref={yes} disabled={busy} onClick={remove}>
              {busy ? 'Deleting…' : 'Yes, delete'}
            </button>{' '}
            <button type="button" className="btn" onClick={keep}>
              Keep it
            </button>
          </div>
        ) : (
          <button type="button" className="btn" ref={del} onClick={() => setConfirm(true)}>
            Delete
          </button>
        )}
        <p role="alert" className="sub-err">
          {err && `Could not delete. ${err}`}
        </p>
      </div>
    </li>
  )
}

export default function Account() {
  useTitle('Your watches')
  const { me } = useMe()
  const { pathname, search } = useLocation()
  const signedIn = me.state === 'in'
  const subs = useRemote((s) => (signedIn ? listSubscriptions(s) : Promise.resolve(null)), `subs:${signedIn}`)
  const builds = useRemote((s) => (signedIn ? listBuilds(s) : Promise.resolve(null)), `builds:${signedIn}`)
  const [gone, setGone] = useState<Set<number>>(new Set())
  const latest = builds.state === 'ok' && builds.data ? (builds.data[0]?.label ?? null) : null

  // The #webhook-help links arrive before the section exists; scroll once it does.
  const { hash } = useLocation()
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash, me.state])

  if (me.state === 'loading') return <Loading what="your account" />
  if (me.state === 'out') {
    return (
      <>
        <h1 className="page-title">Your watches</h1>
        <p className="lede">Sign in with Discord to get a message in your channel when a new patch changes something you watch.</p>
        <p>
          <a className="btn btn-primary" href={loginHref(pathname + search)}>
            Sign in with Discord
          </a>
        </p>
        <Help />
      </>
    )
  }

  const items = subs.state === 'ok' && subs.data ? subs.data.filter((s) => !gone.has(s.id)) : []
  return (
    <>
      <h1 className="page-title">Your watches</h1>
      <p className="lede">Signed in as {me.me.username}. Each watch posts to one Discord webhook when a new patch changes what it covers.</p>
      <div aria-live="polite">{subs.state === 'loading' && <Loading what="your watches" />}</div>
      {subs.state === 'error' && <ErrorBox what="your watches" error={subs.error} onRetry={subs.retry} />}
      {subs.state === 'ok' && items.length === 0 && (
        <Empty>
          You are not watching anything yet. Open an <Link to="/search">asset</Link> and press Watch, pick an area on a
          build page, or <Link to="/changes">search a field</Link> such as BaseDamage and press Watch.
        </Empty>
      )}
      {items.length > 0 && (
        <>
          <h2 className="results-title">
            {items.length} watch{items.length === 1 ? '' : 'es'} <span className="muted">of 25</span>
          </h2>
          <ul className="assets sub-list">
            {items.map((s) => (
              <Row key={s.id} s={s} latest={latest} onGone={() => setGone((g) => new Set(g).add(s.id))} />
            ))}
          </ul>
        </>
      )}
      <Help />
    </>
  )
}
