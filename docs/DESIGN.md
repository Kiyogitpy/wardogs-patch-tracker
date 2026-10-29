# Design: armory ledger

The site answers one question for Wardogs players and data-miners: what got buffed or nerfed, and by how much.
It borrows from ordnance technical manuals and ammunition crate markings. It is not the docs site and does not
look like it. Precise and loud, never costume-military: no stencil fonts, camouflage, dog tags, bullet holes or
textures.

## Tokens

Dark ("gunmetal") is the default theme; light ("field steel") applies under `prefers-color-scheme: light`.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#25292C` | `#E4E6E1` | page |
| `--panel` | `#2E3337` | `#F1F2EE` | tables, filter bar |
| `--ink` | `#E3E6E0` | `#1B2024` | text |
| `--ink-2` | `#A7ADA6` | `#4E555B` | secondary text (≥ 4.5:1 on `--bg` and `--panel`) |
| `--olive` | `#9AA36A` | `#4B5320` | brand, links, added, increases |
| `--yellow` | `#E3B200` | `#E3B200` | **change markings only**: delta tags, hazard stripes, hero roll |
| `--red` | `#F08A80` | `#A8261F` | removed, decreases |
| `--blue` | `#8FB0E8` | `#2D5AA0` | moved |

Yellow never appears on chrome (header, buttons, nav, focus). Focus is a 3px `--ink` outline with a 2px offset.

## Type

One family, two widths, loaded from Google Fonts with `display=swap`:
- **IBM Plex Sans Condensed** (400, 600, 700) for the interface and headings.
- **IBM Plex Mono** (400, 600) only for values, field paths and asset paths. Never for labels.

Scale: 14 / 16 / 20 / 28 / 44 px, plus the hero display size `clamp(96px, 22vw, 280px)` at weight 700 with
`letter-spacing: -0.04em`. Labels are sentence case. No all-caps eyebrows, no `→` appended to link text, no
middle-dot meta strings.

## Signature elements

1. **The hero (home page).** The latest build's single biggest relative `BaseDamage` change, set at display size
   across the page: `60 ▶ 45`. Before is weight 400 in `--ink-2`; after is 700 in `--ink`. The asset's
   player-readable name sits above it at 28px, and the delta tag beside or under it. Below: the next biggest damage
   changes as patch-note lines, and a link "All damage changes" to `/changes?field=Damage&build=<latest>`. Data comes
   from `/api/changes?field=BaseDamage&build=<latest>&limit=200`, sorted client-side by absolute percent change. If
   there are no numeric damage changes, the hero falls back to the build's label and totals in the same display type.
2. **The odometer.** On first load only, the hero's after-number rolls digit by digit from the before value to the
   after value (about 900 ms, one time, ease-out). With `prefers-reduced-motion: reduce` it does not move. This is
   the only non-interactive motion on the site.
3. **Delta tag.** An inline tag with a notched left edge (CSS `clip-path`), `--yellow` background, `#1B2024` text,
   Plex Mono 600: `−15 · −25%`. Used for every numeric change on every page.
4. **Hazard stripe.** A 6px band of 45° `--yellow`/`#1B2024` stripes on the left edge of any row or box holding a
   *changed* value. Added, removed and moved rows use solid `--olive`, `--red` or `--blue` bars plus their glyph and
   word, never color alone.
5. **Crate marking.** On the timeline and the diff view, the build label runs vertically (`writing-mode:
   vertical-rl`, rotated to read bottom to top) in 44px+ condensed bold down a left rail. On phones it turns
   horizontal above the content.

## Layout

Left-aligned and table-first. Assets are sections divided by space and their status bar, not identical boxed cards.
Corners: 0 on data, 3px on inputs and buttons. No drop shadows, no gradients except the hazard stripe. Text lines stay
under 80 characters; tables scroll inside their own container. Everything works at 375px with no horizontal page
scroll.

## Copy

Plain, active, specific: "All damage changes", "Search assets", "No assets match these filters. Clear filters".
Errors say what failed and offer Retry. Use the player-readable asset name where one is obvious (strip `DA_`,
`_ProjectileData` and similar prefixes and suffixes for display only; the full name stays in the path line).
