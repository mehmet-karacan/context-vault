"""Thin facade over the embedding and chat/generation adapters (Aşama 1.3).

Historically this module held both the embedding logic and the chat/RAG
generation logic inline. Both have been moved to dedicated adapters:

- ``infrastructure.embeddings.openai_compatible.OpenAICompatibleEmbeddingProvider``
- ``infrastructure.llm.chat_client.ChatCompletionClient``

This module now only instantiates them and re-exports the original
function names/signatures (``embed_text``, ``embed_texts``,
``generate_answer``) plus the constants callers rely on
(``AVAILABLE_CHAT_MODELS``, ``CHAT_MODEL``, ``PASSAGE_INSTRUCTION``,
``QUERY_INSTRUCTION``) so ``api/v1/chat.py`` and ``api/v1/documents.py``
keep working unchanged.
"""

from typing import List, Optional

from .config import settings
from .infrastructure.embeddings.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
)
from .infrastructure.llm.chat_client import ChatCompletionClient

GATEWAY_BASE_URL = settings.LITELLM_BASE_URL
GATEWAY_API_KEY = settings.LITELLM_API_KEY
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
CHAT_MODEL = settings.CHAT_MODEL

# Allow-list of models the chat UI may request — a comma-separated CHAT_MODELS
# env var, falling back to just the single default CHAT_MODEL.
AVAILABLE_CHAT_MODELS = settings.available_chat_models

# Asymmetric instruction prefixes: passages and queries are embedded
# differently so the query vector points at what a passage *about* the
# query would look like, rather than a paraphrase of the query itself.
# Improves retrieval quality with instruction-aware models like BGE-M3.
PASSAGE_INSTRUCTION = "Bu metni bir belge arama sisteminde bulunmak üzere temsil et: "
QUERY_INSTRUCTION = "Bu soruyu ilgili belge parçalarını bulmak için temsil et: "

_embedding_provider = OpenAICompatibleEmbeddingProvider(
    base_url=GATEWAY_BASE_URL, api_key=GATEWAY_API_KEY, model=EMBEDDING_MODEL
)

_chat_client = ChatCompletionClient(
    base_url=GATEWAY_BASE_URL,
    api_key=GATEWAY_API_KEY,
    default_model=CHAT_MODEL,
    available_models=AVAILABLE_CHAT_MODELS,
)


def embed_text(text: str, instruction: str = "") -> List[float]:
    return _embedding_provider.embed_one(text, instruction)

def embed_texts(texts: List[str], instruction: str = "") -> List[List[float]]:
    return _embedding_provider.embed(texts, instruction)


def generate_answer(
    query: str, context_chunks: List[str], model: Optional[str] = None
) -> str:
    return _chat_client.generate_answer(query, context_chunks, model)


# Public handle to the shared ChatCompletionClient so Aşama 6 callers can use
# its prompt-controlled ``.complete`` API without touching the private field.
chat_client = _chat_client
