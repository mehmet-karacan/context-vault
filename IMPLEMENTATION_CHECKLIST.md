# Document RAG Platform — Gerçek Durum Checklist'i

**Kaynak:** `DOCUMENT_RAG_PLATFORM_ARCHITECTURE_AND_ROADMAP.md`
**Doğrulama yöntemi:** Kod ve klasör içerikleri tek tek okunarak kontrol edildi (`document-rag-platform/` altında).
**Not:** `context-summary.md` ve `done/completed-tasks.md` dosyaları "MVP TAMAMLANDI" diyor ancak bu iddialar koddaki gerçek durumla **örtüşmüyor**. Aşağıdaki işaretler dosya içeriklerine bakılarak doğrulanmıştır, önceki özet dosyalarına güvenilmemiştir.

Lejant: `[x]` doğrulanmış tamam · `[~]` kısmi / sadece iskelet · `[ ]` yok

---

## Aşama 0 — Kapsam ve Mimari Kararlar

- [x] Repository / monorepo dizin yapısı oluşturuldu (`apps/`, `services/`, `packages/`, `tests/`, `infra/`, `docs/`)
- [ ] ADR'ler (ADR-001..006) — `docs/adr/` **boş**, hiç ADR dosyası yok
- [ ] Desteklenen dosya türleri / boyut limitleri resmi olarak dokümante edilmiş değil (kodda sabit yok)
- [ ] Golden soru-cevap seti — `tests/evals/datasets/` **boş**

## Aşama 1 — Temel Altyapı ve Veri Modeli

- [x] Next.js projesi oluşturuldu (`apps/web`, package.json gerçek)
- [~] FastAPI uygulaması oluşturuldu — **tek dosya** (`main.py`), katmanlı mimari (api/application/domain/infrastructure) sadece boş klasörler halinde
- [~] PostgreSQL + pgvector — docker-compose'da tanımlı, ama backend hiç DB'ye bağlanmıyor (in-memory liste kullanılıyor)
- [~] Redis, MinIO — docker-compose'da tanımlı, backend kodunda hiç kullanılmıyor
- [ ] Migration altyapısı — Alembic klasörü var ama `versions/` içinde `.gitkeep` dışında hiçbir şey yok
- [ ] Temel tablolar (13 tablo iddiası) — hiçbir SQLAlchemy modeli / migration yok
- [x] Health-check endpoint'i (`GET /health`) — var, basit
- [ ] OpenAPI'den TypeScript client üretimi — `packages/api-client` **boş**
- [ ] CI içinde lint/type-check/test — `.github` var ama içerik doğrulanmadı, `tests/` klasörleri boş

## Aşama 2 — Dosya Yükleme ve Canlı Durum

- [~] Document oluşturma endpoint'i — var ama presigned URL yok, dosya doğrudan backend'e upload ediliyor (`POST /documents/upload`)
- [ ] Presigned URL üretimi — yok
- [x] Upload ekranı (frontend `app/upload/page.tsx`)
- [ ] `ingestion_jobs` / `ingestion_events` tabloları — yok (DB yok)
- [ ] Celery worker — `workers/` klasörü **boş**, docker-compose'daki worker komutu çalışmayan bir modülü işaret ediyor
- [ ] State machine (12 durum) — sadece 4 basit enum değeri var (`uploaded/processing/indexed/error`)
- [ ] SSE endpoint'i — yok, senkron `await` ile bekleniyor
- [ ] Retry endpoint'i — yok

## Aşama 3 — Gerçek Belge İşleme

- [~] PDF/DOCX/TXT parser — var (`PyPDF2`, `python-docx`) ama Docling **kullanılmıyor**, MD desteği yok
- [~] Chunker — var ama basit karakter-bazlı bölme (500 karakter + 50 overlap), token-bazlı değil, yapısal (başlık/tablo) farkındalığı yok
- [ ] Chunk metadata'sı (chunk_id, section_path, page_start/end vb.) — yok
- [~] Embedding provider — `sentence-transformers` ile BGE-M3 var, adapter katmanı yok (doğrudan `main.py` içinde)
- [ ] pgvector kayıtları / HNSW index — yok (embedding'ler `dict` içinde bellekte tutuluyor)
- [ ] Full-text search kolonu — yok
- [ ] Idempotent retry — yok
- [ ] Belge sürümleme — yok

## Aşama 4 — Sohbet ve Kaynak Gösterme

- [x] Sohbet ekranı (`app/chat/page.tsx`)
- [~] Retrieval — sadece cosine similarity ile tek-en-yakın chunk (`np.argmax`), hibrit arama / RRF yok
- [ ] Reranking — yok
- [ ] Prompt şablonu + LLM çağrısı — **yok**, chat endpoint'i LLM'e hiç gitmiyor, en benzer chunk'ı olduğu gibi döndürüyor
- [ ] Streaming cevap (SSE/fetch stream) — yok, düz JSON response
- [ ] `message_citations` / kaynak-sayfa ilişkisi — yok
- [ ] Abstention davranışı ("bu bilgi belgelerde yok") — yok, sadece eşik altı sonuçlarda sabit mesaj
- [ ] Sohbet geçmişi / conversation tablosu — yok

## Aşama 5 — Kalite ve Değerlendirme

- [ ] Golden dataset runner — `tests/evals/*` tamamen boş
- [ ] Recall@K, MRR ölçümü — yok
- [ ] Prompt injection / çelişkili belge / Türkçe testleri — yok

## Aşama 6 — Güvenlik ve Üretim Hazırlığı

- [ ] Kimlik doğrulama — yok
- [ ] Workspace / tenant izolasyonu, RLS — yok (tek kullanıcılı, tenant kavramı yok)
- [ ] MIME / magic-byte doğrulaması — yok (sadece dosya uzantısına bakılıyor)
- [ ] Dosya boyutu / sayfa limiti — yok
- [ ] Antivirüs taraması — yok
- [ ] Rate limiting — yok
- [ ] Structured logging, metrics — yok
- [ ] Backup/restore, secret manager, staging, CI/CD — doğrulanmadı / yok

---

## Özet

| Aşama | Dokümandaki iddia | Gerçek durum |
|---|---|---|
| 0 | ✅ Tamamlandı | Sadece klasör iskeleti; ADR ve golden set yok |
| 1 | ✅ Tamamlandı | Next.js + tek dosya FastAPI var; DB/queue/storage bağlı değil |
| 2 | ✅ Tamamlandı | Basit senkron upload var; job kuyruğu, SSE, state machine yok |
| 3 | ✅ Tamamlandı | Basit parse+chunk+embed var; pgvector'a hiç yazılmıyor, bellekte duruyor |
| 4 | ✅ Tamamlandı | Chat ekranı var ama **LLM entegrasyonu yok**, sadece benzerlik araması |
| 5 | ✅ Tamamlandı | Değerlendirme altyapısı tamamen boş |
| 6 | Kısmen | Güvenlik maddelerinin hiçbiri kodda yok |

**Sonuç:** Elimizdeki şey, mimari dokümanın öngördüğü sisteme göre gerçek bir **tek-dosyalık, bellek-içi demo/prototip** (upload → basit chunk → embedding → cosine similarity ile en yakın parçayı gösterme). Dokümandaki modüler monolit mimarisi, veritabanı katmanı, worker/queue, hibrit retrieval, LLM tabanlı cevap üretimi ve güvenlik gereksinimlerinin neredeyse hiçbiri henüz uygulanmamış.

## Önerilen sıradaki adımlar

1. `done/completed-tasks.md` ve `context-summary.md` içeriğini gerçek duruma göre düzelt (yanıltıcı, gelecekte kafa karıştırır).
2. Backend'i gerçekten PostgreSQL + SQLAlchemy'ye bağla, ilk migration'ı oluştur.
3. Chat endpoint'ine gerçek bir LLM çağrısı ekle (şu an sadece en yakın chunk'ı döndürüyor, cevap üretmiyor).
4. Katmanlı mimariyi (api/application/domain/infrastructure) doldur ya da bilinçli olarak basit tek-dosya yaklaşımında kal — ikisi arasında karar ver.
5. pgvector'a gerçek yazma + hibrit arama (en azından full-text + vector) ekle.
