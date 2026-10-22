"""Import a session bundle produced by export_session.py into the local database.

Usage:
    python scripts/import_session.py <bundle.json> [--db PATH]

Idempotent: rows that already exist are left untouched, so re-importing the
same bundle is safe. Start the backend at least once first so the database
schema exists. Stdlib-only; no venv required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from session_bundle import import_bundle

DEFAULT_DB = Path(__file__).resolve().parents[1] / "ivr_discovery.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", help="bundle JSON file (repeatable)", nargs="+")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    args = parser.parse_args()

    for path_str in args.bundle:
        path = Path(path_str)
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
            summary = import_bundle(args.db, bundle)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            return 1

        session_id = summary.pop("session_id")
        counts = ", ".join(f"{table}={count}" for table, count in summary.items())
        print(f"Imported {session_id} from {path} ({counts})")
        print(f"Open the app at http://localhost:5173/{session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
