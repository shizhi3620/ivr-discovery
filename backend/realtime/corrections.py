"""Environment-driven corrections for known realtime ASR phrase errors."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=1)
def _corrections() -> tuple[tuple[str, str], ...]:
    raw = os.getenv("ASR_PHRASE_CORRECTIONS_JSON", "").strip()
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict):
        return ()
    return tuple(
        (str(source), str(target))
        for source, target in payload.items()
        if source and target
    )


def apply_asr_corrections(text: str) -> str:
    corrected = text
    for source, target in _corrections():
        corrected = re.sub(
            re.escape(source),
            target,
            corrected,
            flags=re.IGNORECASE,
        )
    return corrected


def reset_asr_corrections() -> None:
    _corrections.cache_clear()


__all__ = ["apply_asr_corrections", "reset_asr_corrections"]
