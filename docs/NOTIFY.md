# Delivering subscriptions

After a build is ingested, `tracker notify --label <label>` sends one Discord webhook message to every active
subscription that the build matches. The update pipeline runs it right after `tracker ingest`.

```
tracker notify --label s3-26000000 [--dry-run]
```

Needs `DATABASE_URL` and `PUBLIC_ORIGIN` (for links). `--dry-run` prints what would be sent and writes nothing.
Exit code 0 when every due message was sent, 1 when any failed (the rest are still sent), 2 for usage errors
(unknown or not ingested label).

## Matching

A subscription is active when `disabled_at` is null. For build `B`:

| Kind | Matches when | Message lists |
|---|---|---|
| `asset` | `asset_status` has a row for (`B`, `asset_id`) | the asset's status and its first 10 changes |
| `area` | any `asset_status` row of `B` has `area = target` | the area's counts from `build_area`, and the first 10 changed assets by the diff order |
| `field` | any `change` of `B` has `field_path` containing `target`, case-insensitive (wildcards literal) | the match count and the first 10 matching changes by the `/api/changes` order |

No match: no message and no delivery row.

## Message

One webhook `POST` per subscription, JSON body:

```json
{
  "username": "Wardogs Patch Tracker",
  "allowed_mentions": {"parse": []},
  "embeds": [{
    "title": "308 Winchester changed in s3-26000000",
    "url": "<PUBLIC_ORIGIN>/asset?path=...",
    "description": "Base damage  45 ▶ 50  (+5, +11%)\n...",
    "footer": {"text": "Watching: asset DA_308Winchester_ProjectileData"}
  }]
}
```

- `allowed_mentions: {"parse": []}` is mandatory: values come from game data, and `@everyone` in a string must not ping.
- Game text is hostile and never rendered as Markdown. Field paths and non-numeric values go in a code span (backticks
  inside them are replaced with `ˋ`, so the span cannot be closed). Names, area titles and targets in titles and bold
  prefixes are backslash-escaped (\  *  _  ~  |  >  [  ]  (  )  #  <  `, a leading `-`, `+` or `1.`), newlines become spaces, and
  `://` is written `:\/\/` so no bare URL is clickable.
- Discord limits: title ≤ 256 characters, description ≤ 4096, footer ≤ 2048, `url` ≤ 2048, and title + description +
  footer ≤ 6000 (the description shrinks to fit). Truncate with `…` and end with "and N more" plus the link. Values
  are shortened to 80 characters, field paths to 200, names to 100 (before escaping, so a cut never splits an
  escape). If the link would exceed 2048 characters (a very long asset path) it points at `/builds/<B>` instead.
- Numbers at or beyond 1e300 in magnitude, and any pair whose difference cannot be computed, are shown as plain
  values without a delta.
- Links: asset → `/asset?path=`, area → `/builds/<B>?area=<id>`, field → `/changes?field=<target>&build=<B>`, all
  URL-encoded.

## Delivery state

Each subscription is built, checked and sent in its own `try`: an unexpected error is logged (subscription id and
exception class only), recorded as `failed` with `last_error = "internal error: <ExceptionClass>"`, and the run
continues with the others. Before sending, the stored URL is checked again against the same pattern as sign-up
(`tracker/webhooks.py`); a stored value that fails is not called, is recorded `failed` with `last_error = "invalid
webhook"` (0 attempts) and gets `disabled_at`.

`delivery` has one row per (subscription, build), written after each attempt:

- 2xx → `status = sent`.
- 429 → wait `retry_after` (from the JSON body, capped at 30 s) and retry, up to 3 attempts in total; then `failed`.
- 5xx or a network error/timeout (10 s) → retry up to 3 attempts with 1 s, 4 s backoff; then `failed`.
- 401, 403 or 404 → the webhook is gone: `failed`, and set `subscription.disabled_at = now()`. No retry.
- Other 4xx → `failed`, no retry.

`attempts` counts tries across runs, `last_error` holds the status code and at most 300 characters of the response
(never the webhook URL). A re-run sends only subscriptions with no `sent` row for that build, so running notify twice
never double-posts. Log lines name the subscription id, never the webhook URL.
