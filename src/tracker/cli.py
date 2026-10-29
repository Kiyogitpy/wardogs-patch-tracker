import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine

from tracker.ingest import TotalsMismatch, ingest
from tracker.notify import UnknownBuild, notify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ingest", help="load a season_diff.py output directory as one build")
    p.add_argument("--label", required=True, help="build label, e.g. s3-26000000")
    p.add_argument("--diff", required=True, type=Path, help="season-diff directory (index.json and area files)")
    p.add_argument("--from", dest="prev_label", help="label of the build the diff was made against")
    p.add_argument("--manifest", type=Path, help="update_pipeline.py manifest.json of this build")
    p.add_argument("--released", type=date.fromisoformat, help="release date, YYYY-MM-DD")
    n = sub.add_parser("notify", help="post Discord webhook messages for the subscriptions a build matches")
    n.add_argument("--label", required=True, help="label of an ingested build")
    n.add_argument("--dry-run", action="store_true", help="print the messages, send and write nothing")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is not set")
    if args.command == "notify":
        return _notify(parser, url, args)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    try:
        with create_engine(url).begin() as conn:
            result = ingest(conn, args.diff, args.label, args.prev_label, manifest, args.released)
    except TotalsMismatch as e:
        print(f"tracker: totals do not match index.json, nothing was written: {e}", file=sys.stderr)
        return 1
    except ValueError as e:  # a bad diff (also bad JSON): nothing was written
        print(f"tracker: bad diff, nothing was written: {e}", file=sys.stderr)
        return 1
    print(f"ingested {args.label}: {result['assets']} assets, {result['changes']} changes; totals match index.json")
    return 0


def _notify(parser, url: str, args) -> int:
    origin = os.environ.get("PUBLIC_ORIGIN")
    if not origin:
        parser.error("PUBLIC_ORIGIN is not set")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs every request URL, which is the webhook secret
    engine = create_engine(url)
    try:
        result = notify(engine, args.label, origin, dry_run=args.dry_run)
    except UnknownBuild:
        print(f"tracker: build {args.label!r} is not ingested", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    if args.dry_run:
        for m in result["messages"]:
            print(f"subscription {m['subscription_id']}: {json.dumps(m['body'], ensure_ascii=False)}")
        print(f"dry run for {args.label}: {result['matched']} message(s) would be sent")
        return 0
    print(f"notified {args.label}: {result['sent']} sent, {result['failed']} failed, "
          f"{result['disabled']} subscription(s) disabled")
    return 1 if result["failed"] else 0
