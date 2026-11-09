"""Embeddings from any OpenAI-compatible `POST {base}/embeddings` endpoint.

Verified live against Gemini's compat endpoint, which differs from OpenAI in two ways: rows
carry no `index` (they come back in input order) and the vector size defaults to 3072, so the
configured `dimensions` is always sent. Truncated Gemini vectors are not unit length, so every
vector is normalised: the memory index ranks by L2 distance, which then orders like cosine.
"""

from __future__ import annotations

import math
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
                self._url,
                json={"model": self._model, "input": list(texts), "dimensions": self.dimensions},
                headers=self._headers,
            )
        except httpx.TransportError as exc:
            raise ProviderError(NAME, f"transport error: {exc!r}", retryable=True) from exc
        if response.status_code != 200:
            raise await error_from_response(NAME, response)
        rows: list[dict[str, Any]] = response.json()["data"]
        rows.sort(key=lambda row: row.get("index", 0))  # stable: no index means input order
        vectors = [_normalised(row["embedding"]) for row in rows]
        if len(vectors) != len(texts):
            raise ProviderError(NAME, f"expected {len(texts)} embeddings, got {len(vectors)}")
        if len(vectors[0]) != self.dimensions:
            raise ProviderError(
                NAME, f"model returned {len(vectors[0])}-d vectors, configured {self.dimensions}"
            )
        return vectors


def _normalised(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector
