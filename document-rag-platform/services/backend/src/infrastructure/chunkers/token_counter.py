"""BGE-M3-compatible token counting with a conservative fallback (Aşama 4).

Implements :class:`domain.ports.TokenCounter` (``count(text: str) -> int``)
for the BGE-M3 embedding model. Per AKTIF_GOREV.md Aşama 4:

    "BGE-M3 tokenizer erişilebiliyorsa model uyumlu sayım kullan;
    erişilemiyorsa konservatif fallback uygula ve kullanılan yöntemi
    metadata'da belirt."

The ``transformers`` package (``tokenizers`` / ``AutoTokenizer``) is a heavy
optional dependency. It is never imported at module load time; it is
lazy-imported inside the constructor so this module imports cleanly even when
those packages are absent (e.g. the throwaway test venv). When the real
tokenizer is unavailable we fall back to a deterministic, conservative
(*over-estimating*) estimator. The chosen method is always exposed so the
chunking layer can surface it in ``metadata_json``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

DEFAULT_BGE_M3_MODEL = "BAAI/bge-m3"

#: Token-counting method identifiers surfaced in metadata.
METHOD_TRANSFORMERS = "transformers_bge_m3"
METHOD_TIKTOKEN = "tokenizers_bge_m3"
METHOD_FALLBACK = "conservative_fallback"


@dataclass(frozen=True)
class CountReport:
    """Small descriptor of how a count was produced (for metadata)."""

    token_count: int
    method: str
    tokenizer_name: Optional[str]
    is_exact: bool


def _estimate_conservative(text: str) -> int:
    """Deterministic, conservative (over-estimating) token count.

    Rationale for why this never *under*-counts on multilingual/Turkish text
    (a token-budget guarantee that breaks if we under-count):

    * A subword tokenizer never emits *more* than one token per Unicode
      codepoint, so for every script ``real_tokens <= len(chars)``.
    * For ASCII / Latin text (English, and Turkish without counting subword
      splitting), BGE-M3's XLM-R vocabulary averages roughly 4-5 chars per
      token for English and ~3.5 for agglutinative Turkish. Dividing the
      character count by **3** is therefore already slightly conservative
      (larger than the real count) for Latin scripts.
    * Scripts that tokenize at (or near) one token per character — CJK,
      Hangul, Kana, and assorted non-Latin codepoints (emoji, combining
      marks, math symbols) — would be badly under-counted by a uniform
      ``chars / 3`` floor. Taking the maximum with ``non_ascii`` (a full
      token per non-ASCII codepoint) keeps such scripts at ~1 token/char,
      i.e. at or above their true tokenization density.

    Combining both bounds as ``max(ceil(chars / 3), non_ascii)`` yields an
    upper bound across all scripts while only modestly over-estimating
    plain Latin text (which is exactly the conservative behaviour we want).

    Never returns 0 for non-empty input.
    """
    if not text:
        return 0

    chars = len(text)
    non_ascii = 0
    for ch in text:
        if ord(ch) > 127:
            non_ascii += 1

    estimate = max(math.ceil(chars / 3), non_ascii)
    return max(estimate, 1)


class BgeM3TokenCounter:
    """Counts tokens for :class:`domain.ports.TokenCounter`.

    ``use_transformers`` controls which path is used:

    * ``None`` — auto-detect: use the real BGE-M3 tokenizer if the heavy
      ``transformers`` dependency is importable, otherwise fall back.
    * ``False`` — always use the conservative fallback (deterministic; used
      by tests to pin behaviour regardless of installed dependencies).
    * ``True`` — require the real tokenizer; raise ``ImportError`` if it
      cannot be loaded.

    The counter is intended to be constructed once and reused across many
    ``count()`` calls. ``count()`` only reads the (immutable) tokenizer or
    performs pure arithmetic, so a single instance is safe to share across
    worker threads.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_BGE_M3_MODEL,
        use_transformers: Optional[bool] = None,
    ):
        self.model_name = model_name
        self._tokenizer = None
        self.method: str = METHOD_FALLBACK
        self.tokenizer_name: Optional[str] = None
        self.is_exact: bool = False

        if use_transformers is True:
            self._tokenizer = _load_bge_m3_tokenizer(model_name)
            self.method = METHOD_TRANSFORMERS
            self.tokenizer_name = model_name
            self.is_exact = True
        elif use_transformers is False:
            # Fallback pinned explicitly (tests, or operators who want the
            # deterministic estimator even when transformers is present).
            self.method = METHOD_FALLBACK
            self.tokenizer_name = None
        else:
            # Auto: prefer the exact tokenizer when available.
            try:
                self._tokenizer = _load_bge_m3_tokenizer(model_name)
            except Exception:
                self._tokenizer = None
            if self._tokenizer is not None:
                self.method = METHOD_TRANSFORMERS
                self.tokenizer_name = model_name
                self.is_exact = True
            else:
                self.method = METHOD_FALLBACK
                self.tokenizer_name = None

    def count(self, text: str) -> int:
        """Returns a plain ``int`` token count for ``text`` (port-compatible)."""
        tokenizer = self._tokenizer
        if tokenizer is not None:
            # ``encode`` without add_special_tokens yields just the content
            # tokens, matching the tokenization the embedding gateway sees.
            return len(
                tokenizer.encode(text, add_special_tokens=False)
            )
        return _estimate_conservative(text)

    def report(self, text: str) -> CountReport:
        """Returns a token count together with the method used (for metadata)."""
        return CountReport(
            token_count=self.count(text),
            method=self.method,
            tokenizer_name=self.tokenizer_name,
            is_exact=self.is_exact,
        )

    def describe(self) -> dict:
        """Returns the *method* descriptor for metadata (independent of text)."""
        return {
            "method": self.method,
            "tokenizer_name": self.tokenizer_name,
            "is_exact": self.is_exact,
            "model_name": self.model_name,
        }


def _load_bge_m3_tokenizer(model_name: str):
    """Lazily imports and returns a BGE-M3 tokenizer, or raises.

    ``transformers`` is intentionally imported here (and only here) so the
    module-level import of this file never requires the heavy dependency.
    """
    from transformers import AutoTokenizer  # heavy, optional

    return AutoTokenizer.from_pretrained(model_name)
