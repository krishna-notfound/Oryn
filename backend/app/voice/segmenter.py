"""Turns a stream of LLM text deltas into speakable chunks for TTS."""

from __future__ import annotations

_MARKDOWN = str.maketrans("", "", "*#`")
_HARD_END = "\n।"  # a newline or Devanagari danda always ends a chunk
_SOFT_END = ".!?"  # ends a chunk only when followed by whitespace ("3.5" stays whole)
_EARLY_END = ",;—"  # allowed once, on the first chunk, to start speaking sooner
_EARLY_MIN_WORDS = 4
_ABBREVIATIONS = frozenset({"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "e.g", "i.e"})


class SentenceSegmenter:
    """Feed deltas, get complete chunks. The first chunk of a reply may end at a
    clause boundary so the voice starts earlier; every later chunk is a sentence."""

    def __init__(self) -> None:
        self._buffer = ""
        self._first = True

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta.translate(_MARKDOWN)
        chunks: list[str] = []
        while (end := self._boundary()) is not None:
            raw, self._buffer = self._buffer[:end], self._buffer[end:]
            if chunk := _speakable(raw):
                chunks.append(chunk)
                self._first = False
        return chunks

    def flush(self) -> str | None:
        """Return whatever is left at the end of the reply and reset for the next one."""
        rest = _speakable(self._buffer)
        self._buffer = ""
        self._first = True
        return rest

    def _boundary(self) -> int | None:
        buf = self._buffer
        for i, ch in enumerate(buf):
            following = buf[i + 1 : i + 2]
            if ch in _HARD_END:
                return i + 1
            if ch in _SOFT_END and following.isspace() and not _is_abbreviation(buf[: i + 1]):
                return i + 1
            if (
                self._first
                and ch in _EARLY_END
                and (ch == "—" or following.isspace())
                and len(buf[:i].split()) >= _EARLY_MIN_WORDS
            ):
                return i + 1
        return None


def _is_abbreviation(text: str) -> bool:
    """`text` ends with a period; is it "Dr.", "e.g.", an initial or a list number?"""
    word = text.split()[-1].lstrip("(\"'").rstrip(".").lower()
    return word in _ABBREVIATIONS or word.isdigit() or (len(word) == 1 and word.isalpha())


def _speakable(raw: str) -> str | None:
    text = raw.strip()
    return text if any(c.isalnum() for c in text) else None
