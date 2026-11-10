from app.voice.catalog import LANGUAGES, VOICES
from app.voice.samples import SAMPLE_TEXT, TWO_WORDS, sample_for


def test_every_language_has_a_native_script_sample_and_two_word_phrase() -> None:
    assert set(SAMPLE_TEXT) == set(LANGUAGES) == set(TWO_WORDS)
    for code in LANGUAGES:
        assert len(SAMPLE_TEXT[code].split()) >= 3
        assert len(TWO_WORDS[code].split()) == 2
    assert SAMPLE_TEXT["hi-IN"].isascii() is False and SAMPLE_TEXT["en-IN"].isascii()


def test_sample_for_covers_every_voice_language() -> None:
    for voice in VOICES:
        assert sample_for(voice.language)
