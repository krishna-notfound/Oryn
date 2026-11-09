from app.voice.segmenter import SentenceSegmenter


def feed_all(text: str, step: int = 3) -> tuple[list[str], str | None]:
    """Feed in small slices, like LLM token deltas."""
    segmenter = SentenceSegmenter()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += segmenter.feed(text[i : i + step], 0.0)
    return out, segmenter.flush()


def test_splits_on_sentence_terminators() -> None:
    out, rest = feed_all("Hello there friend. How are you today? I am glad! ")
    assert out == ["Hello there friend.", "How are you today?", "I am glad!"]
    assert rest is None


def test_waits_for_whitespace_after_terminator() -> None:
    segmenter = SentenceSegmenter()
    assert segmenter.feed("It is done.", 0.0) == []
    assert segmenter.feed(" Next", 0.0) == ["It is done."]
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


def test_first_chunk_flushes_early_at_a_clause_boundary() -> None:
    out, rest = feed_all("Well I think that, honestly, it is fine. ")
    assert out == ["Well I think that,", "honestly, it is fine."]
    assert rest is None


def test_one_word_clause_is_not_flushed_alone() -> None:
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
    segmenter.feed("  ** ", 0.0)
    assert segmenter.flush() is None


def test_flush_resets_for_the_next_reply() -> None:
    segmenter = SentenceSegmenter()
    segmenter.feed("It is fine.", 0.0)
    assert segmenter.flush() == "It is fine."
    assert segmenter.feed("aa bb cc dd, ee ff gg. ", 0.0) == ["aa bb cc dd,", "ee ff gg."]


# ---------------------------------------------------------------- v2 rules


def test_first_flush_at_clause_once_next_word_started() -> None:
    segmenter = SentenceSegmenter()
    assert segmenter.feed("Haan bilkul, ", 0.0) == []  # the next word has not started yet
    assert segmenter.feed("main", 0.0) == ["Haan bilkul,"]


def test_time_based_first_flush_needs_two_words() -> None:
    segmenter = SentenceSegmenter()
    t0 = 100.0
    assert segmenter.feed("Okay", t0) == []
    assert segmenter.poll(t0 + 0.26) == []  # a single word is not worth a Timbre request
    assert segmenter.feed(" so", t0 + 0.27) == []
    assert segmenter.poll(t0 + 0.27) == ["Okay so"]


def test_poll_flushes_first_segment_after_the_wait() -> None:
    segmenter = SentenceSegmenter()
    t0 = 100.0
    segmenter.feed("Okay so", t0)
    assert segmenter.deadline == t0 + 0.25
    assert segmenter.poll(t0 + 0.2) == []
    assert segmenter.poll(t0 + 0.26) == ["Okay so"]
    assert segmenter.deadline is None  # only the first segment is timed
    assert segmenter.poll(t0 + 5) == []


def test_abbreviation_and_decimal_stay_in_one_segment() -> None:
    out, rest = feed_all("Dr. Rao paid 3.5 lakh.")
    assert out == [] and rest == "Dr. Rao paid 3.5 lakh."


def test_long_text_is_split_at_a_space_within_the_limit() -> None:
    segmenter = SentenceSegmenter()
    out = segmenter.feed("word " * 40, 0.0)  # 200 chars, no punctuation
    assert out and all(len(segment) <= 160 for segment in out)
    assert " ".join(out + [segmenter.flush() or ""]).split() == ["word"] * 40


def test_long_sentence_with_a_late_terminator_is_still_capped() -> None:
    segmenter = SentenceSegmenter()
    out = segmenter.feed("word " * 39 + "word. Next", 0.0)
    assert all(len(segment) <= 160 for segment in out)
    assert len(out) >= 2


def test_short_sentence_merges_into_the_next_one() -> None:
    segmenter = SentenceSegmenter()
    assert segmenter.feed("Here is the plan for today. ", 0.0) == ["Here is the plan for today."]
    assert segmenter.feed("Ok. Then we start with breakfast. ", 0.0) == [
        "Ok. Then we start with breakfast."
    ]


def test_short_final_tail_is_emitted_by_flush() -> None:
    segmenter = SentenceSegmenter()
    segmenter.feed("Here is the plan for today. Ok. ", 0.0)
    assert segmenter.flush() == "Ok."


def test_devanagari_danda_is_a_boundary_for_the_first_segment() -> None:
    segmenter = SentenceSegmenter()
    assert segmenter.feed("मैं यहाँ हूँ। आप", 0.0) == ["मैं यहाँ हूँ।"]
