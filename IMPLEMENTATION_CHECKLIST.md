# Document RAG Platform — Gerçek Durum Checklist'i

**Kanonik plan:** `AKTIF_GOREV.md` (aşamalar, §11 env, §12 API, §16 feature flag/rollback, §18 ilerleme kaydı)
**Doğrulama yöntemi:** Gerçek kod okunarak doğrulandı (`document-rag-platform/services/backend/src`).
**Son güncelleme:** 2026-08-20

Lejant: `[x]` doğrulanmış tamam · `[~]` kısmi / sınırlı · `[ ]` yok / yapılacak

> Önceki sürüm bu dosya, "neredeyse hiçbir şey uygulanmamış" gibi yanıltıcı bir tablo içeriyordu. O iddia, Aşama 0–9 tamamlandıktan sonra **geçersizdir**. Aşağıdaki işaretler mevcut koda göredir.

---

## Aşama 0 — Gerçek Durumu Sabitle ve Güvenli Başlangıç ✅

- [x] Başlangıç commit'i sabitlendi (`763703d`), branch tabanı `main`.
- [x] Baseline retrieval + compute metrics (`document-rag-platform/tests/evals/baseline/`).
- [x] Root README ile kanonik dizin işareti.
- [x] Yinelenen iskelet dizinleri `docs/cleanup-candidates.md`'ye listelendi.
- [x] Eski/yanıltıcı durum dokümanları güncelleniyor (Aşama 10).

## Aşama 1 — Konfigürasyon ve Modüler Backend İskeleti ✅

- [x] Typed settings (`src/config.py`, pydantic-settings; `os.getenv` toplandı).
- [x] API `api/v1/{router,projects,documents,repositories,ingestion_jobs,chat,debug}.py`.
- [x] Domain portları (`domain/ports.py`): DocumentParser, OcrProvider, Chunker, TokenCounter, EmbeddingProvider, VectorRetriever, LexicalRetriever, Reranker, ObjectStorage, SourceScanner.
- [x] Uygulama servisleri (`application/`): ingestion, reindex, retrieval, answer, source.
- [x] `main.py` uygulama kurulumu + router bağlama dışında iş kuralı içermiyor.
- [x] Embedding/generation adaptörleri ayrıldı (`infrastructure/embeddings/`, `infrastructure/llm/chat_client.py`; `llm.py` ince facade).
- [x] Unit test iskeleti + startup/health testleri.

## Aşama 2 — Sürümlü Ingestion, MinIO, Alembic ve Worker ✅

- [x] Alembic migration düzeni (models + migration'lar; upgrade/downgrade gerçek DB'de doğrulandı).
- [x] `document_versions`, `source_files`, `document_artifacts`, `ingestion_jobs`, `ingestion_events`, `chunk_embeddings`, `embedding_profiles`, citation/conversation şemaları.
- [x] MinIO `ObjectStorage` adaptörü (`infrastructure/storage/minio_storage.py`, `object_keys.py`) + immutable object key standardı.
- [x] Celery worker (`workers/celery_app.py`, `ingestion_tasks.py`) compose ile bağlı; job retry, idempotency, stage transition.
- [x] Job-tabanlı asenkron upload + `GET /ingestion-jobs/{id}` + ingestion-jobs API.
- [x] Senkron fallback için feature flag bırakıldı (`FEATURE_ASYNC_INGESTION`).
- [x] `MIGRATION_RUNBOOK.md`.

## Aşama 3 — Yapısal Belge Parser Altyapısı ✅

- [x] Parser Router (`parsers/router.py`): MIME/extension/magic seçimi, timeout, max çıktı, `NormalizedSource` çıktısı.
- [x] DOCX (`docx_parser.py`): paragraf+tablo body sırası, heading path, tablo yapısı.
- [x] PDF (`pdf_parser.py` + `docling_parser.py`): dijital/taranmış text-coverage ayrımı, sayfa/bbox, fallback; karma PDF'de düşük-coverage sayfa OCR'a.
- [x] TXT/Markdown (`plain_text_parser.py`): encoding tespiti, satır, yapıları korur.
- [x] Parser fixture'ları (`tests/fixtures/parsers/...`).

## Aşama 4 — Yapıya Duyarlı Chunking ve Embedding Profilleri ✅

- [x] `ChunkerRegistry` (`chunkers/registry.py`) + belge/tablo/kod/PL-SQL chunker'ları.
- [x] `TokenCounter` portu; token-bazlı hedef (`CHUNK_TARGET/MIN/MAX`, `OVERLAP_RATIO`, `PARENT_CHUNK_MAX_TOKENS`).
- [x] Heading context header'i embedding text'e; ham vs `embedding_text` ayrımı.
- [x] Tabloyu hücrede bölmeme, büyük tabloları header tekrarlı gruplama (`table_chunker.py`).
- [x] Parent-child ilişkisi, sequence_no, `content_hash` duplicate tespiti.
- [x] Embedding cache (`embeddings/cache.py`) — content_hash + profil config_hash.
- [x] OpenAI-uyumlu embedding adaptörü, batch/concurrency, retry, vector boyut doğrulaması.

## Aşama 5 — Retrieval V2: Hybrid Search, RRF ve Reranking ✅

- [x] Dense pgvector cosine + filtreler; `chunk_embeddings` ve `chunks.embedding` dense fallback.
- [x] Lexical `search_vector` (GIN, `simple` profil) — `retrieval/lexical.py`.
- [x] Identifier index (GIN) + exact identifier retrieval — `retrieval/identifier.py`, `indexing.py`.
- [x] RRF fusion (`retrieval/rrf.py`) — rank tabanlı, ham skor toplanmaz.
- [x] Reranker portu + `NoopReranker` (güvenli fallback) + `remote.py` adaptörü; `FEATURE_RERANKER` ile kapalı (varsayılan).
- [x] Context builder: parent/komşu genişletme, `CONTEXT_MAX_CHUNKS/TOKENS` bütçesi — `retrieval/context_builder.py`.
- [x] No-answer / intent ayrımı (`retrieval/no_answer.py`): selamlaşma vs yetersiz kanıt; exact/strong-lexical override; boş retrieval selam sayılmaz.
- [x] Retrieval debug endpoint'i + `FEATURE_RETRIEVAL_DEBUG` (production'da kapalı).

## Aşama 6 — Kanıt Paketleme, Cevap Üretimi ve Kaynak UI ✅

- [x] Etiketli kanıt paketleme + prompt-injection koruması (`security/prompt_injection.py`).
- [x] Cevap üretimi (`application/answer_service.py`); `answerable`, `citations`, retrieval debug alanları.
- [x] `message_citations` DB persistansı (`FEATURE_NEW_CITATIONS` kapılı).
- [x] Frontend: citation detay paneli (belge/bölüm/sayfa/dosya/satır/snippet/skor), dev modu, source filtresi, job ilerlemesi.
- [x] No-answer'de uydurma cevap üretilmeme.

## Aşama 7 — Repository, Arşiv ve Klasör Taraması ✅ (feature kapalı varsayılanıyla)

- [x] Kaynaklar: Git URL, ZIP/TAR.GZ, izinli local directory (`repositories/{git_source,archive_source,directory_source}.py`).
- [x] Güvenlik: `CODE_ALLOWED_ROOTS` canonical path, symlink çıkışı engeli, zip-bomb/traversal, boyut/süre limitleri, secret/`.env` skip + redaction — `path_security.py`, `security/*`.
- [x] Ignore kuralları sırası: sistem güvenlik → `.contextvaultignore` → `.gitignore` (`ignore_rules.py`).
- [x] Kod parser + PL/SQL chunker (`code_parser.py`, `plsql_chunker.py`).
- [x] Incremental re-index (`application/reindex_service.py`): commit SHA + content_hash, atomik aktivasyon, silinen dosyalar.
- [x] API: `/repositories/ingest`, `/archives/upload`, `/directories/scan`, `/documents/{id}/refresh`, files/versions.
- [ ] **Tree-sitter AST/symbol chunking ERTELENDİ** → satır + sembol bildirimli fallback (`code_chunker.py`, `code_parser.py` notları).
- [ ] **(`FEATURE_REPOSITORY_INGESTION` varsayılanı `true`)** API kapalıyken 409 döner; kapatmak için `.env`'de `false` gerekir.

## Aşama 8 — Görsel, PNG ve OCR Altyapısı ✅

- [x] OCR provider sözleşmesi (`ocr/base.py`): full_text, blocks, confidence, engine, engine_version.
- [x] Docling + Tesseract provider'ları, fallback zinciri (`ocr/factory.py`; motor yoksa fallback/raporlama).
- [x] Görsel ön işleme (`ocr/preprocessing.py`): EXIF orientation, deskew, denoise, contrast, binarization.
- [x] OCR yönlendirme (`parsers/ocr_routing.py`): dijital PDF'te gereksiz OCR yok, düşük-coverage sayfa/görsel OCR'a.
- [x] OCR sonucu normalize modele `ocr_text` block; düşük confidence `needs_review`; `ocr_json` artifact; bbox citation.
- [x] `FEATURE_OCR=true` (varsayılan) — feature flag ile test edilebilir.

## Aşama 9 — Değerlendirme, Gözlemlenebilirlik ve Güvenlik ✅

- [x] Golden dataset (66 soru) + runner (`tests/evals/run_eval.py`, `golden.jsonl`).
- [x] Metrikler: Recall@1/3/5/10, MRR@10, nDCG@10, no-answer FP/FN, latency; quality gate True (Recall@K=1.0, MRR@10=1.0).
- [x] Generation metrik iskeleti (`evals/generation_metrics.py`; değerler üretim örneğinde n/a).
- [x] Structured log + health/readiness ayrımı, dependency health, rate limiting (in-memory).
- [x] Güvenlik: MIME/magic, boyut/toplam limitleri, archive koruması, prompt-injection testleri, secret redaction, arbitrary-path engeli, CORS `*` değil, stack-trace sızdırılmıyor.

## Aşama 10 — Dokümantasyon, Temizlik ve Son Aktivasyon (DEVAM EDİYOR)

- [x] `context-summary.md` gerçek duruma göre güncellendi.
- [x] `IMPLEMENTATION_CHECKLIST.md` güncel gerçek checklist'e dönüştürüldü (bu dosya).
- [x] `active/current-tasks.md` yanıltıcı iddialardan temizlendi.
- [x] `done/completed-tasks.md` gerçek Aşama 0–9 teslimatlarına göre güncellendi.
- [~] ADR'ler: ADR-001..004 tamam; **ADR-005 (repo tarama güvenlik modeli) ve ADR-006 (OCR provider stratejisi) yazılacak**.
- [ ] Ops runbook'ları: re-index, embedding model/profile değişimi, OCR model/language pack kurulumu, repo scan limitleri, backup/restore.
- [ ] Eski chunk temizliği — yalnız yeni version + eval doğrulandıktan sonra kontrollü.
- [ ] Root `README.md` güncellemesi (ayrı görev).
- [ ] Feature flag aktivasyon/doğrulama raporu (aşağıda).

---

## Feature Flag Aktivasyon Durumu (Section 16)

Kod başına (`src/config.py`) varsayılanlar ve bağlantılar koddan doğrulandı:

| Flag | Varsayılan | Nerede kapılıyor | Durum |
|---|---|---|---|
| `FEATURE_ASYNC_INGESTION` | `true` | `api/v1/documents.py` | ✅ AÇIK, bağlı |
| `FEATURE_STRUCTURED_PARSING` | — (config alanı **yok**) | — | ⚠️ Flag tanımlı değil; yapısal parsing her zaman aktif (rollback anahtarı yok) |
| `FEATURE_HYBRID_RETRIEVAL` | — (config alanı **yok**) | — | ⚠️ Flag tanımlı değil; hibrit retrieval her zaman aktif |
| `FEATURE_RERANKER` | `false` | `retrieval_service.py`, `rerankers/noop.py` | ⚪ KAPALI (tasarım gereği; uzak reranker gateway desteği ister) |
| `FEATURE_OCR` | `true` | `ocr/factory.py`, `parsers/ocr_routing.py` | ✅ AÇIK, bağlı |
| `FEATURE_REPOSITORY_INGESTION` | `true` | `api/v1/repositories.py` (kapalıysa 409) | ✅ AÇIK varsayılan; isterseniz env `false` ile kapatılabilir |
| `FEATURE_NEW_CITATIONS` | `true` | `application/answer_service.py` | ✅ AÇIK, bağlı |

Notlar:
- Çekirdek özelliklerden **async ingestion, OCR, yeni citation'lar doğru biçimde varsayılan AÇIK** ve kod hattına bağlı.
- **`FEATURE_REPOSITORY_INGESTION`** tam uygulanmış, bağlı ve varsayılan `true`'dur (repo/arşiv/klasör ingestion default AÇIK). İstenirse deployment env'inde `false` yapılarak kapatılabilir.
- `FEATURE_STRUCTURED_PARSING` ve `FEATURE_HYBRID_RETRIEVAL` §16'da zorunlu listelenmiş ama `config.py`'de **alan olarak tanımlı değil**; karşılıkları uygulanmış ve her zaman açık. Rollback anahtarı yok. Kod değişikliği gerektirmeyen bilgilendirme niteliğinde bir boşluktur.

---

## Bilinen Sınırlar / Açık Öğeler

1. Tree-sitter ertelemesi → satır/sembol fallback.
2. OCR motorlarının kurulması gerekir (Docling/Tesseract); kurulu değilse fallback/kullanılamaz raporu.
3. Uzak reranker gateway desteği ister; varsayılan noop.
4. Rate limiter in-memory (`RATE_LIMIT_ENABLED=false` relaxed) — üretim için kabul edilebilir.
5. Test venv'deki tek pytest hatası FastAPI 0.141-vs-kilitli-0.109 artefaktı; imajı etkilemez.
