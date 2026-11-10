"""Locale-preserving wrapper for approved action drafts."""


def compose(action, dialogue, locale, duration_sec, max_duration_sec):
    if not isinstance(action, str) or not action.strip():
        raise ValueError("approved action draft is required")
    if not isinstance(dialogue, str) or not isinstance(locale, str) or not locale.strip():
        raise ValueError("dialogue and locale must be strings")
    if not isinstance(duration_sec, (int, float)) or not isinstance(max_duration_sec, (int, float)):
        raise ValueError("duration and verified maximum are required")
    if not 0 < duration_sec <= max_duration_sec:
        raise ValueError("duration exceeds verified model maximum")
    speech = (f"Spoken dialogue language: {locale}. Speak exactly as written, without translation:\n{dialogue}"
              if dialogue else "No spoken dialogue.")
    return {"locale": locale, "dialogue": dialogue, "duration_sec": duration_sec,
            "prompt": f"Duration: {duration_sec} seconds.\n{action}\n{speech}",
            "adapter": "generic-local-review", "provider_validated": False}
