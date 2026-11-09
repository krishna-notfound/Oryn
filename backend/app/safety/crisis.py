"""Crisis-language detection.

A deliberately small phrase list (English, Hindi in Devanagari, romanised Hinglish).
Limits, stated honestly: it is substring matching, so it ignores negation ("I am not
suicidal" still matches), misses indirect or novel phrasing and other languages, and
tolerates no typos beyond the spellings listed. It errs towards over-triggering, which
costs one extra caring reply. It is a safety net under the persona prompt, not a clinical tool.
"""

from __future__ import annotations

import re
import string

_PHRASES = (
    # English
    "kill myself",
    "end my life",
    "end it all",
    "want to die",
    "wanna die",
    "dont want to live",
    "no reason to live",
    "better off dead",
    "suicide",
    "suicidal",
    "hurt myself",
    "harm myself",
    "self harm",
    "cut myself",
    # Hindi (Devanagari)
    "आत्महत्या",
    "खुदकुशी",
    "ख़ुदकुशी",
    "मरना चाहता",
    "मरना चाहती",
    "मर जाना चाहता",
    "मर जाना चाहती",
    "जीना नहीं चाहता",
    "जीना नहीं चाहती",
    "जान दे दूँ",
    "जान दे दूं",
    "अपनी जान ले",
    # Hinglish (romanised)
    "khudkushi",
    "khudkhushi",
    "atmahatya",
    "marna chahta",
    "marna chahti",
    "mar jana chahta",
    "mar jana chahti",
    "mar jaana chahta",
    "mar jaana chahti",
    "jeena nahi chahta",
    "jeena nahi chahti",
    "jeene ka mann nahi",
    "jaan de dunga",
    "jaan de dungi",
    "apni jaan le",
    "suicide karna",
)

_INSTRUCTION = (
    "SAFETY: the user's last message may signal thoughts of suicide or self-harm. For this "
    "reply only: slow down and respond with warmth and calm, in the user's own language. "
    "Acknowledge what they feel without judgment or lecturing. Do not give any method or "
    "detail about self-harm. Gently encourage them to reach out now to someone they trust "
    "or a professional. In India, Tele-MANAS offers free, confidential support around the "
    "clock on 14416; if they are in immediate danger, suggest calling 112. Ask whether they "
    "are safe right now. Keep it short and natural for speech; do not diagnose."
)

_APOSTROPHES = str.maketrans("", "", "'’")
# Punctuation becomes a space; Devanagari combining marks must survive, so no \w tricks.
_PUNCTUATION = str.maketrans(dict.fromkeys(string.punctuation + "।॥…“”", " "))
_SPACES = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = text.lower().translate(_APOSTROPHES).translate(_PUNCTUATION)
    return _SPACES.sub(" ", text).strip()


_NORMALISED_PHRASES = tuple(_normalise(p) for p in _PHRASES)


class CrisisDetector:
    def check(self, text: str) -> str | None:
        """A one-turn system instruction if `text` contains crisis language, else None."""
        haystack = _normalise(text)
        if any(phrase in haystack for phrase in _NORMALISED_PHRASES):
            return _INSTRUCTION
        return None
