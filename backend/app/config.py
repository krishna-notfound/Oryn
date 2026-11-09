"""Application settings. The only place that reads the environment."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    provider_mode: Literal["live", "fake"] = "live"
    # Env value may be a bare origin, a comma-separated list, or a JSON array.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    log_level: str = "INFO"

    # Supabase
    supabase_url: str = ""
    supabase_jwt_secret: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None

    # Gnani — one key for Prisma (STT) and Timbre (TTS)
    gnani_api_key: SecretStr | None = None
    gnani_base_url: str = "https://api.vachana.ai"
    gnani_stt_ws_url: str = "wss://api.vachana.ai/stt/v3/stream"
    gnani_stt_model: str = "prisma-v3.0"
    gnani_stt_format: str = "codemix"
    gnani_tts_model: str = "timbre-v2.5"
    gnani_tts_voice: str = ""
    gnani_tts_sample_rate: int = 24_000

    # Sarvam conversational LLM
    sarvam_api_key: SecretStr | None = None
    sarvam_base_url: str = "https://api.sarvam.ai"
    llm_model: str = ""
    llm_temperature: float = 0.6
    llm_max_tokens: int = 300
    llm_timeout_s: float = 20.0

    # Embeddings (OpenAI-compatible) for memory retrieval
    embeddings_base_url: str = ""
    embeddings_api_key: SecretStr | None = None
    embeddings_model: str = ""
    embeddings_dimensions: int = 0  # must match the model; sizes the vector index

    # Memory
    memory_db_path: Path = Path("./data/memory.db")

    # Conversation / session limits
    context_max_turns: int = 12
    max_tool_calls_per_turn: int = 3
    session_handshake_timeout_s: float = 5.0
    session_max_duration_s: float = 15 * 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
