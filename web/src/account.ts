// Pure helpers for sign-in and watching. The server stays authoritative on all of it.

export const WEBHOOK_RE =
  /^https:\/\/(discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)\/api\/webhooks\/\d{1,20}\/[A-Za-z0-9_-]{1,100}$/

export const isWebhookUrl = (s: string) => WEBHOOK_RE.test(s.trim())

/** `next` must start with one slash and not `//` or `/\`; anything else becomes `/`. */
export function safeNext(next: string): string {
  return next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\') ? next : '/'
}

/** Sign-in is a full-page navigation, not a fetch. */
export const loginHref = (next: string) => `/api/auth/login?next=${encodeURIComponent(safeNext(next))}`

/** The server returns `https://discord.com/api/webhooks/<id>/…<last 4>`; split it for display. */
export function webhookParts(masked: string): { id: string; tail: string } | null {
  const m = /\/webhooks\/(\d+)\/…(.+)$/.exec(masked)
  return m ? { id: m[1], tail: m[2] } : null
}

const KEY = 'watch-webhook'

// sessionStorage only: the URL is a secret, so it never goes to localStorage.
export function loadWebhook(): string {
  try {
    return sessionStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

export function saveWebhook(url: string) {
  try {
    sessionStorage.setItem(KEY, url)
  } catch {
    /* storage blocked */
  }
}
