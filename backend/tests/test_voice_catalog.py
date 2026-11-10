from app.voice.catalog import DEFAULT_VOICE, LANGUAGES, VOICES, find_voice, resolve_voice


def test_catalog_has_all_42_voices_and_a_default_per_language() -> None:
    assert len(VOICES) == 42
    assert set(DEFAULT_VOICE) == set(LANGUAGES)
    assert all(find_voice(name) for name in DEFAULT_VOICE.values())


def test_preferred_voice_is_kept_for_its_own_language() -> None:
    assert resolve_voice("Nalini", "hi-IN") == "Nalini"
    assert resolve_voice("poorvi", "en-IN") == "Poorvi"  # Hinglish voice, case-insensitive


def test_other_language_falls_back_to_same_gender_voice() -> None:
    assert resolve_voice("Deepak", "ta-IN") == DEFAULT_VOICE["ta-IN"]  # Tamil has no male voice
    assert resolve_voice("Deepak", "kn-IN") == "Kavin"
    assert resolve_voice("Nalini", "te-IN") == "Lavanya"


def test_unknown_or_missing_voice_uses_language_default() -> None:
    assert resolve_voice(None, "bn-IN") == "Kirra"
    assert resolve_voice("NoSuchVoice", "gu-IN") == "Falak"
