from app.voice.segmenter import SentenceSegmenter


def feed_all(text: str, step: int = 3) -> tuple[list[str], str | None]:
    """Feed in small slices, like LLM token deltas."""
    segmenter = SentenceSegmenter()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += segmenter.feed(text[i : i + step])
    return out, segmenter.flush()


def test_splits_on_sentence_terminators() -> None:
    out, rest = feed_all("Hello there friend. How are you today? I am glad! ")
    assert out == ["Hello there friend.", "How are you today?", "I am glad!"]
    assert rest is None


def test_waits_for_whitespace_after_terminator() -> None:
    segmenter = SentenceSegmenter()
    assert segmenter.feed("It is done.") == []
    assert segmenter.feed(" Next") == ["It is done."]
    assert segmenter.flush() == "Next"


def test_does_not_split_decimals() -> None:
    out, rest = feed_all("The price is 3.5 rupees today. Thanks ")
    assert out == ["The price is 3.5 rupees today."]
    assert rest == "Thanks"


def test_does_not_split_after_abbreviations() -> None:
    out, rest = feed_all("Dr. Rao and Mr. Shah met e.g. at noon. Fine ")
    assert out == ["Dr. Rao and Mr. Shah met e.g. at noon."]
    assert rest == "Fine"


def test_does_not_split_list_numbering() -> None:
    out, _ = feed_all("1. First thing is rest. Then sleep ")
    assert out == ["1. First thing is rest."]


def test_hindi_danda_ends_a_sentence() -> None:
    out, rest = feed_all("मैं ठीक हूँ। आप कैसे हैं")
    assert out == ["मैं ठीक हूँ।"]
    assert rest == "आप कैसे हैं"


def test_newline_ends_a_sentence() -> None:
    out, rest = feed_all("First line\nSecond line")
    assert out == ["First line"]
    assert rest == "Second line"


def test_first_chunk_flushes_early_at_comma_after_four_words() -> None:
    out, rest = feed_all("Well I think that, honestly, it is fine. ")
    assert out == ["Well I think that,", "honestly, it is fine."]
    assert rest is None


def test_no_early_flush_before_four_words() -> None:
    out, _ = feed_all("Well, honestly it is fine. ")
    assert out == ["Well, honestly it is fine."]


def test_early_flush_only_applies_to_the_first_chunk() -> None:
    out, _ = feed_all("It is a nice day. But then again, it is just a day. ")
    assert out == ["It is a nice day.", "But then again, it is just a day."]


def test_em_dash_flushes_early_too() -> None:
    out, rest = feed_all("That sounds really hard—and I am here")
    assert out == ["That sounds really hard—"]
    assert rest == "and I am here"


def test_comma_inside_number_is_not_a_boundary() -> None:
    out, rest = feed_all("It costs about 1,000 rupees in total")
    assert out == []
    assert rest == "It costs about 1,000 rupees in total"


def test_strips_markdown_symbols() -> None:
    out, rest = feed_all("**Great** idea! Try `this` #first ")
    assert out == ["Great idea!"]
    assert rest == "Try this first"


def test_flush_returns_none_when_nothing_speakable() -> None:
    segmenter = SentenceSegmenter()
    segmenter.feed("  ** ")
    assert segmenter.flush() is None


def test_flush_resets_for_the_next_reply() -> None:
    segmenter = SentenceSegmenter()
    segmenter.feed("It is fine.")
    assert segmenter.flush() == "It is fine."
    assert segmenter.feed("aa bb cc dd, ee ff. ") == ["aa bb cc dd,", "ee ff."]
