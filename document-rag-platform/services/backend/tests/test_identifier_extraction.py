"""Aşama 5.2: extract_identifiers heuristic on sample technical text."""

from __future__ import annotations

from src.infrastructure.retrieval.identifier import extract_identifiers


def test_screaming_snake_column_constant():
    tokens = extract_identifiers("PAYMENT_FLAG 1 olduğunda ne olur?")
    assert "PAYMENT_FLAG" in tokens


def test_pascal_case_class_detected():
    tokens = extract_identifiers("PaymentService katmanı ...")
    assert "PaymentService" in tokens


def test_snake_case_method_column_detected():
    tokens = extract_identifiers("calculate_total fonksiyonu çağrılır")
    assert "calculate_total" in tokens


def test_dotted_package_and_qualified_path():
    tokens = extract_identifiers("services/backend/src/main.py içinde query_chat")
    assert any(t.startswith("services") or "." not in t for t in tokens)
    # Dotted trailing path should surface as one qualified token.
    tokens2 = extract_identifiers("com.example.service.UserService")
    assert any("." in t for t in tokens2)


def test_error_code_and_error_class_detected():
    tokens = extract_identifiers("HTTPError ile E302 koduna bakın")
    assert "HTTPError" in tokens
    assert "E302" in tokens


def test_plain_language_words_not_extracted():
    tokens = extract_identifiers("bu bir günlük sohbet sorusudur")
    # No underscores/uppercase-cascade/qualified tokens present.
    assert tokens == []


def test_returns_deduplicated_unique_tokens():
    tokens = extract_identifiers("PAYMENT_FLAG ve PAYMENT_FLAG tekrar")
    assert tokens.count("PAYMENT_FLAG") == 1


def test_empty_and_none_input():
    assert extract_identifiers("") == []
    assert extract_identifiers(None) == []
