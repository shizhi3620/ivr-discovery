"""Retention classes and expiry sweep (ADR 0029 amended by ADR 0042)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from recording_retention import (
    CLASS_EVIDENCE,
    CLASS_HUMAN,
    CLASS_MENU,
    INDEX_FILENAME,
    classify_recording,
    sweep_expired,
)

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_wav(recording_dir: Path, name: str, *, age: timedelta) -> Path:
    path = recording_dir / name
    path.write_bytes(b"RIFF-fake-wav")
    mtime = (NOW - age).timestamp()
    os.utime(path, (mtime, mtime))
    return path


class TestClassifyRecording:
    def test_records_class_with_timestamp(self, tmp_path):
        classify_recording(tmp_path, "a.wav", CLASS_MENU, now=NOW)

        index = json.loads((tmp_path / INDEX_FILENAME).read_text())
        assert index["a.wav"]["class"] == CLASS_MENU
        assert index["a.wav"]["classified_at"] == NOW.isoformat()

    def test_stricter_class_wins(self, tmp_path):
        classify_recording(tmp_path, "a.wav", CLASS_EVIDENCE, now=NOW)
        classify_recording(tmp_path, "a.wav", CLASS_MENU, now=NOW)
        classify_recording(tmp_path, "a.wav", CLASS_HUMAN, now=NOW)

        index = json.loads((tmp_path / INDEX_FILENAME).read_text())
        assert index["a.wav"]["class"] == CLASS_HUMAN

    def test_looser_class_never_downgrades(self, tmp_path):
        classify_recording(tmp_path, "a.wav", CLASS_HUMAN, now=NOW)
        classify_recording(tmp_path, "a.wav", CLASS_EVIDENCE, now=NOW)
        classify_recording(tmp_path, "a.wav", CLASS_MENU, now=NOW)

        index = json.loads((tmp_path / INDEX_FILENAME).read_text())
        assert index["a.wav"]["class"] == CLASS_HUMAN

    def test_unknown_class_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            classify_recording(tmp_path, "a.wav", "forever", now=NOW)


class TestSweepExpired:
    def test_menu_recording_expires_after_seven_days(self, tmp_path):
        kept = _make_wav(tmp_path, "fresh.wav", age=timedelta(days=1))
        stale = _make_wav(tmp_path, "stale.wav", age=timedelta(days=1))
        classify_recording(tmp_path, "fresh.wav", CLASS_MENU, now=NOW - timedelta(days=6))
        classify_recording(tmp_path, "stale.wav", CLASS_MENU, now=NOW - timedelta(days=8))

        deleted = sweep_expired(tmp_path, now=NOW)

        assert deleted == ["stale.wav"]
        assert kept.exists() and not stale.exists()

    def test_evidence_recording_kept_thirty_days(self, tmp_path):
        recent = _make_wav(tmp_path, "evidence-fresh.wav", age=timedelta(days=1))
        old = _make_wav(tmp_path, "evidence-old.wav", age=timedelta(days=1))
        classify_recording(tmp_path, "evidence-fresh.wav", CLASS_EVIDENCE, now=NOW - timedelta(days=29))
        classify_recording(tmp_path, "evidence-old.wav", CLASS_EVIDENCE, now=NOW - timedelta(days=31))

        deleted = sweep_expired(tmp_path, now=NOW)

        assert deleted == ["evidence-old.wav"]
        assert recent.exists() and not old.exists()

    def test_human_recording_deleted_after_24_hours(self, tmp_path):
        recent = _make_wav(tmp_path, "human-fresh.wav", age=timedelta(hours=1))
        old = _make_wav(tmp_path, "human-old.wav", age=timedelta(hours=1))
        classify_recording(tmp_path, "human-fresh.wav", CLASS_HUMAN, now=NOW - timedelta(hours=23))
        classify_recording(tmp_path, "human-old.wav", CLASS_HUMAN, now=NOW - timedelta(hours=25))

        deleted = sweep_expired(tmp_path, now=NOW)

        assert deleted == ["human-old.wav"]
        assert recent.exists() and not old.exists()

    def test_unindexed_file_defaults_to_menu_class_from_mtime(self, tmp_path):
        fresh = _make_wav(tmp_path, "unindexed-fresh.wav", age=timedelta(days=2))
        stale = _make_wav(tmp_path, "unindexed-stale.wav", age=timedelta(days=9))

        deleted = sweep_expired(tmp_path, now=NOW)

        assert deleted == ["unindexed-stale.wav"]
        assert fresh.exists() and not stale.exists()

    def test_missing_directory_is_a_noop(self, tmp_path):
        assert sweep_expired(tmp_path / "nope", now=NOW) == []
