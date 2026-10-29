import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getMe, createSubscription, ApiError } from './api'
import {
  clearWebhook,
  forgetWebhookUnlessOwnedBy,
  isWebhookUrl,
  loadWebhook,
  loginHref,
  safeNext,
  saveWebhook,
  webhookParts,
} from './account'

describe('webhook regex', () => {
  it.each(['discord.com', 'discordapp.com', 'ptb.discord.com', 'canary.discord.com'])('accepts %s', (h) => {
    expect(isWebhookUrl(`https://${h}/api/webhooks/123456789/aB-_9x`)).toBe(true)
  })
  it.each([
    ['http', 'http://discord.com/api/webhooks/1/a'],
    ['other host', 'https://evil.com/api/webhooks/1/a'],
    ['lookalike host', 'https://discord.com.evil.com/api/webhooks/1/a'],
    ['extra segment', 'https://discord.com/api/webhooks/1/a/b'],
    ['query string', 'https://discord.com/api/webhooks/1/a?wait=true'],
    ['userinfo', 'https://user@discord.com/api/webhooks/1/a'],
    ['userinfo before host', 'https://discord.com@evil.com/api/webhooks/1/a'],
    ['non-numeric id', 'https://discord.com/api/webhooks/x/a'],
    ['21-digit id', `https://discord.com/api/webhooks/${'1'.repeat(21)}/a`],
    ['empty token', 'https://discord.com/api/webhooks/1/'],
    ['fragment', 'https://discord.com/api/webhooks/1/a#x'],
  ])('rejects %s', (_n, u) => expect(isWebhookUrl(u)).toBe(false))
  it('ignores surrounding whitespace from pasting', () => {
    expect(isWebhookUrl('  https://discord.com/api/webhooks/1/a\n')).toBe(true)
  })
})

describe('masked webhook display', () => {
  it('splits the id and the token tail', () => {
    expect(webhookParts('https://discord.com/api/webhooks/1234567890/…k3Zq')).toEqual({ id: '1234567890', tail: 'k3Zq' })
  })
  it('returns null for an unexpected shape', () => {
    expect(webhookParts('whatever')).toBeNull()
  })
})

describe('sign-in next', () => {
  it('encodes the path and query', () => {
    expect(loginHref('/asset?path=A%2FB&view=build')).toBe('/api/auth/login?next=%2Fasset%3Fpath%3DA%252FB%26view%3Dbuild')
  })
  it.each(['//evil.com', '/\\evil.com', 'https://evil.com', 'asset', ''])('falls back to / for %j', (n) => {
    expect(safeNext(n)).toBe('/')
  })
  it('keeps a plain path', () => expect(safeNext('/account')).toBe('/account'))
})

describe('api client', () => {
  afterEach(() => vi.unstubAllGlobals())
  const reply = (status: number, body?: unknown) =>
    new Response(body === undefined ? null : JSON.stringify(body), { status, statusText: 'x' })

  it('treats 401 on /api/me as signed out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(401, { detail: 'not signed in' })))
    expect(await getMe()).toBeNull()
  })
  it('still throws other /api/me failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(500, { detail: 'boom' })))
    await expect(getMe()).rejects.toMatchObject({ status: 500, message: 'boom' })
  })
  it('POSTs JSON with same-origin credentials and surfaces the detail', async () => {
    const f = vi.fn(async () => reply(409, { detail: 'already watching this' }))
    vi.stubGlobal('fetch', f)
    const err = await createSubscription({ kind: 'field', target: 'BaseDamage', webhook_url: 'u' }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, message: 'already watching this' })
    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('same-origin')
    expect(JSON.parse(init.body as string)).toEqual({ kind: 'field', target: 'BaseDamage', webhook_url: 'u' })
  })
  it('joins a 422 validation list into one message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(422, { detail: [{ msg: 'a' }, { msg: 'b' }] })))
    await expect(createSubscription({ kind: 'area', target: 'x', webhook_url: 'u' })).rejects.toMatchObject({ message: 'a; b' })
  })
})

describe('remembered webhook', () => {
  const URL1 = 'https://discord.com/api/webhooks/1/aaa'
  const store = new Map<string, string>()
  beforeEach(() => {
    store.clear()
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('is returned to the user who saved it only', () => {
    saveWebhook('111', URL1)
    expect(loadWebhook('111')).toBe(URL1)
    expect(loadWebhook('222')).toBe('')
  })
  it('is cleared on sign-out', () => {
    saveWebhook('111', URL1)
    clearWebhook()
    expect(store.size).toBe(0)
  })
  it('is cleared when /api/me returns another user or none, kept for the same user', () => {
    saveWebhook('111', URL1)
    forgetWebhookUnlessOwnedBy('111')
    expect(loadWebhook('111')).toBe(URL1)
    forgetWebhookUnlessOwnedBy('222')
    expect(store.size).toBe(0)
    saveWebhook('111', URL1)
    forgetWebhookUnlessOwnedBy(null)
    expect(store.size).toBe(0)
  })
  it('drops an old untagged value', () => {
    store.set('watch-webhook', URL1)
    expect(loadWebhook('111')).toBe('')
    forgetWebhookUnlessOwnedBy('111')
    expect(store.size).toBe(0)
  })
})
