"""Recording retention classes and expiry sweep (ADR 0029, amended by ADR 0042).

Every call WAV written under the provider's recording_dir carries a retention
class recorded in a sidecar index next to the recordings:

- ``menu``: ordinary IVR menu recordings, kept 7 days.
- ``evidence``: exploration evidence — failed nodes, no-input timeout probes,
  unexpected terminals — kept 30 days (ADR 0042).
- ``human``: human boundary or suspected live-answer recordings, deleted
  within 24 hours of classification (ADR 0029, unchanged).

The sweep deletes expired recordings and logs each deletion for audit; deleted
content is never itself retained. Files missing from the index default to the
``menu`` class measured from file mtime.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CLASS_MENU = "menu"
CLASS_EVIDENCE = "evidence"
CLASS_HUMAN = "human"

RETENTION_PERIODS = {
    CLASS_MENU: timedelta(days=7),
    CLASS_EVIDENCE: timedelta(days=30),
    CLASS_HUMAN: timedelta(hours=24),
}

VALID_CLASSES = frozenset(RETENTION_PERIODS)

INDEX_FILENAME = ".retention-index.json"


def _index_path(recording_dir: Path) -> Path:
    return recording_dir / INDEX_FILENAME


def _load_index(recording_dir: Path) -> dict:
    try:
        return json.loads(_index_path(recording_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_index(recording_dir: Path, index: dict) -> None:
    _index_path(recording_dir).write_text(json.dumps(index, indent=1))


def classify_recording(
    recording_dir: Path,
    filename: str,
    retention_class: str,
    *,
    now: datetime | None = None,
) -> None:
    """Tag one recording with its retention class (idempotent upgrade only).

    A stricter (shorter-retention) class always wins over a looser one already
    recorded, so a recording once marked ``human`` can never be reclassed into
    a longer-lived bucket by accident.
    """
    if retention_class not in VALID_CLASSES:
        raise ValueError(f"unknown retention class {retention_class!r}")
    now = now or datetime.now(timezone.utc)
    index = _load_index(recording_dir)
    existing = index.get(filename)
    if existing is not None:
        order = {CLASS_HUMAN: 0, CLASS_MENU: 1, CLASS_EVIDENCE: 2}
        if order[existing["class"]] <= order[retention_class]:
            return
    index[filename] = {
        "class": retention_class,
        "classified_at": now.isoformat(),
    }
    _save_index(recording_dir, index)


def sweep_expired(
    recording_dir: Path,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Delete recordings past their retention period. Returns deleted names."""
    now = now or datetime.now(timezone.utc)
    index = _load_index(recording_dir)
    deleted: list[str] = []
    if not recording_dir.is_dir():
        return deleted
    for path in sorted(recording_dir.glob("*.wav")):
        entry = index.get(path.name)
        if entry is not None:
            retention_class = entry["class"]
            classified_at = datetime.fromisoformat(entry["classified_at"])
        else:
            retention_class = CLASS_MENU
            classified_at = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            )
        period = RETENTION_PERIODS[retention_class]
        if now - classified_at < period:
            continue
        path.unlink()
        index.pop(path.name, None)
        deleted.append(path.name)
        logger.info(
            "Retention sweep deleted %s (class=%s, classified_at=%s)",
            path.name,
            retention_class,
            classified_at.isoformat(),
        )
    if deleted:
        _save_index(recording_dir, index)
    return deleted
