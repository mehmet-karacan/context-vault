# Context Vault — Proje Durum Özeti

**Son güncelleme:** 2026-08-20
**Durum:** Aşama 0–9 **tamamlandı** · Aşama 10 (dokümantasyon, temizlik ve son aktivasyon) **devam ediyor**
**Kanonik plan:** `AKTIF_GOREV.md` (bu dosya yalnız özet; plan ve ilerleme kaydı için `AKTIF_GOREV.md`'ye bakın)

> ⚠️ Önceki sürümlerdeki "MVP TAMAMLANDI / 6 aşama / 13 tablo / 24,666+ dosya" ifadeleri gerçek kodla **çelişiyordu** ve kaldırılmıştır. Aşağıdaki içerik mevcut uygulanmış koda göre doğrulanmıştır.

---

## Mimari (şu an)

Uygulama, kanonik dizin olan `document-rag-platform/services/backend/src` altında **modüler monolit** olarak düzenlenmiştir:

```text
Kaynak (DOCX/PDF/TXT/MD · PNG/JPEG/TIFF · ZIP/TAR · Git URL · izinli yerel klasör)
  → Güvenlik/kaynak doğrulama (MIME+magic, boyut limitleri, path/archive koruması, secret politikası)
  → Orijinal + normalize artifact'ları object storage'a (MinIO)
  → Ingestion job + durum olayları (Celery worker + Redis, kalıcı durum PostgreSQL)
  → Parser Router
      ├─ DOCX parser (paragraf+tablo sırası, heading path)
      ├─ PDF parser (Docling; dijital/taranmış ayrımı, text-coverage, fallback)
      ├─ Plain-text/Markdown parser (encoding, satır, yapı)
      ├─ Code parser + PL/SQL parser (satır/sembol tabanlı; tree-sitter ertelendi)
      └─ Image parser + OCR yönlendirme (Docling/Tesseract)
  → Normalize İçerik Modeli (NormalizedSource / ContentUnit / Hierarchy / Locator)
  → Yapıya duyarlı Chunker Registry (token-bazlı, heading context, parent-child, table/code/PL-SQL)
  → Dense embedding (BGE-M3 1024, cache) + Lexical (search_vector GIN) + Identifier (GIN)
  → PostgreSQL + pgvector (cosine HNSW) · chunk_embeddings · search_vector · identifiers
  → Dense + Lexical + Identifier retrieval → **RRF fusion** → opsiyonel Reranker (noop/remote)
  → Context builder (parent/komşu genişletme, token bütçesi) + Kanıt paketleme
  → No-answer / intent ayrımı → LLM cevap (Qwen) → Citations + retrieval debug + eval
```

### Ana bileşenler ve gerçek karşılıkları

| Katman | Modül(ler) |
|---|---|
| Parser Router | `infrastructure/parsers/router.py` |
| Belge parser'ları | `parsers/docx_parser.py`, `docling_parser.py`, `pdf_parser.py`, `plain_text_parser.py`, `markdown`, `image_parser.py`, `code_parser.py` |
| OCR | `infrastructure/ocr/{factory,docling_provider,tesseract_provider,preprocessing}.py`, `parsers/ocr_routing.py` |
| Chunker | `infrastructure/chunkers/registry.py`, `document_chunker.py`, `table_chunker.py`, `code_chunker.py`, `plsql_chunker.py`, `token_counter.py` |
| Embedding | `infrastructure/embeddings/openai_compatible.py`, `cache.py` |
| Retrieval | `infrastructure/retrieval/{dense,lexical,identifier,rrf,context_builder,indexing,no_answer,base}.py` |
| Reranker | `infrastructure/rerankers/noop.py`, `remote.py` |
| Repository tarama | `infrastructure/repositories/{discovery,git_source,archive_source,directory_source,ignore_rules,language_detection,path_security,scan_result}.py` |
| Storage | `infrastructure/storage/minio_storage.py`, `object_keys.py` |
| Worker | `workers/celery_app.py`, `ingestion_tasks.py` |
| Uygulama servisleri | `application/{ingestion_service,reindex_service,retrieval_service,answer_service,source_service}.py` |
| API | `api/v1/{router,projects,documents,repositories,ingestion_jobs,chat,debug}.py` |
| Güvenlik | `infrastructure/security/{file_validation,limits,arbitrary_path,redaction,prompt_injection}.py` |
| Gözlemlenebilirlik | `infrastructure/observability.py`, `rate_limiter.py`, health/readiness |

Domain sözleşmeleri `domain/ports.py` + `domain/normalized_content.py`; veri modeli ve migration `models.py` + Alembic.

## Desteklenen kaynaklar

- **DOCX** — paragraf + tablo (body sırası), heading path, liste bilgisi; citation = heading path + block index.
- **PDF** — dijital (Docling, sayfa/bbox) + taranmış (düşük text-coverage sayfaları OCR'a yönlendirilir).
- **TXT / Markdown** — encoding tespiti (cp1254/UTF-8 fallback), satır bilgisi, code fence/başlık/tablo yapıları.
- **PNG / JPEG / TIFF görseller** — OCR (Docling/Tesseract), ön işleme (EXIF orientation, deskew, denoise, binarization), bbox citation.
- **Kod** — public/credential-ref Git repo URL, ZIP/TAR.GZ arşiv, `CODE_ALLOWED_ROOTS` altında izinli yerel klasör taraması; `.gitignore`/`.contextvaultignore`; kod **çalıştırılmaz**; .env/private key/secret skip; incremental re-index (commit SHA + content_hash).
- **PL/SQL** — PACKAGE / PROCEDURE / FUNCTION / TRIGGER / TYPE sınır tanıma + ayrı chunker.

## Model ve config güdümlü eşikler

Sabit değerler kod içinde tutulmaz; `src/config.py` (pydantic-settings) üzerinden yönetilir:

- **Embedding:** `EMBEDDING_MODEL=openai/BAAI/bge-m3`, boyut `1024`, cosine; query/passage prefix'leri boş (BGE-M3 instruction gerektirmez).
- **Chat:** `CHAT_MODEL=Qwen/Qwen3.5-27B-FP8` (`CHAT_MODELS` allow-list).
- **Chunking:** `CHUNK_TARGET_TOKENS=600`, `MIN=250`, `MAX=900`, `OVERLAP_RATIO=0.12`, `PARENT_CHUNK_MAX_TOKENS=2400` (token-bazlı).
- **Retrieval:** `VECTOR_CANDIDATE_K=40`, `LEXICAL_CANDIDATE_K=40`, `IDENTIFIER_CANDIDATE_K=20`, `FUSION_CANDIDATE_K=20`, `RRF_K=60`, `CONTEXT_MAX_CHUNKS=8`, `CONTEXT_MAX_TOKENS=12000`.
- **No-answer / intent:** `NO_ANSWER_SCORE_THRESHOLD=0.55`, `NO_ANSWER_MIN_EVIDENCE=1`, `LEXICAL_STRONG_SCORE=0.4`, `SMALLTALK_MIN_CONTENT_LEN=20` — tek mekanizma değil; exact/strong-lexical eşleşme düşük dense skoru ezer, boş retrieval asla selamlaşma sayılmaz.
- **OCR:** `FEATURE_OCR=true`, `OCR_PROVIDER=docling`, `OCR_FALLBACK_PROVIDER=tesseract`, `OCR_MIN_TEXT_COVERAGE=0.02`, `OCR_MIN_CONFIDENCE=0.5`.
- **Repo tarama limitleri:** `CODE_ALLOWED_ROOTS`, `CODE_MAX_FILES`, `CODE_MAX_TOTAL_BYTES`, `CODE_MAX_FILE_BYTES`, `CODE_SCAN_TIMEOUT_SECONDS`, arşiv zip-bomb/traversal koruması.

Not: `FEATURE_REPOSITORY_INGESTION` config varsayılanı `true`'dur; repo/arşiv/klasör ingestion varsayılan olarak açıktır — istenirse deployment env'de `false` yapılarak kapatılabilir (detay: aşağıdaki feature flag bölümü).

## Backend çalışma durumu + indexing düzeltmesi

- Backend, docker compose ile **ayağa kalkıyor**; son teşhis/düzeltme commit'leri (`07b8892`, `345733c`, `07e0a75`) şunları çözdü:
  - Celery worker + FastAPI başlatma (rate limiter `__future__` forward-ref — pydantic 2.5 + FastAPI 0.109 uyumu).
  - Dense retrieval pgvector `CAST(... AS vector)` operatörü.
  - Ingestion'da `chunk_embeddings` + `search_vector` + `identifiers` indeksleme; senkron/asenkron her iki yolda dense fallback (`chunks.embedding`) ve no-answer sorunu giderildi.
- **Testler:** `pytest` (backend full suite) — 492 passed, 8 skipped; tek hata FastAPI 0.141 vs kilitli 0.109 sürüm artefaktı olup **yalnız atılabilir test venv'ini** etkiler, imajı etkilemez. Aşama 9 eval: 66 golden soru, Recall@1/3/5/10 ve MRR@10 = 1.000, quality gate True.

## Kalan / bilinen sınırlar (Aşama 10 + ops)

Ayrıntı için `active/current-tasks.md` ve `AKTIF_GOREV.md` §18'e bakın:

1. **Tree-sitter AST/symbol chunking ertelendi** → kod parser/chunker satır + sembol farkındalıklı fallback kullanır (`code_chunker.py`, `code_parser.py` notları).
2. **OCR engine'leri kurulmalı** — Docling/Tesseract provider'ları uygulandı; motor yoksa factory fallback'e düşer veya OCR kullanılamaz raporlar.
3. **Uzak reranker** — `remote.py` adaptörü mevcut; gerçek kullanım gateway'in rerank desteğini gerektirir; varsayılan `NoopReranker` (+ `FEATURE_RERANKER=false`).
4. **Rate limiter bellekte (in-memory)** — HEAD üretim için kabul edilebilir; `RATE_LIMIT_ENABLED` varsayılanı `false` (relaxed); istenirse Redis tabanlına geçilebilir.
5. **ADR-005 (repo tarama güvenlik modeli) ve ADR-006 (OCR provider stratejisi) + ops runbook'ları** (re-index, embedding değişimi, OCR kurulumu, repo scan limitleri, backup/restore) hâlâ yazılacak — Aşama 10 kapsamında.

## Operasyon

- **Plan / ilerleme (kanonik):** `AKTIF_GOREV.md` — aşama tanımları, §11 env, §12 API, §16 feature flag ve rollback, §18 ilerleme kaydı.
- **Migration runbook:** `document-rag-platform/services/backend/MIGRATION_RUNBOOK.md`.
- **ADR'ler:** `document-rag-platform/docs/adr/ADR-001..004`.
- Eski/yanıltıcı özetler yerine yukarıdaki gerçek duruma güvenin; koddan doğrulayın.

## Referanslar

- `active/current-tasks.md` — mevcut ve sonraki adımlar
- `done/completed-tasks.md` — tamamlanan Aşama 0–9 teslimatları
- `document-rag-platform/README.md` — proje dokümantasyonu
- `DOCUMENT_RAG_PLATFORM_ARCHITECTURE_AND_ROADMAP.md` — orijinal mimari plan
