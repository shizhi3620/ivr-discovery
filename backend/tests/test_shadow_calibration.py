"""Tests for shadow-judgment calibration scoring gates (ADR 0040)."""

from __future__ import annotations

import json
from pathlib import Path

from realtime.shadow_calibration_score import score


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_calibration_passes_when_gates_met(tmp_path):
    rows = [
        {"trigger": "boundary_pending", "classification": "automated_notice", "label": "automated_notice"},
        {"trigger": "boundary_pending", "classification": "human_or_unknown", "label": "human_or_unknown"},
        {"trigger": "target_key", "classification": "ivr_menu", "label": "ivr_menu"},
        {"trigger": "target_key", "classification": "human_or_unknown", "label": "human_or_unknown"},
        {"trigger": "target_key", "classification": "ivr_menu", "label": "ivr_menu"},
    ]
    path = _write(tmp_path / "c.jsonl", rows)
    assert score(path) == 0


def test_calibration_fails_on_cancel_hangup_false_positive(tmp_path):
    rows = [
        {"trigger": "boundary_pending", "classification": "automated_notice", "label": "human_or_unknown"},
        {"trigger": "target_key", "classification": "ivr_menu", "label": "ivr_menu"},
    ]
    path = _write(tmp_path / "c.jsonl", rows)
    assert score(path) == 1


def test_calibration_fails_on_low_accuracy(tmp_path):
    rows = [
        {"trigger": "target_key", "classification": "ivr_menu", "label": "human_or_unknown"},
        {"trigger": "target_key", "classification": "human_or_unknown", "label": "ivr_menu"},
        {"trigger": "target_key", "classification": "human_or_unknown", "label": "human_or_unknown"},
        {"trigger": "target_key", "classification": "ivr_menu", "label": "ivr_menu"},
    ]
    path = _write(tmp_path / "c.jsonl", rows)
    assert score(path) == 1


def test_calibration_requires_labels(tmp_path):
    path = _write(tmp_path / "c.jsonl", [{"trigger": "target_key", "classification": "ivr_menu"}])
    assert score(path) == 2
