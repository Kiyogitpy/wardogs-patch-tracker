import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine

from tracker.ingest import TotalsMismatch, ingest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ingest", help="load a season_diff.py output directory as one build")
    p.add_argument("--label", required=True, help="build label, e.g. s3-26000000")
    p.add_argument("--diff", required=True, type=Path, help="season-diff directory (index.json and area files)")
    p.add_argument("--from", dest="prev_label", help="label of the build the diff was made against")
    p.add_argument("--manifest", type=Path, help="update_pipeline.py manifest.json of this build")
    p.add_argument("--released", type=date.fromisoformat, help="release date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is not set")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    try:
        with create_engine(url).begin() as conn:
            result = ingest(conn, args.diff, args.label, args.prev_label, manifest, args.released)
    except TotalsMismatch as e:
        print(f"tracker: totals do not match index.json, nothing was written: {e}", file=sys.stderr)
        return 1
    print(f"ingested {args.label}: {result['assets']} assets, {result['changes']} changes; totals match index.json")
    return 0
