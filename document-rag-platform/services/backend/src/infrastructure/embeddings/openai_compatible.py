"""OpenAI-compatible embedding adapter (Aşama 1.3).

Calls an OpenAI-compatible gateway's ``/v1/embeddings`` endpoint. This is
the concrete implementation behind ``domain.ports.EmbeddingProvider`` —
moved out of the former monolithic ``llm.py`` without changing any
request/response behavior (model, prefixing, endpoint, parameters).
"""

from __future__ import annotations

from typing import List

from openai import OpenAI


class OpenAICompatibleEmbeddingProvider:
    """Produces dense embedding vectors via an OpenAI-compatible gateway.

    Conforms to ``domain.ports.EmbeddingProvider`` (``embed`` /
    ``embed_one``), with an additional optional ``instruction`` prefix used
    for asymmetric instruction-aware embedding models (e.g. BGE-M3).
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def embed_one(self, text: str, instruction: str = "") -> List[float]:
        # This gateway's /v1/embeddings only accepts a single string per
        # call, not a batch array — so callers must loop for multiple
        # chunks (see embed()).
        response = self._client.embeddings.create(
            model=self._model, input=f"{instruction}{text}"
        )
        return response.data[0].embedding

    def embed(self, texts: List[str], instruction: str = "") -> List[List[float]]:
        return [self.embed_one(t, instruction) for t in texts]
