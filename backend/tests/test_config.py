import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("http://a.test, https://b.test", ["http://a.test", "https://b.test"]),
        ('["https://b.test"]', ["https://b.test"]),
    ],
)
def test_cors_origins_accept_bare_comma_separated_and_json(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected


def test_long_conversation_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.llm_max_tokens == 400 and settings.llm_long_max_tokens == 1200
    assert settings.session_max_duration_s == 3600
    assert settings.contest_timeout_s == 3.0
