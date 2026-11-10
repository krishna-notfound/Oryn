"""Long-form detection and the reply token budget."""

from __future__ import annotations

import pytest

from app.conversation.length import LongFormDetector

detector = LongFormDetector(short_tokens=400, long_tokens=1200)


@pytest.mark.parametrize(
    "text",
    [
        "Tell me a story",
        "ek kahani suna do",
        "mujhe ek kahaani sunao",
        "मुझे एक कहानी सुनाओ",
        "can you explain how vaccines work",
        "please explain in detail",
        "tell me more",
        "continue",
        "go on",
        "aage batao",
        "आगे बताओ",
        "give me a recipe for biryani",
        "step by step please",
        "samjhao yaar",
        "Tell me a bedtime STORY about a dragon",
        "describe the monsoon in Mumbai",
    ],
)
def test_requests_for_length_are_long_form(text: str) -> None:
    assert detector.wants_long(text)
    assert detector.max_tokens(text) == 1200


@pytest.mark.parametrize(
    "text",
    [
        "hi, how are you",
        "what time is it",
        "mera din accha tha",
        "I history-tested my code",
        "i am stressed about work",
        "",
    ],
)
def test_ordinary_chat_keeps_the_short_budget(text: str) -> None:
    assert not detector.wants_long(text)
    assert detector.max_tokens(text) == 400
