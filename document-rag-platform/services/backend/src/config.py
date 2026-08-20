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

# Development origins used by default for CORS when APP_ENV=development and
# no explicit CORS_ALLOW_ORIGINS is configured (AKTIF_GOREV.md §9.5: CORS'u
# üretim için `*` bırakmama).
DEV_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Runtime environment / security (Aşama 9.4-9.5 / §11) ----------
    # APP_ENV selects runtime behaviour (development | production). API_DEBUG
    # and FEATURE_RETRIEVAL_DEBUG are only honored outside production so a
    # production deployment can never accidentally expose stack traces or the
    # retrieval-debug surface (AKTIF_GOREV.md §9.5 / §11).
    APP_ENV: str = "development"
    API_DEBUG: bool = False
    FEATURE_RETRIEVAL_DEBUG: bool = True
    # Comma-separated allow-list of CORS origins. Defaults to DEV_CORS_ORIGINS
    # in development and to an empty (restrictive) list in production — never
    # "*" (AKTIF_GOREV.md §9.5: "CORS'u üretim için `*` bırakmama").
    CORS_ALLOW_ORIGINS: Optional[str] = None

    # --- Rate limiting (Aşama 9.5) --------------------------------------
    # Lightweight in-memory sliding-window rate limiter keyed by client IP,
    # applied to the expensive chat/upload/retrieval endpoints. Relaxed by
    # default (disabled) so existing deployments are unaffected; enable via
    # env in production (§11 style).
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_MAX_REQUESTS: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_KEY_PREFIX: str = "rl"

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

    # --- Reranking (Aşama 5.4) ----------------------------------------------
    # Feature-gated, remote-capable reranker. Off by default: when the feature
    # is disabled or the provider is "none", a pass-through NoopReranker is
    # used so retrieval always has a safe fusion-order fallback (AKTIF_GOREV.md
    # §11 / §16). FEATURE_RERANKER gates the whole pipeline; RERANKER_ENABLED
    # additionally gates the active rerank call; RERANKER_PROVIDER selects the
    # adapter (none | remote); RERANKER_MODEL is the rerank model and
    # RERANK_TOP_K the number of re-ranked candidates to keep.
    FEATURE_RERANKER: bool = False
    RERANKER_ENABLED: bool = False
    RERANKER_PROVIDER: str = "none"
    RERANKER_MODEL: str = ""
    RERANK_TOP_K: int = 8

    # --- OCR (Aşama 8.4) ------------------------------------------------------
    # Minimum OCR confidence (0..1) below which a recognized image/PDF-page
    # result is flagged ``needs_review`` in normalized-content metadata so a
    # human can verify it (AKTIF_GOREV.md §8.4).
    OCR_MIN_CONFIDENCE: float = 0.5

    # --- Features --------------------------------------------------------
    # Aşama 2.4: upload endpoint returns immediately with a queued
    # IngestionJob instead of blocking on parse+chunk+embed. Default True
    # per AKTIF_GOREV.md §11; set to False to fall back to the old fully
    # synchronous upload behavior (kept for transition/rollback safety).
    FEATURE_ASYNC_INGESTION: bool = True

    # Aşama 6: evidence-packaged, citation-persisting chat answers
    # (AKTIF_GOREV.md §6 / §12.4 / §16). Gates the new labeled-evidence prompt
    # packaging and ``message_citations`` DB persistence behind a feature flag
    # so the old chat path can be restored for rollback safety.
    FEATURE_NEW_CITATIONS: bool = True

    # Aşama 3: structure-preserving document parsing (AKTIF_GOREV.md §3 / §16).
    # This field is declared so every mandatory §16 rollback flag exists in
    # config, but the current implementation is ALWAYS-ON — the flag is not yet
    # wired into the parser pipeline (no gate exists today). It is the rollback
    # switch for future trimming: flipping it to False would let a deployment
    # disable structured parsing without code change. Default True because the
    # feature is live right now.
    FEATURE_STRUCTURED_PARSING: bool = True

    # Aşama 5: hybrid (dense + lexical + identifier, RRF-fused) retrieval
    # (AKTIF_GOREV.md §5 / §16). Like FEATURE_STRUCTURED_PARSING, this field is
    # declared so the mandatory §16 rollback flag exists in config, but the
    # current implementation is ALWAYS-ON — retrieval_service.py does not gate
    # on it today. It is the rollback switch for future trimming; the default is
    # True because hybrid retrieval is live right now.
    FEATURE_HYBRID_RETRIEVAL: bool = True

    # --- Parsing (Aşama 3.1) ----------------------------------------------
    # Per-file wall-clock budget for a single parser call (seconds). A parse
    # exceeding this is aborted by the ParserRouter as a hard timeout rather
    # than a soft deadline. Defaults per AKTIF_GOREV.md §11.
    PARSER_TIMEOUT_SECONDS: float = 300.0
    # Upper bound on total characters a normalized parse may produce across
    # all ContentUnit texts. A parser returning more is treated by the
    # ParserRouter as an error (bounded output, protects downstream chunkers).
    MAX_PARSED_TEXT_CHARS: int = 20000000

    # --- Chunking (Aşama 4) --------------------------------------------------
    # Token-based chunking targets (AKTIF_GOREV.md §11). These drive the
    # ChunkerRegistry / content-sensitive chunkers: each resulting leaf chunk
    # is bounded by CHUNK_MIN/MAX_TOKENS (respecting unit boundaries so a
    # paragraph, table cell or code line is never sliced), CHUNK_TARGET_TOKENS
    # is the desired size, CHUNK_OVERLAP_RATIO is the controlled overlap
    # between neighbouring document chunks, and PARENT_CHUNK_MAX_TOKENS bounds
    # the aggregated parent chunks for parent-child retrieval expansion.
    CHUNK_TARGET_TOKENS: int = 600
    CHUNK_MIN_TOKENS: int = 250
    CHUNK_MAX_TOKENS: int = 900
    CHUNK_OVERLAP_RATIO: float = 0.12
    PARENT_CHUNK_MAX_TOKENS: int = 2400

    # --- Retrieval candidates (Aşama 5.1-5.3) ----------------------------------
    # Per-retriever candidate counts and the RRF fusion window
    # (AKTIF_GOREV.md §5.3). Each retriever may raise its own candidate count
    # above these via constructor override; top-level callers use these plus
    # RERANK_TOP_K to size the fused/reranked pipeline.
    VECTOR_CANDIDATE_K: int = 40
    LEXICAL_CANDIDATE_K: int = 40
    IDENTIFIER_CANDIDATE_K: int = 20
    FUSION_CANDIDATE_K: int = 20
    RRF_K: int = 60
    RERANK_TOP_K: int = 8

    # --- Retrieval context expansion (Aşama 5.5) ------------------------------
    # Final RAG context budget. CONTEXT_MAX_CHUNKS caps how many distinct chunks
    # (selected + expanded parents/neighbours) reach the model; CONTEXT_MAX_TOKENS
    # caps the total token budget. The ContextBuilder enforces both and must never
    # exceed them (AKTIF_GOREV.md 5.5).
    CONTEXT_MAX_CHUNKS: int = 8
    CONTEXT_MAX_TOKENS: int = 12000
    # How many controlled adjacent chunks (by sequence_no) are pulled in around a
    # selected chunk during expansion.
    CONTEXT_ADJACENT_WINDOW: int = 1

    # --- Repository / directory scan (Aşama 7) --------------------------------
    # Security and resource limits for repository/archive/directory source
    # discovery and scanning (AKTIF_GOREV.md §7.2 / §11 / §12.3). These are the
    # configurable knobs discovery enforces — no such numbers are hardcoded in
    # the scanning logic.
    # Comma-separated list of absolute filesystem roots that a web-requested
    # scan path may resolve under. Any path outside these is refused (AKTIF
    # 7.2: "Yalnız CODE_ALLOWED_ROOTS altında kalan canonical path'lere izin
    # ver").
    CODE_ALLOWED_ROOTS: str = "/imports,/workspace"
    # Maximum number of files a single scan may discover.
    CODE_MAX_FILES: int = 20000
    # Maximum total bytes across all discovered files in one scan.
    CODE_MAX_TOTAL_BYTES: int = 1073741824
    # Maximum bytes for a single discovered file; larger files are skipped.
    CODE_MAX_FILE_BYTES: int = 2097152
    # Wall-clock budget in seconds for a single scan.
    CODE_SCAN_TIMEOUT_SECONDS: int = 900
    # Whether directory symlinks are traversed during discovery. Default False:
    # symlinks are never followed (AKTIF 7.2 symlink escape guard).
    CODE_FOLLOW_SYMLINKS: bool = False
    # Whether git submodules are pulled/expanded automatically. Default False.
    CODE_ALLOW_SUBMODULES: bool = False
    # Whether git LFS large objects are downloaded. Default False.
    CODE_ALLOW_GIT_LFS: bool = False
    # Secret handling policy for sensitive paths: "skip" (default) skips
    # sensitive files during discovery.
    CODE_SECRET_POLICY: str = "skip"

    # --- Repository / archive ingestion feature (Aşama 7) --------------------
    # Gates the whole repository/archive/directory source-ingestion feature
    # behind a flag so it can be rolled back safely (AKTIF_GOREV.md §11 / §16:
    # FEATURE_REPOSITORY_INGESTION). The API routes in ``api/v1/repositories.py``
    # refuse to run when this is off. Default True per §11 example and Global
    # DoD (§17: "Repository URL, archive ve izinli klasör tarama çalışıyor");
    # set to False only to roll the feature back.
    FEATURE_REPOSITORY_INGESTION: bool = True
    # Archive "zip bomb" / traversal protective limits (AKTIF_GOREV.md §7.2:
    # "Archive path traversal ve zip bomb koruması uygula", "Maksimum dosya
    # sayısı, tek dosya boyutu, toplam byte ve tarama süresi limiti koy").
    CODE_ARCHIVE_MAX_TOTAL_BYTES: int = 1073741824
    CODE_ARCHIVE_MAX_ENTRY_BYTES: int = 2097152
    CODE_ARCHIVE_MAX_ENTRIES: int = 20000

    # --- OCR (Aşama 8) --------------------------------------------------------
    # OCR routing config (AKTIF_GOREV.md §8 / §11 / §16). FEATURE_OCR gates the
    # whole feature; OCR_ENABLED additionally gates the active OCR call. When
    # the selected OCR_PROVIDER's engine is not installed, the factory degrades
    # to OCR_FALLBACK_PROVIDER, and if neither is available it reports OCR as
    # unavailable. OCR_LANGUAGES uses the "tur+eng" profile (provider-resolved).
    # OCR_MIN_TEXT_COVERAGE is the per-page threshold below which a page is
    # routed to OCR (see pdf_parser coverage_metadata). OCR_MIN_CONFIDENCE (the
    # threshold below which an OCR result is flagged needs_review metadata, 8.4)
    # is defined together with the OCR routing logic below; see §8.4 comment.
    FEATURE_OCR: bool = True
    OCR_ENABLED: bool = True
    OCR_PROVIDER: str = "docling"
    OCR_FALLBACK_PROVIDER: str = "tesseract"
    OCR_LANGUAGES: str = "tur+eng"
    OCR_MIN_TEXT_COVERAGE: float = 0.02

    # --- Security / file validation (Aşama 9.5) --------------------------------
    # Upload and ingestion resource limits + policy knobs the reusable security
    # helpers (src/infrastructure/security) read from config so thresholds can be
    # varied per deployment/test. Defaults per AKTIF_GOREV.md §11 / §9.5.
    # Maximum bytes for a single uploaded document; a larger upload is refused
    # (Aşama 9.5 "dosya boyutu ... limitleri").
    MAX_DOCUMENT_BYTES: int = 20971520  # 20 MB
    # Maximum cumulative bytes across one ingestion batch; exceeding it refuses
    # the batch (Aşama 9.5 "toplam ingestion limitleri").
    MAX_TOTAL_INGESTION_BYTES: int = 1073741824  # 1 GB
    # Maximum number of files in a single ingestion batch.
    MAX_INGESTION_FILES: int = 20000
    # Wall-clock budget (seconds) for a single parser call; the ParserRouter
    # already enforces this (Aşama 3.1). Declared here as the single knob the
    # security suite asserts against.
    PARSER_TIMEOUT_SECONDS: float = 300.0
    # Soft upper bound for a single parse's peak memory usage (MB). The parser
    # output-size limit (MAX_PARSED_TEXT_CHARS) bounds output; this documents the
    # memory budget and is surfaced by the security limits helpers.
    PARSER_MEMORY_LIMIT_MB: int = 2048
    # Whether extension-vs-magic mismatches that "look binary" are refused even
    # when only the extension claims a binary type (Aşama 9.5 "MIME ve magic-byte
    # doğrulama"). When False only a directly observed magic/extension conflict is
    # refused; a claimed-but-unverified binary extension is still refused here for
    # safety, so this flag mainly governs fringe ambiguity handling.
    MIME_VALIDATION_STRICT: bool = True
    # Comma-separated extra secret-detection regex patterns (extended by
    # src/infrastructure/security/redaction.py). Empty by default — the built-in
    # credential/secret patterns already ship inside the helper.
    SECRET_PATTERNS: str = ""

    # --- No-answer / intent policy (Aşama 5.6) --------------------------------
    # These are configurable calibration defaults, NOT hardcoded constants and NOT
    # a single decision mechanism. The AnswerPolicy combines dense score, lexical /
    # identifier evidence and evidence count: an exact identifier or strong lexical
    # match overrides a low dense score, and an empty retrieval is never treated as
    # small-talk. Tuned against the golden dataset (AKTIF_GOREV.md 5.6).
    NO_ANSWER_SCORE_THRESHOLD: float = 0.55
    NO_ANSWER_MIN_EVIDENCE: int = 1
    # A lexical score at/above this counts as "strong lexical" evidence that can
    # rescue a candidate whose dense score is below NO_ANSWER_SCORE_THRESHOLD.
    LEXICAL_STRONG_SCORE: float = 0.4
    # Minimum normalized token length below which a query is too short to be an
    # informative document question (one input into small-talk detection).
    SMALLTALK_MIN_CONTENT_LEN: int = 20

    @property
    def available_chat_models(self) -> List[str]:
        """Parsed CHAT_MODELS allow-list, falling back to [CHAT_MODEL]."""
        raw = self.CHAT_MODELS if self.CHAT_MODELS is not None else self.CHAT_MODEL
        models = [m.strip() for m in raw.split(",") if m.strip()]
        return models or [self.CHAT_MODEL]

    # --- Security / observability derived config (Aşama 9) -------------
    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() == "production"

    @property
    def debug_enabled(self) -> bool:
        """API_DEBUG is only honored in non-production envs. In production it
        is forced off so unhandled exceptions never leak a stack trace to the
        user (AKTIF_GOREV.md §9.5)."""
        return bool(self.API_DEBUG) and not self.is_production

    @property
    def retrieval_debug_enabled(self) -> bool:
        """FEATURE_RETRIEVAL_DEBUG gated off in production so the
        debug/retrieval endpoint is never exposed there (AKTIF §9.5 / §11)."""
        return bool(self.FEATURE_RETRIEVAL_DEBUG) and not self.is_production

    @property
    def cors_origins(self) -> List[str]:
        """Resolved CORS allow-list. Explicit CORS_ALLOW_ORIGINS wins;
        otherwise dev origins in development and an empty (restrictive) list
        in production. Never resolves to "*" (§9.5)."""
        if self.CORS_ALLOW_ORIGINS:
            return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
        if not self.is_production:
            return list(DEV_CORS_ORIGINS)
        return []



settings = Settings()
