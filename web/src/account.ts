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

// sessionStorage only: the URL is a secret, so it never goes to localStorage. It is stored with the id of
// the user who entered it and is read back only for that user.
export function loadWebhook(userId: string): string {
  try {
    const stored = JSON.parse(sessionStorage.getItem(KEY) ?? 'null')
    return stored?.user === userId && typeof stored.url === 'string' ? stored.url : ''
  } catch {
    return ''
  }
}

export function saveWebhook(userId: string, url: string) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ user: userId, url }))
  } catch {
    /* storage blocked */
  }
}

export function clearWebhook() {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* storage blocked */
  }
}

/** Forget the remembered URL unless it belongs to `userId` (null: nobody is signed in). */
export function forgetWebhookUnlessOwnedBy(userId: string | null) {
  try {
    const stored = JSON.parse(sessionStorage.getItem(KEY) ?? 'null')
    if (stored !== null && (userId === null || stored?.user !== userId)) clearWebhook()
  } catch {
    clearWebhook()
  }
}
