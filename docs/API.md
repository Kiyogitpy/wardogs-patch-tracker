# API contract

JSON over HTTP, all routes under `/api`. Read-only and public. The web app calls these through the Vite dev proxy
(`/api` → `http://127.0.0.1:8000`), and in production through the same origin, so no CORS.

Only builds with an ingested diff (`ingested_at` not null) are listed or served. A base build that exists only as
another build's `--from` returns 404.

Dates are ISO 8601 strings (`released_at`: `YYYY-MM-DD`; `ingested_at`: full timestamp with offset). `before`/`after`
are the stored JSON values: number, string, boolean, null, array or object. They come from Postgres `jsonb`, which
keeps numbers exactly (`60.0` stays `60.0`) but drops the sign of zero (`-0.0` → `0.0`) and reorders object keys
(shorter keys first, then by bytes).

## `GET /api/builds`

Ingested builds, newest first: `released_at` descending (nulls last), then `id` descending.

```json
[
  {
    "label": "s2-cl516586",
    "prev_label": "season1",
    "steam_buildid": "25937608",
    "released_at": "2026-10-15",
    "ingested_at": "2026-10-29T14:02:11.123456+00:00",
    "totals": {"added": 600, "removed": 26, "modified": 2102, "moved": 12, "property_changes": 20704, "removed_values": 1272}
  }
]
```

`totals` is the sum of the build's `build_area.counts`. `prev_label`, `steam_buildid` and `released_at` may be null.

## `GET /api/builds/{label}`

One build plus what the diff view needs to build its filters. 404 `{"detail": "build not found"}` if missing.

```json
{
  "label": "s2-cl516586",
  "prev_label": "season1",
  "steam_buildid": "25937608",
  "released_at": "2026-10-15",
  "ingested_at": "2026-10-29T14:02:11.123456+00:00",
  "totals": {"added": 600, "removed": 26, "modified": 2102, "moved": 12, "property_changes": 20704, "removed_values": 1272},
  "areas": [
    {"id": "weapons-ammo", "title": "Weapons & Ammo",
     "counts": {"added": 162, "removed": 16, "modified": 425, "moved": 11, "property_changes": 1208, "removed_values": 989}}
  ],
  "types": [{"type": "WeaponData", "assets": 120}]
}
```

`areas` keeps the order of `index.json` (`build_area.position`). `types` is every asset type in the build with its asset count, by count
descending, then type ascending by code point.

## `GET /api/builds/{label}/assets`

The assets of one build's diff with their changes, paginated by asset. 404 as above.

| Query | Meaning |
|---|---|
| `area` | area id, exact |
| `status` | `added`, `removed`, `modified` or `moved`; 422 otherwise |
| `type` | asset type, exact |
| `q` | case-insensitive substring of the asset name or path |
| `field` | case-insensitive substring of a change's `field_path`. Only assets with at least one matching change are returned, and `changes` holds only the matching ones |
| `limit` | 1–200, default 50; 422 outside the range |
| `offset` | 0–1,000,000, default 0; 422 outside the range |

`area`, `type`, `q` and `field` are at most 200 characters and may not contain NUL; 422 otherwise.

Order: status (`modified`, `moved`, `added`, `removed`, as `season_diff.py`), then lower-case name, then path, both
compared by code point (`_` sorts before letters).
Changes inside an asset keep ingest order (`change.id`).

```json
{
  "total": 425,
  "items": [
    {
      "path": "Wardogs/Content/Weapons/DA_308Winchester_ProjectileData.json",
      "name": "DA_308Winchester_ProjectileData",
      "type": "BHProjectileData",
      "area": "weapons-ammo",
      "status": "modified",
      "old_path": null,
      "paired_by": null,
      "exports": null,
      "note": null,
      "noise": {"renamed_keys": 0, "ref_shifts": 0, "dup_keys": 0, "swapped_keys": 0, "type_reinterpret": 0},
      "changes": [
        {"field_path": "Properties.ProjectileStats.DamageValues.BaseDamage", "op": "changed", "before": 60.0, "after": 45.0}
      ]
    }
  ]
}
```

`op` is one of `added`, `removed`, `changed` or `resequenced`. For `added`, `before` is null; for `removed`, `after`
is null. `exports` is an integer (the number of exports in an added asset) or null.

`total` counts the assets matching every filter, ignoring `limit`/`offset`. `old_path` is the `asset_alias.old_path`
recorded for this asset in this build, else null.

## Build order

"Newest first" is the order of `GET /api/builds`: `released_at` descending (nulls last), then `id` descending.
"Chronological" is exactly the reverse.

## `GET /api/assets`

Search assets across every ingested build. Text parameters follow the rules above (at most 200 characters, no NUL).

| Query | Meaning |
|---|---|
| `q` | case-insensitive substring of the name, the current path or any former path (`asset_alias.old_path`). Empty or absent lists every asset |
| `limit` | 1–200, default 50 |
| `offset` | 0–1,000,000, default 0 |

Order: lower-case name, then current path, both by code point.

```json
{
  "total": 3,
  "items": [
    {
      "path": "Wardogs/Plugins/GameFeatures/Weapons/Platforms/WEPN_017/Content/Data/AnimSet_Mosin.json",
      "name": "AnimSet_Mosin",
      "type": "BHAnimationSet",
      "builds": 1,
      "last": {"label": "s2-cl516586", "status": "moved", "area": "weapons-ammo"}
    }
  ]
}
```

`builds` is the number of ingested builds whose diff lists the asset; `last` is the newest of them. An asset that is
in no ingested diff is not listed.

## `GET /api/asset`

One asset and its whole history. 404 `{"detail": "asset not found"}`.

| Query | Meaning |
|---|---|
| `path` | required, at most 1,000 characters, no NUL. The current path, or a former path: then the asset it moved to is returned, and `path` in the response is the current one. A current path wins over a former path. An asset in no ingested diff is 404 |
| `field` | optional; case-insensitive substring of `field_path`. Narrows every `changes` list; builds stay listed even when their list becomes empty |

```json
{
  "path": "Wardogs/Plugins/GameFeatures/Weapons/Platforms/WEPN_017/Content/Data/AnimSet_Mosin.json",
  "name": "AnimSet_Mosin",
  "type": "BHAnimationSet",
  "aliases": [{"old_path": "Wardogs/Plugins/GameFeatures/Weapons/Sniper/Mosin/Content/Data/AnimSet_Mosin.json", "build": "s2-cl516586"}],
  "history": [
    {
      "build": {"label": "s2-cl516586", "prev_label": "season1", "released_at": "2026-10-15"},
      "area": "weapons-ammo",
      "status": "moved",
      "paired_by": "same-name",
      "exports": null,
      "note": null,
      "noise": {"renamed_keys": 0, "ref_shifts": 0, "dup_keys": 0, "swapped_keys": 0, "type_reinterpret": 0},
      "changes": [{"field_path": "Package", "op": "changed", "before": "/Mosin/Data/AnimSet_Mosin", "after": "/WEPN_017/Data/AnimSet_Mosin"}]
    }
  ]
}
```

`history` holds one entry per ingested build whose diff lists the asset, in chronological order; `changes` keep ingest
order. `aliases` are chronological too. A former path that several assets once had resolves to the asset that moved
from it most recently.

## `GET /api/changes`

Field changes across every ingested build, for "what happened to every `...Damage` field".

| Query | Meaning |
|---|---|
| `field` | required, 2–200 characters; case-insensitive substring of `field_path` |
| `build` | build label, exact; 404 `{"detail": "build not found"}` if it is not an ingested build |
| `area` | area id, exact |
| `op` | `added`, `removed`, `changed` or `resequenced` |
| `limit` | 1–200, default 50 |
| `offset` | 0–1,000,000, default 0 |

Order: build newest first, then lower-case asset name and path by code point, then ingest order.

```json
{
  "total": 1,
  "items": [
    {
      "build": "s2-cl516586",
      "released_at": "2026-10-15",
      "asset": {"path": "Wardogs/Content/.../DA_308Winchester_ProjectileData.json", "name": "DA_308Winchester_ProjectileData", "type": "BHProjectileData"},
      "area": "weapons-ammo",
      "status": "modified",
      "field_path": "Properties.ProjectileStats.DamageValues.BaseDamage",
      "op": "changed",
      "before": 60.0,
      "after": 45.0
    }
  ]
}
```

`status` is the asset's status in that build.

## Errors

FastAPI defaults: 404 `{"detail": "..."}`, 422 validation body. No other error shapes.
