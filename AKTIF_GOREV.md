# AKTIF_GOREV.md

## Context Vault V3 — Güvenilir Çekirdek, Tek Kanonik Yapı ve Süreklilik Platformu

| Alan | Değer |
|---|---|
| Durum | `APPROVED_ACTIVE_TASK` |
| Yetki modeli | `AUTHORIZED_WITH_ADMISSION_GATES` |
| Onay kaynağı | Mehmet KARACAN'ın 2026-09-02 tarihli “güncel depoyu yeniden incele, eksik/hatalı kısımları bul ve uygulanabilir aktif görev oluştur” talebi |
| Hazırlanma zamanı | 2026-09-02, Europe/Istanbul |
| Depo | `mehmet-karacan/context-vault` |
| Baseline branch | `main` |
| Baseline HEAD | `6b99a53d19e7f5f7b07b403e751c629f79ab7663` |
| Mevcut kanonik uygulama kökü | `document-rag-platform/` — ADR-001 korunacaktır |
| Yerel uygulama/commit | Bu görev kapsamı ve kabul kapıları içinde yetkili |
| Remote push / merge / release | **Yetkisizdir.** Mehmet KARACAN'ın ayrıca açık onayı gerekir |
| Force-push / history rewrite | **Kesinlikle yetkisizdir.** Yalnız ayrı güvenlik olayı kararı ve açık onayla ele alınabilir |
| Önceki aktif görev | Değiştirilmeden arşivlenecek; bu dosya tamamlanmamış bulguları devralarak onu supersede eder |

> Bu dosya yalnız tavsiye listesi değildir. Uygulama sırası, durma koşulları, kanıtlar, test kapıları, veri koruma kuralları ve tamamlanma ölçütleriyle birlikte yürütülebilir aktif görevdir.

---

## 1. Görevin amacı

Context Vault şu an değerli bir belge/kod RAG prototipine ve önemli miktarda uygulama koduna sahiptir; ancak depo durumu, çalışan veritabanı, migration geçmişi, CI, güvenlik sınırları, ingestion yolları, retrieval/citation semantiği ve durum dokümanları aynı gerçeği anlatmamaktadır.

Bu görevin amacı sistemi yalnız “daha fazla özellik içeren bir RAG uygulaması” yapmak değildir. Hedef:

1. Kaybolmuş veya çelişkili sistem gerçeğini yeniden kurmak.
2. Veriyi ve mevcut işlevleri kaybetmeden migration/CI/runtime bütünlüğünü onarmak.
3. Yetkilendirmesi fail-closed, kanıtları izlenebilir, tekrar çalıştırılabilir ve ölçülebilir bir RAG çekirdeği oluşturmak.
4. Bu sağlam çekirdeğin üzerine; proje bağlamını, aktif işleri, kararları, kanıtları, modelleri, skill'leri ve çoklu CLI kullanımını yöneten gerçek bir **Context Vault süreklilik katmanı** kurmak.
5. Sistemin başarısını checkbox veya doküman iddiasıyla değil; temiz kurulum, restore denemesi, gerçek uçtan uca test, güvenlik sızıntısı testi, ölçüm raporu ve bağımsız doğrulama ile kanıtlamak.

### 1.1 “Mükemmel” kabul edilen hedef nitelikler

Sistem ancak aşağıdaki niteliklerin tamamını taşıdığında olgun kabul edilir:

- **Tek gerçek:** kod, şema, runtime, CI ve dokümantasyon birbirini doğrular.
- **Geri getirilebilirlik:** PostgreSQL ve nesne deposu yedeği gerçekten restore edilmiştir.
- **Fail-closed güvenlik:** scope/kimlik/filtre hatası daha geniş aramaya dönüşmez.
- **İzlenebilirlik:** her cevap, kullanılan bağlam, model, profil, sürüm ve kanıta bağlanır.
- **İdempotans:** retry, worker çökmesi veya çift teslim veri çoğaltmaz ve aktif sürümü bozmaz.
- **Ölçülebilirlik:** kalite, gecikme, sızıntı, cevaplanabilirlik ve citation doğruluğu gerçek dataset ile izlenir.
- **Sağlayıcı bağımsızlığı:** çekirdek OpenCode, Claude, Codex veya tek bir LLM/embedding sağlayıcısına gömülmez.
- **Sınırlı bağlam:** tüm vault otomatik yüklenmez; gerekli ve ilgili içerik bütçeli şekilde derlenir.
- **İnsan kontrollü mutasyon:** hazırlama salt-okunur; uygulama açık scope, drift kontrolü, claim ve receipt ister.
- **Public-safe kaynak:** public Git geçmişine kurum içi güven zinciri, endpoint, kimlik bilgisi, ham özel belge veya runtime dökümü girmez.

---

## 2. Otorite ve çalışma protokolü

### 2.1 Otorite sırası

Bir çelişki olduğunda aşağıdaki sıra bağlayıcıdır:

1. Mehmet KARACAN'ın açık ve güncel yazılı kararı.
2. Bu dosyanın baseline'a bağlı aktif kapsamı ve admission gate'leri.
3. Doğrudan ölçülen runtime/şema/CI kanıtı.
4. Baseline commit'teki çalışan kod ve migration dosyaları.
5. Kabul edilmiş ADR'ler.
6. README, checklist, context-summary, tamamlandı listeleri ve diğer anlatı dokümanları.

Markdown checkbox'ı, commit mesajı, sohbet özeti, Obsidian görünümü, dashboard, model cevabı veya yerel Git geçmişi tek başına operasyonel gerçek değildir.

### 2.2 Başlangıç protokolü

Her uygulama oturumu aşağıdakilerle başlamalıdır:

1. `git status --short --branch` ile çalışma ağacı doğrulanır.
2. `git rev-parse HEAD` alınır.
3. HEAD bu dosyadaki baseline'dan farklıysa **kod mutasyonu yapılmaz**; yalnız drift raporu üretilir ve görev baseline'ı insan onayıyla yenilenir.
4. Doğrudan `main` üzerinde çalışılmaz. Yerel çalışma branch'i: `fix/context-vault-v3-hardening`.
5. Önceki `AKTIF_GOREV.md`, içerik değiştirilmeden `done/active-tasks/2026-08-31-rag-reliability-productization.md` yoluna taşınır.
6. Bu dosya depo köküne `AKTIF_GOREV.md` adıyla konur.
7. Çalışma ağacında kullanıcıya ait ilgisiz değişiklik varsa silinmez, stash edilmez, formatlanmaz ve commit'e alınmaz; görev durur ve durum receipt'e yazılır.
8. İlk mutasyondan önce salt-okunur admission audit'i tamamlanır.

### 2.3 Her aşamanın zorunlu döngüsü

Her aşama aynı protokolle yürütülür:

1. **Prepare:** kapsam, etkilenen dosyalar, veri etkisi, test planı ve rollback salt-okunur hazırlanır.
2. **Drift check:** branch başlangıç SHA'sı, DB revision'ı, şema fingerprint'i ve ilgili artifact hash'leri tekrar doğrulanır.
3. **Claim:** aynı kaynak üzerinde başka mutasyon olmadığını gösteren çalışma kaydı oluşturulur.
4. **Apply:** yalnız o aşamanın tanımlı kapsamı uygulanır.
5. **Verify:** pozitif, negatif, hata ve geri dönüş testleri çalıştırılır.
6. **Receipt:** komut, başlangıç/bitiş zamanı, exit code, başlangıç/son SHA, test sonucu, artifact hash'i ve bilinen risk kaydedilir.
7. **Independent review:** aşamayı uygulamayan ayrı ajan/oturum veya insan, yalnız kanıta bakarak doğrular.
8. **Local commit:** testler ve receipt tamamlanmadan commit atılmaz. Push yapılmaz.

### 2.4 Kanıt sınıflandırması

- Public repoya yalnız redakte edilmiş, kişisel/kurumsal veri içermeyen özet kanıt girer.
- Ham DB dump, MinIO liste dökümü, endpoint, token, kurum içi path, belge adı/içeriği ve hassas log public repoya girmez.
- Özel kanıtlar repo dışı güvenli konumda tutulur; public receipt yalnız hash, sayım ve sonuç içerir.
- “Çalıştı” ifadesi kanıt değildir. Exit code `0`, test raporu ve exact SHA gerekir.

---

## 3. 2026-09-02 güncel durum özeti

### 3.1 Baseline gerçekleri

- Public depo, default branch `main`.
- Yalnız `main` branch'i görünmektedir; branch protection etkin değildir ve repository ruleset listesi boştur.
- Güncel HEAD `6b99a53d19e7f5f7b07b403e751c629f79ab7663`.
- Son commit backend, frontend, RAG eval ve security workflow'larının otomatik push/PR tetiklerini kaldırıp yalnız `workflow_dispatch` bırakmıştır.
- `commit-ownership` otomatik çalışmaya devam etmektedir; esas kalite kapıları otomatik değildir.
- Açık issue ve yayınlanmış release görünmemektedir.
- ADR-001, `document-rag-platform/` dizinini kanonik uygulama kökü olarak kabul etmiştir; buna rağmen kökte benzer isimli boş/iskelet dizinler hâlâ durmaktadır.

### 3.2 Runtime audit'ten gelen kritik gerçekler

`document-rag-platform/artifacts/audit/2026-08-31-runtime-audit.md` aşağıdaki kritik durumu kaydetmektedir:

- Çalışan DB'nin Alembic revision'ı `b2f1c0a10005`.
- Kaynak kodundaki son revision `b2f1c0a10003`.
- Kaynakta `...0004` ve `...0005` migration dosyaları yoktur.
- Alembic çalışan DB revision'ını çözememektedir; boş DB'den tekrarlanabilir kurulum kanıtı yoktur.
- 337 chunk ve 337 chunk_embedding kaydı vardır; iki chunk aktif document version'ıyla uyuşmamaktadır.
- Aktif embedding profile `config_hash` değeri `NULL` durumundadır.
- Extension'sız bir belge `UnsupportedFileTypeError` ile hata durumundadır.
- Orphan `rag-migrate` container kaydı vardır.
- Gerçek retrieval/generation değerlendirmesi bağlı değildir; fake/offline eval kalite kanıtı değildir.

### 3.3 GitHub Actions'ta doğrulanan gerçek başarısızlıklar

- Backend run `33400923781`, job `99516735112`: dependency kurulumu geçtikten sonra `ruff format --check .` aşamasında kesilmiştir; 97 dosya format dışıdır. Lint, mypy, test, migration ve coverage adımları hiç çalışmamıştır.
- RAG eval run `33400923547`, job `99516734315`: `alembic upgrade head`, uygulama settings singleton'ı `LITELLM_API_KEY` istediği için migration başlamadan kesilmiştir. Eval adımlarının hiçbiri çalışmamıştır.
- Bu başarısızlıklar düzeltilmeden workflow tetiklerinin kapatılması kırmızı kalite durumunu görünmez yapmış, çözmemiştir.

---

## 4. Bulgu ve hata envanteri

Aşağıdaki bulgular bu görevin kapsamıdır. `P0` maddeler çözülmeden yeni ürün özelliği geliştirilemez.

### 4.1 P0 — Dur ve önce düzelt

| Kimlik | Bulgu | Kanıt / konum | Zorunlu sonuç |
|---|---|---|---|
| CV-001 | Çalışan DB migration geçmişi kaynak kodundan iki revision ileride; 04/05 kayıp | runtime audit, `alembic/versions/` | Migration lineage kurtarılacak veya yeni DB'ye doğrulanmış lineage reset/cutover yapılacak |
| CV-002 | CI kalite workflow'ları ilk gerçek çalıştırmada kırmızı olup sonra otomatik tetikleri kapatılmış | `.github/workflows/*`, run `33400923781`, `33400923547` | Kök nedenler düzeltilecek, push/PR tetikleri kalıcı açılacak |
| CV-003 | `main` korumasız, ruleset yok | GitHub branch/ruleset durumu | Required checks + PR koruması etkinleşmeden release yok |
| CV-004 | Public kaynakta kuruma özgü endpoint ve `ttroot-g3.crt` bulunuyor | `.env.example`, `src/config.py`, backend Dockerfile | Kurumsal overlay dışarı alınacak; sertifika/endpoint kararı ve history taraması yapılacak |
| CV-005 | Kimlik doğrulama ve sahiplik sınırı yok; `project_id` opsiyonel, default proje sessiz oluşturulabiliyor | `api/v1/chat.py`, projects/documents routes | Production fail-closed auth + zorunlu workspace/project scope |
| CV-006 | Retriever `TypeError` aldığında filtreleri çıkararak tekrar deneyebiliyor | `application/retrieval_service.py` | Her filtre hatası kapalı başarısızlık; filtresiz fallback yasak |
| CV-007 | Normalize edilmiş içerik secret/redaction kararından önce kalıcı artifact olarak yazılabiliyor | `workers/ingestion_tasks.py` | İçerik politikası storage/embedding/remote-LLM öncesi uygulanacak |
| CV-008 | MinIO, DB ve Celery arasında transaction/outbox yok; orphan obje veya sonsuza kadar queued job oluşabilir | `api/v1/documents.py` | Staging + transactional outbox + sweeper + idempotency |
| CV-009 | Golden etiketlerden sonuç üreten FakeRetriever/FakeAnswerer kalite kapısı olarak sunuluyor | `tests/evals/run_eval.py` | Fake yalnız contract smoke; gerçek pipeline eval zorunlu |
| CV-010 | Durum dokümanları eski SHA ve eski gerçekleri “tamamlandı” olarak gösteriyor | `context-summary.md`, `active/current-tasks.md`, `IMPLEMENTATION_CHECKLIST.md` | Anlatı dokümanı kanıt olmaktan çıkarılacak; machine-readable doğrulanmış durum üretilecek |

### 4.2 P1 — Güvenilirlik ve veri bütünlüğü

| Kimlik | Bulgu | Kanıt / konum | Zorunlu sonuç |
|---|---|---|---|
| CV-011 | Sync upload, async worker ve reindex farklı/legacy parser-chunker akışları kullanıyor | `api/v1/documents.py`, `workers/ingestion_tasks.py`, `application/reindex_service.py` | Tek IngestionOrchestrator ve tek profil sözleşmesi |
| CV-012 | Cross-version chunk kayıtları mevcut | runtime audit | Veri onarımı + DB constraint + invariant testi |
| CV-013 | `documents.active_version_id`, aynı document'a ait version olmayı DB seviyesinde garanti etmiyor | `models.py` | Composite FK/constraint veya güvenli trigger |
| CV-014 | Chunk `document_id` ile `version_id` farklı belgelere işaret edebilir | `models.py` | DB seviyesinde aynı document invariant'ı |
| CV-015 | Dense arama canonical `chunk_embeddings` ile legacy `chunks.embedding` alanını her sorguda union ediyor | `retrieval/dense.py` | Backfill doğrulanınca tek canonical profile-index yolu |
| CV-016 | RRF aynı retriever listesindeki duplicate hit'leri birden fazla katkı olarak sayabiliyor | `retrieval/rrf.py` | Retriever başına chunk başına tek katkı |
| CV-017 | Rank yeniden ataması dinamik `rerank_score` bilgisini kaybedebiliyor | `application/retrieval_service.py` | Immutable typed hit model ve stage score provenance |
| CV-018 | Neighbor genişletme project/document/version/source_file sınırını tam taşımıyor | `retrieval/context_builder.py`, chat/debug resolvers | Tam scope anahtarıyla komşuluk |
| CV-019 | Cevapta kullanılan `[Sx]` etiketleri parse edilmeden tüm adaylar citation olarak persist edilebiliyor | `application/answer_service.py` | Yalnız kullanılan/doğrulanan citation'lar kaydedilecek |
| CV-020 | ContextBuilder bütçesi ile AnswerService'in modele gönderdiği evidence paketi tek kanonik nesne değil | retrieval + answer servisleri | Model yalnız doğrulanmış `ContextBundle` alacak |
| CV-021 | Debug endpoint kimlik/scope olmadan tam içerik ve skor döndürebiliyor | `api/v1/debug.py` | Production'da kapalı + admin rolü + redacted payload |
| CV-022 | Readiness dependency bozukken HTTP 200 `degraded` döndürüyor | `api/v1/health.py` | Hazır değilse 503; liveness ayrı |
| CV-023 | Uygulama startup sırasında extension/index DDL çalıştırıyor ve index hatasını yutuyor | `db.py:init_db` | Tüm DDL Alembic'e; startup yalnız doğrulama |
| CV-024 | Migration, LLM API key'i olmayan temiz CI ortamında bile başlayamıyor | `alembic/env.py`, `config.py` | MigrationConfig yalnız DB alanlarını yükleyecek |
| CV-025 | CI Python sürümü, container sürümü ve araç bağımlılıkları tutarsız | backend workflow, Dockerfile, requirements-dev | Tek runtime sürüm kaynağı; tüm CI araçları lock'ta |
| CV-026 | Transitive lock/hash, action SHA pin, image digest ve güvenilir SBOM kapısı eksik | requirements, workflows, compose | Reproducible supply-chain zinciri |
| CV-027 | Accepted ADR'ye rağmen kök iskeletleri tutuluyor; iki kanonik yapı izlenimi sürüyor | ADR-001, repo root | ADR-001 korunarak boş skeleton'lar kontrollü silinecek |
| CV-028 | Frontend ana sayfa tek büyük bileşen; feature klasörleri boş/yarım | `apps/web/app/page.tsx` | Feature ayrımı, typed API client, testlenebilir state |
| CV-029 | UI karakter bazlı `chunkSize=500` kontrolünü kullanıcı ayarı gibi sunuyor | frontend, legacy upload API | Immutable chunker profile; ham karakter ayarı kaldırılacak |
| CV-030 | Frontend package scripts içinde unit/e2e/typecheck kalite zinciri eksik | `apps/web/package.json` | Test/typecheck/e2e script'leri ve CI |

### 4.3 P2 — Olgunluk, güvenlik ve gerçek Context Vault boşlukları

| Kimlik | Bulgu | Zorunlu sonuç |
|---|---|---|
| CV-031 | Naive `datetime.utcnow()` ve timezone'suz kolonlar | UTC timezone-aware ortak zaman politikası |
| CV-032 | Status, role, stage, progress gibi alanlarda check/enum invariant'ları zayıf | DB constraint + typed domain enum |
| CV-033 | Citation satırı immutable alıntı/hash, retrieval run, prompt/model/profile sürümü taşımıyor | Denetlenebilir evidence snapshot/provenance |
| CV-034 | Secret redaction, veri sınıflandırma ve remote-provider politikası birbirinden ayrılmamış | `ContentPolicyDecision` ve quarantine |
| CV-035 | Document silme DB kaydıyla sınırlı; object/artifact/vector GC yaşam döngüsü yok | Soft-delete + retention + idempotent GC |
| CV-036 | Celery enqueue başarısızlığı güvenli şekilde tekrar sürülemiyor | Outbox dispatcher + retry/lease |
| CV-037 | Reindex delete-first davranışı başarısızlıkta aktif version'ı chunksız bırakabilir | Build-new → validate → atomic activate → later GC |
| CV-038 | Rate limit varsayılan kapalı/in-memory; multi-process veya kötüye kullanım koruması yok | Redis-backed policy; local-only istisna |
| CV-039 | Repository ingestion varsayılan açık; URL/path kaynağı yüksek riskli | Production default kapalı + allowlist + SSRF/path politikası |
| CV-040 | Config'te duplicate alanlar, hardcoded provider default'u ve import-time singleton var | Layered typed config + deployment overlay + dependency injection |
| CV-041 | Issue/release/ruleset yönetimi yok | Release ve değişiklik yönetişimi |
| CV-042 | Dolu ADR dizininde `.gitkeep` ve benzeri temizlik artıkları var | Kanonik ağaç temizliği |
| CV-043 | Checklist “ADR-005/006 yazılacak” derken dosyalar mevcut | Otomatik doküman drift testi |
| CV-044 | Exact SHA'ya bağlı, makinece doğrulanan proje durum manifesti yok | `status/verified-state.json` üretimi |
| CV-045 | Operasyonel iş durumu, kalıcı bilgi, vector search ve analytics ayrımı tanımlı değil | Dört katmanlı otorite modeli |
| CV-046 | Work item, attempt, claim/lease, event ve terminal receipt çekirdeği yok | PostgreSQL Work Graph |
| CV-047 | Proje bağlamını token bütçesiyle derleyen load-tier tabanlı ContextCompiler yok | Bounded context compiler |
| CV-048 | Model/provider/skill kayıtları; güven, sürüm, hash ve data-policy routing'i yok | Registry + policy router |
| CV-049 | CLI entegrasyonları çekirdekten bağımsız adapter sözleşmesine sahip değil | OpenCode/Claude/Codex adapter'ları |
| CV-050 | Ham prompt/transcript veya model cevabının canonical knowledge'a dönüşmesini yöneten promotion akışı yok | Draft → review → approved knowledge lifecycle |

---

## 5. Bağlayıcı mimari kararlar

Bu görev içinde aşağıdaki kararlar yeniden tartışılmadan uygulanacaktır. Değişiklik ancak ayrı ADR ve Mehmet KARACAN onayıyla yapılabilir.

### 5.1 Kanonik depo kökü

- ADR-001 korunur: kanonik uygulama `document-rag-platform/` altındadır.
- Kök seviyedeki gerçek governance dosyaları (`AKTIF_GOREV.md`, `README.md`, `CONTRIBUTING.md`, `.github/`, sahiplik script'leri, `active/`, `done/`) kökte kalabilir.
- Kök seviyedeki yalnız `.gitkeep` içeren sahte `apps/`, `services/`, `docs/`, `tests/`, `packages/`, `infra/` dizinleri inventory sonrası silinir.
- Uygulamayı repo köküne taşıma bu görevin kapsamında değildir. Böyle bir taşıma ancak ölçülmüş fayda, migration planı ve ADR-001'i supersede eden yeni ADR ile yapılabilir.

### 5.2 Veri otoritesi

- PostgreSQL: operasyonel durum, kimlik/scope, sürüm, iş akışı, citation provenance ve Work Graph otoritesi.
- MinIO/S3 uyumlu storage: immutable büyük artifact'ler; PostgreSQL referans ve checksum taşır.
- pgvector/FTS/index: yeniden üretilebilir arama projeksiyonu; canonical bilgi değildir.
- Analytics/metrics: event ve receipt'lerden türetilir; operasyonel karar kaynağı değildir.
- Markdown/Obsidian: insan görünümü ve derlenmiş projection; operasyonel otorite değildir.

### 5.3 Mutasyon protokolü

- `prepare` salt-okunurdur.
- `apply`, expected revision/drift token, açık scope, idempotency key ve geçerli claim/lease olmadan başlayamaz.
- Etki yaratmadan önce claim alınır.
- Tamamlandı sayılmak için terminal receipt zorunludur.
- Fire-and-forget, “komutu gönderdim; bitmiş kabul et”, modelin doğrudan canonical bilgi yazması ve filtresiz fallback yasaktır.

### 5.4 Context yükleme politikası

Tüm bağlam otomatik yüklenmez. Her kaynak şu sınıflardan birine sahip olur:

- `MUST_LOAD`: aktif görev, güvenlik/otorite politikası, mevcut work item, exact scope.
- `SHOULD_LOAD_IF_RELEVANT`: ilgili ADR, proje manifesti, yakın geçmiş karar/receipt.
- `RETRIEVE_ON_DEMAND`: geniş dokümanlar, kod, eski görevler, arşiv.
- `NEVER_AUTO_LOAD`: secrets, ham kişisel veriler, ham transcript, DB dump, özel anahtar, sınıflandırılmamış kurumsal içerik.

### 5.5 Provider ve veri politikası

- Provider core'dan ayrıdır; OpenAI-compatible remote, kurum içi gateway ve yerel embedding aynı sözleşmeyi uygular.
- Embedding profile; provider, model, dimension, distance metric, prefix'ler ve config hash ile immutable sürümlenir.
- Farklı vector uzayları aynı index/kolonda karıştırılmaz.
- Her provider'ın izin verdiği data classification açıkça kayıtlıdır.
- Hassas içerik, policy izin vermeden remote embedding/generation servisine gönderilmez.

---

## 6. Aşama bağımlılık grafiği

```text
AŞAMA 0  Admission, arşiv ve kanıt sabitleme
   ↓
AŞAMA 1  Migration/veri olayı ve güvenli restore
   ↓
AŞAMA 2  CI, branch koruması ve supply-chain
   ↓
AŞAMA 3  Kanonik ağaç ve doğrulanabilir dokümantasyon
   ↓
AŞAMA 4  Kimlik, workspace/project scope ve fail-closed API
   ↓
AŞAMA 5  Şema invariant'ları ve zaman modeli
   ↓
AŞAMA 6  Tek ingestion hattı, outbox ve içerik politikası
   ↓
AŞAMA 7  Typed retrieval, active-version/profile ve context bundle
   ↓
AŞAMA 8  Yapılandırılmış cevap, citation doğrulama ve prompt güvenliği
   ↓
AŞAMA 9  Gerçek eval, adversarial/fault/concurrency kapıları
   ↓
AŞAMA 10 Frontend ve ürün sözleşmesi
   ↓
AŞAMA 11 Operasyon, gözlemlenebilirlik, backup/DR ve release candidate
   ↓
AŞAMA 12 Gerçek Context Vault Work Graph + ContextCompiler + registry'ler
   ↓
AŞAMA 13 Bağımsız doğrulama, doküman kapanışı ve release kararı
```

- Aşama 0–2 bitmeden özellik geliştirilmez.
- Aşama 4–9 bitmeden RAG “production-ready” olarak etiketlenmez.
- Aşama 12, Aşama 9'un gerçek kalite ve güvenlik kapıları geçmeden başlamaz.
- Aşama atlanamaz; yalnız açık insan kararıyla kapsam küçültülebilir.


## 7. AŞAMA 0 — Admission, arşiv ve kanıt sabitleme

### 7.1 Amaç

Hiçbir şeyi “düzeltmeye” başlamadan önce hangi repo, şema, veri ve CI durumunun düzeltildiğini ispatlanabilir biçimde sabitlemek.

### 7.2 Yapılacaklar

- [x] Çalışma ağacının temizliği ve exact HEAD doğrulanacak.
- [x] Yerel `fix/context-vault-v3-hardening` branch'i oluşturulacak; `main` üzerinde commit atılmayacak.
- [x] Önceki aktif görev byte-for-byte arşivlenecek; kaynak ve arşiv SHA-256 değerleri aynı olacak.
- [x] Aşağıdaki public-safe audit yapısı oluşturulacak:

```text
document-rag-platform/artifacts/audit/2026-09-02-baseline/
  audit-manifest.json
  repository-summary.md
  ci-summary.md
  schema-summary.md
  data-integrity-summary.md
  security-exposure-summary.md
  documentation-drift-summary.md
  SHA256SUMS
```

- [x] `audit-manifest.json` en az şu alanları taşıyacak:
  - repository, branch, head_sha, dirty_state;
  - observed_at_utc, observer/tool version;
  - code_alembic_heads, runtime_alembic_version;
  - PostgreSQL ve extension sürümleri;
  - tablo sayımları ve yalnız anonim aggregate invariant sonuçları;
  - compose service/image referansları ve container health sonucu;
  - son kalite workflow run kimlikleri/sonuçları;
  - public tree'deki hassas olabilecek dosya yolları;
  - her audit dosyasının SHA-256 değeri.
- [x] Özel DB/MinIO/log kanıtı repo dışı güvenli dizine alınacak; public manifest yalnız hash ve aggregate sonuç içerecek.
- [x] `context-summary.md`, `active/current-tasks.md`, `IMPLEMENTATION_CHECKLIST.md`, `done/completed-tasks.md`, mevcut `AKTIF_GOREV.md`, ADR'ler ve runbook'lar arasında çelişki matrisi çıkarılacak.
- [x] Kaynak ağacındaki `.gitkeep`, duplicate config, duplicate feature flag, deprecated/legacy yol ve orphan artifact adayları listelenecek.
- [x] GitHub ayarlarının salt-okunur çıktısı alınacak: branch listesi, protection durumu, ruleset, Actions tetikleri, environment/secrets isimleri varsa yalnız isim düzeyinde, açık issues/releases.
- [x] `scripts/verify_baseline.py` oluşturulacak. Script:
  - expected SHA farklıysa non-zero çıkacak;
  - dirty tree varsa non-zero çıkacak;
  - code migration head'lerini yazacak;
  - public audit dosyalarında secret pattern taraması yapacak;
  - JSON çıktısı üretecek.
- [x] Tüm audit çıktılarında endpoint, kullanıcı adı, belge adı, token, IP, kurum içi host ve dosya içeriği redakte edilecek.

### 7.3 Kabul kriterleri

- [x] Önceki aktif görev kayıpsız arşivlenmiş ve hash eşitliği kanıtlanmış.
- [x] Baseline exact SHA `6b99a53d19e7f5f7b07b403e751c629f79ab7663` olarak doğrulanmış.
- [x] Runtime DB revision ile code migration head farkı machine-readable audit'te yer almış.
- [x] Backend ve RAG eval başarısız run kimlikleri/aşamaları audit'te kayıtlı.
- [x] Public audit tree'si gitleaks ve özel regex setinden geçiyor.
- [x] Bu aşamada uygulama kodu, runtime DB verisi ve MinIO objesi değiştirilmemiş.
- [x] Bağımsız doğrulayıcı manifest hash'lerini yeniden üretebiliyor.

### 7.4 Durma koşulları

Aşağıdaki durumlardan biri varsa Aşama 1'e geçilmez:

- Baseline drift etmişse.
- Çalışma ağacında kaynağı belirsiz değişiklik varsa.
- Runtime DB veya object storage'a erişim olmadan “mevcut veri korunacak” deniyorsa.
- Audit public-safe hale getirilemiyorsa.
- Önceki aktif görev arşivi doğrulanamıyorsa.

---

## 8. AŞAMA 1 — Migration/veri olayı ve güvenli restore

### 8.1 Amaç

Kayıp migration lineage'ı körlemesine `stamp` veya downgrade etmeden çözmek; mevcut PostgreSQL/MinIO verisinin geri getirilebilir olduğunu kanıtlamak; blank environment kurulumunu yeniden mümkün kılmak.

### 8.2 Mutasyondan önce zorunlu yedek

- [x] Yeni lineage için tutarlı sentetik PostgreSQL yedeği alınacak:
  - schema-only dump;
  - data dump veya uygun formatta tam dump;
  - roles/extension bilgisi;
  - row-count ve seçili invariant fingerprint'leri.
- [x] Yeni lineage için MinIO object inventory alınacak:
  - bucket, object key hash'i, size, etag/checksum, version bilgisi;
  - içerik veya özel object adı public repoya yazılmayacak.
- [x] Config ve compose/deployment manifestleri secret'sız biçimde kaydedilecek.
- [x] Yedek ayrı bir temiz PostgreSQL ve ayrı bucket'a **restore edilecek**.
- [x] Restore sonrası DB sayımları, FK/invariant sorguları ve object referans bütünlüğü karşılaştırılacak.
- [ ] Restore denenmeden migration dosyası veya canlı veri değiştirilmeyecek.

### 8.3 Kayıp `b2f1c0a10004` / `b2f1c0a10005` karar ağacı

#### Yol A — Exact migration dosyaları kurtarılabiliyorsa

- [x] Yerel reflog, worktree, stash, eski clone, CI artifact, backup, editör geçmişi ve ulaşılabilir Git object'leri salt-okunur taranacak.
- [ ] Bulunan dosyalar revision id, down_revision, içerik hash'i ve çalıştırıldığı dönemin şema diff'iyle doğrulanacak.
- [ ] Dosyalar “tahmini” değil exact olduğuna dair kanıt olmadan Yol A kabul edilmeyecek.
- [ ] `0001 → 0005` temiz DB kurulumu yapılacak.
- [ ] Temiz DB şeması restore edilmiş canlı şema kopyasıyla normalize edilmiş schema diff üzerinden karşılaştırılacak.
- [ ] Sonra yalnız additive/forward reconciliation revision'ı oluşturulacak.

#### Yol B — Exact migration dosyaları kurtarılamıyorsa

- [x] `0004/0005` için boş/no-op sahte dosya oluşturulmayacak.
- [x] Canlı DB üzerinde `alembic stamp`, manuel `DELETE FROM alembic_version` veya kör downgrade yapılmayacak.
- [x] Pre-1.0 lineage reset ADR'si hazırlanacak.
- [x] Mevcut doğrulanabilir şemadan yeni, tek-head'li bir V3 baseline migration seti oluşturulacak.
- [x] Yeni boş DB V3 baseline ile kurulacak.
- [x] Restore edilmiş kopyadan yeni DB'ye idempotent veri taşıma aracı yazılacak.
- [x] Taşıma; document/version/chunk/profile/artifact/job/conversation/citation ilişkilerini kimlik hash'i ve invariant'larla doğrulayacak.
- [x] Uygulama yeni DB üzerinde startup smoke + backend integration + offline eval kapılarından geçecek.
- [ ] Eski DB salt-okunur tutulacak; cutover ancak Mehmet KARACAN'ın ayrıca açık onayıyla yapılacak.
- [ ] Cutover sonrası eski DB retention süresi dolmadan silinmeyecek.

### 8.4 Migration sisteminin düzeltilmesi

- [x] `alembic/env.py`, tam uygulama `Settings()` singleton'ını import etmeyecek.
- [x] Ayrı `MigrationSettings` yalnız DB URL, schema ve güvenli migration parametrelerini yükleyecek.
- [x] Migration için LLM, embedding, Redis veya MinIO anahtarı gerekmeyecek.
- [x] `CREATE EXTENSION`, index ve diğer DDL `db.py:init_db` içinden migration'a taşınacak.
- [x] Startup:
  - DB'yi değiştirmeyecek;
  - beklenen migration head'ini kontrol edecek;
  - uyumsuzsa readiness'i 503 yapacak ve açıklayıcı structured error üretecek.
- [ ] Birden fazla migration head varsa CI fail edecek.
- [x] Migration dosyalarının import-time side effect'i olmayacak.
- [x] Data migration tablo bazında commit edilen, yeniden başlatılabilir ve receipt üreten ayrı komutla yürütülecek.
- [x] Production rollback, güvenli olmadığı yerde zorla downgrade değil restore/cutover prosedürüyle yapılacak.

### 8.5 Veri bütünlüğü onarımı

Yedek/restore kanıtından sonra:

- [ ] İki cross-version chunk tek tek kaynağıyla sınıflandırılacak.
- [ ] Doğru version'a güvenli taşıma veya kontrollü yeniden ingestion yapılacak.
- [ ] `chunks.document_id`, `chunks.version_id → document_versions.document_id` invariant'ı sıfır ihlal verecek.
- [ ] Aktif embedding profile için deterministik `config_hash` hesaplanacak; hash model/prefix/dimension/distance/provider sürümünü kapsayacak.
- [ ] Extension'sız hata belgesi silinmeden önce binary/MIME sniff ile sınıflandırılacak; destekliyse yeniden işlenecek, değilse güvenli ve anlaşılır terminal hata durumuna alınacak.
- [ ] Orphan `rag-migrate` container'ın kaynağı belirlenip güvenli şekilde kaldırılacak; compose bir-shot migration servisi idempotent hale getirilecek.
- [ ] DB'de orphan artifact referansı, version'sız chunk, profilesız embedding, aktif olmayan version'a bağlı retrieval adayı ve bozuk FK sıfır olacak.

### 8.6 Test matrisi

- [x] Blank DB: base → head.
- [x] Restore edilmiş sentetik production-benzeri DB: mevcut durum → hedef head.
- [x] Re-run: `upgrade head` ikinci kez veri değiştirmemeli.
- [x] Uygun reversible migration için clean DB `upgrade → downgrade → upgrade`.
- [x] Data migration kontrollü olarak yarıda durdurulup güvenli retry edildi.
- [x] Uygulama beklenen head gerisinde/ilerisinde DB ile başlamayı reddeder veya not-ready olur.
- [x] Yanlış/eksik LLM key migration'ı etkilemez.
- [x] Schema fingerprint hedefle birebir eşleşir.

### 8.7 Teslimatlar

- [x] `docs/adr/ADR-007-migration-lineage-recovery-or-reset.md`
- [x] Güncel `MIGRATION_RUNBOOK.md`
- [x] `scripts/verify_migrations.py`
- [x] Seçilen yola göre V3 baseline ve `scripts/migrate_v3_data.py` aracı
- [x] Public-safe migration incident receipt
- [x] Private restore drill kanıtı ve public hash özeti

### 8.8 Kabul kriterleri

- [x] Kaynak kodu ve çalışan hedef DB aynı tanınabilir migration head'ine sahip.
- [x] Blank DB tek komut zinciriyle kurulabiliyor.
- [x] Restore edilmiş sentetik veri kopyası hedef sürüme taşınıp doğrulanmış.
- [x] Cross-version ve orphan invariant ihlalleri doğrulanan V3 fixture'ında sıfır.
- [x] `alembic upgrade head`, LLM/MinIO/Redis secret'ı olmadan çalışıyor.
- [x] Startup DDL yapmıyor.
- [x] Kör stamp, veri kaybı, sahte revision veya doğrulanmamış downgrade yapılmamış.

### 8.9 Aşama 1 uygulama durumu — 2026-09-02

- Durum: `PASS_FOR_NEW_V3_LINEAGE`.
- Yol A maddeleri uygulanmadı; exact `0004/0005` bulunamadığı için Yol B seçildi.
- Eski DB'yi read-only tutma/retention ve eski runtime'daki iki cross-version
  chunk, extension'sız belge ve `rag-migrate` onarımı `NOT_APPLICABLE_SOURCE_UNAVAILABLE`
  olarak sınıflandırıldı; bu maddeler yapılmış gibi işaretlenmedi.
- Yeni V3 DB'de migration verifier tüm ölçülen invariant'ları `0` buldu; aktif
  profil deterministic `config_hash` taşıyor.
- `scripts/migrate_v3_data.py` kaynak bağlantıyı read-only açtı; kontrollü
  kesinti `RECOVERY_REQUIRED`, retry ve ikinci re-run `PASS` verdi.
- Yeni hedefte startup smoke `HTTP 200`; offline eval `33 passed`; backend
  regresyon paketi `516 passed, 6 skipped`.
- Public receipts:
  `artifacts/migrations/2026-09-02-v3-lineage/MIGRATION_RECEIPT.json` ve
  `DATA_MIGRATION_RECEIPT.json`.

---

## 9. AŞAMA 2 — CI, branch koruması ve supply-chain

### 9.1 Amaç

Kaliteyi görünmez yapmak yerine her push/PR'da otomatik, tekrarlanabilir ve zorunlu hale getirmek.

### 9.2 Yerel kalite tabanını onarma

- [ ] Python runtime sürümü için tek kaynak seçilecek (`.python-version` ve/veya `pyproject.toml`); Docker, CI ve doküman aynı sürümü kullanacak.
- [ ] Node runtime için tek sürüm kaynağı seçilecek; frontend image ve CI aynı sürümü kullanacak.
- [ ] Backend packaging `pyproject.toml` + transitive lock/hash zincirine geçirilecek. Tercih edilen tek araç `uv`; eşdeğer araç seçilecekse ADR ile değiştirilir.
- [ ] Runtime, test, lint, type, security ve migration araçları açık gruplara ayrılacak.
- [ ] `pytest-cov`, `pip-audit`, SBOM üreticisi ve workflow'da çağrılan her araç lock içinde bulunacak.
- [ ] İlk formatter düzeltmesi yalnız mekanik bir commit olacak:
  - davranış değişikliği yok;
  - format öncesi/sonrası test sonucu karşılaştırılacak;
  - aynı commit'e feature veya refactor eklenmeyecek.
- [ ] Ruff config repo içinde sabitlenecek; local ve CI aynı path/config'i kullanacak.
- [ ] MyPy bir anda anlamsız global ignore ile “yeşil” yapılmayacak. Önce kritik paketler strict olacak; kapsam bir ratchet dosyasıyla yalnız genişleyecek.
- [ ] Frontend `lint`, `typecheck`, `unit`, `e2e-smoke`, `build` script'leri package.json'a eklenecek.

### 9.3 Workflow standardı

Backend, frontend, RAG eval ve security workflow'ları:

- [ ] `pull_request` ve `push` tetiklerini geri alacak; yalnız manuel çalışmaya düşürülmeyecek.
- [ ] Security için ayrıca schedule çalışması olacak.
- [ ] `permissions` en düşük yetkiyle açıkça tanımlanacak.
- [ ] `concurrency` ile aynı branch'in eski çalışması iptal edilecek.
- [ ] Her job'a timeout konacak.
- [ ] Third-party action'lar mutable tag yerine commit SHA ile pinlenecek.
- [ ] Service image'ları sürüm ve mümkünse digest ile pinlenecek.
- [ ] Cache key lockfile hash'ine bağlı olacak.
- [ ] Test/JUnit/coverage/SBOM/vulnerability/eval raporları artifact olarak yüklenecek.
- [ ] Secret olmayan test config'i workflow içinde açık; secret gereken private benchmark ayrı environment'da olacak.
- [ ] Healthcheck `pg_isready -U raguser -d rag_platform` gibi doğru DB'yi kontrol edecek.
- [ ] Workflow “skip”, `continue-on-error`, kör grep veya sahte gate ile yeşil olmayacak.

### 9.4 Zorunlu CI işleri

#### Backend

- [ ] Lock doğrulama ve install.
- [ ] Ruff format check.
- [ ] Ruff lint.
- [ ] MyPy kritik paketler + ratchet.
- [ ] Unit test.
- [ ] Integration test.
- [ ] Blank DB migration.
- [ ] Restored-schema compatibility fixture.
- [ ] Coverage raporu; kritik domain modülleri için ayrı minimum.
- [ ] Import smoke ve OpenAPI schema generation.

#### Frontend

- [ ] Reproducible `npm ci`.
- [ ] Lint.
- [ ] Typecheck.
- [ ] Unit/component test.
- [ ] Production build.
- [ ] Browser smoke/e2e.
- [ ] Generated API client drift check.

#### Security

- [ ] Gitleaks tüm history + diff.
- [ ] Python/Node dependency audit.
- [ ] SBOM (CycloneDX veya SPDX).
- [ ] Container image scan.
- [ ] SAST/CodeQL uygun dillerde.
- [ ] License policy raporu.
- [ ] Public-safe artifact/content taraması.
- [ ] Commit ownership kontrolü.

#### RAG eval

- [ ] Aşama 9 tamamlanana kadar yalnız contract + end-to-end fixture kapısı olarak adlandırılacak.
- [ ] Fake sonuç hiçbir yerde “quality pass” üretmeyecek.
- [ ] Real/private benchmark ayrık ve açıkça etiketli olacak.

### 9.5 GitHub yönetişimi

- [ ] `main` için ruleset/branch protection etkinleştirilecek:
  - PR zorunlu;
  - required status checks;
  - branch up-to-date;
  - force-push ve delete kapalı;
  - conversation resolution;
  - bypass yalnız Mehmet KARACAN ve acil durum gerekçesi/receipt ile.
- [ ] Doğrudan push kabul edilmemeli.
- [ ] CODEOWNERS eklenecek; security/governance/migration dosyaları özel review isteyecek.
- [ ] Dependabot/Renovate benzeri dependency PR otomasyonu kontrollü kurulacak; otomatik merge yok.
- [ ] Pull request template; risk, migration, test, rollback, data-classification ve evidence alanları içerecek.

### 9.6 Kabul kriterleri

- [ ] Exact bir commit SHA'sında backend, frontend, security ve RAG integration workflow'ları otomatik tetiklenmiş ve yeşil.
- [ ] Workflow'lar manual-only değil.
- [ ] Backend formatter sonrasında lint/type/test/migration/coverage adımlarının gerçekten çalıştığı run loguyla kanıtlı.
- [ ] Migration workflow'u LLM key olmadan geçiyor.
- [ ] Branch ruleset API çıktısı public-safe receipt'e eklenmiş.
- [ ] Direct push testinin engellendiği doğrulanmış.
- [ ] High/critical dependency veya image açığı için belgelenmemiş istisna yok.
- [ ] Lockfile dışı dependency drift'i CI tarafından yakalanıyor.

---

## 10. AŞAMA 3 — Kanonik ağaç ve doğrulanabilir dokümantasyon

### 10.1 Amaç

Bir ajanın veya geliştiricinin hangi dosyanın gerçek, hangi dizinin placeholder, hangi durumun doğrulanmış olduğunu tahmin etmeden anlayacağı tek depo yapısı kurmak.

### 10.2 Ağaç temizliği

- [ ] Root ve nested tree inventory alınacak.
- [ ] ADR-001'e uygun biçimde yalnız `.gitkeep` içeren root skeleton dizinleri silinecek.
- [ ] Gerçek içerik taşıyan root dizin otomatik silinmeyecek; sınıflandırma ve ayrı karar gerekecek.
- [ ] Dolu dizinlerde gereksiz `.gitkeep` kaldırılacak.
- [ ] Aynı işleve ait birden fazla compose/env/config/workflow kaynağı kalmayacak.
- [ ] `document-rag-platform/` altındaki path'ler README, workflow, Docker, runbook ve script'lerde tek biçimde kullanılacak.
- [ ] Tree cleanup commit'i davranış değişikliği içermeyecek.

### 10.3 Durum dokümanlarını düzeltme

- [ ] `context-summary.md`, `active/current-tasks.md`, `IMPLEMENTATION_CHECKLIST.md` ve `done/completed-tasks.md` yeniden sınıflandırılacak:
  - current truth değil, insan görünümü;
  - last_verified_sha;
  - last_verified_at;
  - evidence_manifest;
  - stale olduğunda otomatik uyarı.
- [ ] `status/verified-state.json` oluşturulacak ve script tarafından üretilecek.
- [ ] `scripts/generate_verified_status.py`:
  - Git SHA, migration head, CI sonuç referansları, test/eval artifact hash'leri ve doküman drift'ini toplar;
  - elle yazılmış “tamamlandı” iddiasını kabul etmez;
  - repo dirty veya evidence eksikse `verified=false` üretir.
- [ ] Checklist'teki ADR-005/006 gibi mevcut dosyayla çelişen maddeler temizlenecek.
- [ ] History rewrite sonrası geçersiz SHA referansları kaldırılacak veya “historical/non-canonical” olarak işaretlenecek.
- [ ] README yalnız kanonik kurulum ve doğrulanmış özellikleri anlatacak; roadmap'i mevcut özellik gibi sunmayacak.

### 10.4 Governance belgeleri

- [ ] Root `SECURITY.md` eklenecek:
  - secret bildirimi;
  - public/private veri sınırı;
  - supported versions;
  - vulnerability handling;
  - history rewrite/rotation kararı.
- [ ] `CONTRIBUTING.md` yalnız commit sahipliği değil; branch, test, migration, formatting, data-policy ve review akışını da kapsayacak.
- [ ] CODEOWNERS, PR template ve issue template eklenecek.
- [ ] Public reponun lisans modeli açık karara bağlanacak: uygun `LICENSE` veya açık proprietary/no-license bildirimi. Varsayım yapılmayacak.
- [ ] ADR index'i oluşturulacak; status, supersedes, evidence ve owner alanları standardize edilecek.
- [ ] Runbook index'i ve son doğrulama tarih/SHA'sı eklenecek.

### 10.5 Public-safe kaynak temizliği

- [ ] `ttroot-g3.crt` için sahiplik/provenance/politika kararı alınacak.
- [ ] Kuruma özel CA ise public tree'den çıkarılacak; deployment sırasında secret/mount/build-arg ile inject edilecek.
- [ ] Public ve dağıtılabilir bir CA ise bile kaynak, lisans, checksum ve neden gerektiği belgelenmeden repoda tutulmayacak.
- [ ] Corporate endpoint varsayılanları `.env.example` ve `config.py` içinden çıkarılacak; `*.example.invalid` benzeri nötr örnek kullanılacak.
- [ ] Kurum içi deployment değerleri public repodan ayrı overlay'de tutulacak.
- [ ] Tüm Git geçmişi secret/certificate/private endpoint açısından taranacak.
- [ ] Gerçek credential bulunduysa rotation derhal yapılacak; history rewrite ayrı insan onayı olmadan uygulanmayacak.

### 10.6 Kabul kriterleri

- [ ] Repo kökünde kanonik olmayan boş app skeleton'ı yok.
- [ ] README → workflow → compose → source path'leri tutarlı.
- [ ] `verified-state.json` exact HEAD için üretilmiş ve `verified=true` yalnız tüm kanıtlar geçince oluşuyor.
- [ ] Stale doküman CI'da yakalanıyor.
- [ ] Public source tree corporate endpoint/özel trust bundle/secret/raw runtime veri taramasından geçiyor.
- [ ] ADR ve runbook index'leri mevcut dosyaları eksiksiz listeliyor.

---

## 11. AŞAMA 4 — Kimlik, workspace/project scope ve fail-closed API

### 11.1 Amaç

Bir kullanıcının veya hatalı çağrının başka proje/belge/konuşma içeriğini görmesini mimari olarak engellemek; scope eksikliğini “default proje” ile gizlememek.

### 11.2 Kimlik modeli

İlk desteklenen modlar:

- `AUTH_MODE=disabled`: yalnız `ENVIRONMENT=local`, loopback bind ve açık warning ile.
- `AUTH_MODE=api_key`: ilk güvenli single-user/team modu; anahtar yalnız hash olarak tutulur.
- `AUTH_MODE=oidc`: provider adapter sözleşmesi; tam enterprise SSO bu görevin zorunlu ilk teslimi değildir.

Production/staging:

- [x] `AUTH_MODE=disabled` ile startup fail edecek.
- [x] Default MinIO/DB/API credential kabul edilmeyecek.
- [x] Her request `PrincipalContext` taşıyacak.
- [x] Workspace/project membership doğrulanacak.

### 11.3 API sözleşmesi

- [x] Tüm uygulama route'ları `/api/v1` prefix'i altında toplanacak.
- [x] `/health/live` auth gerektirmeyebilir; readiness hassas dependency ayrıntısını anonim kullanıcıya açmayacak.
- [x] `project_id` chat, upload, repository ingestion, list, get, delete, reindex ve retrieval için zorunlu scope olacak.
- [x] Sessiz “ilk proje” seçimi ve “Varsayılan” proje yaratımı kaldırılacak.
- [x] `conversation_id` verilirse principal + workspace + project sahipliği doğrulanacak; yoksa yeni conversation güvenli scope ile oluşturulacak.
- [x] UUID/enum/filter validation boundary'de yapılacak; raw DB exception client'a dönmeyecek.
- [x] İş kuralı hataları HTTP 200 `success:false` değil anlamlı 4xx/5xx problem detail döndürecek.
- [x] Delete, durum değiştiren semantiğe uygun `DELETE` veya belgelenmiş command endpoint olacak; CSRF/idempotency politikası uygulanacak.

### 11.4 Fail-closed retrieval scope

Typed ve zorunlu bir sözleşme oluşturulacak:

```text
RetrievalScope
  principal_id
  workspace_id
  project_id
  allowed_document_ids | null
  active_versions_only = true
  allowed_source_types
  embedding_profile_id
  data_policy
```

- [x] Unknown filter key `ValidationError` üretir; sessiz ignore edilmez.
- [x] `document_ids=[]`, “tüm belgeler” değil “hiçbir belge” anlamına gelir.
- [x] Scope yoksa retrieval çalışmaz.
- [x] Retriever interface uyumsuzluğu `TypeError` ile filtresiz retry'a dönüşmez.
- [x] Dense, lexical, identifier, resolver, neighbor expansion, citation lookup ve debug aynı scope'u uygular.
- [x] Repository katmanında unscoped query API'si üretim kodundan erişilemez hale getirilir.

### 11.5 Yüksek riskli endpoint'ler

- [x] `/debug/retrieval` production default kapalı; yalnız admin scope ve audit event ile açılır.
- [x] Debug payload full content yerine varsayılan hash/snippet/redacted metadata döndürür.
- [x] Repository URL ingestion production default kapalıdır.
- [x] Açıldığında protocol/host/IP allowlist, DNS rebinding/redirect kontrolü, credential URL reddi, clone limitleri ve egress policy uygulanır.
- [x] Local directory ingestion yalnız canonical allowed roots içinde; symlink ve race testi yapılır.
- [x] Rate limit Redis-backed ve principal/route/cost sınıfına göre uygulanır.

### 11.6 Güvenlik testleri

- [x] A kullanıcısı B workspace/project/document/conversation kimliğini tahmin ederek erişemez.
- [x] Empty/unknown/malformed filter veri genişletmez.
- [x] Retriever mock'u eski signature kullandığında istek fail eder, filtresiz çalışmaz.
- [x] Debug endpoint anonim ve normal kullanıcı için kapalıdır.
- [x] Auth disabled production config'i startup'ta reddedilir.
- [x] Repository ingestion localhost, metadata IP, private subnet ve redirect ile SSRF yapamaz.
- [x] CORS yalnız allowlist origin; wildcard+credentials kombinasyonu yok.

### 11.7 Kabul kriterleri

- [x] Tüm veri erişim yolları `PrincipalContext + RetrievalScope` taşır.
- [x] Cross-project/workspace leakage testleri yüzde 100 geçer ve leakage sayısı `0`.
- [x] Default proje/unchecked conversation davranışı kalmaz.
- [x] Scope/filtre hatası hiçbir koşulda daha geniş sorgu üretmez.
- [x] Production güvenli olmayan auth/config ile başlamaz.

### 11.8 Aşama 4 uygulama kaydı — 2026-09-02

- API ve retrieval kapsamı `PrincipalContext` + immutable `RetrievalScope`
  ile fail-closed hale getirildi; repository adapter'ları somut `project_id`
  olmadan sorgu üretmiyor.
- `cv3_00000002` migration'ı boş, izole PostgreSQL 16/pgvector üzerinde iki
  ardışık `upgrade head` ile uygulandı; verifier sonucu `PASS`, head
  `cv3_00000002`.
- Gerçek PostgreSQL + HTTP/ORM scope testi iki workspace kurdu; diğer
  workspace proje/belge sızıntısı `0` ve absent/cross-scope yanıtı aynı
  `404` oldu.
- Backend regresyonu: `545 passed, 6 skipped, 2 deselected`; A4 migration
  entegrasyonu: `2 passed`; frontend lint/typecheck/unit/contract/build
  kapıları geçti. Parser timeout testindeki gecikmeli worker kapanışı Aşama 6
  izolasyon işine açık bulgu olarak taşındı.

---

## 12. AŞAMA 5 — Şema invariant'ları ve zaman modeli

### 12.1 Amaç

Uygulama kodu hata yapsa bile PostgreSQL'in geçersiz aktif sürüm, çapraz belge chunk'ı, birden fazla aktif profil veya anlamsız durum üretmesini engellemek.

### 12.2 Kimlik ve scope tabloları

- [x] `principals`
- [x] `workspaces`
- [x] `workspace_memberships`
- [x] `project_memberships` veya workspace politikasına göre açık karar
- [x] `api_keys` — yalnız hash, prefix, created/expires/revoked/last_used
- [x] Project kayıtları workspace'e zorunlu bağlı
- [x] Audit actor alanları principal'a bağlı

### 12.3 Version/chunk/profile invariant'ları

- [x] `document_versions` için `(document_id, id)` uygun unique key oluşturulacak.
- [x] `documents.active_version_id` aynı document'a ait version'ı composite FK veya eşdeğer güvenli constraint ile garanti edecek.
- [x] `chunks(document_id, version_id)` aynı document'ın version'ına bağlı olacak.
- [x] Aktif version yalnız `completed/ready` durumunda seçilebilir.
- [x] Bir document için aynı anda bir aktif version olacak.
- [x] Bir embedding profile kimliği dimension/model/config hash ile immutable olacak.
- [x] İlgili scope içinde yalnız bir aktif embedding profile için partial unique index veya açık activation tablosu kullanılacak.
- [x] `chunk_embeddings` dimension/profil uyumu ingestion boundary ve DB metadata ile doğrulanacak.
- [x] Legacy `chunks.embedding` geçiş tamamlandıktan sonra okunmayacak; deprecation ve kontrollü drop planı olacak.

### 12.4 Domain durumları

String durumlar typed enum/check constraint ile sınırlandırılacak:

- document status;
- version status;
- ingestion job status/stage;
- role;
- source/artifact type;
- data classification;
- provider status;
- work item/attempt/claim/receipt status.

- [x] `progress` 0–100 aralığında olacak.
- [x] Terminal job yeniden running olamayacak.
- [x] `activated_at` yalnız aktif/completed version için anlamlı olacak.
- [x] Error state gerekli error_code/message invariant'ını taşıyacak.
- [x] State transition'lar merkezi domain service ve DB guard ile korunacak.

### 12.5 Zaman ve silme politikası

- [x] Tüm yeni zaman kolonları timezone-aware UTC.
- [x] Naive mevcut kolonlar kontrollü migration ile dönüştürülecek; timezone varsayımı ADR'de yazılacak.
- [x] Uygulamada tek `Clock` abstraction; testlerde fake clock.
- [ ] `created_at`, `updated_at`, `deleted_at`, `activated_at`, lease expiry semantiği standardize edilecek.
- [x] Soft-delete edilmiş kayıt default query'lerden çıkacak; legal/retention politikasına göre GC yapılacak.

### 12.6 Index ve performans

- [x] Gerçek sorgu planlarına göre workspace/project/document/version/status FK ve filtre index'leri eklenecek.
- [x] FTS/identifier/vector index'leri migration tarafından yönetilecek.
- [x] Her index için hedef sorgu ve `EXPLAIN (ANALYZE, BUFFERS)` kanıtı olacak.
- [x] Gereksiz/duplicate index silme ancak kullanım ve rollback analizi sonrası yapılacak.

### 12.7 Migration/test kapısı

- [x] Her constraint önce violation audit'i çalıştıracak.
- [x] Backfill ve constraint doğrulama ayrı, gözlenebilir adımlar olacak.
- [x] Constraint eklenirken büyük tabloda lock riski ölçülecek; gerekirse `NOT VALID → backfill → VALIDATE` stratejisi.
- [x] Property/invariant testleri yanlış document/version/profile ilişkisini DB'nin reddettiğini gösterir.
- [x] Concurrency testi aynı document için iki version aktivasyonundan yalnız birini kabul eder.

### 12.8 Kabul kriterleri

- [x] Runtime invariant sorgularının tamamı sıfır ihlal verir.
- [x] Geçersiz cross-version insert/update DB tarafından reddedilir.
- [x] Birden fazla aktif profile/version concurrency altında oluşmaz.
- [x] Production tablolarında timezone semantiği belgeli ve tutarlıdır.
- [x] Index değişiklikleri sorgu planı/latency kanıtıyla kabul edilmiştir.

### 12.9 Aşama 5 uygulama kaydı — 2026-09-02

- `cv3_00000003` boş PostgreSQL 16/pgvector DB'de `base → head`,
  `head → cv3_00000002 → head` çevrimlerini geçti. Kurtarılan veri kopyasında
  `1/1/1/1/1` project/document/version/chunk/profile sayıları korunarak aynı
  schema hash elde edildi.
- Strict verifier `167` kolon, `70` constraint ve `13` runtime invariant için
  `PASS` verdi; tüm violation sayıları `0`.
- Gerçek DB testleri cross-document/version, invalid active version, status,
  progress, error detail, event, profile mutation ve çift aktivasyonu reddetti;
  concurrent compare-and-swap yalnız tek version'ı aktive etti.
- 10.000 satırlık izole lock fixture'ında migration 1,44 saniyede tamamlandı,
  beş tablo sayımı korundu ve verifier yeniden `PASS` verdi. Altı hedef sorgu
  `EXPLAIN (ANALYZE, BUFFERS)` altında kendi migration-managed index'ini kullandı.
- Workspace üyeliğinin workspace içindeki tüm aktif projeleri kapsadığı,
  timezone varsayımı, global embedding profile ve soft-delete/GC politikası
  ADR-008 ile kaydedildi. Henüz var olmayan provider ve Work Graph
  status/lease tablolarının enum ve zaman invariant'ları Aşama 12'de bu kapıya
  geri dönülerek tamamlanacaktır; bu nedenle ilgili üst düzey kapsam iddiası
  açık bırakıldı.


## 13. AŞAMA 6 — Tek ingestion hattı, outbox ve içerik politikası

### 13.1 Amaç

Upload, repository, directory, archive, reindex ve retry işlemlerinin aynı parser/chunker/embedding/indexing sözleşmesinden geçmesini; DB, object storage ve queue arasındaki kısmi başarısızlıkların veri kaybı veya sahte başarı üretmemesini sağlamak.

### 13.2 Tek kanonik ingestion mimarisi

Aşağıdaki tek application service oluşturulacak:

```text
IngestionOrchestrator
  accept_source(command)
  stage_source()
  classify_content()
  create_version_and_job()
  dispatch_via_outbox()
  parse_and_normalize()
  chunk_with_profile()
  embed_with_profile()
  index_candidate_version()
  validate_candidate_version()
  activate_atomically()
  schedule_old_version_gc()
```

- [ ] `api/v1/documents.py` içindeki legacy `extract_text` ve karakter bazlı `chunk_text` üretim yolundan çıkarılacak.
- [ ] Worker, API modülünden parser/chunker import etmeyecek.
- [ ] Reindex aynı orchestrator'ı ve immutable profile'ları kullanacak.
- [ ] Sync endpoint gerekiyorsa ayrı pipeline çalıştırmayacak; aynı job'ı oluşturup bounded wait/poll ile sonucu döndürecek.
- [ ] Varsayılan ürün davranışı async job olacak.
- [ ] Parser/chunker/embedding profile kimlikleri version üzerinde zorunlu ve immutable olacak.
- [ ] UI'dan gönderilen raw karakter chunk size üretim semantiğini değiştirmeyecek.

### 13.3 Kaynak ve dosya tespiti

- [ ] Source adapter'ları ortak `SourceDescriptor` üretir: source_type, origin, revision, content length/hash, detected MIME, declared MIME, data classification hint.
- [ ] Extension tek başına güven kaynağı değildir; magic/MIME/structure birlikte değerlendirilir.
- [ ] Extension'sız desteklenen dosya tespit edilebilir.
- [ ] Declared MIME ile detected type uyuşmazlığı policy event üretir.
- [ ] Archive traversal, decompression bomb, symlink escape, generated/binary file ve ignore kuralları tek scanner katmanında uygulanır.
- [ ] Repository clone geçici sandbox, timeout, boyut, dosya sayısı, history depth ve egress sınırına sahiptir.

### 13.4 İçerik politikası ve hassas veri

Her source için processing başlamadan `ContentPolicyDecision` üretilir:

```text
classification
contains_credentials
contains_private_key
contains_pii
permit_original_storage
permit_normalized_storage
permit_local_embedding
permit_remote_embedding
permit_local_generation
permit_remote_generation
redaction_required
quarantine_reason
policy_version
```

- [ ] Credential/private key/token tespit edilen içerik otomatik remote servise gönderilmez.
- [ ] Yüksek riskli içerik silent redaction ile “normal belge” yapılmaz; quarantine + kullanıcı kararı gerekir.
- [ ] Confidential iş dokümanı ile credential/secret aynı kategori sayılmaz; politika ayrı karar verir.
- [ ] Orijinal artifact saklanacaksa encryption-at-rest, erişim kontrolü, retention ve audit uygulanır.
- [ ] Normalize artifact, policy kararı verilmeden kalıcı storage'a yazılmaz.
- [ ] Chunk/log/debug/eval artifact'inde secret veya tam belge içeriği bulunmaz.
- [ ] Remote provider çağrısına data classification ve policy kararının audit izi eklenir.

### 13.5 DB–object storage–queue tutarlılığı

- [ ] Object önce deterministic staging key'e yüklenir; checksum doğrulanır.
- [ ] DB transaction document/version/job ve `outbox_events` kaydını birlikte oluşturur.
- [ ] Commit sonrası dispatcher outbox event'ini Celery/queue'ya teslim eder.
- [ ] Queue publish başarısızlığı event'i kaybetmez; retry edilir.
- [ ] Staging object için referanssız/orphan sweeper vardır.
- [ ] Final immutable object key, version/artifact id ve checksum'dan türetilir.
- [ ] Outbox/inbox idempotency key unique constraint ile korunur.
- [ ] Worker aynı mesajı birden çok aldığında aynı terminal sonucu üretir; duplicate chunk/artifact/version oluşturmaz.

### 13.6 Job claim, lease ve retry

- [ ] Worker job'ı etki öncesi atomik claim eder.
- [ ] Lease owner, lease expiry, heartbeat ve attempt kayıtları tutulur.
- [ ] Stale lease güvenli reconcile edilir.
- [ ] Retryable ve terminal error code'ları ayrılır.
- [ ] Retry mevcut aktif version'ı silmez.
- [ ] Stage geçişleri monotonic ve receipt'li olur.
- [ ] Cancel yalnız güvenli noktalarda etkili olur; yarım artifact/chunk cleanup planı üretir.
- [ ] Celery task id operasyonel otorite değil, attempt metadata'sıdır.

### 13.7 Build-new → validate → activate

- [ ] Yeni version bağımsız candidate olarak parse/chunk/embed/index edilir.
- [ ] Candidate için minimum invariant'lar:
  - source/artifact checksum tutarlı;
  - chunk sequence deterministik ve unique;
  - embeddings eksiksiz ve doğru profile/dimension;
  - gerekli index alanları dolu;
  - content policy ihlali yok;
  - smoke retrieval başarılı.
- [ ] Aktivasyon tek DB transaction'ında yapılır.
- [ ] Eski aktif version aktivasyon tamamlanana kadar okunabilir kalır.
- [ ] Eski version/artifact/chunk silme ayrı retention/GC işi olur.
- [ ] Aktivasyon sonrası query cache/profile cache invalidate edilir.

### 13.8 Silme ve garbage collection

- [ ] Delete önce soft-delete/retention state üretir.
- [ ] Conversation/citation retention ve legal hold politikası belirlenir.
- [ ] GC yalnız referans sayımı ve retention dolduktan sonra object/vector/chunk siler.
- [ ] GC idempotent, dry-run destekli ve receipt üretir.
- [ ] Failed/abandoned staging object'ler için ayrı sweeper vardır.
- [ ] Restore edilen DB ile object store arasında missing/orphan raporu üretilebilir.

### 13.9 Test matrisi

- [ ] Aynı upload/idempotency key iki kez gönderilir; tek version/job sonucu.
- [ ] Object upload sonrası DB commit fail; sweeper orphan'ı bulur.
- [ ] DB commit sonrası queue fail; outbox daha sonra teslim eder.
- [ ] Worker parsing/embedding/indexing/activation aşamalarının her birinde kill edilir ve retry edilir.
- [ ] Eski aktif version tüm candidate hatalarında erişilebilir kalır.
- [ ] Extension'sız TXT/PDF/DOCX/image fixture doğru router'a gider.
- [ ] MIME spoof, zip bomb, traversal, symlink escape ve oversized source reddedilir.
- [ ] Secret fixture quarantine olur; remote provider mock'una byte gönderilmez.
- [ ] Concurrent iki reindex yalnız geçerli candidate'ı aktive eder.
- [ ] Delete/GC retry duplicate veya yanlış object silmez.

### 13.10 Kabul kriterleri

- [ ] Üretim kodunda tek ingestion orchestrator vardır.
- [ ] Legacy sync chunker ve dual write yolu kullanılmaz.
- [ ] DB/object/queue fault injection testleri veri kaybı ve sonsuz queued job üretmez.
- [ ] Aktif version candidate hazır olmadan değişmez.
- [ ] Secret/high-risk içerik remote provider'a sızmaz.
- [ ] Tüm ingestion terminal durumları receipt ve error code taşır.

---

## 14. AŞAMA 7 — Typed retrieval, active version/profile ve ContextBundle

### 14.1 Amaç

Dense, lexical ve identifier sonuçlarının aynı tipli provenance modelinde birleşmesini; her sorgunun yalnız yetkili, aktif ve aynı vector profile'a ait veriyi aramasını; modele giden bağlamın tek kanonik paket olmasını sağlamak.

### 14.2 Tipli retrieval nesneleri

Dinamik attribute ve `Any` ağırlıklı taşıma kaldırılacak. En az aşağıdaki immutable tipler oluşturulacak:

```text
RetrieverHit
  chunk_id, document_id, version_id, source_file_id
  workspace_id, project_id, embedding_profile_id
  retriever_name, retriever_rank, raw_score
  match_type, matched_terms, locator, content_hash

FusedHit
  identity + per_retriever_contributions + rrf_score + fusion_rank

RerankedHit
  fused_hit + reranker_model/profile + reranker_score + final_rank

ContextItem
  scoped identity + content/snippet + parent/neighbor relation
  locator + policy classification + token_count + evidence_hash

ContextBundle
  query_id, scope, retrieval_run_id, profile ids
  selected_items, rejected_items/reasons
  token/chunk budget, truncation summary, provenance
```

- [ ] Rank ataması nesneyi yeniden kurup stage score kaybetmeyecek.
- [ ] Her stage input/output'u typed ve testlenebilir olacak.
- [ ] Debug serialization full object yerine güvenli projection kullanacak.

### 14.3 Dense retrieval

- [ ] Yalnız `chunk_embeddings` ve seçili aktif embedding profile kullanılacak.
- [ ] `chunks.embedding` legacy fallback backfill doğrulandıktan sonra kaldırılacak.
- [ ] Query vector dimension/profile doğrulanmadan SQL çalışmayacak.
- [ ] Active document version, completed status, workspace/project ve policy predicates zorunlu.
- [ ] HNSW/IVFFlat tuning transaction-local ayarlarla ve benchmark kanıtıyla yapılacak.
- [ ] Vector index'in gerçekten kullanıldığı query planıyla doğrulanacak.
- [ ] Empty query embedding veya provider failure “boş vector ile devam” değil typed error/no-answer nedeni üretir.

### 14.4 Lexical retrieval

- [ ] Dil/identifier doğasına uygun FTS strategy benchmark ile seçilecek.
- [ ] `plainto_tsquery(simple)` tek zorunlu yol olmayacak; phrase, OR/websearch ve identifier sorguları ayrılacak.
- [ ] Hardcoded stopword/substring rescue false-positive etkisi ölçülecek.
- [ ] Search vector'ın üretildiği parser/profile sürümü izlenecek.
- [ ] Active version/scope/policy predicates dense ile aynı olacak.
- [ ] Rank açıklaması matched terms ve query formunu taşıyacak.

### 14.5 Identifier retrieval

- [ ] Exact, normalized exact, prefix, trigram/fuzzy ve substring ayrı match type olacak.
- [ ] `%`, `_`, escape ve case normalization güvenli uygulanacak.
- [ ] Leading wildcard varsayılan olmayacak.
- [ ] `pg_trgm` gerçekten kullanılıyorsa uygun index/operator ve query planı kanıtlanacak; kullanılmıyorsa “trigram” adı kaldırılacak.
- [ ] Arbitrary sabit skor yerine match type/rank kalibrasyonu benchmark ile yapılacak.
- [ ] PL/SQL/package/schema/table/column/symbol metadata ayrı alanlarda aranabilecek.

### 14.6 RRF, dedupe ve reranking

- [ ] Bir retriever listesinde aynı chunk bir kez katkı verir.
- [ ] RRF contribution map kaydedilir; ham skorlar toplanmaz.
- [ ] Dedupe, content_hash/source/version metadata bağlandıktan sonra yapılır.
- [ ] Aynı içerik farklı version'daysa yalnız aktif/scope uygun olan kalır.
- [ ] Reranker input'u maksimum aday/token bütçesine uyar.
- [ ] Reranker kapalı/başarısızsa açıkça `noop/fallback_reason` yazar; score uydurmaz.
- [ ] Reranker sonucu `reranker_score` ve model/profile ile korunur.
- [ ] Reranker fallback güvenlik scope'unu değiştirmez.

### 14.7 Parent/neighbor ve ContextBundle

- [ ] Neighbor anahtarı yalnız `source_id + sequence_no` olmayacak; workspace, project, document, version, source_file ve sequence birlikte sınırlar.
- [ ] Parent/neighbor resolver aynı `RetrievalScope` ile çağrılır.
- [ ] Parent pool yalnız son seçilmiş adaylarla sınırlı değil; scope'lu resolver üzerinden gerektiğinde alınır.
- [ ] Duplicate content/token budget optimizasyonu deterministic olur.
- [ ] Context budget model tokenizer/profile ile hesaplanır; kaba kelime sayımı yalnız açık fallback olabilir.
- [ ] Bundle seçilmeyen adaylar ve ret nedenlerini debug için tutar, modele göndermez.
- [ ] Full content loglanmaz; evidence hash ve redacted snippet kullanılır.
- [ ] AnswerService yalnız `ContextBundle.selected_items` üzerinden prompt kurabilir.

### 14.8 Retrieval run kaydı ve observability

- [ ] Her sorgu için `retrieval_runs` kaydı:
  - principal/workspace/project;
  - normalized query hash, gerekiyorsa şifreli/retention'lı raw query;
  - retriever/profile/config sürümleri;
  - candidate/selected sayıları;
  - per-stage latency;
  - no-answer reason;
  - error/fallback reason;
  - bundle hash.
- [ ] Metric cardinality kontrolü yapılır; raw document/chunk id metric label olmaz.
- [ ] Slow query ve index miss trace edilir.

### 14.9 Test matrisi

- [ ] Same-retriever duplicate RRF katkısını artırmaz.
- [ ] Rank yeniden ataması reranker ve stage skorlarını korur.
- [ ] Legacy/canonical embedding karışmaz.
- [ ] Inactive/cross-project/cross-version chunk hiçbir retriever'da dönmez.
- [ ] Empty document list sonuç döndürmez.
- [ ] Unknown filter ve signature mismatch fail-closed.
- [ ] Neighbor başka version/project'e geçemez.
- [ ] Identifier wildcard/case/escape adversarial testleri.
- [ ] Dense/FTS/trigram index usage plan testleri.
- [ ] Bundle token bütçesi hiçbir koşulda aşılmaz.

### 14.10 Kabul kriterleri

- [ ] Retrieval pipeline uçtan uca typed ve immutable stage provenance taşır.
- [ ] Tüm retriever'larda scope, active version ve profile predicates zorunlu.
- [ ] Legacy embedding fallback üretim sorgusundan kaldırılmış.
- [ ] RRF duplicate, rerank score loss ve cross-version neighbor hataları testlerle kapanmış.
- [ ] Modele giden tek bağlam nesnesi `ContextBundle`.
- [ ] Permission/version leakage `0`.

---

## 15. AŞAMA 8 — Yapılandırılmış cevap, citation doğrulama ve prompt güvenliği

### 15.1 Amaç

Modelin hangi kanıtı kullandığını tahmin etmek yerine yapılandırılmış olarak bildirmesini, bildirimin doğrulanmasını ve yalnız gerçekten kullanılan kanıtların immutable provenance ile kaydedilmesini sağlamak.

### 15.2 Tek cevap sözleşmesi

Modelden serbest metin + sonradan regex tahmini yerine schema-constrained çıktı alınacak:

```text
AnswerEnvelope
  answerable: boolean
  no_answer_reason: enum | null
  answer_text: string
  claims:
    - claim_text
      source_labels[]
  used_source_labels[]
  uncertainty[]
  safety_flags[]
```

- [ ] `source_labels`, yalnız ContextBundle'daki mevcut `[S1..Sn]` kümesinden olabilir.
- [ ] Unknown label, boş claim source veya schema ihlali kontrollü repair/retry ya da no-answer üretir.
- [ ] Modelin kullanmadığı candidate citation olarak persist edilmez.
- [ ] Cevap metni ve structured claims tutarlılık kontrolünden geçer.
- [ ] Bir claim birden fazla kaynağa dayanabilir; ilişki ayrı tabloda tutulur.

### 15.3 Kanıt paketleme

- [ ] Prompt'a yalnız `ContextBundle.selected_items` girer.
- [ ] Her source için label, güvenli locator, document/source adı, version ve sınırlı içerik verilir.
- [ ] İçerik açık “UNTRUSTED SOURCE DATA” sınırları içinde yer alır.
- [ ] Source içindeki instruction, system/developer talimatı sayılamaz.
- [ ] Query, policy ve evidence bölümleri birbirinden açıkça ayrılır.
- [ ] Prompt bütçesi model context window, reserved output ve safety margin ile hesaplanır.
- [ ] Truncation, label-content eşleşmesini bozmaz.

### 15.4 Citation provenance modeli

`message_citations` genişletilecek veya normalize edilecek:

- [ ] message_id, claim_id, retrieval_run_id;
- [ ] chunk/document/version/source_file/profile kimlikleri;
- [ ] source label ve answer içindeki kullanım sırası;
- [ ] retrieval/fusion/reranker score snapshot;
- [ ] page/line/bbox/symbol locator;
- [ ] evidence excerpt veya güvenli immutable snapshot;
- [ ] evidence hash/content hash;
- [ ] model, prompt template/version ve generation config;
- [ ] citation validation sonucu;
- [ ] created_at UTC.

- [ ] Kaynak daha sonra reindex/silinse bile citation neye dayandığını hash/snapshot ile açıklayabilmeli.
- [ ] Hassas excerpt şifreli/retention'lı saklanır; public loga girmez.

### 15.5 No-answer ve belirsizlik

- [ ] Smalltalk, policy refusal, insufficient evidence, provider failure ve permission-denied birbirinden ayrılır.
- [ ] Empty retrieval otomatik “smalltalk” sayılmaz.
- [ ] No-answer threshold gerçek dataset ile kalibre edilir.
- [ ] Exact identifier güçlü kanıt gibi özel durumlar ölçülmüş kural olarak uygulanır.
- [ ] Provider timeout/error kanıt yokmuş gibi sessiz cevap üretmez.
- [ ] UI no-answer nedenini kullanıcıya uygun, debug detayını yalnız yetkili role gösterir.

### 15.6 Prompt injection ve içerik güvenliği

- [ ] Direct ve indirect prompt injection fixture'ları oluşturulur.
- [ ] “Talimatları yok say”, tool çağır, secret göster, başka belge getir, citation uydur gibi kaynak metinleri veri kabul edilir.
- [ ] Model tool kullanacaksa allowlist, typed arguments, scope ve explicit apply gate gerekir.
- [ ] Retrieval debug, prompt ve full context loglama varsayılan kapalıdır.
- [ ] Provider request/response retention politikası belgelenir.
- [ ] Remote provider'a gönderilen her çağrı data-policy kontrolünden geçer.

### 15.7 Conversation güvenilirliği

- [ ] Conversation history workspace/project/principal scope'lu yüklenir.
- [ ] Geçmiş mesajların tümü otomatik context'e eklenmez; özet/retrieval ve token budget uygulanır.
- [ ] Modelin önceki cevabı canonical fact sayılmaz.
- [ ] Conversation title/summary model üretimiyse provenance ve draft status taşır.
- [ ] Mesaj silme/retention/citation bütünlüğü belirlenir.

### 15.8 Test matrisi

- [ ] Model var olmayan `[S99]` döndürür; citation persist edilmez ve cevap kontrollü işlenir.
- [ ] Model bundle'daki 5 kaynaktan yalnız 2'sini kullanır; DB'de yalnız 2 source/claim ilişkisi oluşur.
- [ ] Claim source'suz kalır; gate bunu yakalar.
- [ ] Kaynak içinde prompt injection bulunur; scope/tool/policy değişmez.
- [ ] Context truncation label-content eşleşmesini bozmaz.
- [ ] Reindex sonrası eski mesaj citation hash/snapshot'ı doğrulanır.
- [ ] Provider timeout, malformed JSON ve partial stream güvenli terminal sonuç üretir.
- [ ] Cross-project conversation id reddedilir.

### 15.9 Kabul kriterleri

- [ ] Model cevabı schema validation'dan geçmeden kullanıcıya/persistence'a gitmez.
- [ ] Persist edilen citation'ların tamamı kullanılan label ve claim'e bağlıdır.
- [ ] Citation precision/coverage gerçek eval ile ölçülebilir.
- [ ] Prompt injection source scope/policy/tool davranışını değiştiremez.
- [ ] Cevap yeniden üretilebilir provenance taşır.

---

## 16. AŞAMA 9 — Gerçek eval, adversarial/fault/concurrency kapıları

### 16.1 Amaç

Etiketlerden cevabı kopyalayan fake koşuyu kalite kanıtı olmaktan çıkarmak; aynı production ingestion/retrieval/answer yolunu kullanan, sızıntı ve hata davranışını da ölçen üç katmanlı değerlendirme sistemi kurmak.

### 16.2 Eval katmanları

#### Katman 1 — Contract smoke

- Fake/mocked provider kullanılabilir.
- Amaç schema, metric hesaplama ve orchestration contract'ını test etmektir.
- Dosya/rapor adı açıkça `contract-smoke` olur.
- Release quality score veya “Recall=1.0” kanıtı üretmez.
- Expected labels'dan candidate/answer üreten mevcut `FakeRetriever/FakeAnswerer` yalnız burada kalabilir.

#### Katman 2 — Offline gerçek pipeline fixture

- Fresh PostgreSQL + pgvector + Redis + MinIO üzerinde çalışır.
- Fixture belgeler upload/repository endpoint'i değil, production'daki aynı `IngestionOrchestrator` üzerinden işlenir.
- Parser, chunker, outbox, worker, index, retrieval, ContextBundle ve citation persistence gerçek kod yoludur.
- Deterministik test embedding provider kullanılabilir; sonuç doğrudan golden label'dan türetilmez.
- Permission/version/profile leakage, idempotency ve fault testleri bu katmanda zorunludur.

#### Katman 3 — Approved real-provider benchmark

- Onaylı gerçek embedding ve generation provider kullanır.
- Private CI environment veya kontrollü local runner'da çalışır.
- Dataset veri sınıflandırması provider policy ile uyumludur.
- Model cevabına golden answer/source verilmez.
- Sonuç artifact'i exact provider/model/profile/prompt/config hash taşır.
- Release kalite kararı bu katmanın onaylı baseline'ına göre verilir.

### 16.3 Golden dataset standardı

Her kayıt en az şunları taşır:

```text
id, query, intent, answerable
workspace/project fixture
expected facts/aspects
expected source constraints
forbidden sources
permission persona
query type
language
adversarial tags
notes + reviewer + version
```

- [ ] Golden dataset code, table, identifier, prose, OCR, archive/repository, multi-document, contradictory source, temporal version, no-answer ve permission senaryolarını kapsar.
- [ ] Query/source leakage engellenir; fixture üretimi golden expected result'tan runtime candidate oluşturmaz.
- [ ] Dataset train/tune ve holdout bölümlerine ayrılır.
- [ ] Her değişiklik review ve dataset version hash'i taşır.
- [ ] Hassas şirket içi fixture public repoya konmaz; public sentetik eşdeğer ve private pack ayrılır.

### 16.4 Ölçümler

#### Retrieval

- Recall@1/3/5/10
- MRR@10
- nDCG@10
- context precision/recall
- first relevant rank
- duplicate rate
- active-version/profile leakage
- cross-project/workspace leakage
- identifier exact/fuzzy başarı oranı

#### Answer/citation

- answerability FP/FN ve query type kırılımı
- citation precision
- citation recall/coverage
- unsupported claim rate
- source-label validity
- answer sufficiency
- contradiction handling
- prompt injection success rate

#### Operasyon

- p50/p95/p99 ingestion ve retrieval latency
- provider çağrı sayısı/token/cost
- retry/duplicate/orphan oranı
- queue wait ve stage duration
- restore/migration/reindex süresi
- error code dağılımı

### 16.5 Negatif/adversarial/fault/concurrency senaryoları

- [ ] Cross-tenant/project/document/conversation erişimi.
- [ ] Inactive veya eski version'ın daha yüksek skorla gelmesi.
- [ ] Yanlış embedding profile/dimension.
- [ ] Empty/unknown filter ve retriever signature mismatch.
- [ ] Duplicate RRF ve duplicate source content.
- [ ] Prompt injection ve citation fabrication.
- [ ] Secret içeren belge ve remote provider block.
- [ ] MIME spoof, extensionless source, corrupted PDF/DOCX/image.
- [ ] DB/Redis/MinIO/provider/queue timeout ve kesinti.
- [ ] Worker'ın her stage'de kill/retry edilmesi.
- [ ] Aynı upload/reindex/delete komutunun eşzamanlı ve tekrar teslimi.
- [ ] Migration yarıda kalma ve restore/cutover.
- [ ] Stale claim/lease ve clock skew.
- [ ] Context budget overflow ve oversized source.
- [ ] Contradictory documents ve güncel/eski source ayrımı.

### 16.6 Regression ve release gate

- [ ] İlk approved real-provider koşusu “baseline” olarak insan onayıyla mühürlenir.
- [ ] Sonraki değişikliklerde:
  - permission/version leakage kesin `0`;
  - invalid citation label kesin `0`;
  - expected no-answer fixture'larında fabricated answer kesin `0`;
  - Recall@5 ve MRR approved baseline'a göre 2 yüzde puandan fazla düşemez;
  - citation precision/coverage approved baseline'a göre 2 yüzde puandan fazla düşemez;
  - p95 latency aynı donanım/fixture'da yüzde 20'den fazla kötüleşirse açık performans onayı gerekir;
  - yeni critical/high security bulgusu kabul edilmez.
- [ ] Flaky test üç tekrar ve seed kaydıyla analiz edilir; “rerun until green” yok.
- [ ] Baseline güncellemesi yalnız metrik düştüğü için yapılmaz; gerekçe, diff ve insan onayı gerekir.

### 16.7 Teslimatlar

- [ ] `tests/evals/contract_smoke/`
- [ ] `tests/evals/offline_e2e/`
- [ ] `tests/evals/real_benchmark/`
- [ ] Versioned public synthetic dataset
- [ ] Private dataset manifest/hash sözleşmesi
- [ ] `scripts/run_eval.py` açık `--tier` seçimiyle
- [ ] Machine-readable JSON + human-readable Markdown rapor
- [ ] Baseline comparator ve regression gate
- [ ] CI artifact retention politikası

### 16.8 Kabul kriterleri

- [ ] Fake eval hiçbir release/quality kapısını tek başına geçiremez.
- [ ] Offline E2E fresh infrastructure üzerinde production pipeline'ı kullanır.
- [ ] Approved real-provider benchmark etiketten sonuç türetmeden çalışır.
- [ ] Leakage ve citation validity mutlak kapıları geçer.
- [ ] Quality regression exact SHA ve config hash ile raporlanır.
- [ ] Fault/concurrency/recovery senaryoları deterministic receipt üretir.


## 17. AŞAMA 10 — Frontend ve ürün sözleşmesi

### 17.1 Amaç

Frontend'i tek büyük sayfa ve backend'in iç ayrıntılarını açığa çıkaran kontrol paneli olmaktan çıkarıp; güvenli scope, job yaşam döngüsü, citation kanıtı ve hata durumlarını doğru temsil eden testlenebilir bir ürün arayüzüne dönüştürmek.

### 17.2 Frontend modülerleşmesi

`apps/web/app/page.tsx` işlevsel alanlara ayrılacak. Hedef yapı:

```text
apps/web/
  app/
    projects/
    sources/
    chat/
    jobs/
    settings/
    admin/diagnostics/
  features/
    auth/
    projects/
    ingestion/
    retrieval/
    conversations/
    citations/
  components/
  lib/api/
  tests/
```

- [ ] Route/page bileşenleri orchestration dışında iş mantığı taşımayacak.
- [ ] Server state için tek veri erişim katmanı; ad-hoc `fetch` tekrarları olmayacak.
- [ ] Form/schema validation backend contract'ıyla uyumlu olacak.
- [ ] Loading/error/empty/partial/permission-denied durumları ayrı gösterilecek.
- [ ] Development debug görünümü production bundle'da yetkisiz erişilebilir olmayacak.

### 17.3 Typed API sözleşmesi

- [ ] Backend OpenAPI schema CI'da deterministic üretilir.
- [ ] TypeScript client/schema otomatik oluşturulur.
- [ ] Generated client elle düzenlenmez.
- [ ] Backend schema değişip client güncellenmezse CI fail eder.
- [ ] Problem detail/error code'lar typed UI mesajlarına eşlenir.
- [ ] API base URL ve auth config build-time hardcode değil deployment config'tir.

### 17.4 Proje, kaynak ve ingestion deneyimi

- [ ] Kullanıcı workspace/project seçmeden upload/chat/retrieval yapamaz.
- [ ] Sessiz default proje yoktur.
- [ ] Kaynak ekleme ekranı source type, data classification ve remote-processing policy kararını açık gösterir.
- [ ] Raw “chunk size (karakter)” alanı kaldırılır.
- [ ] Gelişmiş kullanıcıya yalnız kayıtlı immutable chunker/embedding profile seçimi sunulabilir.
- [ ] Job durumu gerçek backend event'inden gelir; sahte progress yoktur.
- [ ] SSE/WebSocket veya bounded polling; reconnect ve terminal state güvenli.
- [ ] Retry aynı idempotency key/iş kaydı üzerinden yapılır; duplicate upload yaratmaz.
- [ ] Quarantine/policy-rejected source kullanıcıya anlaşılır ve secret göstermeyen nedenle sunulur.

### 17.5 Chat ve citation deneyimi

- [ ] Conversation project scope'u görünür ve değiştirilemez bağlam olarak gösterilir.
- [ ] No-answer, provider error, permission denied ve policy refusal farklı UI durumlarıdır.
- [ ] Citation panel yalnız kullanılan source'ları gösterir.
- [ ] Belge/version/file/page/line/bbox/symbol locator ve güvenli excerpt sunulur.
- [ ] Eski citation'ın dayandığı version ile güncel version farklıysa kullanıcıya gösterilir.
- [ ] Retrieval score son kullanıcı için yanıltıcı “doğruluk yüzdesi” şeklinde gösterilmez.
- [ ] Admin diagnostics; stage ranks, fallback reason ve timing'i yalnız yetkili role gösterir.
- [ ] Tam prompt/context veya secret içeriği UI debug'ında bulunmaz.

### 17.6 Erişilebilirlik ve güvenlik

- [ ] Klavye erişimi, focus yönetimi, semantik etiket, ekran okuyucu ve kontrast testleri.
- [ ] File upload tür/boyut/policy bilgisi erişilebilir şekilde sunulur.
- [ ] XSS için model cevabı/source HTML'i güvenli render edilir; raw HTML varsayılan yasak.
- [ ] Auth token localStorage'da düz metin kalıcı tutulmaz; seçilen auth mimarisine uygun güvenli yöntem kullanılır.
- [ ] CSRF/session politikası auth moduna göre uygulanır.
- [ ] Hassas error detail ve stack trace gösterilmez.

### 17.7 Test kapısı

- [ ] Unit: state reducers/hooks/schema mapping.
- [ ] Component: project selection, upload/job, no-answer, citation, permission states.
- [ ] Contract: generated client ve backend fixture.
- [ ] E2E: login/local auth, project create/select, upload, completed job, chat, used citation, delete/retention.
- [ ] E2E negatif: cross-project URL, expired key, failed/quarantined job, provider outage.
- [ ] Accessibility otomatik tarama + kritik akışta keyboard smoke.
- [ ] Production build ve bundle secret/config taraması.

### 17.8 Kabul kriterleri

- [ ] `page.tsx` monoliti işlevsel feature'lara ayrılmış.
- [ ] Backend API tipleri elle kopyalanmıyor; generated contract kullanılıyor.
- [ ] Project scope ve auth olmadan veri işlemi yapılamıyor.
- [ ] UI gerçek job/citation/no-answer semantiğini doğru gösteriyor.
- [ ] Unit/component/e2e/build/a11y kapıları CI'da çalışıyor.

---

## 18. AŞAMA 11 — Operasyon, gözlemlenebilirlik, backup/DR ve release candidate

### 18.1 Amaç

Sistemin yalnız geliştirici makinesinde “up” görünmesi değil; güvenli başlatma, izleme, arıza teşhisi, geri yükleme ve kontrollü release işlemlerinin tekrarlanabilir olması.

### 18.2 Deployment/compose ayrımı

- [ ] `compose.yaml` güvenli ortak taban olur.
- [ ] `compose.dev.yaml`: source bind mount, local debug ve geliştirici kolaylıkları.
- [ ] `compose.ci.yaml`: ephemeral, deterministic, no external secret/provider.
- [ ] Production örneği source bind mount içermez.
- [ ] Host portları local varsayılanda `127.0.0.1`'e bind edilir.
- [ ] Redis auth/persistence/eviction politikası açıkça belirlenir.
- [ ] MinIO root credential yalnız bootstrap içindir; uygulama sınırlı service account kullanır.
- [ ] DB user migration ve runtime için mümkünse ayrılır; least privilege uygulanır.
- [ ] Network'ler frontend/backend/data/egress ihtiyacına göre ayrılır.
- [ ] Migration one-shot service başarıyla bitmeden backend ready olmaz.
- [ ] Orphan container/volume tespiti `doctor` komutunda bulunur.

### 18.3 Container hardening

- [ ] Multi-stage build.
- [ ] Non-root user.
- [ ] Minimal base ve digest pin.
- [ ] Lockfile/hash ile install.
- [ ] Gereksiz build tool/runtime secret image'da kalmaz.
- [ ] CA bundle image içine public source'tan gömülmez; deployment mount/secret ile gelir.
- [ ] Healthcheck ve graceful shutdown.
- [ ] Read-only root filesystem ve tmpfs gereksinimleri test edilir.
- [ ] Resource request/limit veya compose limitleri belgelenir.
- [ ] Image SBOM, signature/provenance ve vulnerability scan artifact'i üretilir.

### 18.4 Health/readiness

- [ ] Liveness yalnız process/event loop sağlığını ölçer.
- [ ] Readiness; migration head, DB, Redis, object storage, queue ve zorunlu provider bağımlılıklarını kontrol eder.
- [ ] Zorunlu dependency veya schema uyumsuzsa HTTP 503 döner.
- [ ] Opsiyonel provider bozuksa capability-specific degraded durumu üretir; tüm sistemin hazır olduğu yalanını söylemez.
- [ ] Health endpoint anonim kullanıcıya host/credential/model gibi hassas detay vermez.
- [ ] Startup ve shutdown job/lease güvenli kapanışını uygular.

### 18.5 Structured log, trace ve metric

- [ ] JSON structured log; timestamp UTC, level, service, environment, request_id, trace_id, principal hash, workspace/project hash, operation/work item/attempt/job id, error code.
- [ ] Raw prompt, full context, belge içeriği, token, credential ve PII varsayılan loglanmaz.
- [ ] OpenTelemetry veya eşdeğer açık standarda uygun trace:
  - HTTP request;
  - outbox dispatch;
  - ingestion stages;
  - provider call;
  - retrieval stages;
  - answer/citation validation.
- [ ] Metric'ler:
  - queue depth/age;
  - job stage duration/failure/retry;
  - orphan/stale lease/outbox backlog;
  - retrieval latency/candidate counts/no-answer;
  - provider latency/error/token/cost;
  - citation invalid/unsupported claim;
  - DB pool/index/slow query;
  - backup age/restore drill age.
- [ ] High-cardinality raw ids metric label yapılmaz.

### 18.6 SLO ve alarm

İlk baseline ölçümünden sonra owner/on-call bilgisiyle SLO'lar yazılacak:

- API availability ve p95 latency;
- ingestion completion/failure süresi;
- outbox backlog yaşı;
- permission leakage ve invalid citation mutlak sıfır hedefi;
- backup freshness;
- restore drill başarısı;
- real benchmark regression.

- [ ] Her alarm actionable runbook'a bağlanır.
- [ ] Alert yalnız dashboard değildir; owner/escalation ve receipt üretir.
- [ ] Error budget aşımında yeni feature yerine reliability işi önceliklenir.

### 18.7 Runbook'lar

Mevcut runbook'lar gerçek komutlarla yeniden doğrulanacak; en az:

- [ ] installation/first boot;
- [ ] migration/recovery/lineage reset;
- [ ] backup/restore ve object reconciliation;
- [ ] reindex/embedding profile change;
- [ ] provider/CA/key rotation;
- [ ] stuck job/outbox/stale lease;
- [ ] object GC/quarantine;
- [ ] repository ingestion incident/SSRF;
- [ ] degraded readiness/provider outage;
- [ ] security incident ve public secret exposure;
- [ ] release/rollback.

Her runbook:

- prerequisites;
- dry-run;
- exact commands;
- expected output;
- stop condition;
- rollback;
- evidence/receipt;
- last_verified_sha/date içerir.

### 18.8 Backup ve disaster recovery

- [ ] Otomatik DB backup + object versioning/inventory politikası.
- [ ] Encryption ve key erişimi.
- [ ] Retention ve restore point seçimi.
- [ ] En az bir temiz ortam restore drill'i.
- [ ] DB–object referans reconciliation.
- [ ] RPO/RTO ölçümü ve gerçek değerleri.
- [ ] Restore edilen sistemde migration, auth, retrieval ve citation smoke.
- [ ] Backup var demek yeterli değildir; restore receipt zorunlu.

### 18.9 Release candidate kapısı

- [ ] Temiz clone.
- [ ] Empty local cache/volume.
- [ ] Lockfile'dan build.
- [ ] Blank DB migration.
- [ ] Synthetic E2E ingestion/retrieval/citation.
- [ ] Private approved real benchmark.
- [ ] Security/SBOM/image scan.
- [ ] Backup/restore drill.
- [ ] Docs/status exact SHA.
- [ ] Ruleset ve required checks.
- [ ] Known-risk register; P0/P1 açık yok.

### 18.10 Kabul kriterleri

- [ ] Production benzeri deployment default credential, source mount ve public CA içermez.
- [ ] Not-ready sistem 503 verir.
- [ ] Trace ile bir upload'tan citation'a kadar zincir takip edilebilir.
- [ ] Hassas içerik log/metric/artifact'te bulunmaz.
- [ ] Restore drill ve rollback kanıtı vardır.
- [ ] Release candidate exact SHA üzerinde tüm zorunlu kapılardan geçmiştir.

---

## 19. AŞAMA 12 — Gerçek Context Vault süreklilik katmanı

### 19.1 Başlama ön koşulu

Bu aşama yalnız Aşama 9 gerçek kalite/güvenlik kapısı ve Aşama 11 restore/release-candidate altyapısı geçtikten sonra başlayabilir. Amaç bozuk RAG'ın üzerine ajan otomasyonu eklemek değildir.

### 19.2 Amaç

Context Vault'u yalnız belge arayan uygulamadan; projeleri tanıyan, aktif işi ve kararları sürdüren, çoklu CLI/ajan arasında kontrollü devir sağlayan, kanıtlı mutasyon yapan ikinci beyin/süreklilik platformuna dönüştürmek.

### 19.3 Dört ayrı bilgi sınıfı

#### A. Operasyonel durum — authoritative

- Aktif work item;
- attempt ve claim/lease;
- state transition event'leri;
- approval ve mutation scope;
- terminal receipt;
- artifact referansları.

**Otorite:** PostgreSQL Work Graph.

#### B. Kalıcı bilgi/karar — reviewed authority

- Onaylı kararlar;
- proje kuralları;
- mimari ADR'ler;
- doğrulanmış kullanıcı tercihi/politika;
- supersession ve geçerlilik süresi.

**Otorite:** versioned knowledge kayıtları + onaylı source artifact.

#### C. Arama/vector projection — reconstructable

- Chunk, embedding, FTS, identifier index;
- context retrieval sonucu;
- cache.

**Otorite değildir:** kaynaklardan yeniden üretilebilir.

#### D. Analytics/telemetry — derived

- Metrik, dashboard, kullanım, kalite trendi, maliyet.

**Otorite değildir:** event/receipt'lerden türetilir.

### 19.4 PostgreSQL Work Graph

Minimum domain tabloları/sözleşmeleri:

```text
work_items
work_attempts
work_claims
work_events
work_approvals
work_receipts
artifact_refs
decision_records
knowledge_items
knowledge_revisions
context_sources
context_manifests
compiled_contexts
provider_registry
model_registry
skill_registry
```

#### Work item

- id, workspace/project, title, objective, scope, exclusions;
- priority, risk class, required approvals;
- expected baseline/revision;
- status, created_by, owner;
- parent/dependency ilişkileri;
- acceptance criteria ve evidence requirements.

#### Attempt

- work item, executor/adapter/model/provider;
- started/finished time;
- input context manifest hash;
- expected revision/drift token;
- outcome ve terminal reason.

#### Claim/lease

- resource scope;
- claimant/attempt;
- acquired/expires/heartbeat;
- fencing token;
- released/reconciled reason.

#### Event

- append-only state transition;
- actor, reason, correlation/causation id;
- previous/new state;
- payload schema version/hash.

#### Receipt

- prepare/apply/verify/close türü;
- command/tool/action;
- start/end, exit status;
- input/output artifact hash;
- before/after revision;
- test/evidence referansları;
- rollback result;
- signer/attestation.

### 19.5 Work Graph durum makinesi

```text
DRAFT
  → READY
  → CLAIMED
  → RUNNING
  → BLOCKED | VERIFYING
  → COMPLETED | FAILED | CANCELLED | RECOVERY_REQUIRED
```

Bağlayıcı kurallar:

- [ ] `RUNNING` için geçerli claim/lease ve fencing token gerekir.
- [ ] Etki claim'den önce başlayamaz.
- [ ] Expected revision drift etmişse attempt `BLOCKED/RECOVERY_REQUIRED` olur.
- [ ] `COMPLETED` için terminal receipt ve acceptance evidence gerekir.
- [ ] Lease süresi dolması otomatik “tamamlandı” değildir; reconciler gerçek etkiyi kontrol eder.
- [ ] Retry yeni attempt'tir; aynı idempotency key duplicate etki yaratmaz.
- [ ] Model/CLI kendi approval'ını veremez.
- [ ] Root security/authority/source-of-truth/global DoD değişikliği insan onayı ister.

### 19.6 Prepare/apply/close komut protokolü

Minimum CLI sözleşmesi:

```text
cv doctor
cv project register
cv work create
cv work prepare <id>
cv work claim <id>
cv work heartbeat <claim>
cv work apply <id> --expected-revision ... --idempotency-key ...
cv work verify <id>
cv work close <id> --receipt ...
cv work reconcile
cv context compile
cv knowledge propose
cv knowledge review
cv knowledge approve
cv provider test
cv skill verify
```

- [ ] `prepare` DB/repo/dış sistemi değiştirmez.
- [ ] `apply` scope dışına çıkamaz; adapter typed capability ile sınırlandırılır.
- [ ] Mutating tool call'ların sonucu receipt'e bağlanır.
- [ ] CLI kesilirse claim/reconcile akışı çalışır.
- [ ] Dry-run ile gerçek apply çıktısı farkı açıklanabilir.
- [ ] Her command JSON ve insan-okunur çıktı üretebilir.

### 19.7 Proje kimliği ve proje bağlam dosyaları

Her yönetilen proje için iki insan/makine dostu kaynak standardı:

```text
PROJECT_CONTEXT.md
.contextvault/project.yaml
```

`PROJECT_CONTEXT.md`:

- projenin amacı ve sınırları;
- ana mimari ve canonical path'ler;
- kurulum/test/çalıştırma komutları;
- kritik iş kuralları;
- güvenlik/veri sınıflandırması;
- bilinen riskler;
- önemli karar ve runbook bağlantıları;
- “bu dosyada canonical olmayanlar” açıklaması.

`.contextvault/project.yaml`:

- stable project_id;
- repository/root mapping;
- context source allowlist;
- ignored paths;
- load tier'ları;
- token budget;
- provider/data-policy;
- doğrulama komutları;
- owner ve approval policy;
- schema version.

- [ ] Dosyalar validator ve schema taşır.
- [ ] Bir CLI projeye girdiğinde önce bu manifest ile projeyi tanır.
- [ ] Generated bölümler elle düzenlenmez; human ve generated source ayrıdır.
- [ ] `.zekam/` entegrasyonu gerekiyorsa bu kaynaklardan üretilen compatibility projection olur; canonical truth olmaz.

### 19.8 ContextCompiler

Compiler girdileri:

1. Global güvenlik/otorite politikası.
2. Proje manifesti ve `PROJECT_CONTEXT.md`.
3. Aktif work item, claim ve acceptance criteria.
4. İlgili ADR/decision/knowledge kayıtları.
5. Son doğrulanmış receipt/handoff.
6. Query/task'e göre RAG ile bulunan on-demand içerik.
7. Model/provider capability ve token budget.

Compiler davranışı:

- [ ] `MUST_LOAD`, `SHOULD_LOAD_IF_RELEVANT`, `RETRIEVE_ON_DEMAND`, `NEVER_AUTO_LOAD` uygular.
- [ ] Full vault preload yapmaz.
- [ ] Her included item source id, version, hash, reason ve token cost taşır.
- [ ] Duplicate/superseded/stale bilgi deterministik elenir.
- [ ] Aktif görev ve security policy token baskısında düşürülemez.
- [ ] Context window, reserved output ve safety margin dikkate alınır.
- [ ] Compiled context immutable manifest/hash ile attempt'e bağlanır.
- [ ] Farklı CLI'lar aynı manifest için semantik olarak aynı core context'i alır; yalnız adapter formatı değişir.
- [ ] Sensitive item provider policy'ye göre exclude/local-only olabilir.

### 19.9 Knowledge yaşam döngüsü

```text
PROPOSED → REVIEWED → APPROVED → SUPERSEDED | REVOKED | EXPIRED
```

- [ ] Chat/transcript/model cevabı otomatik approved knowledge olmaz.
- [ ] Model extraction yalnız `PROPOSED` kayıt oluşturabilir.
- [ ] İnsan veya yetkili policy approval gerekir.
- [ ] Her knowledge revision source/evidence, owner, scope, confidence, validity interval ve supersedes taşır.
- [ ] Çelişkili bilgi overwrite edilmez; conflict kaydı açılır.
- [ ] Hassas bilgi data classification ve provider policy ile korunur.
- [ ] Obsidian/Markdown görünümü approved knowledge'ın projection'ıdır; elle değişiklik import/promotion akışından geçer.

### 19.10 Provider ve model registry

Her provider/model kaydı:

- provider/model id ve adapter;
- capability: chat, embedding, rerank, OCR, vision, tool use;
- context/output limitleri;
- embedding dimension/distance/prefix;
- data classifications allowed;
- local/remote ve network requirement;
- health/circuit-breaker durumu;
- latency/cost/quality benchmark;
- version/config hash;
- fallback policy.

- [ ] Kurum içi OpenAI-compatible embedding ve yerel BGE aynı portu uygular.
- [ ] Kullanıcının erişemediği model kimliği route edilemez; exact model id doğrulanır.
- [ ] Fallback farklı vector profile'ı sessiz kullanmaz.
- [ ] Routing yalnız isim değil capability + data policy + health + benchmark'a göre yapılır.
- [ ] Provider config secret'sız registry metadata ve secret store referansı olarak ayrılır.

### 19.11 Skill registry ve güven modeli

Her skill için:

- source URI/repo;
- version/commit SHA;
- content/package hash;
- publisher/owner;
- trust level;
- required tools/permissions/network/filesystem scope;
- supported CLI/adapters;
- install/update/uninstall receipt;
- license/security scan;
- enabled scope: global/project/work item.

- [ ] Skill yüklemek kodu kör çalıştırmak değildir; önce inspect/verify/policy.
- [ ] Mutable `main/latest` pin'i production skill kaynağı olamaz.
- [ ] Skill, root authority veya security policy'yi değiştiremez.
- [ ] CLI'ya özel skill core registry'den bağımsız kopyalanmaz; adapter projection üretilir.
- [ ] Scientific/agent skill setleri dahil dış kaynaklar aynı admission sürecinden geçer.

### 19.12 Çoklu CLI adapter'ları

Core provider-neutral kalacak; en az:

- OpenCode adapter;
- Claude CLI adapter;
- Codex/OpenAI CLI adapter.

Her adapter:

- ContextCompiler manifestini CLI formatına çevirir;
- tool/capability mapping yapar;
- claim/heartbeat/receipt protokolünü uygular;
- CLI çıktısını canonical state'e doğrudan yazmaz;
- model/CLI kimliğini attempt metadata'sında tutar;
- aynı work item üzerinde fencing token'a uyar.

- [ ] Adapter conformance test suite olacak.
- [ ] Bir adapter yokken core çalışmaya devam edecek.
- [ ] CLI transcript'i opsiyonel debug artifact'tir; canonical memory değildir.

### 19.13 Handoff ve süreklilik

Terminal veya ara handoff şu structured özeti üretir:

- work item/attempt/claim;
- amaç ve exact scope;
- baseline/current revision;
- yapılan mutasyonlar ve receipt'ler;
- doğrulanan/başarısız testler;
- açık risk ve blocker;
- sonraki izinli adım;
- gerekli context manifest hash'i;
- private artifact referansları.

- [ ] Yeni ajan/CLI handoff'u doğrulamadan apply yapamaz.
- [ ] “Son konuşmada şöyle demiştik” yerine decision/receipt referansı kullanılır.
- [ ] Handoff stale ise compiler uyarır ve yeni prepare ister.

### 19.14 Context Vault güvenilirlik testleri

- [ ] Aynı work item'a iki CLI claim olur; yalnız fencing token sahibi etki yaratır.
- [ ] Claim sonrası repo/DB drift eder; apply engellenir.
- [ ] CLI process ölür; stale lease reconcile edilir, iş sahte tamamlanmaz.
- [ ] Aynı idempotency key ile iki apply; tek etki ve iki gözlem/tek canonical receipt.
- [ ] Full-vault preload teşebbüsü budget/policy tarafından engellenir.
- [ ] `NEVER_AUTO_LOAD` içeriği remote provider manifestine girmez.
- [ ] Superseded knowledge compile edilmez.
- [ ] Model kendi cevabını approved knowledge yapamaz.
- [ ] Üç CLI adapter'ı aynı core context manifestini korur.
- [ ] Provider fallback yanlış embedding profile'a geçmez.
- [ ] Skill hash değişir; execution admission fail eder.
- [ ] Receipt olmadan work item completed olamaz.

### 19.15 Kabul kriterleri

- [ ] Operasyonel iş otoritesi PostgreSQL Work Graph'tır.
- [ ] Prepare/apply/claim/receipt protokolü gerçek bir mutasyon akışında kanıtlanmıştır.
- [ ] Her proje standart context manifestiyle tanınabilir.
- [ ] ContextCompiler load tier ve token budget ile bounded context üretir.
- [ ] Knowledge promotion insan kontrollü ve versioned'dır.
- [ ] Provider/model/skill kayıtları exact version/hash/data policy taşır.
- [ ] OpenCode/Claude/Codex adapter'ları conformance testinden geçer.
- [ ] Obsidian/Markdown/transcript/Git history operasyonel authority olarak kullanılmaz.

---

## 20. AŞAMA 13 — Bağımsız doğrulama, doküman kapanışı ve release kararı

### 20.1 Amaç

Uygulayıcının kendi test yorumuyla değil, temiz ve bağımsız ortamda bütün sistemin yeniden kurulabildiğini ve bu görevin her kabul kriterinin kanıtlandığını doğrulamak.

### 20.2 Bağımsız doğrulayıcı kuralları

- Uygulamayı yapan aynı ajan/oturum final verifier olamaz.
- Verifier temiz clone ve boş runtime kullanır.
- Önceden kalmış container, volume, cache, venv veya node_modules kullanmaz.
- Verifier source code'u değiştirmez; yalnız doğrulama raporu üretir.
- Başarısız kriteri “küçük” diyerek atlayamaz; severity ve blocker durumunu yazar.

### 20.3 Temiz ortam doğrulaması

- [ ] Exact candidate SHA checkout.
- [ ] Commit ownership ve clean tree.
- [ ] Lockfile'dan backend/frontend install.
- [ ] Compose config validation.
- [ ] Blank DB migration.
- [ ] Seed/bootstrap principal/workspace/project.
- [ ] Offline E2E ingestion → retrieval → answer → used citation.
- [ ] Auth/scope leakage testleri.
- [ ] Fault/retry/idempotency testleri.
- [ ] Frontend e2e/a11y/build.
- [ ] Security/SBOM/container scan.
- [ ] Approved real-provider benchmark.
- [ ] Backup → destroy test environment → restore → smoke.
- [ ] Work Graph prepare/claim/apply/receipt ve multi-CLI conformance.
- [ ] Public-safe scan.
- [ ] Docs/status/ADR/runbook exact SHA drift check.

### 20.4 Final rapor

`artifacts/release/<candidate-sha>/` altında public-safe:

```text
release-manifest.json
verification-report.md
test-summary.json
migration-summary.json
security-summary.json
rag-eval-summary.json
context-vault-protocol-summary.json
restore-summary.json
known-risks.md
SHA256SUMS
```

Private raw artifacts güvenli store'da tutulur; manifest yalnız referans/hash taşır.

### 20.5 Release kararı

Aşağıdakiler olmadan release/tag/merge kararı verilemez:

- [ ] P0/P1 açık bulgu yok.
- [ ] Required CI checks exact SHA'da yeşil.
- [ ] Branch ruleset etkin.
- [ ] Blank migration ve restore drill başarılı.
- [ ] Permission/version leakage `0`.
- [ ] Invalid citation label `0`.
- [ ] Approved real benchmark regression gate'i geçmiş.
- [ ] Critical/high security açığı veya public secret yok.
- [ ] Rollback/runbook/owner bilgileri hazır.
- [ ] Independent verifier `PASS` vermiş.
- [ ] Mehmet KARACAN remote push/merge/release için açık onay vermiş.

### 20.6 Görev kapanışı

- [ ] Final candidate SHA ve release receipt bu dosyaya işlenir.
- [ ] Tamamlanan aktif görev `done/active-tasks/` altına immutable olarak taşınır.
- [ ] Root `AKTIF_GOREV.md` yalnız yeni açık ve onaylı görev varsa değiştirilir; boşuna yeni kapsam üretilmez.
- [ ] `verified-state.json` release SHA'sını ve evidence manifestini gösterir.
- [ ] Açık P2/gelecek işler ayrı backlog'a taşınır; tamamlanmış gibi işaretlenmez.


---

## 21. Uygulama commit planı

Her commit tek amaçlı, geri alınabilir ve kendi test kanıtına sahip olmalıdır. Aşağıdaki sıra önerilen minimum seridir; bir commit aşırı büyürse aynı başlık altında küçük parçalara ayrılabilir, fakat farklı aşamalar karıştırılamaz.

1. `chore(governance): archive previous active task and lock v3 baseline`
2. `chore(audit): add public-safe admission manifest and drift verifier`
3. `fix(migrations): decouple migration config from application secrets`
4. `fix(migrations): recover or reset lineage with verified clean-db path`
5. `fix(data): repair version profile and artifact invariants`
6. `chore(format): apply repository formatter without behavior changes`
7. `chore(build): unify runtime versions and dependency locks`
8. `ci: restore automatic required quality and security gates`
9. `chore(repo): remove non-canonical skeletons and documentation drift`
10. `feat(auth): add principal workspace and fail-closed project scope`
11. `fix(schema): enforce version chunk profile and timezone invariants`
12. `refactor(ingestion): route all sources through one orchestrator`
13. `feat(ingestion): add staging outbox claims policy and safe activation`
14. `refactor(retrieval): introduce typed hits and canonical profile search`
15. `fix(retrieval): close scope rrf rerank and neighbor correctness gaps`
16. `feat(answer): add structured claims and validated citation provenance`
17. `test(eval): separate contract offline-e2e and real benchmark tiers`
18. `refactor(web): split product features and generate typed api client`
19. `chore(ops): harden deployment observability backup and readiness`
20. `feat(context-vault): add work graph receipts and context compiler`
21. `feat(context-vault): add provider model skill and cli adapter registries`
22. `docs(release): add verified runbooks status and independent report`

### 21.1 Commit kuralları

- Commit author/committer yalnız Mehmet KARACAN politikasıyla uyumlu olur.
- AI/CLI `Co-Authored-By` trailer'ı eklenmez.
- Test başarısızken “WIP tamamlandı” commit'i atılmaz; gerekiyorsa açık `wip/` branch ve receipt kullanılır, release zincirine alınmaz.
- Mechanical format commit'i behavior diff içermez.
- Migration/data commit'i backup/restore receipt olmadan atılmaz.
- Generated dosyanın kaynağı ve drift check'i aynı commit'te bulunur.
- Public artifact'ler secret taramasından geçmeden commit edilmez.
- Her commit mesajı neden, değişiklik, test/kanıt, risk ve geri dönüş bilgisini taşır.

---

## 22. Zorunlu doğrulama araçları

Aşağıdaki script'ler cross-platform ve machine-readable JSON çıktı üretecek. Shell/PowerShell wrapper eklenebilir; esas iş mantığı tek Python/uygun core uygulamasında tutulur.

| Araç | Sorumluluk |
|---|---|
| `scripts/verify_baseline.py` | SHA, dirty tree, audit manifest, drift |
| `scripts/verify_migrations.py` | heads/current, blank upgrade, schema fingerprint, invariant |
| `scripts/verify_data_integrity.py` | version/chunk/profile/artifact/outbox/orphan kontrolü |
| `scripts/verify_public_safety.py` | secret, private endpoint, cert, raw content, dump/log taraması |
| `scripts/generate_verified_status.py` | exact SHA'ya bağlı machine status |
| `scripts/verify_api_scope.py` | auth/workspace/project/conversation leakage |
| `scripts/verify_ingestion.py` | E2E, idempotency, outbox, retry, policy |
| `scripts/verify_retrieval.py` | active version/profile, RRF, context budget, index plan |
| `scripts/verify_citations.py` | label/claim/evidence hash/provenance |
| `scripts/run_eval.py` | açık tier seçimi ve regression gate |
| `scripts/verify_frontend.py` | client drift, lint/type/unit/e2e/build/a11y |
| `scripts/verify_runtime.py` | compose, health/readiness, log/trace, orphan/container |
| `scripts/verify_restore.py` | DB/object restore ve reconciliation |
| `scripts/verify_context_protocol.py` | claim/fencing/receipt/compiler/adapter conformance |
| `scripts/verify_release.py` | tüm kanıtları tek release manifestinde birleştirme |

### 22.1 Ortak script sözleşmesi

Her verifier:

- `--json-output <path>` destekler;
- `--strict` modunda warning'i fail'e çevirebilir;
- exit code `0=pass`, `1=validation failure`, `2=usage/config`, `3=environment unavailable` standardını kullanır;
- çalıştırılan exact commit SHA ve tool version yazar;
- secret değerini çıktılamaz;
- deterministic sıralama ve UTC timestamp kullanır;
- test edilebilir library fonksiyonu + ince CLI katmanı içerir;
- failure'da hangi acceptance criterion'ın kapandığını açıkça bildirir.

---

## 23. Kanıt ve receipt standardı

Her mutating veya release-gating işlem için receipt şeması en az:

```json
{
  "schema_version": "1.0",
  "receipt_type": "prepare|apply|verify|close|restore|release",
  "operation_id": "...",
  "work_item_id": "...",
  "attempt_id": "...",
  "actor": "...",
  "tool_adapter": "...",
  "started_at": "UTC",
  "finished_at": "UTC",
  "baseline_revision": "...",
  "result_revision": "...",
  "scope_hash": "...",
  "context_manifest_hash": "...",
  "idempotency_key_hash": "...",
  "commands": [],
  "exit_status": 0,
  "tests": [],
  "artifacts": [],
  "security_redactions": [],
  "rollback": {},
  "result": "PASS|FAIL|BLOCKED|RECOVERY_REQUIRED"
}
```

- Ham command içinde secret varsa receipt'e redakte edilmiş biçimi girer.
- Artifact entry path/URI, hash, classification ve retention taşır.
- Receipt değiştirilemez; düzeltme yeni superseding receipt olarak eklenir.
- Public receipt private URI veya içerik değil, opaque id/hash kullanır.

---

## 24. Yasak kısayollar ve anti-pattern'ler

Aşağıdakiler bu görevde açıkça yasaktır:

1. Kayıp migration'ı görmezden gelip `alembic stamp` ile mevcut DB'yi “uyumlu” göstermek.
2. Backup restore edilmeden canlı veri veya migration geçmişini değiştirmek.
3. Boş/no-op `0004/0005` dosyasıyla bilinmeyen geçmişi taklit etmek.
4. CI kırmızı olduğu için otomatik trigger, test, lint, mypy, coverage veya security adımını kapatmak.
5. `continue-on-error`, geniş `ignore`, `|| true`, kör skip veya test silerek yeşil sonuç üretmek.
6. FakeRetriever/FakeAnswerer sonucu gerçek RAG kalite kanıtı saymak.
7. Filtre/scope hatasında filtresiz aramaya dönmek.
8. `project_id` yokken ilk/default projeyi seçmek veya yaratmak.
9. Client'tan gelen conversation/document/version id'yi scope doğrulamadan kullanmak.
10. Candidate hazır olmadan aktif version'ı silmek/değiştirmek.
11. DB, object storage ve queue'ya outbox/idempotency olmadan bağımsız dual write yapmak.
12. Secret tespitinden önce normalize içerik/embedding/prompt üretmek.
13. Tam belge, prompt, context, token veya PII'yi log/debug/public artifact'e koymak.
14. Kurum içi endpoint veya CA'yı public default'a gömmek.
15. Farklı embedding model/dimension/profile sonuçlarını aynı vector uzayında karıştırmak.
16. Startup sırasında sessiz DDL veya schema “tamiri” yapmak.
17. Readiness bozukken HTTP 200 ile load balancer'a hazır görünmek.
18. Root ve nested iki ayrı uygulama ağacında yeni kod üretmek.
19. Ham transcript/model cevabı/Git history/Obsidian dosyasını operational truth kabul etmek.
20. Modelin kendi ürettiği bilgiyi otomatik approved knowledge yapması.
21. Tüm vault'u context window'a otomatik yüklemek.
22. Claim/fencing/drift kontrolü olmadan birden fazla CLI'ın aynı kaynağı değiştirmesi.
23. Receipt olmadan işi tamamlandı saymak.
24. Third-party skill/action/image/model'i mutable `latest/main` ile production'a almak.
25. Kullanıcıya ait proje dosyalarını, görev kayıtlarını veya belgeleri “eski YZ izi” sanıp silmek.
26. İlgisiz refactor, framework değişimi, Kubernetes veya yeni ürün özelliğini P0/P1 onarımına karıştırmak.
27. Remote push, merge, release veya history rewrite'ı açık insan onayı olmadan yapmak.

---

## 25. Kapsam dışı işler

Aşağıdaki konular yararlı olabilir; ancak bu aktif görevin zorunlu kapsamına dahil değildir ve P0/P1 teslimini geciktiremez:

- Kubernetes/multi-region/active-active kurulum;
- graph database eklemek;
- LLM fine-tuning;
- özel mobil uygulama;
- tam enterprise IAM/SCIM yönetim paneli;
- tüm programlama dilleri için AST parser;
- ücretli üçüncü taraf observability/platform zorunluluğu;
- otonom ajanların insan onayı olmadan dış sistemleri değiştirmesi;
- kozmetik UI yeniden markalama;
- repo köküne büyük monorepo taşıması;
- migration incident çözülmeden database motoru değiştirme;
- kanıtlanmamış “daha akıllı” memory framework'ünü core otorite yapmak.

Bu maddeler ayrı ADR/backlog ve açık onay gerektirir.

---

## 26. Önceki aktif görevin devralınan kapsamı

Önceki aktif görev silinmeyecek; arşivlenecektir. Aşağıdaki yararlı başlıklar bu görevde kaybolmadan devam eder:

| Önceki odak | Yeni karşılık |
|---|---|
| Runtime audit ve migration drift | Aşama 0–1 |
| CI workflow ve kalite kapıları | Aşama 2 |
| Kanonik repo yapısı/doküman | Aşama 3 |
| Retrieval filtreleri ve hybrid kalite | Aşama 4 ve 7 |
| Versioned ingestion/profile | Aşama 5–6 |
| Citation/no-answer/prompt güvenliği | Aşama 8 |
| Golden dataset/eval | Aşama 9 |
| Frontend ürünleştirme | Aşama 10 |
| Runbook/operasyon | Aşama 11 |

Ancak önceki dosyadaki checkbox veya “tamamlandı” durumu otomatik devralınmaz. Her madde bu dosyanın kanıt standardıyla yeniden doğrulanır.

---

## 27. Global Definition of Done

Bu aktif görev yalnız aşağıdaki koşulların **tamamı** sağlanırsa tamamlanır.

### 27.1 Repo ve yönetişim

- [ ] Tek kanonik uygulama ağacı var.
- [ ] Baseline/verified status exact SHA'ya bağlı.
- [ ] `main` required checks ile korunuyor.
- [ ] Otomatik kalite/security workflow'ları açık ve yeşil.
- [ ] Public-safe source ve açık lisans/proprietary kararı var.

### 27.2 Migration ve veri

- [ ] Migration lineage tanınabilir ve tek head.
- [ ] Blank DB kurulumu başarılı.
- [ ] Existing-data migration/cutover yolu doğrulanmış.
- [ ] Backup gerçekten restore edilmiş.
- [ ] Cross-version/profile/artifact invariant ihlali sıfır.
- [ ] Startup DDL yapmıyor.

### 27.3 Güvenlik ve scope

- [ ] Production auth fail-closed.
- [ ] Workspace/project/document/conversation leakage sıfır.
- [ ] Scope hatası filtresiz fallback üretmiyor.
- [ ] Debug/repository ingestion/rate limit güvenli policy altında.
- [ ] Secret/high-risk içerik remote provider'a sızmıyor.
- [ ] Public tree'de gerçek secret/private endpoint/internal CA yok veya açık onaylı provenance kararı var.

### 27.4 Ingestion ve retrieval

- [ ] Tek ingestion orchestrator.
- [ ] Staging + outbox + idempotent worker + safe activation.
- [ ] Active version/profile tüm retriever'larda zorunlu.
- [ ] Legacy embedding yolu kaldırılmış.
- [ ] RRF/rerank/neighbor/context budget hataları kapanmış.
- [ ] Fault/retry/concurrency testleri geçiyor.

### 27.5 Cevap ve kalite

- [ ] Structured AnswerEnvelope validation.
- [ ] Yalnız kullanılan source'lar citation.
- [ ] Immutable evidence/provenance.
- [ ] Fake eval release kapısı değil.
- [ ] Offline E2E ve approved real benchmark geçiyor.
- [ ] Permission/version leakage ve invalid label `0`.
- [ ] Quality/latency regression bütçesi geçiyor.

### 27.6 Ürün ve operasyon

- [ ] Frontend typed API ve testlenebilir feature mimarisinde.
- [ ] Gerçek job/no-answer/citation durumu doğru gösteriliyor.
- [ ] Readiness bozuksa 503.
- [ ] Structured log/trace/metric hassas veri sızdırmıyor.
- [ ] Restore, incident, key/CA rotation, reindex ve release runbook'ları doğrulanmış.

### 27.7 Context Vault sürekliliği

- [ ] PostgreSQL Work Graph operational authority.
- [ ] Prepare → claim → apply → verify → receipt protokolü.
- [ ] Project context standardı ve validator.
- [ ] Bounded ContextCompiler ve load tier'ları.
- [ ] Reviewed/versioned knowledge lifecycle.
- [ ] Provider/model/skill registries ve data-policy routing.
- [ ] OpenCode/Claude/Codex adapter conformance.
- [ ] Model cevabı/transcript/Obsidian/Git history canonical operational truth değil.

### 27.8 Bağımsız doğrulama

- [ ] Temiz clone/blank runtime doğrulaması.
- [ ] Independent verifier `PASS`.
- [ ] Release manifest ve SHA256SUMS.
- [ ] P0/P1 açık yok.
- [ ] Remote işlem için Mehmet KARACAN açık onayı.

---

## 28. İlk uygulanacak somut çalışma paketi

Bu dosya uygulama talimatıyla açıldığında ajan bütün aşamaları aynı anda başlatmayacaktır. İlk çalışma paketi yalnız şudur:

### Paket CV3-BOOTSTRAP

1. Başlangıç protokolünü çalıştır.
2. Baseline drift kontrolü yap.
3. Önceki aktif görevi immutable arşivle.
4. Aşama 0 public-safe audit manifestini üret.
5. Backend/RAG CI başarısızlıklarını lokal veya temiz container'da yeniden üret; hiçbir şeyi henüz kapatma/atlama.
6. DB/MinIO backup ve restore planını salt-okunur hazırla.
7. Kayıp migration recovery kaynaklarını inventory et.
8. `PREPARE_RECEIPT` oluştur.
9. Aşama 1'de hangi karar yolunun uygulanacağını kanıtla:
   - `RECOVER_EXACT_MIGRATIONS`, veya
   - `V3_NEW_DB_LINEAGE_RESET_AND_CUTOVER`.
10. Veri mutasyonuna başlamadan önce karar ve backup/restore admission sonuçlarını raporla.

### CV3-BOOTSTRAP tamamlanma kriteri

- Aşama 0 eksiksiz PASS.
- Backup/restore için erişilebilir kaynak ve güvenli hedef belirlenmiş.
- Migration Yol A/B kararı kanıta dayanıyor.
- Uygulanacak ilk mutating commit'in exact dosya/kapsam/test/rollback planı hazır.
- Kullanıcı dosyası silinmemiş, remote push yapılmamış, DB mutasyonu yapılmamış.

### CV3-BOOTSTRAP uygulama kaydı — 2026-09-02

- Durum: `PASS`; Aşama 0 ve `CV3-BOOTSTRAP` tamamlandı.
- Baseline: `6b99a53d19e7f5f7b07b403e751c629f79ab7663`; başlangıç tree temiz; `origin/main` ayrışması `0/0`.
- Çalışma branch'i: `fix/context-vault-v3-hardening`.
- Önceki görev arşivi: hash eşitliği `PASS`.
- Public-safe audit manifest ve checksum doğrulaması: `PASS`.
- Temiz detached baseline üzerinde `scripts/verify_baseline.py --strict`: `PASS`.
- Zekam registry exact source-root çözümlemesi: `PASS`; proje local-only/read-only bağlandı.
- Backend CI yeniden üretimi: beklenen `FAIL`; Ruff `97` dosyayı format dışı buldu.
- RAG migration yeniden üretimi: beklenen `FAIL`; Alembic DB bağlantısından önce LLM key validation'ında durdu.
- Gitleaks v8.30.1 history + working-tree taraması: `PASS`, bulgu `0`; yalnız redaction testlerindeki üç sentetik fixture exact fingerprint ile sınırlandı.
- Migration recovery kararı: `V3_NEW_DB_LINEAGE_RESET_AND_CUTOVER`, durum `AUTHORIZED_AND_ADMITTED`.
- Eski kaynak DB/MinIO bulunamadı; kullanıcı eski veriyi kurtarılamaz kabul ederek yeni boş ve izole V3 lineage için açık devam talimatı verdi. Bu karar sonradan bulunabilecek eski kaynağı silme yetkisi vermez.
- Yeni izole V3 restore drill: PostgreSQL blank/re-run/downgrade-upgrade ve ayrı DB restore `PASS`; MinIO ayrı bucket inventory/byte restore `PASS`.
- Yapılmayanlar: eski kaynak/volume silme, push, merge, release, history rewrite.
- Kanıt: `document-rag-platform/artifacts/audit/2026-09-02-baseline/`.

---

## 29. Son bağlayıcı talimat

“`AKTIF_GOREV.md` dosyasını uygula” veya “AGENTS başlangıç protokolünü uygulayarak onaylı aktif görevden devam et” talimatı verildiğinde:

- Yalnız bu dosyanın onaylı kapsamı uygulanır.
- Yeni görev, ürün fikri, framework değişimi veya kapsam genişletmesi eklenmez.
- En erken tamamlanmamış admission gate'ten başlanır.
- Önceden tamamlandı denilen madde kanıtsızsa yeniden doğrulanır.
- Kullanıcıya ait kaynak kodu, belge, görev kaydı ve proje içeriği korunur.
- Remote push/merge/release yapılmaz.
- İlk cevapta plan tekrar yazmak yerine baseline/drift ve ilk somut kanıt sunulur.
- Bir blocker çıkarsa güvenli yerde durulur; yapılan ve yapılmayan işler receipt ile açıkça ayrılır.

**İlk geçerli adım: AŞAMA 0 / Paket `CV3-BOOTSTRAP`.**
