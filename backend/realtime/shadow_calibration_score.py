"""Score shadow-judgment calibration labels against the ADR 0040 gates.

Input is a JSONL file produced by ``shadow_calibration.py`` plus a labels file
mapping each ``(wav, decision_index)`` to the human ground-truth classification.
Each JSONL line must carry a ``label`` field once reviewed; this script computes
the three gates:

- cancel_hangup_false_positive == 0
- request_hangup_false_positive <= 1
- overall_accuracy >= 0.9

Coverage is part of the gate, not just the metric: a direction with zero
labelled decision points satisfies its false-positive gate vacuously, which is
not evidence of safety. ``score`` returns a distinct exit code ``3`` when either
veto direction has no samples.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

VALID = {"ivr_menu", "automated_notice", "human_or_unknown"}


def score(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    labelled = [r for r in rows if r.get("label") in VALID]
    if not labelled:
        print("No labelled decision points. Add a `label` field to each shadow_verdict row.")
        return 2

    total = len(labelled)
    correct = sum(1 for r in labelled if r.get("classification") == r["label"])

    cancel_fp = 0
    request_fp = 0
    for r in labelled:
        predicted = r.get("classification")
        truth = r["label"]
        # The event trigger identifies which rule action the model tried to veto.
        trigger = r.get("trigger")
        if trigger == "boundary_pending":
            # Rule wants to hang up (human boundary); a non-human prediction
            # cancels that hangup, which is the high-risk direction.
            if predicted in ("ivr_menu", "automated_notice") and truth == "human_or_unknown":
                cancel_fp += 1
        elif trigger == "target_key":
            # Rule wants to send DTMF; human_or_unknown vetoes it.
            if predicted == "human_or_unknown" and truth in ("ivr_menu", "automated_notice"):
                request_fp += 1

    accuracy = correct / total
    print(f"labelled={total} correct={correct} accuracy={accuracy:.3f}")
    print(f"cancel_hangup_false_positive={cancel_fp}")
    print(f"request_hangup_false_positive={request_fp}")

    cancel_samples = sum(1 for r in labelled if r.get("trigger") == "boundary_pending")
    request_samples = sum(1 for r in labelled if r.get("trigger") == "target_key")
    print(f"cancel_hangup_samples={cancel_samples}")
    print(f"request_hangup_samples={request_samples}")

    failures = []
    if cancel_fp != 0:
        failures.append("cancel-hangup direction has false positives (gate: 0)")
    if request_fp > 1:
        failures.append("request-hangup direction exceeds 1 false positive")
    if accuracy < 0.9:
        failures.append("overall accuracy below 0.9")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    uncovered = []
    if cancel_samples == 0:
        uncovered.append("cancel-hangup direction (boundary_pending) has 0 samples")
    if request_samples == 0:
        uncovered.append("request-hangup direction (target_key) has 0 samples")
    if uncovered:
        for u in uncovered:
            print(f"INSUFFICIENT COVERAGE: {u}")
        print("Coverage is gated: an untested direction is not a passed direction.")
        return 3

    print("PASS: calibration gates satisfied")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Score shadow calibration")
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    raise SystemExit(score(args.jsonl))


if __name__ == "__main__":
    main()
