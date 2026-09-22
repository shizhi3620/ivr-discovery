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
    placeholders: list[tuple[str, str]] = []
    for index, (source, target) in enumerate(_corrections()):
        placeholder = f"\ue000{index}\ue001"
        corrected, count = re.subn(
            re.escape(source),
            placeholder,
            corrected,
            flags=re.IGNORECASE,
        )
        if count:
            placeholders.append((placeholder, target))
    for placeholder, target in placeholders:
        corrected = corrected.replace(placeholder, target)
    return corrected


def reset_asr_corrections() -> None:
    _corrections.cache_clear()


__all__ = ["apply_asr_corrections", "reset_asr_corrections"]
