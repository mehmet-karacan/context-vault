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

    # --- Object storage (MinIO / S3-compatible) ------------------------
    # Required. docker-compose.yml already injects ENDPOINT/ACCESS_KEY/
    # SECRET_KEY into the backend and worker containers; BUCKET has a
    # sensible default so existing .env files without it don't break.
    MINIO_ENDPOINT: str = "http://minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "context-vault"

    # --- Task queue (Redis / Celery) -----------------------------------
    # Used as both Celery broker and result backend. docker-compose.yml
    # already injects this into the ``backend`` and ``worker`` containers;
    # the default matches the docker-network hostname used there so a bare
    # `docker compose up` works without an explicit .env override.
    REDIS_URL: str = "redis://redis:6379"

    # --- Ingestion worker (Aşama 2.5) -------------------------------------
    # Max number of Celery task retries for a single ingestion job, beyond
    # the initial attempt. Transient failures (embeddings gateway timeouts,
    # MinIO blips, DB connection drops) are retried with exponential backoff;
    # permanent validation errors (missing version/document, empty content)
    # are never retried.
    INGESTION_MAX_RETRIES: int = 3
    # Base (first) retry delay in seconds; doubled after each retry.
    INGESTION_RETRY_BACKOFF_SECONDS: float = 10.0
    # Backoff is capped so an 3-retry job never sleeps for hours.
    INGESTION_RETRY_BACKOFF_MAX_SECONDS: float = 300.0
    # Hard and soft Celery time limits for ingestion tasks (seconds). Real
    # parse/embed of a large document can legitimately take minutes, so these
    # must stay comfortably above the long-running embedding window rather
    # than being treated as a cheap request timeout.
    INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS: int = 3600
    INGESTION_TASK_TIME_LIMIT_SECONDS: int = 3700

    # --- Features --------------------------------------------------------
    # Aşama 2.4: upload endpoint returns immediately with a queued
    # IngestionJob instead of blocking on parse+chunk+embed. Default True
    # per AKTIF_GOREV.md §11; set to False to fall back to the old fully
    # synchronous upload behavior (kept for transition/rollback safety).
    FEATURE_ASYNC_INGESTION: bool = True

    # --- Parsing (Aşama 3.1) ----------------------------------------------
    # Per-file wall-clock budget for a single parser call (seconds). A parse
    # exceeding this is aborted by the ParserRouter as a hard timeout rather
    # than a soft deadline. Defaults per AKTIF_GOREV.md §11.
    PARSER_TIMEOUT_SECONDS: float = 300.0
    # Upper bound on total characters a normalized parse may produce across
    # all ContentUnit texts. A parser returning more is treated by the
    # ParserRouter as an error (bounded output, protects downstream chunkers).
    MAX_PARSED_TEXT_CHARS: int = 20000000

    @property
    def available_chat_models(self) -> List[str]:
        """Parsed CHAT_MODELS allow-list, falling back to [CHAT_MODEL]."""
        raw = self.CHAT_MODELS if self.CHAT_MODELS is not None else self.CHAT_MODEL
        models = [m.strip() for m in raw.split(",") if m.strip()]
        return models or [self.CHAT_MODEL]


settings = Settings()
