"""Which language a turn is in, and what the model is told about it.

A fixed-language user always gets their language. With auto-detect (`catalog.AUTO_LANGUAGE`)
each spoken turn takes the language the speech-to-text detected, falling back to the last one
heard in this session and finally to Hindi, the lingua franca the default voice speaks best.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.voice.catalog import AUTO_LANGUAGE, LANGUAGES

FALLBACK_LANGUAGE = "hi-IN"
SHORT_SEGMENT_WORDS = 4  # fewer words than this is too little to tell Hindi from Marathi

_BY_BASE = {code.split("-")[0]: code for code in LANGUAGES}


def normalize_language(code: str | None) -> str | None:
    """A supported Prisma/Timbre code ('hi', 'hi-in' and 'hi-IN' all give 'hi-IN'), or None."""
    if not code:
        return None
    base = code.replace("_", "-").split("-")[0].lower()
    return _BY_BASE.get(base)


def is_auto(user_language: str) -> bool:
    return user_language == AUTO_LANGUAGE


def turn_language(user_language: str, detected: str | None, last_detected: str | None) -> str:
    if not is_auto(user_language):
        return user_language
    return normalize_language(detected) or last_detected or FALLBACK_LANGUAGE


def language_note(code: str) -> str:
    """The per-turn system note that pins the reply language when auto-detect is on."""
    name = LANGUAGES.get(code, code)
    note = f"Reply in {name} ({code})."
    if code == "hi-IN":
        return (
            f"{note} If the user spoke Hinglish in Latin letters, answer in Hinglish in Latin "
            "letters; if they used Devanagari, answer in Devanagari."
        )
    if code == "en-IN":
        return note
    return f"{note} Write it in its own script."


def settle_detected(
    detected: str | None, text: str, *, previous: str | None, extras: Sequence[str] = ()
) -> str | None:
    """The language a final segment is taken to be in. Hindi and Marathi share a script and much
    vocabulary, so the detector often calls a short Marathi segment Hindi: when the user speaks
    Marathi (their previous turn, or a language they listed) a segment under
    `SHORT_SEGMENT_WORDS` words stays Marathi."""
    code = normalize_language(detected)
    if code == "hi-IN" and len(text.split()) < SHORT_SEGMENT_WORDS:
        listed = {normalize_language(extra) for extra in extras}
        if previous == "mr-IN" or "mr-IN" in listed:
            return "mr-IN"
    return code
