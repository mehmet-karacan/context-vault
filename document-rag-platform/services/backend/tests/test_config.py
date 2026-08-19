"""Aşama 1 kabul kriteri: "Typed settings doğrulaması eksik zorunlu
credential'da açık hata verir."

Exercises ``src.config.Settings`` directly (not the module-level ``settings``
singleton, which is already constructed by the time tests run) so each test
can freely add/remove env vars without needing to reload the module. No
database or LLM-gateway connection is made anywhere in this file — this is
pure field validation.
"""

import pytest
from pydantic import ValidationError

from src.config import Settings


def _error_fields(exc_info) -> set:
    return {err["loc"][0] for err in exc_info.value.errors()}


def test_missing_database_url_raises_validation_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LITELLM_API_KEY", "dummy-key")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "DATABASE_URL" in _error_fields(exc_info)


def test_missing_litellm_api_key_raises_validation_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "LITELLM_API_KEY" in _error_fields(exc_info)


def test_missing_both_required_fields_reports_both(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    fields = _error_fields(exc_info)
    assert "DATABASE_URL" in fields
    assert "LITELLM_API_KEY" in fields


def test_settings_construct_succeeds_with_required_fields_present(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("LITELLM_API_KEY", "dummy-key")
    monkeypatch.delenv("CHAT_MODELS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.DATABASE_URL == "postgresql://user:pass@host:5432/db"
    assert settings.LITELLM_API_KEY == "dummy-key"
    # Defaults are preserved when not overridden by the environment.
    assert settings.EMBEDDING_MODEL == "openai/BAAI/bge-m3"
    assert settings.CHAT_MODEL == "Qwen/Qwen3.5-27B-FP8"


def test_available_chat_models_falls_back_to_chat_model(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("LITELLM_API_KEY", "dummy-key")
    monkeypatch.delenv("CHAT_MODELS", raising=False)
    monkeypatch.setenv("CHAT_MODEL", "some/default-model")

    settings = Settings(_env_file=None)

    assert settings.available_chat_models == ["some/default-model"]


def test_available_chat_models_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("LITELLM_API_KEY", "dummy-key")
    monkeypatch.setenv("CHAT_MODELS", "model-a, model-b ,model-c")

    settings = Settings(_env_file=None)

    assert settings.available_chat_models == ["model-a", "model-b", "model-c"]
