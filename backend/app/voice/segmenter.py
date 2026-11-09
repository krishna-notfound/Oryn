"""Turns a stream of LLM text deltas into speakable segments for TTS.

Time is passed in (`now`, seconds from any monotonic clock) so the rules are testable without
sleeping; the caller drives the wait-based flush by calling `poll` when its read times out.

Rules:
  * First segment: ends at the first clause boundary (`, ; : - . ! ? newline danda`) once the
    clause has >= 2 words and >= `first_min_words` words are buffered (the next word has
    started, so "Haan bilkul, main" speaks "Haan bilkul," while "Haan, main" keeps going).
    If nothing qualifies within `first_max_wait_s` of the first token and >= 2 words are
    buffered, `poll` releases them anyway: starting to speak beats a perfect clause.
  * Later segments: sentence boundaries only. A sentence shorter than `min_tail_words` is not
    sent alone (a ~1 s Timbre request for 0.4 s of audio); it is carried into the next one.
  * No segment exceeds `max_chars`; longer text is cut at the last space.
  * Never splits inside a word, a number ("3.5") or an abbreviation ("Dr.").
"""

from __future__ import annotations

_MARKDOWN = str.maketrans("", "", "*#`")
_HARD_END = "\n।"  # a newline or Devanagari danda always ends a sentence
_SOFT_END = ".!?"  # ends a sentence only when followed by whitespace ("3.5" stays whole)
_CLAUSE_END = ",;:—"  # also ends the *first* segment, so the voice starts sooner
_ABBREVIATIONS = frozenset({"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "e.g", "i.e"})


class SentenceSegmenter:
    def __init__(
        self,
        first_min_words: int = 3,
        first_max_wait_s: float = 0.25,
        max_chars: int = 160,
        min_tail_words: int = 3,
    ) -> None:
        self._first_min_words = first_min_words
        self._first_max_wait_s = first_max_wait_s
        self._max_chars = max_chars
        self._min_tail_words = min_tail_words
        self._reset()

    def _reset(self) -> None:
        self._buffer = ""
        self._carry = ""  # short sentences waiting to join the next segment
        self._first = True
        self._started_at: float | None = None

    @property
    def deadline(self) -> float | None:
        """When `poll` will release the first segment by time, or None if no timer is running."""
        if self._first and self._started_at is not None:
            return self._started_at + self._first_max_wait_s
        return None

    def feed(self, delta: str, now: float) -> list[str]:
        segments = self.poll(now)  # the deadline may have passed before this delta arrived
        self._buffer += delta.translate(_MARKDOWN)
        if self._started_at is None and self._buffer.strip():
            self._started_at = now
        while (raw := self._next_raw()) is not None:
            if segment := self._release(raw):
                segments.append(segment)
        return segments

    def poll(self, now: float) -> list[str]:
        """Time-based flush of the first segment."""
        deadline = self.deadline
        if deadline is None or now < deadline or _words(self._buffer) < 2:
            return []
        raw, self._buffer = self._buffer, ""
        self._first = False
        return [text] if (text := _speakable(raw)) else []

    def flush(self) -> str | None:
        """Whatever is left at the end of the reply; resets for the next one."""
        rest = _speakable(self._carry + self._buffer)
        self._reset()
        return rest

    # ------------------------------------------------------------ splitting

    def _next_raw(self) -> str | None:
        """Cut the next segment's raw text off the buffer, or None if it is not ready."""
        end = self._boundary()
        if end is None or end > self._max_chars:
            if len(self._buffer.strip()) <= self._max_chars:
                return None
            end = self._last_space(self._max_chars)
            if end is None:
                return None  # one enormous token; wait for whitespace rather than split a word
        raw, self._buffer = self._buffer[:end], self._buffer[end:].lstrip()
        return raw

    def _release(self, raw: str) -> str | None:
        text = _speakable(self._carry + raw)
        if text is None:
            self._carry = ""
            return None
        if not self._first and _words(text) < self._min_tail_words:
            self._carry = text + " "
            return None
        self._carry = ""
        self._first = False
        return text

    def _boundary(self) -> int | None:
        buf = self._buffer
        for i, ch in enumerate(buf):
            following = buf[i + 1 : i + 2]
            if ch in _HARD_END:
                found = True
            elif ch in _SOFT_END:
                found = following.isspace() and not _is_abbreviation(buf[: i + 1])
            else:
                found = self._first and ch in _CLAUSE_END and (ch == "—" or following.isspace())
            if found and self._first_clause_ok(buf, i):
                return i + 1
        return None

    def _first_clause_ok(self, buf: str, end: int) -> bool:
        if not self._first:
            return True
        return _words(buf[: end + 1]) >= 2 and _words(buf) >= self._first_min_words

    def _last_space(self, limit: int) -> int | None:
        index = self._buffer.rfind(" ", 1, limit + 1)
        return index if index > 0 else None


def _words(text: str) -> int:
    return len(text.split())


def _is_abbreviation(text: str) -> bool:
    """`text` ends with a period; is it "Dr.", "e.g.", an initial or a list number?"""
    word = text.split()[-1].lstrip("(\"'").rstrip(".").lower()
    return word in _ABBREVIATIONS or word.isdigit() or (len(word) == 1 and word.isalpha())


def _speakable(raw: str) -> str | None:
    text = raw.strip()
    return text if any(c.isalnum() for c in text) else None
