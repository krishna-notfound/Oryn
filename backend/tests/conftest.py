from collections.abc import AsyncIterator

import pytest
from support import Harness

from app.config import Settings


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls the real provider APIs (needs keys in the env)")


@pytest.fixture
def live_settings() -> Settings:
    """Real settings from the environment/.env; skips the test when keys are missing."""
    settings = Settings()
    required = {
        "GNANI_API_KEY": settings.gnani_api_key,
        "GNANI_TTS_VOICE": settings.gnani_tts_voice,
        "SARVAM_API_KEY": settings.sarvam_api_key,
    }
    if missing := [name for name, value in required.items() if not value]:
        pytest.skip(f"live providers need {', '.join(missing)}")
    return settings


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    h = Harness()
    yield h
    for engine in h.engines:
        await engine.close()
