# API contract (step 2)

JSON over HTTP, all routes under `/api`. Read-only and public. The web app calls these through the Vite dev proxy
(`/api` → `http://127.0.0.1:8000`), and in production through the same origin, so no CORS.

Only builds with an ingested diff (`ingested_at` not null) are listed or served. A base build that exists only as
another build's `--from` returns 404.

Dates are ISO 8601 strings (`released_at`: `YYYY-MM-DD`; `ingested_at`: full timestamp with offset). `before`/`after`
are the stored JSON values exactly: number, string, boolean, null, array or object.

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
descending, then type ascending.

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
| `offset` | ≥ 0, default 0 |

Order: status (`modified`, `moved`, `added`, `removed`, as `season_diff.py`), then lower-case name, then path.
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

`total` counts the assets matching every filter, ignoring `limit`/`offset`. `old_path` is the `asset_alias.old_path`
recorded for this asset in this build, else null.

## Errors

FastAPI defaults: 404 `{"detail": "..."}`, 422 validation body. No other error shapes.
