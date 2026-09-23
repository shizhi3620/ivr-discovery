from __future__ import annotations

import json
import logging

from dotenv import load_dotenv

from ai import AIProvider, get_ai_provider
from realtime.corrections import apply_asr_corrections

load_dotenv()

logger = logging.getLogger(__name__)

PARSE_PROMPT = """You are analyzing a transcript from an IVR (Interactive Voice Response) phone system call. Our AI agent called the number and listened. In the transcript, "user" is the IVR system speaking, and "assistant"/"agent" is our listener.

CRITICAL: If the agent pressed a button (e.g. "Pressed Button: 3"), focus ONLY on what the IVR said AFTER the button press. Ignore the menu that was read before the button press — that was the parent menu. The submenu after the press is what we care about.

Extract the menu options from the CURRENT menu level (after any button press).

IMPORTANT RULES:
1. Only include options that represent actual menu destinations (e.g. "Billing", "Technical support", "Track a package")
2. EXCLUDE navigation/utility options like: "repeat", "start over", "go back", "continue", "main menu", "hear options again", "previous menu"
3. If an option has BOTH a DTMF key AND a voice equivalent (e.g. "say mobile or press 1"), only include it ONCE with the DTMF key
4. For voice-only options with no DTMF key, use "say1", "say2", etc.
5. Only include options EXPLICITLY stated by the IVR system
6. If the IVR asked for input that our agent can't provide (enter a number, record a message, provide account info, etc.) — return empty options and set prompt_text to describe what action is required (e.g. "Enter 10-digit fax number", "Record a message then press #")
7. If no menu was presented, return an empty options array
8. For conversational IVRs that say "you can say things like X, Y, or Z" — those ARE the menu options
9. Include explicit end-call or opt-out choices (for example "结束通话") as terminal options; they are part of the call flow
10. Preserve the IVR's original language in prompt_text and option labels. Do not translate English prompts into Chinese or Chinese prompts into English
11. Set human_transfer to true when the call reaches a live agent, representative, queue, "please hold", or an equivalent human-service boundary. However, if the IVR plays a hold/quality notice and THEN presents a real DTMF menu with explicit "press N" options, set human_transfer to false and extract those options instead (a hold announcement is not a terminal human boundary when a menu follows).

Return ONLY valid JSON:
{
  "prompt_text": "Brief summary of what the IVR said at THIS menu level (after any button press)",
  "human_transfer": false,
  "options": [
    {"dtmf_key": "1", "label": "Billing"},
    {"dtmf_key": "2", "label": "Technical support"}
  ]
}

"""

# Navigation/utility labels to filter out
SKIP_LABELS = {
    "repeat", "start over", "go back", "continue", "main menu",
    "hear options again", "previous menu", "replay", "hear again",
    "return to main menu", "repeat options", "repeat menu",
}


def _normalize_label(label: str) -> str:
    return label.strip().lower()


def _deduplicate_options(options: list[dict]) -> list[dict]:
    """Remove duplicates where the same option has both DTMF and voice versions.

    Keeps the DTMF version when both exist.
    """
    # Group by normalized label
    by_label: dict[str, list[dict]] = {}
    for opt in options:
        key = _normalize_label(opt["label"])
        by_label.setdefault(key, []).append(opt)

    deduped = []
    for label, group in by_label.items():
        # Skip navigation options
        if label in SKIP_LABELS:
            logger.info(f"Skipping navigation option: {label}")
            continue

        # Prefer DTMF over voice
        dtmf = [o for o in group if not o["dtmf_key"].startswith("say")]
        if dtmf:
            deduped.append(dtmf[0])
        else:
            deduped.append(group[0])

    return deduped


def _extract_json_object(text: str) -> dict:
    """Parse a JSON object, tolerating markdown fences or trailing prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start < 0:
            raise
        result, _ = json.JSONDecoder().raw_decode(cleaned[start:])

    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object, got {type(result).__name__}")
    return result


async def parse_transcript(
    transcript_text: str,
    *,
    provider: AIProvider | None = None,
    dtmf_path: str = "",
) -> dict:
    """Parse an IVR transcript and extract menu structure using the AI Provider.

    Returns {"prompt_text": str, "options": [{"dtmf_key": str, "label": str}]}
    """
    if not transcript_text or len(transcript_text.strip()) < 5:
        return {"prompt_text": "", "options": []}
    transcript_text = apply_asr_corrections(transcript_text)

    try:
        provider = provider or get_ai_provider()
        path_context = (
            "The mandatory navigation prefix already replayed in this call was: "
            f"{dtmf_path}.\n"
            "Ignore earlier prefix menus and parse only the final node reached "
            "after the full prefix. For example, after 1w1 ignore the privacy "
            "menu and the language menu.\n\n"
            if dtmf_path
            else "This is the root call; parse the first menu reached.\n\n"
        )
        text = (await provider.complete(
            PARSE_PROMPT + path_context + "Transcript:\n" + transcript_text,
            max_tokens=4096,
            json_mode=provider.capabilities.json_mode,
        )).strip()

        result = _extract_json_object(text)

        prompt_text = str(result.get("prompt_text", ""))
        options = result.get("options", [])
        human_transfer = bool(result.get("human_transfer", False))

        # Validate options
        valid_options = []
        for opt in options:
            if isinstance(opt, dict) and "dtmf_key" in opt and "label" in opt:
                valid_options.append({
                    "dtmf_key": str(opt["dtmf_key"]),
                    "label": str(opt["label"]),
                })

        # Deduplicate and filter navigation options
        valid_options = _deduplicate_options(valid_options)

        logger.info(f"Parsed {len(valid_options)} options from transcript (after dedup/filter), human_transfer={human_transfer}")
        return {"prompt_text": prompt_text, "options": valid_options, "human_transfer": human_transfer}

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return {
            "prompt_text": transcript_text[:200],
            "options": [],
            "parse_error": f"invalid JSON: {e}",
        }
    except Exception as e:
        logger.exception(f"Error parsing transcript: {e}")
        return {
            "prompt_text": "",
            "options": [],
            "parse_error": str(e) or type(e).__name__,
        }
