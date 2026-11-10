"""Deciding whether overlapping speech stops Oryn or only nods along.

When the user starts talking while Oryn is speaking, nothing is cancelled yet: the turn is
*contested*. The browser ducks the audio, the reply keeps streaming, and the final transcript
of the overlap settles it. "haan", "hmm", a laugh (a backchannel) means *go on* and is
dropped; real words mean *stop and answer*. If no transcript arrives in time the contest
simply lapses and Oryn carries on.

`BackchannelClassifier` is pure; `InterruptionArbiter` owns the contested flag and its timer.
Spec: docs/superpowers/specs/2026-11-10-human-conversation-design.md section 1
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from enum import StrEnum

log = logging.getLogger(__name__)

CONTEST_TIMEOUT_S = 3.0
MAX_BACKCHANNEL_WORDS = 3

# English plus Hindi/Hinglish nods, in Latin and Devanagari: understood in every language
# because Indian users mix them freely.
_COMMON = """
ok okay k yeah yes yep yup right sure cool wow nice mm hm hmm mhm uh-huh uhhuh oh ah aha ooh
haan haa ha achha accha acha theek thik ji sahi bilkul arre wah hai
हाँ हां हा अच्छा ठीक जी हम्म सही बिल्कुल वाह अरे ओह
"""

# Acknowledgements in the other supported languages, counted only for that language.
_BY_LANGUAGE = {
    "ta": "ஆமா ஆம் சரி ம்ம் ஓ",
    "te": "అవును సరే హా ఓహో",
    "bn": "হ্যাঁ হুম আচ্ছা ঠিক ওহ",
    "mr": "हो बरं अच्छा ठीक",
    "gu": "હા સારું અચ્છા ઠીક",
    "kn": "ಹೌದು ಸರಿ ಹೂಂ ಓಹ್",
    "ml": "അതെ ശരി ഉം ഓ",
    "pa": "ਹਾਂ ਠੀਕ ਅੱਛਾ ਜੀ",
}

_LAUGHTER = re.compile(r"(?:ha|he){2,}h?|lol|lmao")
_ELONGATION = re.compile(r"(.)\1+")


def _squash(word: str) -> str:
    """'haaan' -> 'han', 'hmmm' -> 'hm': repeated letters collapse so stretched nods match."""
    return _ELONGATION.sub(r"\1", word)


def _squashed(words: str) -> frozenset[str]:
    return frozenset(map(_squash, words.split()))


class BackchannelClassifier:
    """True for short listener noises that mean "go on" rather than "stop"."""

    def __init__(self) -> None:
        self._common = _squashed(_COMMON)
        self._by_language = {code: _squashed(words) for code, words in _BY_LANGUAGE.items()}

    def is_backchannel(self, text: str, language: str | None) -> bool:
        words = self._words(text)
        if len(words) > MAX_BACKCHANNEL_WORDS:
            return False
        known = self._common | self._by_language.get((language or "").split("-")[0].lower(), set())
        return all(_LAUGHTER.fullmatch(w) or _squash(w) in known for w in words)

    @staticmethod
    def _words(text: str) -> list[str]:
        cleaned = "".join(
            " " if unicodedata.category(c)[0] in "PSZ" and c != "-" else c
            for c in unicodedata.normalize("NFC", text.lower())
        )
        return [w for w in (word.strip("-") for word in cleaned.split()) if w]


class Verdict(StrEnum):
    RESUME = "resume"  # a backchannel: keep speaking
    INTERRUPT = "interrupt"  # real words: stop and answer


ExpiryHandler = Callable[[], Awaitable[None]]


class InterruptionArbiter:
    """The contested state of a turn. Not tied to a turn itself: the engine decides *when* to
    contest (a confirmed turn is audible) and acts on the verdict; this keeps the flag, the
    timeout and the classifier together. One timer task at most, owned here and cancelled by
    `clear`, `judge` and `close`."""

    def __init__(
        self,
        on_expired: ExpiryHandler,
        *,
        timeout_s: float = CONTEST_TIMEOUT_S,
        classifier: BackchannelClassifier | None = None,
    ) -> None:
        self._on_expired = on_expired
        self._timeout_s = timeout_s
        self._classifier = classifier or BackchannelClassifier()
        self._contested = False
        self._timer: asyncio.Task[None] | None = None

    @property
    def contested(self) -> bool:
        return self._contested

    def contest(self) -> bool:
        """Start contesting. False if it already was, so the timer is not restarted by a second
        signal for the same speech (the browser's VAD and the provider's both fire)."""
        if self._contested:
            return False
        self._contested = True
        self._arm()
        return True

    def hold(self) -> None:
        """The user is still talking: the contest cannot lapse until `touch` says they stopped."""
        if self._contested:
            self._disarm()

    def touch(self) -> None:
        """Give the transcript a fresh `timeout_s` (the user just stopped talking)."""
        if self._contested:
            self._arm()

    def judge(self, text: str, language: str | None) -> Verdict:
        """The overlap's transcript is in: settle the contest."""
        self.clear()
        if self._classifier.is_backchannel(text, language):
            return Verdict.RESUME
        return Verdict.INTERRUPT

    def clear(self) -> None:
        self._contested = False
        self._disarm()

    async def close(self) -> None:
        timer = self._timer
        self.clear()
        if timer is not None:
            await asyncio.wait({timer})

    # ------------------------------------------------------------ timer

    def _arm(self) -> None:
        self._disarm()
        self._timer = asyncio.create_task(self._expire(), name="contest-timer")

    def _disarm(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    async def _expire(self) -> None:
        await asyncio.sleep(self._timeout_s)
        self._timer = None  # fired: from here a new contest may arm its own timer
        self._contested = False
        try:
            await self._on_expired()
        except Exception:
            log.exception("contest expiry failed")
