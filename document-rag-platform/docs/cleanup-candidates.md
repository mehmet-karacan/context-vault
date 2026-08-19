# Cleanup Candidates — Aşama 0 Denetimi

**Bu liste yalnızca adaydır; hiçbir dosya/dizin otomatik silinmemiştir.** Aşağıdaki maddeler yalnızca gözlem ve öneridir; silme/taşıma kararı ayrı bir onay ve ayrı bir aşama gerektirir (bkz. `AKTIF_GOREV.md` Bölüm 4, madde 12: "Kullanıcıya ait mevcut dosyalar ve repo içeriği otomatik silinmez").

Doğrulama yöntemi: dosyalar doğrudan okunarak ve `Get-ChildItem`/`grep` ile içerik kontrol edilerek yapıldı; commit `763703d` (repo kökü `AKTIF_GOREV.md`'de belirtilen başlangıç commit'i) üzerinde, 2026-08-19 tarihinde.

---

## 1. Yinelenen / iskelet dizinler

Repo kökünde, `document-rag-platform/` altındakiyle aynı isimde ikinci bir dizin ağacı var. Kanonik dizin `document-rag-platform/`dir (bkz. `AKTIF_GOREV.md` satır 11). Kök seviyesindeki eşleşen dizinler incelendi:

| Kök dizin | İçerik durumu | Gerekçe |
|---|---|---|
| `apps/web/` | Yalnızca `.gitkeep` dosyaları (`app/`, `components/`, `lib/` altında); gerçek `package.json`, kaynak kod yok | İskelet — `document-rag-platform/apps/web/` gerçek Next.js uygulamasının (package.json, node_modules, .next, gerçek app/component dosyaları) bir kopyası/öncüsü gibi duruyor, kullanılmıyor |
| `services/backend/` | Yalnızca boş klasör ağacı (`api/v1`, `application`, `domain`, `infrastructure`, `workers`, `alembic/versions`, `tests` hepsi tek `.gitkeep`) | İskelet — gerçek backend kodu (`main.py`, `llm.py`, `models.py`, `db.py`, `requirements.txt`, `Dockerfile`) yalnızca `document-rag-platform/services/backend/` altında var |
| `docs/adr/` | Yalnızca `.gitkeep` | İskelet — `document-rag-platform/docs/adr/` da aynı şekilde boş (bkz. madde 2), ikisi de ADR içermiyor; iki paralel boş klasör |
| `tests/` (e2e, evals/datasets, evals/generation, evals/retrieval, integration) | Yalnızca `.gitkeep` dosyaları | İskelet — `document-rag-platform/tests/` da neredeyse tamamen boş, tek istisna `tests/evals/baseline/` altında yeni eklenmiş (henüz commit edilmemiş) `db-schema-snapshot.sql` ve `environment-report.md` — bu ikisi Aşama 0 çalışmasının parçası, kök `tests/` ile ilgisi yok |
| `packages/api-client/`, `packages/contracts/` | Yalnızca `.gitkeep` | İskelet — `document-rag-platform/packages/` içindekiler de aynı şekilde boş; hiçbiri kullanılmıyor |
| `infra/compose/`, `infra/docker/` | Yalnızca `.gitkeep` | İskelet — gerçek `docker-compose.yml` ve `Dockerfile` yalnızca `document-rag-platform/` kökünde ve `document-rag-platform/services/backend/` altında var |

**Sonuç:** Kök seviyesindeki `apps/`, `services/`, `docs/`, `tests/`, `packages/`, `infra/` dizinlerinin **tamamı** iskelet/terk edilmiş görünüyor — hiçbiri gerçek kod veya konfigürasyon içermiyor, hepsi yalnızca `.gitkeep` ile monorepo düzenini önceden hazırlamak amacıyla oluşturulmuş. Gerçek çalışan uygulama tamamen `document-rag-platform/` altında.

Not: `document-rag-platform/` kendi içinde de bazı planlanmış-ama-doldurulmamış iskelet klasörler taşıyor (`services/backend/src/api/v1`, `application`, `domain`, `infrastructure/{ai,database,parsing,storage}`, `workers`, `alembic/versions`, `docs/adr`, `tests/`, `packages/*`, `infra/*`). Bunlar silme adayı değildir — bunlar `AKTIF_GOREV.md` Bölüm 7'deki hedef backend dizin yapısının önceden hazırlanmış iskeletleridir ve ileride doldurulacaktır. Yalnızca **kök seviyesindeki birebir kopya** dizin ağacı silme/temizlik adayıdır.

### Diğer kök dosyaları (bilgi amaçlı, silme adayı değil)

- `new 1.txt` — kanonik başlatma komutlarını içeren küçük bir not dosyası (`docker compose up --build`, `npm run dev`). Silme adayı değil ama isimlendirmesi düzensiz; içeriği zaten root README'ye taşınabilir.
- `.idea/`, `.venv/` — `.gitignore` ile zaten dışlanmış, git'e commit edilmemiş yerel geliştirme artefaktları; repo temizliği açısından bir sorun değil.

---

## 2. Doküman / kod çelişkileri

Her madde: **iddia (dosya:satır)** → **gerçek kod (dosya:satır)**.

### 2.1 Benzeri görülmemiş "MVP TAMAMLANDI" iddiaları

- **`context-summary.md:4`** — "✅ MVP TAMAMLANDI", **`context-summary.md:24`** — "Docker: ✅ 6 servis", **`context-summary.md:17`** — "Celery Worker ✅ 5 task, state machine" →
  Gerçek kod: `document-rag-platform/docker-compose.yml:77-88` worker komutu `celery -A src.workers.celery_app worker -l info` çalıştırıyor ama `document-rag-platform/services/backend/src/workers/` dizininde `.gitkeep` dışında hiçbir dosya yok (bkz. Glob taraması, sıfır sonuç); `celery_app` modülü **repoda hiçbir yerde mevcut değil**. Worker container'ı başlatılırsa `ModuleNotFoundError` ile çökeceği kod okumasından açık.
- **`done/completed-tasks.md:33-37`** — "ingestion_jobs ve ingestion_events tabloları ✅", "Celery worker yapısı ✅", "State machine (12 durum) ✅", "SSE endpoint'i ✅", "Retry endpoint'i ✅", **`done/completed-tasks.md:7`** — "Mimari karar kayıtları (ADR'ler) oluşturuldu (6 adet)" →
  Gerçek kod: `document-rag-platform/services/backend/src/models.py:1-57` içinde yalnızca `Project`, `Document`, `Chunk` tabloları var; `ingestion_jobs`/`ingestion_events` yok. `document-rag-platform/docs/adr/` içinde `.gitkeep` dışında dosya yok (0 ADR). `Document.status` alanı (`models.py:35`) yalnızca düz `String`, 4 basit değer alıyor (`uploaded/processing/indexed/error` — `main.py:305`), 12 durumlu bir state machine yok. `main.py` içinde SSE endpoint'i veya retry endpoint'i yok (bkz. `main.py` route listesi, satır 158-398 — sadece `/`, `/health`, `/projects`, `/chat/models`, `/documents/upload`, `/documents`, `/documents/{id}`, `/documents/{id}/status`, `/documents/{id}/delete`, `/chat/query`).
- **`active/current-tasks.md:3-16`** — "✅ TAMAMLANDI - MVP Hazır!" ve Aşama 0-5'in tamamının "✅" işaretlenmesi → Aynı gerekçelerle yanıltıcı; bu dosya `done/completed-tasks.md`'nin özetini tekrarlıyor ve aynı hatalı iddiaları taşıyor.

### 2.2 `IMPLEMENTATION_CHECKLIST.md`'nin kendisi artık güncel kodla çelişiyor

Bu dosya daha önceki bir denetimde `context-summary.md`/`done/completed-tasks.md`'deki "MVP TAMAMLANDI" iddialarını düzeltmek için yazılmış, fakat şu anki koda göre **kendisi de artık kısmen yanlış/eski**:

- **`IMPLEMENTATION_CHECKLIST.md:22`** — "PostgreSQL + pgvector — docker-compose'da tanımlı, ama backend hiç DB'ye bağlanmıyor (in-memory liste kullanılıyor)" →
  Gerçek kod: `document-rag-platform/services/backend/src/db.py:1-58` gerçek bir SQLAlchemy engine + `SessionLocal` kurar, `init_db()` içinde `CREATE EXTENSION vector` ve gerçek `chunks_embedding_idx` HNSW index'i oluşturur; `main.py` içindeki her endpoint `Depends(get_db)` ile gerçek bir Postgres session kullanıyor (örn. `main.py:174-201` projeler, `main.py:283-321` belgeler). In-memory liste yok.
- **`IMPLEMENTATION_CHECKLIST.md:57`** — "Prompt şablonu + LLM çağrısı — yok, chat endpoint'i LLM'e hiç gitmiyor, en benzer chunk'ı olduğu gibi döndürüyor" →
  Gerçek kod: `document-rag-platform/services/backend/src/main.py:391` `generate_answer(...)` çağrısı var; `document-rag-platform/services/backend/src/llm.py:55-78` gerçek bir OpenAI-uyumlu `chat.completions.create` çağrısı yapıyor, sistem promptu (`RAG_SYSTEM_PROMPT`, `llm.py:19-27`) ve günlük-sohbet promptu (`CHAT_SYSTEM_PROMPT`, `llm.py:29-33`) ayrı tanımlı.
- **`IMPLEMENTATION_CHECKLIST.md:55`** — "sadece cosine similarity ile tek-en-yakın chunk (`np.argmax`)" →
  Gerçek kod: `main.py:337,347,358-385` `TOP_K=3`, `SIMILARITY_THRESHOLD=0.55` ve ayrı bir keyword/ILIKE eşleştirme yolu var (bkz. 2.5); tek-chunk `argmax` yaklaşımı yok.
- **`IMPLEMENTATION_CHECKLIST.md:44`** — "Chunker — var ama basit karakter-bazlı bölme (500 karakter + 50 overlap)" → **doğru**, bu iddia güncel kodla uyuşuyor (`main.py:97-126`, `main.py:213,248`).
- **`IMPLEMENTATION_CHECKLIST.md:43`** — "PDF/DOCX/TXT parser — var (PyPDF2, python-docx) ama Docling kullanılmıyor, MD desteği yok" → PyPDF2/python-docx kısmı doğru (`main.py:67-94`), ancak "MD desteği yok" iddiası **yanlış**: `main.py:92` `.md` uzantısını `.txt` ile aynı düz-metin fonksiyonuna yönlendiriyor (Markdown yapısını korumadan, ama "destek yok" değil, "yapısız destek var").

Genel değerlendirme: `IMPLEMENTATION_CHECKLIST.md` muhtemelen kod bu haline gelmeden önceki bir ara duruma göre yazılmış; DB entegrasyonu ve LLM çağrısı o tarihten sonra eklenmiş olmalı. Doküman artık **kendisi de güncel değil** ve tek başına doğru kabul edilmemeli (tam olarak `AKTIF_GOREV.md` Bölüm 2, madde 3'ün uyardığı durum).

### 2.3 `document-rag-platform/README.md` ile kod arasındaki eşik farkı

- **`document-rag-platform/README.md:17`** — "Alaka eşiği: Kosinüs benzerliği ≥ 0.52" ve **`document-rag-platform/README.md:119`** — "0.52 alaka eşiği, sınırlı sayıda gerçek sorguyla kalibre edildi" →
  Gerçek kod: `document-rag-platform/services/backend/src/main.py:347` `SIMILARITY_THRESHOLD = 0.55`. README'de yazan `0.52` ile koddaki `0.55` **birbirini tutmuyor** — tam olarak `AKTIF_GOREV.md:74`'ün önceden işaret ettiği çelişki.

### 2.4 `document-rag-platform/README.md`'nin diğer güncel-olmayan iddiaları

- **`document-rag-platform/README.md:26,29`** — "Arka planda kuyruk/worker çalıştırmaz — yükleme senkron işlenir" → Bu doğru ve güncel (worker gerçekten bağlı değil), ama README'nin "Yapmaz" tablosunda hiçbir yerde henüz reklamı yapılan bir kuyruk/worker planından bahsedilmiyor; bu README ile `AKTIF_GOREV.md`'nin Aşama 2 hedefleri (gerçek worker) arasında henüz bir çelişki yok, sadece bilgi eksikliği var.
- **`document-rag-platform/README.md:19`** — "Docker Compose (postgres · redis · minio · backend) + `npm run dev` (frontend)" — worker servisi listede yok (docker-compose.yml'da worker gerçekten var, `document-rag-platform/docker-compose.yml:77-92`) ama zaten çalışmayan bir modüle bağlı olduğu için README bunu es geçmiş — tutarlı ama okuyucuyu yanıltabilir çünkü worker container'ı `docker compose up` ile başlatılır ve hemen crash olur.

### 2.5 `AKTIF_GOREV.md`'nin "başlangıç gerçekliği" olarak listelediği iddiaların doğrulama sonucu

Aşağıdaki iddiaların hepsi kod okunarak **doğrulandı** (çelişki değil, teyit):

| İddia (`AKTIF_GOREV.md` satır) | Doğrulama |
|---|---|
| `EMBEDDING_MODEL` varsayılanı `openai/BAAI/bge-m3` (satır 66) | Doğru — `document-rag-platform/services/backend/src/llm.py:8` |
| `Chunk.embedding` alanı `Vector(1024)` (satır 68) | Doğru — `document-rag-platform/services/backend/src/models.py:11,54` (`EMBEDDING_DIMENSION = 1024`) |
| Cosine HNSW index (satır 69) | Doğru — `document-rag-platform/services/backend/src/db.py:47-53` |
| DOCX parser yalnızca `doc.paragraphs` (satır 70) | Doğru — `document-rag-platform/services/backend/src/main.py:67-71`, tablo/başlık işlenmiyor |
| Chunking ~500 karakter + 50 overlap, token bazlı değil (satır 71) | Doğru — `main.py:97,213,248` |
| Dense cosine + `ILIKE` kelime eşleşmesi (satır 72) | Doğru — `main.py:328-333` (cosine), `main.py:361` (ILIKE) |
| `TOP_K = 3` (satır 73) | Doğru — `main.py:337` |
| Global eşik kodda `0.55`, README'de farklı (satır 74) | Doğru — bkz. 2.3 (kod `0.55`, README `0.52`) |
| Reranker yok (satır 76) | Doğru — `llm.py` ve `main.py` içinde reranker/rerank referansı yok |
| Gerçek full-text search / BM25 / RRF yok (satır 77) | Doğru — yalnızca `ILIKE`, `search_vector`/`tsvector`/`rrf` referansı yok |
| Orijinal dosya MinIO'da kalıcı tutulmaz, geçici dosya silinir (satır 79) | Doğru — `main.py:234-280`, `tempfile.NamedTemporaryFile` + `finally` bloğunda `os.unlink` |
| Worker `celery_app` modülüne bağlı olmayabilir (satır 81) | Doğru — bkz. 2.1, modül repoda yok |
| Test/evaluation klasörleri büyük ölçüde boş (satır 82) | Doğru — `document-rag-platform/services/backend/tests/` yalnızca `.gitkeep`; `document-rag-platform/tests/evals/{datasets,generation,retrieval}` yalnızca `.gitkeep` (yalnız `tests/evals/baseline/` altında bu denetimden bağımsız yeni eklenmiş, henüz commit edilmemiş 2 dosya var) |
| Repo kökü ve `document-rag-platform/` altında yinelenen iskelet dizinler (satır 83) | Doğru — bkz. Bölüm 1 |

### 2.6 Kaynak gösterilmeyen ek gözlem

- `document-rag-platform/services/backend/src/api/v1/`, `application/`, `domain/`, `infrastructure/*`, `workers/` klasörleri var ama hepsi `.gitkeep` — `AKTIF_GOREV.md` Bölüm 7'deki hedef yapı önceden iskelet olarak açılmış ama henüz hiçbir dosya taşınmamış/oluşturulmamış. Bu, "modüler backend iskeleti" ile ilgili herhangi bir doküman iddiası varsa (yok, ama gelecekte biri "iskelet var, taşındı" derse) önceden yanlış olacaktır.
- `document-rag-platform/services/backend/ttroot-g3.crt` — backend dizininde bir sertifika dosyası duruyor; hiçbir doküman bundan bahsetmiyor, amacı (muhtemelen kurumsal gateway TLS güveni) dokümante edilmemiş.
