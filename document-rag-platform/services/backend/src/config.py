"""Centralized, typed application configuration.

Every environment variable the backend reads lives here as a field on
``Settings``. Other modules should import the module-level ``settings``
singleton instead of calling ``os.getenv`` / ``os.environ`` directly, so
there is exactly one place that knows what configuration the app needs.

Env var names and default values are intentionally unchanged from the
previous ad-hoc ``os.getenv`` calls scattered across ``db.py`` / ``llm.py``
— this module only centralizes and types them.
"""

from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------
    # Required. There is no safe default for a real deployment; if it's
    # missing we want a loud, immediate startup failure rather than
    # silently trying to reach a bogus localhost database.
    DATABASE_URL: str

    # --- LLM gateway (LiteLLM-compatible) ------------------------------
    LITELLM_BASE_URL: str = "https://aihub-api.turktelekom.com.tr/v1"
    # Required. There is no default credential; failing fast at startup
    # is clearer than a downstream 401 from the gateway mid-request.
    LITELLM_API_KEY: str
    EMBEDDING_MODEL: str = "openai/BAAI/bge-m3"
    CHAT_MODEL: str = "Qwen/Qwen3.5-27B-FP8"
    # Comma-separated allow-list of chat models the UI may request.
    # None (unset) means "fall back to CHAT_MODEL only".
    CHAT_MODELS: Optional[str] = None

    @property
    def available_chat_models(self) -> List[str]:
        """Parsed CHAT_MODELS allow-list, falling back to [CHAT_MODEL]."""
        raw = self.CHAT_MODELS if self.CHAT_MODELS is not None else self.CHAT_MODEL
        models = [m.strip() for m in raw.split(",") if m.strip()]
        return models or [self.CHAT_MODEL]


settings = Settings()
