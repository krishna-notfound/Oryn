"""Per-turn language resolution and the note that pins the reply language."""

from __future__ import annotations

import pytest

from app.conversation.language import (
    FALLBACK_LANGUAGE,
    language_note,
    normalize_language,
    settle_detected,
    turn_language,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("hi-IN", "hi-IN"),
        ("hi", "hi-IN"),
        ("TA_in", "ta-IN"),
        ("en", "en-IN"),
        ("xx", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_language(code: str | None, expected: str | None) -> None:
    assert normalize_language(code) == expected


def test_fixed_language_ignores_detection() -> None:
    assert turn_language("ta-IN", "hi-IN", "en-IN") == "ta-IN"


def test_auto_takes_the_detected_language() -> None:
    assert turn_language("auto", "te-IN", "hi-IN") == "te-IN"
    assert turn_language("auto", "bn", None) == "bn-IN"


def test_auto_falls_back_to_the_last_language_then_hindi() -> None:
    assert turn_language("auto", None, "mr-IN") == "mr-IN"
    assert turn_language("auto", "klingon", "mr-IN") == "mr-IN"
    assert turn_language("auto", None, None) == FALLBACK_LANGUAGE == "hi-IN"


def test_notes_name_the_language_and_script() -> None:
    assert language_note("ta-IN").startswith("Reply in Tamil (ta-IN).")
    assert "own script" in language_note("ta-IN")
    assert language_note("en-IN") == "Reply in English (en-IN)."
    hindi = language_note("hi-IN")
    assert hindi.startswith("Reply in Hindi (hi-IN).") and "Hinglish" in hindi


@pytest.mark.parametrize(
    ("detected", "text", "previous", "extras", "expected"),
    [
        ("hi-IN", "ho ka", "mr-IN", (), "mr-IN"),  # short, and the user was speaking Marathi
        ("hi-IN", "ho ka", None, ("mr-IN",), "mr-IN"),  # short, and they listed Marathi
        ("hi-IN", "ho ka", None, ("mr",), "mr-IN"),
        ("hi-IN", "ek do teen", "mr-IN", (), "mr-IN"),  # three words is still short
        ("hi-IN", "aaj mausam bahut accha hai", "mr-IN", (), "hi-IN"),  # long enough to trust
        ("hi-IN", "ho ka", "ta-IN", ("en-IN",), "hi-IN"),  # no Marathi in sight
        ("ta-IN", "ho ka", "mr-IN", (), "ta-IN"),  # only Hindi is second-guessed
        (None, "ho ka", "mr-IN", (), None),
    ],
)
def test_short_hindi_segments_stay_marathi_for_marathi_speakers(
    detected: str | None,
    text: str,
    previous: str | None,
    extras: tuple[str, ...],
    expected: str | None,
) -> None:
    assert settle_detected(detected, text, previous=previous, extras=extras) == expected
