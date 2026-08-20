# Document RAG Platform

Belgeleri (PDF, DOCX, TXT), görselleri (OCR ile), Git repository'leri, ZIP/TAR arşivlerini ve izinli yerel klasörleri yükleyip/tarayıp; dense + lexical + identifier hibrit arama (RRF), kanıta dayalı (citation'lı) sohbet ve "kaynaklarda bilgi yok" davranışı sunan modüler (FastAPI + Celery) RAG platformu.

Kanonik uygulama dizini: **`document-rag-platform/`** (repo kökü, `AKTIF_GOREV.md`'deki hedef mimarinin gerçekleştiği yerdir).

## Bir bakışta

| Alan | Değer |
|---|---|
| Tür | Çok kaynaklı RAG (Retrieval-Augmented Generation) sohbet platformu |
| Girdiler | PDF · DOCX · TXT/MD · PNG/JPEG (OCR) · Git repo URL · ZIP/TAR · izinli klasör |
| Vektör deposu | PostgreSQL + pgvector (`Vector(1024)`), dense + lexical (tsvector) + identifier GIN |
| Retrieval | dense + lexical + identifier → **RRF** → dedupe → opsiyonel reranker → context → LLM |
| Embedding / chat | OpenAI uyumlu LiteLLM gateway (`LITELLM_BASE_URL` / `LITELLM_API_KEY`) üzerinden, tamamen **config ile** (`EMBEDDING_MODEL`, `CHAT_MODEL`) — kodda sabit model adı yok |
| Async ingestion | Celery worker + Redis broker; `FEATURE_ASYNC_INGESTION` ile senkron fallback |
| Migration | Alembic — `alembic upgrade head` (bkz. `services/backend/MIGRATION_RUNBOOK.md`) |
| Dağıtım | Docker Compose — `postgres` · `redis` · `minio` · `backend` (uvicorn :8000) · `worker` (celery) + `apps/web` (Next.js) |

> Eşikler ve top-k değerleri kodda **sabit değildir**; `services/backend/src/config.py` üzerinden ortam değişkeniyle yönetilir ve `GET /debug/retrieval` ile görüntülenebilir. Aşağıdaki tüm sayılar o dosyadaki **varsayılanlardır** ve `.env` ile değiştirilebilir.

## Servisler

`docker-compose.yml` aşağıdakileri ayağa kaldırır:

| Servis | Image / komut | Port | Rol |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 5432 | Veri + pgvector + full-text index |
| `redis` | `redis:7-alpine` | 6379 | Celery broker + result backend |
| `minio` | `minio/minio` | 9000 / 9001 (konsol) | Orijinal dosya + artifact object storage |
| `backend` | `src` build → uvicorn | 8000 | FastAPI API + (`/docs` Swagger) |
| `worker` | `src` build → `celery -A src.workers.celery_app worker -l info` | — | Ingestion job'ları (parse → chunk → embed → index) |

Frontend (`apps/web`, Next.js) compose'un dışında `npm run dev` ile ayrı çalışır (varsayılan `http://localhost:3000`).

## Hızlı başlangıç

1. `.env.example` dosyasını kopyalayıp değerleri gir (`.env`, `.gitignore`'dadır):

   ```bash
   cp .env.example .env
   ```

   Zorunlu alanlar: `DATABASE_URL` (compose tarafından `POSTGRES_*`'den üretilir), `LITELLM_API_KEY`, `LITELLM_BASE_URL`. Model/eşik/güvenlik ayarları `services/backend/src/config.py`'deki varsayılanlarla çalışır.

2. Servisleri ayağa kaldır:

   ```bash
   docker compose up -d --build
   ```

3. Migration'ı uygula (bkz. `services/backend/MIGRATION_RUNBOOK.md`):

   ```bash
   docker compose exec backend alembic upgrade head
   ```

4. Frontend'i başlat:

   ```bash
   cd apps/web
   npm install
   npm run dev
   ```

   Tarayıcıda `http://localhost:3000`. Backend tek başına `http://localhost:8000`, Swagger `http://localhost:8000/docs`.

> Backend'e veritabanı tarafında erişim için: `docker exec -it rag-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`.

## API uçları

Router'lar `src/api/v1/` altındadır ve kök üzerinden (`/api/v1` ön eki YOK) bağlanır:

| Metot | Uç | Modül |
|---|---|---|
| GET | `/` , `/health`, `/health/live`, `/health/readiness`, `/ready` | `health.py` |
| GET/POST | `/projects` · `DELETE /projects/{id}` | `projects.py` |
| POST | `/documents/upload` (multipart) | `documents.py` |
| GET | `/documents` · `/documents/{id}` · `/documents/{id}/status` | `documents.py` |
| POST | `/documents/{id}/delete` | `documents.py` |
| POST/GET | repo/archive/directory + refresh + files/versions (feature-gated) | `repositories.py` |
| GET | `/ingestion-jobs/{id}` · `/ingestion-jobs/{id}/events` | `ingestion_jobs.py` |
| POST/GET | `/chat/query` · `/chat/models` | `chat.py` |
| POST | `/debug/retrieval` (production'da kapalı) | `debug.py` |

## Yükleme → ingestion akışı

Varsayılan (asenkron — `FEATURE_ASYNC_INGESTION=true`):

1. `POST /documents/upload` anında `Document` + ilk `DocumentVersion` + `IngestionJob` (`status=queued`) kaydeder, orijinal dosyayı MinIO'ya (`object_keys.original_key`) yazar ve Celery task'ını kuyruğa atar; **parse/chunk/embed'ü beklemez** (`documents.py:_upload_document_async`).
2. `worker` (`src/workers/ingestion_tasks.py:process_ingestion_job`) job'ı `validating → storing → parsing → chunking → embedding → indexing → activating` aşamalarından geçirir.
3. Her aşama `ingestion_events`'e yazılır; durum `GET /ingestion-jobs/{id}` (+ `/events`) ve `GET /documents/{id}/status` ile izlenir.
4. Tüm chunk/embedding hazır olduktan sonra version `ready` olur ve `documents.active_version_id` atomik olarak değiştirilir (`activating`); yeni version hazır olana dek eski aktif version okumaya devam eder.

Fallback (senkron): `FEATURE_ASYNC_INGESTION=false` ayarlanırsa upload aynı istek içinde parse → chunk → embed → index yapar (rollback/debug için korunmuştur).

Worker güvenliği (Aşama 2 kabul kriterleri): `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`; geçici hatalar `INGESTION_MAX_RETRIES` (varsayılan 3) kez üstel backoff ile yeniden denenir, kalıcı doğrulama hataları asla yeniden denenmez. Aynı job yeniden alınırsa idempotent "wipe + rewrite" sayesinde duplicate chunk oluşmaz.

Bu akış ve job yönetimi için bkz. `docs/runbooks/upload-and-ingestion-jobs.md`.

## Retrieval pipeline (sohbet)

`POST /chat/query` (`src/application/retrieval_service.py`):

```
sorgu + filtreler
  -> dense (pgvector) + lexical (full-text) + identifier (tekil sembol) adayları
  -> RRF birleştirme (rrf.fuse, RRF_K=60)
  -> aynı içerikli kopyaları temizle (rrf.dedupe)
  -> opsiyonel reranker (FEATURE_RERANKER + RERANKER_ENABLED; varsayılan Noop)
  -> context oluşturma (ContextBuilder; CONTEXT_MAX_CHUNKS=8, CONTEXT_MAX_TOKENS=12000)
  -> no-answer / intent kararı (AnswerPolicy)
  -> kanıt paketleme -> LLM cevap -> citation persistence (message_citations)
```

No-answer ve intent (Aşama 5.6, `src/infrastructure/retrieval/no_answer.py`):

- **Selamlaşma/günlük sohbet** — deterministik kısa kurallarla `intent=smalltalk`.
- **Belge sorusu + yeterli kanıt** — `answerable=true`.
- **Belge sorusu + yetersiz kanıt** — `answerable=false`; sistem uydurmaz, "Kaynaklarda bilgi yok…" döner ve **modeli çağırmaz**.

Karar tek bir sabit eşiğe dayanmaz: `NO_ANSWER_SCORE_THRESHOLD` (varsayılan `0.55`) ile birlikte kanıt sayısı, `LEXICAL_STRONG_SCORE` (`0.4`) ve exact identifier sinyali kullanılır; güçlü lexical veya exact identifier eşleşmesi düşük dense skoru ezebilir. Retrieval boş diye belge sorusu asla otomatik "günlük sohbet" sayılmaz. Tüm eşikler `config.py`'den gelir, kodda sabit değildir.

Retrieval aday/eşikleri (varsayılanlar): `VECTOR_CANDIDATE_K=40`, `LEXICAL_CANDIDATE_K=40`, `IDENTIFIER_CANDIDATE_K=20`, `FUSION_CANDIDATE_K=20`, `RRF_K=60`, `RERANK_TOP_K=8`.

## Desteklenen kaynaklar (Aşama 7 / 8)

| Kaynak | Uç | Adaptör |
|---|---|---|
| Belge (PDF/DOCX/TXT/MD) | `POST /documents/upload` | `parsers` (`pdf_parser`, `docx_parser`, `plain_text_parser`) |
| Görsel / OCR | görsel parser + OCR | `ocr` (`docling` / `tesseract`) |
| Git repository | `POST /repositories/ingest` | `repositories/git_source.py` |
| ZIP/TAR arşiv | `POST /archives/upload` | `repositories/archive_source.py` |
| Klasör | `POST /directories/scan` | `repositories/discovery.py` |

Repository/arşiv/klasör taraması `FEATURE_REPOSITORY_INGESTION` arkasındadır (varsayılan `true`); isterseniz `.env`'de `false` yaparak kapatabilirsiniz. Tarama güvenlik sınırları `CODE_*` değişkenleriyle yönetilir (bkz. `docs/runbooks/repository-scan-limits.md`), kod hiçbir koşulda çalıştırılmaz.

## Ortam değişkenleri (özet)

Genel değişkenler `.env.example`'da; gerçek tipler ve **tüm** varsayılanlar `services/backend/src/config.py`'dedir:

- **Core:** `APP_ENV`, `API_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `CORS_ALLOW_ORIGINS`
- **Gateway:** `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `EMBEDDING_MODEL`, `CHAT_MODEL`, `CHAT_MODELS`
- **Object storage:** `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
- **Postgres/MinIO:** `POSTGRES_*`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`
- **Chunking:** `CHUNK_TARGET_TOKENS=600`, `CHUNK_MIN_TOKENS=250`, `CHUNK_MAX_TOKENS=900`, `CHUNK_OVERLAP_RATIO=0.12`, `PARENT_CHUNK_MAX_TOKENS=2400`
- **Retrieval:** `VECTOR_CANDIDATE_K`, `LEXICAL_CANDIDATE_K`, `IDENTIFIER_CANDIDATE_K`, `FUSION_CANDIDATE_K`, `RRF_K`, `RERANK_TOP_K`, `CONTEXT_MAX_CHUNKS`, `CONTEXT_MAX_TOKENS`, `NO_ANSWER_*`, `LEXICAL_STRONG_SCORE`, `SMALLTALK_MIN_CONTENT_LEN`
- **Reranker:** `FEATURE_RERANKER`, `RERANKER_ENABLED`, `RERANKER_PROVIDER`, `RERANKER_MODEL`
- **OCR:** `FEATURE_OCR`, `OCR_ENABLED`, `OCR_PROVIDER`, `OCR_FALLBACK_PROVIDER`, `OCR_LANGUAGES=tur+eng`, `OCR_MIN_TEXT_COVERAGE`, `OCR_MIN_CONFIDENCE`
- **Ingestion worker:** `FEATURE_ASYNC_INGESTION`, `INGESTION_MAX_RETRIES`, `INGESTION_RETRY_BACKOFF_SECONDS`, `INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS`, `INGESTION_TASK_TIME_LIMIT_SECONDS`
- **Repository scan:** `FEATURE_REPOSITORY_INGESTION`, `CODE_ALLOWED_ROOTS`, `CODE_MAX_FILES`, `CODE_MAX_TOTAL_BYTES`, `CODE_MAX_FILE_BYTES`, `CODE_SCAN_TIMEOUT_SECONDS`, `CODE_FOLLOW_SYMLINKS`, `CODE_ALLOW_SUBMODULES`, `CODE_ALLOW_GIT_LFS`, `CODE_SECRET_POLICY`, `CODE_ARCHIVE_*`
- **Güvenlik:** `MAX_DOCUMENT_BYTES` (20 MB), `MAX_TOTAL_INGESTION_BYTES` (1 GB), `MAX_INGESTION_FILES`, `PARSER_TIMEOUT_SECONDS`, `PARSER_MEMORY_LIMIT_MB`, `MIME_VALIDATION_STRICT`, `SECRET_PATTERNS`
- **Diğer feature'lar:** `FEATURE_NEW_CITATIONS`, `FEATURE_RETRIEVAL_DEBUG`, `RATE_LIMIT_*`

## Proje yapısı (özet)

```
document-rag-platform/
├─ docker-compose.yml           postgres · redis · minio · backend · worker
├─ .env.example
├─ docs/
│  ├─ adr/                        mimari karar kayıtları (ADR-001..006)
│  └─ runbooks/                   operasyon runbook'ları (6 adet)
└─ services/backend/
   ├─ alembic/                    migration zinciri (baseline → şema → backfill)
   ├─ MIGRATION_RUNBOOK.md        migration komutları
   └─ src/
      ├─ config.py                tüm env değişkenleri + varsayılanlar (tekil kaynak)
      ├─ models.py                ORM modelleri
      ├─ main.py                  uygulama factory + router bağlama
      ├─ api/v1/                  health · projects · documents · ingestion_jobs · chat · repositories · debug
      ├─ application/             reindex_service · retrieval_service · answer_service
      ├─ infrastructure/          parsers · chunkers · embeddings · retrieval · rerankers · repositories · ocr · storage · security
      └─ workers/                 celery_app · ingestion_tasks
```

Ayrıntılı iç yapı için `AKTIF_GOREV.md` Bölüm 7 (hedef dizin) ve 8 (veri modeli) bölümlerine bakın. Veri modeli: `projects`, `documents`, `document_versions`, `source_files`, `document_artifacts`, `ingestion_jobs`, `ingestion_events`, `embedding_profiles`, `chunks`, `chunk_embeddings`, `conversations`, `messages`, `message_citations`.

## Operasyon runbook'ları

`docs/runbooks/` altında gerçek kod/komutlarla eşleşen altı operasyon dokümanı:

- `upload-and-ingestion-jobs.md` — asenkron yükleme, job yaşam döngüsü, worker yeniden başlatma ve retry
- `reindex.md` — orijinal artifact'tan re-index (`POST /documents/{id}/refresh`, `ReindexService`)
- `embedding-model-change.md` — embedding modeli/profil değişimi ve kontrollü re-index
- `ocr-models.md` — OCR provider'ları, dil profili ve language pack kurulumu
- `repository-scan-limits.md` — `CODE_*` scan limitleri ve güvenlik kuralları
- `backup-restore.md` — Postgres dump/restore + MinIO nesne deposu backup

## Bilinen sınırlamalar

- Tek-kullanıcılı yerel sunum; kimlik doğrulama/user ayrımı yoktur.
- `FEATURE_REPOSITORY_INGESTION` varsayılan `true`'dur; isterseniz `.env`'de `false` yaparak kapatabilirsiniz.
- Opsiyonel ağır bağımlılıklar (Docling OCR, Tesseract) API image'ına zorunlu değildir; varsa `available` olarak devreye girer, yoksa fallback/`needs_review` ile degrade olur (bkz. `docs/runbooks/ocr-models.md`).

## Geliştirici

Mehmet Karacan
