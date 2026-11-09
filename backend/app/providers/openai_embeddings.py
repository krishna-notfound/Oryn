"""Embeddings from any OpenAI-compatible `POST {base}/embeddings` endpoint."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import Embedder, ProviderError
from app.providers.http import error_from_response

NAME = "openai-embeddings"


class OpenAICompatibleEmbedder(Embedder):
    name = NAME

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._client = client
        self._model = settings.embeddings_model
        self._url = f"{settings.embeddings_base_url.rstrip('/')}/embeddings"
        self._headers = (
            {"Authorization": f"Bearer {settings.embeddings_api_key.get_secret_value()}"}
            if settings.embeddings_api_key
            else {}
        )
        # Declared up front: the memory store sizes its vector index from it at startup.
        self.dimensions = settings.embeddings_dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                self._url, json={"model": self._model, "input": list(texts)}, headers=self._headers
            )
        except httpx.TransportError as exc:
            raise ProviderError(NAME, f"transport error: {exc!r}", retryable=True) from exc
        if response.status_code != 200:
            raise await error_from_response(NAME, response)
        rows: list[dict[str, Any]] = sorted(response.json()["data"], key=lambda r: r["index"])
        vectors = [row["embedding"] for row in rows]
        if len(vectors) != len(texts):
            raise ProviderError(NAME, f"expected {len(texts)} embeddings, got {len(vectors)}")
        if len(vectors[0]) != self.dimensions:
            raise ProviderError(
                NAME, f"model returned {len(vectors[0])}-d vectors, configured {self.dimensions}"
            )
        return vectors
