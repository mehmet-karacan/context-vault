# Tamamlanan Görevler

**Son güncelleme:** 2026-08-20
**Kanonik plan:** `AKTIF_GOREV.md`

Bu dosya tamamlanmış işlerin gerçek kaydıdır. Mevcut durum: **Aşama 0–9 tamamlandı**, Aşama 10 devam ediyor. (Eski "6 aşama / 13 tablo / 24,666+ dosya" iddiaları gerçek kodla çeliştiği için düzeltildi.)

## Aşama 0 — Gerçek Durumu Sabitle ve Güvenli Başlangıç ✅

- Başlangıç commit'i sabitlendi; `main` branch tabanı.
- Baseline retrieval + metric script (`tests/evals/baseline/`): `retrieval-top10.jsonl`, `db-schema-snapshot.sql`, `metrics-report.md`, `compute_metrics.py`.
- Kanonik dizin (`document-rag-platform/`) root README ile işaretlendi.
- Yinelenen iskelet dizinleri silinmedi, `docs/cleanup-candidates.md`'ye listelendi.

## Aşama 1 — Konfigürasyon ve Modüler Backend İskeleti ✅

- Typed settings (`src/config.py`, pydantic-settings; `os.getenv` toplandı).
- `api/v1/` router'ları (projects, documents, repositories, ingestion_jobs, chat, debug, health).
- Domain portları (`domain/ports.py`): DocumentParser, OcrProvider, Chunker, TokenCounter, EmbeddingProvider, VectorRetriever, LexicalRetriever, Reranker, ObjectStorage, SourceScanner.
- Uygulama servisleri (`application/`): ingestion, reindex, retrieval, answer, source.
- Embedding/generation adaptörleri (`infrastructure/embeddings/`, `infrastructure/llm/chat_client.py`); `llm.py` ince facade'a dönüştürüldü.
- Unit test iskeleti + startup/health testleri.

## Aşama 2 — Sürümlü Ingestion, MinIO, Alembic ve Worker ✅

- Alembic migration düzeni; gerçek DB'de upgrade/downgrade doğrulandı.
- Sürümlü veri modeli: `document_versions`, `source_files`, `document_artifacts`, `ingestion_jobs`, `ingestion_events`, `chunk_embeddings`, `embedding_profiles`, citation/conversation tabloları.
- MinIO `ObjectStorage` adaptörü + immutable object key standardı (original/normalized/artifacts).
- Celery worker (`workers/celery_app.py`, `ingestion_tasks.py`); job retry, idempotency, stage transition.
- Job-tabanlı asenkron upload (senkron fallback `FEATURE_ASYNC_INGESTION` ile) + ingestion-jobs API.
- `MIGRATION_RUNBOOK.md`.

## Aşama 3 — Yapısal Belge Parser Altyapısı ✅

- Parser Router (`parsers/router.py`): MIME/extension/magic, timeout, max çıktı, `NormalizedSource`.
- DOCX parser: paragraf+tablo sırası, heading path, tablo yapıları.
- PDF parser + Docling: dijital/taranmış text-coverage ayrımı, sayfa/bbox, fallback; karma PDF'de düşük-coverage sayfa OCR'a.
- TXT/Markdown parser: encoding tespiti (cp1254/UTF-8 fallback), satır bilgisi, yapı koruma.
- Parser fixture'ları (`tests/fixtures/parsers/`).

## Aşama 4 — Yapıya Duyarlı Chunking ve Embedding Profilleri ✅

- `ChunkerRegistry` + belge/tablo/kod/PL-SQL chunker'ları; `TokenCounter`.
- Token-bazlı chunking (`CHUNK_TARGET/MIN/MAX`, `OVERLAP_RATIO`, `PARENT_CHUNK_MAX_TOKENS`).
- Heading context header'i + ham vs `embedding_text` ayrımı.
- Tabloyu hücre ortasında bölmeme; büyük tabloları header tekrarlı gruplama.
- Parent-child ilişkisi, sequence_no, `content_hash` duplicate tespiti.
- Embedding cache (content_hash + profil config_hash).
- OpenAI-uyumlu embedding adaptörü; batch/concurrency, retry, vector boyut doğrulaması.

## Aşama 5 — Retrieval V2: Hybrid Search, RRF ve Reranking ✅

- Dense pgvector cosine + filtreler; `chunk_embeddings` ve `chunks.embedding` dense fallback.
- Lexical `search_vector` (GIN, `simple` profil); Identifier index (GIN) + exact identifier retrieval.
- RRF fusion (rank tabanlı).
- Reranker portu + `NoopReranker` (güvenli fallback) + uzak adaptör iskeleti.
- Context builder: parent/komşu genişletme + token/sayı bütçesi.
- No-answer / intent ayrımı: selamlaşma vs yetersiz kanıt; exact/strong-lexical override; boş retrieval selam sayılmaz.
- Retrieval debug endpoint'i.

## Aşama 6 — Kanıt Paketleme, Cevap Üretimi ve Kaynak UI ✅

- Etiketli kanıt paketleme + prompt-injection koruması.
- Cevap üretimi (`application/answer_service.py`): `answerable`, `citations`, retrieval debug.
- `message_citations` DB persistansı (`FEATURE_NEW_CITATIONS` kapılı).
- Frontend: citation detay paneli, dev modu, source filtresi, job ilerlemesi; no-answer'de uydurma cevap yok.

## Aşama 7 — Repository, Arşiv ve Klasör Taraması ✅ (feature kısıtlı)

- Git URL, ZIP/TAR.GZ arşiv, izinli local directory kaynakları.
- Güvenlik: `CODE_ALLOWED_ROOTS` canonical path, symlink çıkışı engeli, zip-bomb/traversal, limitler, secret/`.env` skip + redaction.
- Ignore kuralları sırası (sistem → `.contextvaultignore` → `.gitignore`).
- Kod parser + PL/SQL chunker (tree-sitter ertelendi; satır/sembol fallback).
- Incremental re-index (`application/reindex_service.py`): commit SHA + content_hash, atomik aktivasyon.
- API: `/repositories/ingest`, `/archives/upload`, `/directories/scan`, `/documents/{id}/refresh`, files/versions.

## Aşama 8 — Görsel, PNG ve OCR Altyapısı ✅

- OCR provider sözleşmesi; Docling + Tesseract provider'ları, fallback zinciri.
- Görsel ön işleme: EXIF orientation, deskew, denoise, contrast, binarization.
- OCR yönlendirme: dijital PDF'te gereksiz OCR yok; görsel/düşük-coverage sayfa OCR'a.
- `ocr_text` normalize block, düşük confidence `needs_review`, `ocr_json` artifact, bbox citation.
- `FEATURE_OCR=true`.

## Aşama 9 — Değerlendirme, Gözlemlenebilirlik ve Güvenlik ✅

- Golden dataset (66 soru) + runner + metrikler (Recall@1/3/5/10, MRR@10, nDCG@10, no-answer FP/FN, latency); quality gate True (Recall@K=1.0, MRR@10=1.0).
- Generation metric iskeleti.
- Structured log, health/readiness ayrımı, dependency health, rate limiting (in-memory).
- Güvenlik: MIME/magic, boyut/toplam limitleri, archive koruması, prompt-injection testleri, secret redaction, arbitrary-path engeli, CORS `*` değil, stack-trace sızdırılmıyor.
- Backend docker compose ile ayağa kalkıyor; indexing (chunk_embeddings + search_vector + identifiers) ve dense/no-answer düzeltmeleri yapıldı (`07b8892`, `345733c`, `07e0a75`).

## Aşama 10 — Dokümantasyon, Temizlik ve Son Aktivasyon (DEVAM EDİYOR)

- ✅ Durum dokümanları gerçek koda göre düzeltildi (bu dört dosya).
- ⏳ ADR-005/006, ops runbook'ları, kontrollü eski-chunk temizliği, root README, aktivasyon — `active/current-tasks.md`'de.

---

## Gerçek Durum Özeti

- **Backend:** modüler monolit, FastAPI; docker compose ile çalışıyor, indexing düzeltildi.
- **Testler:** pytest 492 passed, 8 skipped; tek hata atılabilir venv'de FastAPI sürüm artefaktı (imajı etkilemez).
- **Eval:** 66 golden soru, Recall@K=1.0, MRR@10=1.0, quality gate True.
- **Feature flag:** `FEATURE_ASYNC_INGESTION=true`, `FEATURE_OCR=true`, `FEATURE_NEW_CITATIONS=true`, `FEATURE_RERANKER=false`, `FEATURE_REPOSITORY_INGESTION=true` (env ile kapatılabilir).
