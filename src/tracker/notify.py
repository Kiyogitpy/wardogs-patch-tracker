"""Deliver Discord webhook messages for the subscriptions a build matches. See docs/NOTIFY.md."""
import json
import logging
import math
import time
from decimal import ROUND_HALF_UP, Decimal, localcontext
from urllib.parse import quote

import httpx
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert

from tracker.api import NEWEST_FIRST, _contains
from tracker.db import asset, asset_status, build, build_area, change, delivery, subscription

log = logging.getLogger("tracker.notify")

MINUS = "−"
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_LINES = 10
MAX_VALUE = 80
MAX_LINE = 400  # keeps ten lines plus the "and N more" line near the description limit
MAX_ERROR = 300
ATTEMPTS = 3
BACKOFF = (1, 4)
RETRY_AFTER_CAP = 30
TIMEOUT = 10
GONE = (401, 403, 404)


class UnknownBuild(Exception):
    pass


# ---------- value formatting (mirrors web/src/format.ts) ----------

def _plain(n: int | float, digits: int) -> str:
    if isinstance(n, float) and not math.isfinite(n):
        return str(n)
    with localcontext() as ctx:
        ctx.prec = 60
        d = Decimal(repr(n))
        if d == 0:
            return "0"
        d = d.quantize(Decimal(1).scaleb(d.adjusted() - digits + 1), rounding=ROUND_HALF_UP)
        text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _signed(n, text: str) -> str:
    if n > 0:
        return f"+{text}"
    if n < 0:
        return MINUS + text.replace("-", "")
    return text


def _number_pair(before, after) -> tuple[str, str]:
    b, a = _plain(before, 6), _plain(after, 6)
    return (_plain(before, 17), _plain(after, 17)) if b == a and before != after else (b, a)


def _delta(before, after) -> str | None:
    d = float(f"{after - before:.12g}")
    text = _signed(d, _plain(d, 6))
    if before == 0:
        return f"({text})"
    p = d / abs(before) * 100
    rounded = round(p, 1) if abs(p) < 10 else math.floor(p + 0.5)
    if rounded == 0 and d != 0:
        pct = f"{'+' if p > 0 else MINUS}<0.1%"
    else:
        pct = _signed(rounded, str(rounded) if isinstance(rounded, int) else f"{rounded:g}") + "%"
    return f"({text}, {pct})"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _value(v) -> str:
    return _cut(json.dumps(v, separators=(",", ":"), ensure_ascii=False), MAX_VALUE)


def change_line(c) -> str:
    if _is_number(c.before) and _is_number(c.after) and math.isfinite(c.before) and math.isfinite(c.after):
        b, a = _number_pair(c.before, c.after)
        values = f"{b} ▶ {a}  {_delta(c.before, c.after)}"
    else:
        values = f"{_value(c.before)} ▶ {_value(c.after)}"
    return _cut(f"`{c.field_path}`  {values}", MAX_LINE)


def description(lines: list[str], total: int, link: str) -> str:
    """At most 10 lines, then "and N more" with the link; lines are dropped until it fits Discord's limit."""
    shown = lines[:MAX_LINES]
    while True:
        more = total - len(shown)
        text = "\n".join(shown + ([f"and {more} more: {link}"] if more > 0 else []))
        if len(text) <= MAX_DESCRIPTION or not shown:
            return _cut(text, MAX_DESCRIPTION)
        shown.pop()


def _embed(title: str, url: str, lines: list[str], total: int, footer: str) -> dict:
    return {
        "username": "Wardogs Patch Tracker",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": _cut(title, MAX_TITLE),
            "url": url,
            "description": description(lines, total, url),
            "footer": {"text": footer},
        }],
    }


# ---------- matching and message building ----------

def _q(text: str) -> str:
    return quote(text, safe="")


def _asset_message(conn, sub, b, origin):
    row = conn.execute(
        select(asset.c.name, asset.c.path, asset_status.c.status)
        .select_from(asset_status.join(asset, asset.c.id == asset_status.c.asset_id))
        .where(asset_status.c.build_id == b.id, asset_status.c.asset_id == sub.asset_id)
    ).first()
    if row is None:
        return None
    where = (change.c.build_id == b.id, change.c.asset_id == sub.asset_id)
    total = conn.execute(select(func.count()).where(*where)).scalar_one()
    rows = conn.execute(select(change).where(*where).order_by(change.c.id).limit(MAX_LINES)).all()
    verb = "changed" if row.status == "modified" else row.status
    lines = [f"Status: {row.status}"] + [change_line(c) for c in rows]
    url = f"{origin}/asset?path={_q(row.path)}"
    return _embed(f"{row.name} {verb} in {b.label}", url, lines, total + 1, f"Watching: asset {row.name}")


def _area_message(conn, sub, b, origin):
    where = (asset_status.c.build_id == b.id, asset_status.c.area == sub.target)
    joined = asset_status.join(asset, asset.c.id == asset_status.c.asset_id)
    total = conn.execute(select(func.count()).select_from(joined).where(*where)).scalar_one()
    if total == 0:
        return None
    # Same order as /api/builds/{label}/assets.
    status_order = case({"modified": 0, "moved": 1, "added": 2, "removed": 3}, value=asset_status.c.status)
    rows = conn.execute(
        select(asset.c.name, asset_status.c.status).select_from(joined).where(*where)
        .order_by(status_order, func.lower(asset.c.name).collate("C"), asset.c.path.collate("C")).limit(MAX_LINES)
    ).all()
    area = conn.execute(select(build_area.c.title, build_area.c.counts)
                        .where(build_area.c.build_id == b.id, build_area.c.area == sub.target)).first()
    title = area.title if area else sub.target
    counts = ", ".join(f"{area.counts[k]} {k}" for k in ("added", "removed", "modified", "moved")
                       if area.counts.get(k)) if area else ""
    lines = ([counts] if counts else []) + [f"{r.status}  {r.name}" for r in rows]
    url = f"{origin}/builds/{_q(b.label)}?area={_q(sub.target)}"
    return _embed(f"{title} changed in {b.label}", url, lines, total + bool(counts), f"Watching: area {sub.target}")


def _field_message(conn, sub, b, origin):
    where = (change.c.build_id == b.id, _contains(change.c.field_path, sub.target))
    joined = change.join(build, build.c.id == change.c.build_id).join(asset, asset.c.id == change.c.asset_id)
    total = conn.execute(select(func.count()).select_from(joined).where(*where)).scalar_one()
    if total == 0:
        return None
    # Same order as /api/changes.
    rows = conn.execute(
        select(asset.c.name, change.c.field_path, change.c.before, change.c.after).select_from(joined).where(*where)
        .order_by(*NEWEST_FIRST, func.lower(asset.c.name).collate("C"), asset.c.path.collate("C"), change.c.id)
        .limit(MAX_LINES)
    ).all()
    lines = [_cut(f"**{r.name}**  {change_line(r)}", MAX_LINE) for r in rows]
    url = f"{origin}/changes?field={_q(sub.target)}&build={_q(b.label)}"
    title = f"{total} change{'' if total == 1 else 's'} matching “{sub.target}” in {b.label}"
    return _embed(title, url, lines, total, f"Watching: field {sub.target}")


BUILDERS = {"asset": _asset_message, "area": _area_message, "field": _field_message}


# ---------- sending ----------

def _error(code: int, response: httpx.Response, webhook: str) -> str:
    return f"HTTP {code}: " + _cut(response.text.replace(webhook, "[webhook]"), MAX_ERROR)


def _retry_after(response: httpx.Response) -> float:
    try:
        value = float(response.json()["retry_after"])
    except (ValueError, KeyError, TypeError):
        value = 1.0
    return min(max(value, 0.0), RETRY_AFTER_CAP)


def send(client: httpx.Client, webhook: str, body: dict, sleep) -> tuple[str, int, str | None, bool]:
    """POST with the spec's retry rules. Returns (status, tries, last_error, webhook_gone)."""
    error = None
    for attempt in range(1, ATTEMPTS + 1):
        delay = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
        try:
            response = client.post(webhook, json=body, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            error = f"network error: {type(e).__name__}"  # str(e) can carry the request URL
        else:
            code = response.status_code
            if 200 <= code < 300:
                return "sent", attempt, None, False
            error = _error(code, response, webhook)
            if code in GONE:
                return "failed", attempt, error, True
            if code == 429:
                delay = _retry_after(response)
            elif code < 500:
                return "failed", attempt, error, False
        if attempt < ATTEMPTS:
            sleep(delay)
    return "failed", ATTEMPTS, error, False


def _record(engine, sub_id: int, build_id: int, status: str, tries: int, error: str | None, gone: bool) -> None:
    """One transaction per delivery, so a crash later in the run keeps the record of what was sent."""
    with engine.begin() as conn:
        stmt = insert(delivery).values(subscription_id=sub_id, build_id=build_id, status=status, attempts=tries,
                                       last_error=error)
        conn.execute(stmt.on_conflict_do_update(
            index_elements=["subscription_id", "build_id"],
            set_={"status": status, "attempts": delivery.c.attempts + tries, "last_error": error,
                  "updated_at": func.now()}))
        if gone:
            conn.execute(update(subscription).where(subscription.c.id == sub_id, subscription.c.disabled_at.is_(None))
                         .values(disabled_at=func.now()))


def notify(engine, label: str, public_origin: str, client: httpx.Client | None = None, dry_run: bool = False,
           sleep=time.sleep) -> dict:
    """Send every due message for build `label`. Raises UnknownBuild if it is not ingested."""
    origin = public_origin.rstrip("/")
    with engine.connect() as conn:
        b = None if "\x00" in label else conn.execute(
            select(build.c.id, build.c.label).where(build.c.label == label, build.c.ingested_at.is_not(None))).first()
        if b is None:
            raise UnknownBuild(label)
        sent = select(delivery.c.subscription_id).where(
            delivery.c.subscription_id == subscription.c.id, delivery.c.build_id == b.id, delivery.c.status == "sent")
        subs = conn.execute(select(subscription).where(subscription.c.disabled_at.is_(None), ~sent.exists())
                            .order_by(subscription.c.id)).all()
        due = []
        for sub in subs:
            body = BUILDERS[sub.kind](conn, sub, b, origin)
            if body is not None:
                due.append((sub, body))

    summary = {"label": label, "matched": len(due), "sent": 0, "failed": 0, "disabled": 0, "messages": []}
    if dry_run:
        summary["messages"] = [{"subscription_id": s.id, "body": body} for s, body in due]
        return summary

    own_client = client is None
    client = client or httpx.Client()
    try:
        for sub, body in due:
            status, tries, error, gone = send(client, sub.webhook_url, body, sleep)
            _record(engine, sub.id, b.id, status, tries, error, gone)
            summary[status] += 1
            summary["disabled"] += gone
            log.info("subscription %s: %s after %s attempt(s)%s", sub.id, status, tries,
                     " (webhook gone, disabled)" if gone else "")
    finally:
        if own_client:
            client.close()
    return summary
