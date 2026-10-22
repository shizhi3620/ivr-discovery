"""Export one discovery session (tree + cached report) as a portable JSON bundle.

Usage:
    python scripts/export_session.py <session_id> [output.json] [--db PATH]

The bundle can be imported on another machine with scripts/import_session.py.
Stdlib-only; no venv required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from session_bundle import export_bundle

DEFAULT_DB = Path(__file__).resolve().parents[1] / "ivr_discovery.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("output", nargs="?", help="output JSON path (default: auto)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    args = parser.parse_args()

    try:
        bundle = export_bundle(args.db, args.session_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    phone = bundle["session"].get("phone_number") or "ivr"
    output = (
        Path(args.output)
        if args.output
        else Path.cwd() / f"ivr-session-{phone}-{args.session_id[:8]}.json"
    )
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Exported {phone} session {args.session_id}: "
        f"{len(bundle['nodes'])} nodes, {len(bundle['edges'])} edges"
    )
    print(f"Bundle written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
