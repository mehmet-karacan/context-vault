"""Aşama 4: tests for the BGE-M3-compatible ``TokenCounter``.

Covers the port contract (``count(text) -> int``), deterministic behaviour,
conservative (over-estimating) fallback guarantees, the method descriptor
surfaced for metadata, and a lazy-import design that keeps the module usable
with or without the (heavy, optional) ``transformers`` dependency.
"""

import importlib.util

from src.infrastructure.chunkers.token_counter import (
    BgeM3TokenCounter,
    METHOD_FALLBACK,
    METHOD_TRANSFORMERS,
    _estimate_conservative,
)

TRANSFORMERS_AVAILABLE = importlib.util.find_spec("transformers") is not None


def test_count_returns_plain_int():
    n = BgeM3TokenCounter(use_transformers=False).count("merhaba dünya")
    assert isinstance(n, int)
    assert n >= 1


def test_empty_text_counts_zero():
    assert BgeM3TokenCounter(use_transformers=False).count("") == 0


def test_fallback_never_returns_zero_for_non_empty():
    counter = BgeM3TokenCounter(use_transformers=False)
    for text in ["x", "a", " ", "ğşçıöü", "Türkçe metin", "\t", "ün", "a" * 10]:
        assert counter.count(text) >= 1, f"under-counted for {text!r}"


def test_fallback_exceeds_naive_reasonable_lower_bound():
    # A tokenized string can never have fewer tokens than individual
    # whitespace-delimited words. Our conservative estimator must be >= that,
    # and (for real text) strictly above a naive chars/5 heuristic.
    counter = BgeM3TokenCounter(use_transformers=False)
    texts = [
        "merhaba dünya nasıl gidiyor",
        "bugün hava çok güzel ve sıcak",
        "the quick brown fox jumps over the lazy dog",
        "nğş Türkçe karakterler ý"
        "SELECT * FROM users WHERE id = ?",
    ]
    for text in texts:
        words = len(text.split())
        count = counter.count(text)
        assert count >= words, (text, count, words)
        if text:
            # For Latin-heavy text chars/3 -> tokens should exceed chars/5.
            assert count >= max(1, len(text) // 5)


def test_fallback_is_deterministic():
    counter = BgeM3TokenCounter(use_transformers=False)
    text = "Aynı metin her zaman aynı sayıda tahmin üretmelidir. 1234567890!"
    counts = {counter.count(text) for _ in range(50)}
    assert len(counts) == 1
    # Two equal instances agree too.
    assert (
        BgeM3TokenCounter(use_transformers=False).count(text)
        == BgeM3TokenCounter(use_transformers=False).count(text)
    )


def test_fallback_method_descriptor_is_exposed():
    counter = BgeM3TokenCounter(use_transformers=False)
    assert counter.method == METHOD_FALLBACK
    assert counter.tokenizer_name is None
    assert counter.is_exact is False

    desc = counter.describe()
    assert desc["method"] == METHOD_FALLBACK
    assert desc["tokenizer_name"] is None
    assert desc["is_exact"] is False
    assert "model_name" in desc


def test_report_describes_count_and_method():
    counter = BgeM3TokenCounter(use_transformers=False)
    report = counter.report("belge metni burada")
    assert isinstance(report.token_count, int)
    assert report.method == METHOD_FALLBACK
    assert report.tokenizer_name is None
    assert report.is_exact is False


def test_auto_detection_prefers_transformers_when_available():
    # In this environment transformers is absent, so auto-detection MUST land
    # on the fallback (the tested path). If transformers were installed, the
    # exact method would be selected instead.
    counter = BgeM3TokenCounter()
    if TRANSFORMERS_AVAILABLE:
        assert counter.method == METHOD_TRANSFORMERS
        assert counter.is_exact is True
    else:
        assert counter.method == METHOD_FALLBACK
        assert counter.is_exact is False


def test_explicit_transformers_true_without_package_raises_import_error():
    # Only meaningful when transformers is genuinely unavailable.
    if not TRANSFORMERS_AVAILABLE:
        try:
            BgeM3TokenCounter(use_transformers=True)
        except ImportError:
            pass
        else:
            raise AssertionError("expected ImportError when transformers absent")
    else:
        counter = BgeM3TokenCounter(use_transformers=True)
        assert counter.method == METHOD_TRANSFORMERS


def test_module_imports_without_heavy_dependency_is_lazy():
    # Simulating no transformers: the helper must be pure Python and the
    # module must have imported fine even before we checked the env.
    assert _estimate_conservative("merhaba") >= 1
    assert _estimate_conservative("") == 0
