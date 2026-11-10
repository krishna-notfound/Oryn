"""How long Oryn may talk this turn.

Chat gets a short token budget so replies stay conversational; when the user asks for a story,
an explanation or "tell me more" the budget opens up. The segmenter streams sentence by
sentence, so a long reply starts speaking just as fast as a short one.
"""

from __future__ import annotations

import re

# Words and phrases (English, Hinglish, Devanagari) that ask for more than a sentence or two.
_LONG_FORM = re.compile(
    r"""
    \b(?:stor(?:y|ies)|kahaa?ni(?:yan)?|kissa|kisse|tale|poem|kavita|recipe|essay|
        explain|explanation|elaborate|describe|detail(?:s|ed)?|step[\s-]by[\s-]step|
        tell\s+me\s+more|more\s+about|continue|go\s+on|keep\s+going|
        samjh?aa?o|samjha\s+do|vistaar|aage|batao\s+detail)\b
    | कहानी | किस्सा | कविता | समझाओ | समझा\s+दो | विस्तार | आगे | और\s+बताओ
    """,
    re.IGNORECASE | re.VERBOSE,
)


class LongFormDetector:
    def __init__(self, *, short_tokens: int, long_tokens: int) -> None:
        self._short = short_tokens
        self._long = long_tokens

    @staticmethod
    def wants_long(text: str) -> bool:
        return _LONG_FORM.search(text) is not None

    def max_tokens(self, text: str) -> int:
        return self._long if self.wants_long(text) else self._short
