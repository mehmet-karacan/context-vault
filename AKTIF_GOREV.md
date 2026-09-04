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

- [x] `api/v1/documents.py` içindeki legacy `extract_text` ve karakter bazlı `chunk_text` üretim yolundan çıkarılacak.
- [x] Worker, API modülünden parser/chunker import etmeyecek.
- [x] Reindex aynı orchestrator'ı ve immutable profile'ları kullanacak.
- [x] Sync endpoint gerekiyorsa ayrı pipeline çalıştırmayacak; aynı job'ı oluşturup bounded wait/poll ile sonucu döndürecek.
- [x] Varsayılan ürün davranışı async job olacak.
- [x] Parser/chunker/embedding profile kimlikleri version üzerinde zorunlu ve immutable olacak.
- [x] UI'dan gönderilen raw karakter chunk size üretim semantiğini değiştirmeyecek.

### 13.3 Kaynak ve dosya tespiti

- [x] Source adapter'ları ortak `SourceDescriptor` üretir: source_type, origin, revision, content length/hash, detected MIME, declared MIME, data classification hint.
- [x] Extension tek başına güven kaynağı değildir; magic/MIME/structure birlikte değerlendirilir.
- [x] Extension'sız desteklenen dosya tespit edilebilir.
- [x] Declared MIME ile detected type uyuşmazlığı policy event üretir.
- [x] Archive traversal, decompression bomb, symlink escape, generated/binary file ve ignore kuralları tek scanner katmanında uygulanır.
- [x] Repository clone geçici sandbox, timeout, boyut, dosya sayısı, history depth ve egress sınırına sahiptir.

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

- [x] Credential/private key/token tespit edilen içerik otomatik remote servise gönderilmez.
- [x] Yüksek riskli içerik silent redaction ile “normal belge” yapılmaz; quarantine + kullanıcı kararı gerekir.
- [x] Confidential iş dokümanı ile credential/secret aynı kategori sayılmaz; politika ayrı karar verir.
- [x] Orijinal artifact saklanacaksa encryption-at-rest, erişim kontrolü, retention ve audit uygulanır.
- [x] Normalize artifact, policy kararı verilmeden kalıcı storage'a yazılmaz.
- [x] Chunk/log/debug/eval artifact'inde secret veya tam belge içeriği bulunmaz.
- [x] Remote provider çağrısına data classification ve policy kararının audit izi eklenir.

### 13.5 DB–object storage–queue tutarlılığı

- [x] Object önce deterministic staging key'e yüklenir; checksum doğrulanır.
- [x] DB transaction document/version/job ve `outbox_events` kaydını birlikte oluşturur.
- [x] Commit sonrası dispatcher outbox event'ini Celery/queue'ya teslim eder.
- [x] Queue publish başarısızlığı event'i kaybetmez; retry edilir.
- [x] Staging object için referanssız/orphan sweeper vardır.
- [x] Final immutable object key, version/artifact id ve checksum'dan türetilir.
- [x] Outbox/inbox idempotency key unique constraint ile korunur.
- [x] Worker aynı mesajı birden çok aldığında aynı terminal sonucu üretir; duplicate chunk/artifact/version oluşturmaz.

### 13.6 Job claim, lease ve retry

- [x] Worker job'ı etki öncesi atomik claim eder.
- [x] Lease owner, lease expiry, heartbeat ve attempt kayıtları tutulur.
- [x] Stale lease güvenli reconcile edilir.
- [x] Retryable ve terminal error code'ları ayrılır.
- [x] Retry mevcut aktif version'ı silmez.
- [x] Stage geçişleri monotonic ve receipt'li olur.
- [x] Cancel yalnız güvenli noktalarda etkili olur; yarım artifact/chunk cleanup planı üretir.
- [x] Celery task id operasyonel otorite değil, attempt metadata'sıdır.

### 13.7 Build-new → validate → activate

- [x] Yeni version bağımsız candidate olarak parse/chunk/embed/index edilir.
- [x] Candidate için minimum invariant'lar:
  - source/artifact checksum tutarlı;
  - chunk sequence deterministik ve unique;
  - embeddings eksiksiz ve doğru profile/dimension;
  - gerekli index alanları dolu;
  - content policy ihlali yok;
  - smoke retrieval başarılı.
- [x] Aktivasyon tek DB transaction'ında yapılır.
- [x] Eski aktif version aktivasyon tamamlanana kadar okunabilir kalır.
- [x] Eski version/artifact/chunk silme ayrı retention/GC işi olur.
- [x] Aktivasyon sonrası query cache/profile cache invalidate edilir.

### 13.8 Silme ve garbage collection

- [x] Delete önce soft-delete/retention state üretir.
- [x] Conversation/citation retention ve legal hold politikası belirlenir.
- [x] GC yalnız referans sayımı ve retention dolduktan sonra object/vector/chunk siler.
- [x] GC idempotent, dry-run destekli ve receipt üretir.
- [x] Failed/abandoned staging object'ler için ayrı sweeper vardır.
- [x] Restore edilen DB ile object store arasında missing/orphan raporu üretilebilir.

### 13.9 Test matrisi

- [x] Aynı upload/idempotency key iki kez gönderilir; tek version/job sonucu.
- [x] Object upload sonrası DB commit fail; sweeper orphan'ı bulur.
- [x] DB commit sonrası queue fail; outbox daha sonra teslim eder.
- [x] Worker parsing/embedding/indexing/activation aşamalarının her birinde kill edilir ve retry edilir.
- [x] Eski aktif version tüm candidate hatalarında erişilebilir kalır.
- [x] Extension'sız TXT/PDF/DOCX/image fixture doğru router'a gider.
- [x] MIME spoof, zip bomb, traversal, symlink escape ve oversized source reddedilir.
- [x] Secret fixture quarantine olur; remote provider mock'una byte gönderilmez.
- [x] Concurrent iki reindex yalnız geçerli candidate'ı aktive eder.
- [x] Delete/GC retry duplicate veya yanlış object silmez.

### 13.10 Kabul kriterleri

- [x] Üretim kodunda tek ingestion orchestrator vardır.
- [x] Legacy sync chunker ve dual write yolu kullanılmaz.
- [x] DB/object/queue fault injection testleri veri kaybı ve sonsuz queued job üretmez.
- [x] Aktif version candidate hazır olmadan değişmez.
- [x] Secret/high-risk içerik remote provider'a sızmaz.
- [x] Tüm ingestion terminal durumları receipt ve error code taşır.

### 13.11 Aşama 6 uygulama kaydı — 2026-09-02

- Durum: `PASS`; uygulama commit'i
  `69096dc485c91ff6dcda13d6b9d247946dbff3c5`.
- Üretim ağacındaki legacy `ReindexService` ve sync upload/chunker yolu
  kaldırıldı. Upload, repository, directory, archive, refresh/reindex ve retry
  tek `IngestionOrchestrator` üzerinden immutable profile/policy ile çalışıyor;
  yapısal guard ikinci orchestrator ve dual-path flag'ini reddediyor.
- `cv3_00000004` izole PostgreSQL'de
  `0003 → 0004 → 0003 → 0004` çevrimini geçti; `alembic check` temiz. Strict
  verifier 255 kolon, 110 constraint ve 17 runtime invariant'ın tamamını `0`
  buldu; schema hash
  `7328301b5e7ccd22ba66b1c4e3c23292ac751049ede639628cae1dca339d087b`.
- Gerçek PostgreSQL testleri upload/inbox/outbox idempotency, publish recovery,
  stale claim/lease, safe cancel, registered ve unregistered staging sweep,
  reindex active-version koruması, concurrent compare-and-swap, her kritik
  stage sonrası crash/retry yakınsaması ve citation/legal-hold GC kapılarını
  doğruladı.
- Gerçek MinIO testleri application-layer AES-256-GCM ciphertext, AAD/tamper
  reddi, üç final immutable artifact ve staging cleanup davranışını doğruladı.
  Eksik/bozuk encryption key startup'ı durduruyor; legacy plaintext read
  varsayılan kapalı.
- Source router extension'sız TXT/PDF/DOCX/image, MIME mismatch audit, spoof,
  traversal, zip bomb, symlink, boyut ve ignore sınırlarını test etti. Secret
  fixture storage/queue/provider öncesi quarantine edildi; izinli remote
  embedding çağrısı actor/workspace/classification/policy audit izi üretti.
- Backend tam regresyonu: `587 passed, 2 skipped`; Ruff lint PASS ve
  `171 files already formatted`. Skip'ler koşula bağlı optional entegrasyon
  yollarıdır; A6 zorunlu PostgreSQL/MinIO testleri çalıştırıldı ve geçildi.
- Aktivasyon sonrası stale active-version query/profile cache riski yoktur:
  runtime'da böyle bir cache bulunmuyor; embedding cache yalnız immutable
  content/profile hash'iyle anahtarlanıyor ve aktivasyon pointer'ını cache'lemiyor.
- Public-safe kanıt:
  `artifacts/ingestion/2026-09-02-a6/INGESTION_RECEIPT.json`; işletim ve
  retention kararı ADR-009 ile güncel ingestion runbook'unda kayıtlıdır.
- Yapılmayanlar: kullanıcı/verili DB migration'ı, mevcut object silme, remote
  push/PR/merge/release ve history rewrite.

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

- [x] Rank ataması nesneyi yeniden kurup stage score kaybetmeyecek.
- [x] Her stage input/output'u typed ve testlenebilir olacak.
- [x] Debug serialization full object yerine güvenli projection kullanacak.

### 14.3 Dense retrieval

- [x] Yalnız `chunk_embeddings` ve seçili aktif embedding profile kullanılacak.
- [x] `chunks.embedding` legacy fallback backfill doğrulandıktan sonra kaldırılacak.
- [x] Query vector dimension/profile doğrulanmadan SQL çalışmayacak.
- [x] Active document version, completed status, workspace/project ve policy predicates zorunlu.
- [x] HNSW/IVFFlat tuning transaction-local ayarlarla ve benchmark kanıtıyla yapılacak.
- [x] Vector index'in gerçekten kullanıldığı query planıyla doğrulanacak.
- [x] Empty query embedding veya provider failure “boş vector ile devam” değil typed error/no-answer nedeni üretir.

### 14.4 Lexical retrieval

- [x] Dil/identifier doğasına uygun FTS strategy benchmark ile seçilecek.
- [x] `plainto_tsquery(simple)` tek zorunlu yol olmayacak; phrase, OR/websearch ve identifier sorguları ayrılacak.
- [x] Hardcoded stopword/substring rescue false-positive etkisi ölçülecek.
- [x] Search vector'ın üretildiği parser/profile sürümü izlenecek.
- [x] Active version/scope/policy predicates dense ile aynı olacak.
- [x] Rank açıklaması matched terms ve query formunu taşıyacak.

### 14.5 Identifier retrieval

- [x] Exact, normalized exact, prefix, trigram/fuzzy ve substring ayrı match type olacak.
- [x] `%`, `_`, escape ve case normalization güvenli uygulanacak.
- [x] Leading wildcard varsayılan olmayacak.
- [x] `pg_trgm` gerçekten kullanılıyorsa uygun index/operator ve query planı kanıtlanacak; kullanılmıyorsa “trigram” adı kaldırılacak.
- [x] Arbitrary sabit skor yerine match type/rank kalibrasyonu benchmark ile yapılacak.
- [x] PL/SQL/package/schema/table/column/symbol metadata ayrı alanlarda aranabilecek.

### 14.6 RRF, dedupe ve reranking

- [x] Bir retriever listesinde aynı chunk bir kez katkı verir.
- [x] RRF contribution map kaydedilir; ham skorlar toplanmaz.
- [x] Dedupe, content_hash/source/version metadata bağlandıktan sonra yapılır.
- [x] Aynı içerik farklı version'daysa yalnız aktif/scope uygun olan kalır.
- [x] Reranker input'u maksimum aday/token bütçesine uyar.
- [x] Reranker kapalı/başarısızsa açıkça `noop/fallback_reason` yazar; score uydurmaz.
- [x] Reranker sonucu `reranker_score` ve model/profile ile korunur.
- [x] Reranker fallback güvenlik scope'unu değiştirmez.

### 14.7 Parent/neighbor ve ContextBundle

- [x] Neighbor anahtarı yalnız `source_id + sequence_no` olmayacak; workspace, project, document, version, source_file ve sequence birlikte sınırlar.
- [x] Parent/neighbor resolver aynı `RetrievalScope` ile çağrılır.
- [x] Parent pool yalnız son seçilmiş adaylarla sınırlı değil; scope'lu resolver üzerinden gerektiğinde alınır.
- [x] Duplicate content/token budget optimizasyonu deterministic olur.
- [x] Context budget model tokenizer/profile ile hesaplanır; kaba kelime sayımı yalnız açık fallback olabilir.
- [x] Bundle seçilmeyen adaylar ve ret nedenlerini debug için tutar, modele göndermez.
- [x] Full content loglanmaz; evidence hash ve redacted snippet kullanılır.
- [x] AnswerService yalnız `ContextBundle.selected_items` üzerinden prompt kurabilir.

### 14.8 Retrieval run kaydı ve observability

- [x] Her sorgu için `retrieval_runs` kaydı:
  - principal/workspace/project;
  - normalized query hash, gerekiyorsa şifreli/retention'lı raw query;
  - retriever/profile/config sürümleri;
  - candidate/selected sayıları;
  - per-stage latency;
  - no-answer reason;
  - error/fallback reason;
  - bundle hash.
- [x] Metric cardinality kontrolü yapılır; raw document/chunk id metric label olmaz.
- [x] Slow query ve index miss trace edilir.

### 14.9 Test matrisi

- [x] Same-retriever duplicate RRF katkısını artırmaz.
- [x] Rank yeniden ataması reranker ve stage skorlarını korur.
- [x] Legacy/canonical embedding karışmaz.
- [x] Inactive/cross-project/cross-version chunk hiçbir retriever'da dönmez.
- [x] Empty document list sonuç döndürmez.
- [x] Unknown filter ve signature mismatch fail-closed.
- [x] Neighbor başka version/project'e geçemez.
- [x] Identifier wildcard/case/escape adversarial testleri.
- [x] Dense/FTS/trigram index usage plan testleri.
- [x] Bundle token bütçesi hiçbir koşulda aşılmaz.

### 14.10 Kabul kriterleri

- [x] Retrieval pipeline uçtan uca typed ve immutable stage provenance taşır.
- [x] Tüm retriever'larda scope, active version ve profile predicates zorunlu.
- [x] Legacy embedding fallback üretim sorgusundan kaldırılmış.
- [x] RRF duplicate, rerank score loss ve cross-version neighbor hataları testlerle kapanmış.
- [x] Modele giden tek bağlam nesnesi `ContextBundle`.
- [x] Permission/version leakage `0`.

### 14.11 Aşama 7 uygulama kaydı — 2026-09-02

- Durum: `PASS`; uygulama commit'i
  `bffcf45b94c9702ddac43a30171e9d489591efdb`.
- İzole PostgreSQL üzerinde backend tam regresyonu `591 passed, 9 skipped`
  sonucu verdi; üç zorunlu typed retrieval entegrasyon testi ayrıca geçti.
- `cv3_00000004 → cv3_00000005 → cv3_00000004 → cv3_00000005`
  migration çevrimi ve `alembic check` geçti. Legacy-only vector bulunan ayrı
  fail-gate DB'sinde upgrade veri silmeden reddedildi ve DB `cv3_00000004`
  seviyesinde kaldı.
- Strict verifier 276 kolon, 116 constraint ve 19 invariant'ın tamamını `0`
  buldu; schema hash
  `1f5d88e9ab3bfea7b344c14f3ace80faa5f048f9cc2eaa3fc32cc94f1fe6603`.
- Bounded benchmark'ta HNSW `ef_search=20/40/80` için recall@10 `1.0`;
  seçilen değer `20`. Vector, FTS ve trigram plan uygunluğu doğrulandı;
  benchmark transaction'ı geri alındı ve provider credential yüklenmedi.
- Scope/active-version/profile leakage entegrasyon fixture'ında `0`; empty
  document scope üç retriever'da da sıfır sonuç verdi. RRF duplicate,
  reranker provenance, neighbor boundary, wildcard/escape ve ContextBundle
  bütçe regresyonları geçti.
- Public-safe kanıt:
  `artifacts/retrieval/2026-09-02-a7/RETRIEVAL_RECEIPT.json`; hash manifesti
  aynı dizindeki `SHA256SUMS` dosyasındadır.
- Yapılmayanlar: kullanıcı/verili DB migration'ı, mevcut object silme, remote
  push/PR/merge/release ve history rewrite.

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

- [x] `source_labels`, yalnız ContextBundle'daki mevcut `[S1..Sn]` kümesinden olabilir.
- [x] Unknown label, boş claim source veya schema ihlali kontrollü repair/retry ya da no-answer üretir.
- [x] Modelin kullanmadığı candidate citation olarak persist edilmez.
- [x] Cevap metni ve structured claims tutarlılık kontrolünden geçer.
- [x] Bir claim birden fazla kaynağa dayanabilir; ilişki ayrı tabloda tutulur.

### 15.3 Kanıt paketleme

- [x] Prompt'a yalnız `ContextBundle.selected_items` girer.
- [x] Her source için label, güvenli locator, document/source adı, version ve sınırlı içerik verilir.
- [x] İçerik açık “UNTRUSTED SOURCE DATA” sınırları içinde yer alır.
- [x] Source içindeki instruction, system/developer talimatı sayılamaz.
- [x] Query, policy ve evidence bölümleri birbirinden açıkça ayrılır.
- [x] Prompt bütçesi model context window, reserved output ve safety margin ile hesaplanır.
- [x] Truncation, label-content eşleşmesini bozmaz.

### 15.4 Citation provenance modeli

`message_citations` genişletilecek veya normalize edilecek:

- [x] message_id, claim_id, retrieval_run_id;
- [x] chunk/document/version/source_file/profile kimlikleri;
- [x] source label ve answer içindeki kullanım sırası;
- [x] retrieval/fusion/reranker score snapshot;
- [x] page/line/bbox/symbol locator;
- [x] evidence excerpt veya güvenli immutable snapshot;
- [x] evidence hash/content hash;
- [x] model, prompt template/version ve generation config;
- [x] citation validation sonucu;
- [x] created_at UTC.

- [x] Kaynak daha sonra reindex/silinse bile citation neye dayandığını hash/snapshot ile açıklayabilmeli.
- [x] Hassas excerpt şifreli/retention'lı saklanır; public loga girmez.

### 15.5 No-answer ve belirsizlik

- [x] Smalltalk, policy refusal, insufficient evidence, provider failure ve permission-denied birbirinden ayrılır.
- [x] Empty retrieval otomatik “smalltalk” sayılmaz.
- [x] No-answer threshold gerçek dataset ile kalibre edilir.
- [x] Exact identifier güçlü kanıt gibi özel durumlar ölçülmüş kural olarak uygulanır.
- [x] Provider timeout/error kanıt yokmuş gibi sessiz cevap üretmez.
- [x] UI no-answer nedenini kullanıcıya uygun, debug detayını yalnız yetkili role gösterir.

### 15.6 Prompt injection ve içerik güvenliği

- [x] Direct ve indirect prompt injection fixture'ları oluşturulur.
- [x] “Talimatları yok say”, tool çağır, secret göster, başka belge getir, citation uydur gibi kaynak metinleri veri kabul edilir.
- [x] Model tool kullanacaksa allowlist, typed arguments, scope ve explicit apply gate gerekir.
- [x] Retrieval debug, prompt ve full context loglama varsayılan kapalıdır.
- [x] Provider request/response retention politikası belgelenir.
- [x] Remote provider'a gönderilen her çağrı data-policy kontrolünden geçer.

### 15.7 Conversation güvenilirliği

- [x] Conversation history workspace/project/principal scope'lu yüklenir.
- [x] Geçmiş mesajların tümü otomatik context'e eklenmez; özet/retrieval ve token budget uygulanır.
- [x] Modelin önceki cevabı canonical fact sayılmaz.
- [x] Conversation title/summary model üretimiyse provenance ve draft status taşır.
- [x] Mesaj silme/retention/citation bütünlüğü belirlenir.

### 15.8 Test matrisi

- [x] Model var olmayan `[S99]` döndürür; citation persist edilmez ve cevap kontrollü işlenir.
- [x] Model bundle'daki 5 kaynaktan yalnız 2'sini kullanır; DB'de yalnız 2 source/claim ilişkisi oluşur.
- [x] Claim source'suz kalır; gate bunu yakalar.
- [x] Kaynak içinde prompt injection bulunur; scope/tool/policy değişmez.
- [x] Context truncation label-content eşleşmesini bozmaz.
- [x] Reindex sonrası eski mesaj citation hash/snapshot'ı doğrulanır.
- [x] Provider timeout, malformed JSON ve partial stream güvenli terminal sonuç üretir.
- [x] Cross-project conversation id reddedilir.

### 15.9 Kabul kriterleri

- [x] Model cevabı schema validation'dan geçmeden kullanıcıya/persistence'a gitmez.
- [x] Persist edilen citation'ların tamamı kullanılan label ve claim'e bağlıdır.
- [x] Citation precision/coverage gerçek eval ile ölçülebilir.
- [x] Prompt injection source scope/policy/tool davranışını değiştiremez.
- [x] Cevap yeniden üretilebilir provenance taşır.

### 15.10 Aşama 8 uygulama kaydı — 2026-09-02

- Durum: `PASS`; uygulama commit'i
  `24480ed0eed34cf0e9b87730f2fffce2b550beba`.
- Model sınırı strict `AnswerEnvelope` JSON schema kullanıyor. Dynamic label,
  claim-text ve used-label kontrolleri response/persistence öncesinde çalışıyor;
  tek bounded repair sonrası malformed/provider yolları typed no-answer oluyor.
- Beş adaydan yalnız kullanılan iki label'ın persist edilmesi, `[S99]`,
  source'suz claim, injection, bütün-label truncation, timeout ve partial stream
  testleri geçti. Remote data-policy reddinde provider çağrı sayısı `0`.
- `cv3_00000006` blank kurulum ve
  `0005 → 0006 → 0005 → 0006` çevrimi geçti; `alembic check` temiz. Strict
  verifier 310 kolon, 128 constraint ve 22 invariant'ın tamamını `0` buldu;
  schema hash
  `c2e927fdcd5a90ef15770e97545fec77c53db3b0c44060f1219651a36061e00e`.
- Gerçek PostgreSQL lifecycle testinde claim/citation ilişkisi ve AES-GCM
  excerpt yazıldı; source chunk silindikten sonra FK null olurken encrypted
  snapshot/evidence hash doğrulanabilir kaldı. Conversation history exact
  principal/workspace/project scope ve token budget ile yüklendi.
- No-answer/identifier politikası 19 production-policy testi ile offline A9
  production-pipeline koşusuna dahil edildi. Koşuda fabricated no-answer `0`,
  invalid label `0`, citation precision/coverage `1.0` ölçüldü; bu sonuç
  real-provider release baseline'ı değildir.
- Backend tam regresyonu gerçek izole PostgreSQL ve MinIO ile
  `607 passed, 2 skipped`; frontend lint/typecheck ve `2` unit test geçti.
  Skip'ler bu hostta bulunmayan Pillow ve Tesseract optional gerçek-binary
  yollarıdır.
- Public-safe kanıt: `artifacts/answers/2026-09-02-a8/ANSWER_RECEIPT.json`.
- Yapılmayanlar: kullanıcı/verili DB migration'ı, provider çağrısı, mevcut
  object silme ve remote push/PR/merge/release.

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

- [x] Golden dataset code, table, identifier, prose, OCR, archive/repository, multi-document, contradictory source, temporal version, no-answer ve permission senaryolarını kapsar.
- [x] Query/source leakage engellenir; fixture üretimi golden expected result'tan runtime candidate oluşturmaz.
- [x] Dataset train/tune ve holdout bölümlerine ayrılır.
- [ ] Her değişiklik review ve dataset version hash'i taşır.
- [x] Hassas şirket içi fixture public repoya konmaz; public sentetik eşdeğer ve private pack ayrılır.

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

- [x] Cross-tenant/project/document/conversation erişimi.
- [x] Inactive veya eski version'ın daha yüksek skorla gelmesi.
- [x] Yanlış embedding profile/dimension.
- [x] Empty/unknown filter ve retriever signature mismatch.
- [x] Duplicate RRF ve duplicate source content.
- [x] Prompt injection ve citation fabrication.
- [x] Secret içeren belge ve remote provider block.
- [x] MIME spoof, extensionless source, corrupted PDF/DOCX/image.
- [x] DB/Redis/MinIO/provider/queue timeout ve kesinti.
- [x] Worker'ın her stage'de kill/retry edilmesi.
- [x] Aynı upload/reindex/delete komutunun eşzamanlı ve tekrar teslimi.
- [x] Migration yarıda kalma ve restore/cutover.
- [x] Stale claim/lease ve clock skew.
- [x] Context budget overflow ve oversized source.
- [x] Contradictory documents ve güncel/eski source ayrımı.

### 16.6 Regression ve release gate

- [ ] İlk approved real-provider koşusu “baseline” olarak insan onayıyla mühürlenir.
- [x] Sonraki değişikliklerde:
  - permission/version leakage kesin `0`;
  - invalid citation label kesin `0`;
  - expected no-answer fixture'larında fabricated answer kesin `0`;
  - Recall@5 ve MRR approved baseline'a göre 2 yüzde puandan fazla düşemez;
  - citation precision/coverage approved baseline'a göre 2 yüzde puandan fazla düşemez;
  - p95 latency aynı donanım/fixture'da yüzde 20'den fazla kötüleşirse açık performans onayı gerekir;
  - yeni critical/high security bulgusu kabul edilmez.
- [x] Flaky test üç tekrar ve seed kaydıyla analiz edilir; “rerun until green” yok.
- [x] Baseline güncellemesi yalnız metrik düştüğü için yapılmaz; gerekçe, diff ve insan onayı gerekir.

### 16.7 Teslimatlar

- [x] `tests/evals/contract_smoke/`
- [x] `tests/evals/offline_e2e/`
- [x] `tests/evals/real_benchmark/`
- [x] Versioned public synthetic dataset
- [x] Private dataset manifest/hash sözleşmesi
- [x] `scripts/run_eval.py` açık `--tier` seçimiyle
- [x] Machine-readable JSON + human-readable Markdown rapor
- [x] Baseline comparator ve regression gate
- [x] CI artifact retention politikası

### 16.8 Kabul kriterleri

- [x] Fake eval hiçbir release/quality kapısını tek başına geçiremez.
- [x] Offline E2E fresh infrastructure üzerinde production pipeline'ı kullanır.
- [ ] Approved real-provider benchmark etiketten sonuç türetmeden çalışır.
- [x] Leakage ve citation validity mutlak kapıları geçer.
- [x] Quality regression exact SHA ve config hash ile raporlanır.
- [x] Fault/concurrency/recovery senaryoları deterministic receipt üretir.

### 16.9 Aşama 9 ara uygulama kaydı — 2026-09-02

- Durum: `BLOCKED`; uygulama commit'i
  `60aed18a41dc98feb11f169cb63db1517e8ade68`.
- Contract-smoke 66 kaydı çalıştırdı ancak rapor zorunlu olarak
  `quality_claim=false` ve `release_gate_eligible=false` yazdı.
- Offline tier fresh `cv3_00000006` PostgreSQL/pgvector, gerçek Redis ve gerçek
  AES-GCM MinIO ile production ingestion → retrieval → ContextBundle →
  AnswerEnvelope → used citation yolunu çalıştırdı. Golden expected alanları
  runtime fixture/candidate üretiminde okunmadı.
- Seed `20260902` ile 42 test üç bağımsız tekrarın her birinde geçti. Her koşuda
  permission/version leakage `0`, invalid label `0`, fabricated no-answer `0`,
  prompt-injection success `0`, citation precision/coverage `1.0` oldu. Bu
  ölçümler offline güvenlik/contract kanıtıdır; real-provider kalite baseline'ı
  değildir.
- `scripts/run_eval.py` üç explicit tier, exact SHA/dataset hash, JSON/Markdown,
  absolute leakage/citation kapıları, 2 puan quality ve yüzde 20 p95 comparator
  uygular. Public CI yalnız contract/offline tier'larını çalıştırır ve artifact'i
  14 gün saklar.
- Public synthetic v2 dataset 16 kayıt, train/holdout ve 15 query type içerir;
  private pack yalnız opaque id/hash/classification sözleşmesiyle temsil edilir.
- Açık kapılar: dataset owner review, açık provider/model/private-pack/bütçe
  onayıyla real-provider benchmark ve ardından insan tarafından baseline seal.
  Bu nedenle Aşama 9 ve release kalite kapısı tamamlanmış sayılmıyor.
- Public-safe kanıt:
  `artifacts/evals/2026-09-02-a9/A9_PRE_PROVIDER_RECEIPT.json`.
- Yapılmayanlar: gerçek provider çağrısı, kullanıcı DB/object mutasyonu ve
  remote push/PR/merge/release.


### 16.10 A9 benchmark admission ek çalışma kaydı — 2026-09-02

Başlangıç SHA: `c25bebcdeb92d70ba0d6ba8d4fd05a06c247d944`.
Durum: **yerel admission regression PASS; A9 gerçek benchmark hâlâ BLOCKED**.

- Salt-okunur inceleme: `_real_benchmark` manifestte yalnız dört alanın varlığını
  kontrol ediyor; şema sürümü/tarih/hash ve boş reviewer geçerli sayılabiliyor.
  Dört sıfır güvenlik sayacı olan eksik rapor `release_gate_eligible=true` alabiliyor;
  wrapper ölçmediği golden-data aktarımı sonucunu sabit `false` yazıyor.
- Yerel çalışma kapsamı/claim: yalnız `scripts/run_eval.py`, tier regression
  testleri, benchmark runbook'u, bu kayıt ve yeni immutable kanıt eki. Mevcut lock'taki
  jsonschema validator doğrudan dev bağımlılığı olarak pyproject/lock'ta kaydedildi;
  offline lock kontrolü 122 paketle geçti, paket sürümü değiştirilmedi.
  Aynı kapsamda başka çalışma gözlenmedi. Yeni mimari veya sonraki aşama başlatılmadı.
- Plan: sentetik/stub runner ile negatif test → fail-closed manifest/report
  validation → kanıtı olmayan release/golden-data iddiasını kaldırma → unit/CLI
  kontrolleri ve receipt. Onaysız provider/process dispatch durmalı.
- Veri etkisi: yalnız geçici sentetik test dosyaları; kullanıcı DB/MinIO/vektör
  verisi ve gerçek provider kullanılmadı. Tam backend suite mevcut isolated test
  DB/Redis/MinIO üzerinde koştu; kaynak veri sıfırlanmadı. Rollback yalnız bu verifier patch'i;
  önceden üretilmiş receipt'ler değiştirilmez.
- Yeniden üretim: yeni 17 negatif test eski kodda FAIL, mevcut 4 test PASS.
  Düzeltme sonrası genişletilmiş **41 tier testi PASS**; tam backend **657 PASS,
  2 skip**, 1 Starlette/httpx uyarısı. Ruff, MyPy strict (14 dosya), deterministic
  OpenAPI drift ve locked dependency check PASS. Bir ara eval koşusu eksik test
  bağlantı ayarları nedeniyle 2 FAIL/75 PASS verdi; doğru isolated environment ile
  yapılan tam koşu bu iki testi de geçti. Başarısız koşu gizlenmedi.
- Manifest şeması format/required/additional-field/review kontrolleri artık provider
  dispatch'ten önce çalışır. Boş onay veya eksik manifest/runner fail-closed kalır.
  Gerçek CLI admission probe exit **3**, provider çağrısı yok.
- Rapor provenance hash'leri ve zorunlu kalite/güvenlik metrikleri typed/finite
  doğrulanır; eksik comparator alanı sessiz PASS üretmez. Arbitrary runner alanları
  envelope SHA/tier veya raw prompt olarak dışarı taşınmaz. Dataset hash'i private
  manifestten gelir; public dataset hash'i gerçek pack kanıtı gibi etiketlenmez.
- Wrapper artık rapor şeklini actual provider execution veya insan baseline seal'i
  saymaz: `quality_claim=runner-reported-unverified`, `baseline_review_required=true`,
  `release_gate_eligible=false`. Golden transfer gözlenmemişse `null`; bildirilen
  `true` başarısızdır. Bu düzeltme §16.9'daki önceki release-gate uygulaması iddiasını
  daraltır; insan seal/final admission henüz uygulanmış veya geçilmiş sayılmaz.
- Yeni kanıt: `artifacts/evals/2026-09-02-a9-admission/`. A9 owner review,
  provider/model/private-pack/bütçe onayı ve gerçek baseline; A11–A13 açık kalır.

---

### 16.11 A9 approval/budget/metric binding çalışma kaydı — 2026-09-03

Başlangıç SHA: `518f7f694c83355ed483b062ce0331b6cd8048da`.
Durum: **yerel approval/budget/provenance admission PASS; A9 gerçek koşu BLOCKED**.

- Bağımsız alt ajan §16.10 admission patch'ini 41/41 test, CLI exit 3,
  artifact/source hash'leriyle dar kapsamda `APPROVE` etti. A9 kapanışını, alt
  ajanın owner/insan yerine onay vermesini ve A9 açıkken A11'e geçişi `REJECT`
  etti. Bu karar insan onayı veya baseline seal değildir.
- Ek bulgular: approval id yalnız nonblank; provider/model/private pack/
  classification/bütçe/runner bağlamı yok; subprocess timeout ve kullanım tavanı
  yok; zorunlu metrik seti eksik; baseline comparator seal/provenance/environment
  bağlamıyor; `--strict` etkisiz.
- Claim/kapsam: `scripts/run_eval.py`, A9 tier testleri, versioned approval/seal
  şemaları, real-benchmark runbook'u ve yeni immutable receipt. Aynı kaynaklarda
  başka çalışma gözlenmedi. Test-first, yalnız sentetik stub process; provider,
  kullanıcı DB/MinIO veya remote etkisi yok.
- Rollback: bu verifier/schema değişikliklerini revert et; hiçbir DB/object veya
  eski receipt'i silme. A9 geçmeden A11'e başlanmaz.
- İki bağımsız inceleme turunda ilk patch `REJECT` edildi: whitespace reviewer/
  sealer, ters percentile, eksik first-rank/query breakdown, transitif runner,
  self-reported environment, strict'in geçilememesi ve release-candidate/release
  semantiği yeniden üretildi. Concurrent edit penceresindeki bağımsız ara koşu
  70 PASS/8 FAIL; sabit bytes sonraki koşular 78 ve nihai **83/83 PASS**. Ara
  başarısızlık gizlenmedi.
- Versioned approval şeması private manifest dosya+dataset+classification'ı;
  provider/model, tek doğrudan executable runner hash'i, wrapper-computed
  Python/OS/machine/lock environment hash'i, expiry ve süre/call/input/output/USD
  tavanlarına bağlar. Child yalnız açık credential allowlist'i ve bounded metadata
  alır; ambient HOME/Codex/session ortamı aktarılmaz. Provider-side hard limit
  ancak dış provider tarafından uygulanabilir; env+timeout+reported-usage kontrolü
  defense-in-depth'tir ve daha geniş garanti değildir.
- Private manifest records/query-types ile rapor breakdown seti ve toplamı exact
  eşleşir. Retrieval/context/leakage/identifier/answerability/citation/unsupported
  claim/contradiction/prompt injection; first-rank, retry/duplicate/orphan, nested
  ingestion/retrieval/end-to-end ve queue/stage percentile, error distribution ve
  provider call/token/cost alanları typed/bounded/finite kontrol edilir. Percentile
  sırası zorunlu; golden transfer explicit `false` ve mutlak sızıntılar `0` değilse
  sonuç PASS değildir.
- Baseline comparator exact report hash'li insan seal'i ile provider/model/dataset/
  profile/prompt/config/environment provenance eşliği ister. İlk candidate strict
  modda seal uyarısıyla fail olur. Valid baseline yalnız
  `regression_candidate_eligible` üretebilir; wrapper
  `release_gate_eligible=false` bırakır ve bağımsız current-run review'ü uydurmaz.
- Final exact-source doğrulaması: **699 backend PASS, 2 skip**, 1 Starlette/httpx
  uyarısı; **83 tier PASS**; Ruff, MyPy strict 14 dosya, deterministic OpenAPI,
  122-package offline lock ve diff-check PASS. Contract-smoke strict PASS; onaysız
  real CLI probe exit 3 ve provider çağrısı 0. Kullanıcı DB/object verisi değişmedi.
- Bağımsız alt ajan final stabilized source hash'lerinde yerel patch'i `APPROVE`
  etti; A9 closure/owner approval/provider izni/baseline seal olmadığını ayrıca
  kaydetti. Kanıt: `artifacts/evals/2026-09-03-a9-bindings/`.
- A9'u kapatmak için hâlâ gerçek owner-reviewed private pack, exact provider/model,
  gerçek credential/provider-side budget control, insan approval manifesti,
  gerçek koşu ve ilk baseline insan seal'i gerekir. Bunları model/alt ajan üretemez.

### 16.12 A9 no-effect approval preflight çalışma kaydı — 2026-09-03

Başlangıç SHA: `f25dbbf653720ae12ea153514bd34ea0fa6447a7`.
Durum: **yerel operator-preparation PASS; A9 gerçek koşu BLOCKED**.

- Kullanıcının devam talebi üzerine dış insan/provider kapısı atlanmadı. Salt-okunur
  aramada repository, `/Users/mkaracan/Projeler` ve Downloads altında gerçek private
  pack/approval/baseline seal/provider runner bulunmadı; bilinen provider credential
  environment adlarının tamamı absent kaldı. A11'e geçilmedi.
- Bağımsız alt ajan mevcut operator readiness'i `REJECT` etti: standart dosya SHA'sı
  alınabilse de custom runner-bundle ve wrapper-computed environment hash'lerini
  üreten desteklenen public komut, ayrıca provider dispatch yapmadan approval
  drift/expiry kontrolü yoktu.
- Test-first yeniden üretimde yeni beş sözleşme testi **5 FAIL/83 PASS** verdi.
  `approval-preflight` artık owner-reviewed private manifest ile exact manifest,
  dataset/classification, executable runner bundle, environment, tool ve repository
  hash'lerini bounded JSON'a çıkarır. Approval kimliği/zamanı, provider/model,
  credential veya bütçe kararı üretmez; sonuç açıkça `HUMAN_APPROVAL_REQUIRED`,
  `provider_invoked=false` ve `credential_values_read=false` kalır.
- `check-approval` insanın verdiği approval şemasını, expiry ve private manifest/
  dataset/classification/runner/environment drift'ini salt-okunur doğrular. Runner'ı
  çalıştırmaz, credential environment değerlerine bakmaz ve yalnız bounded hash/
  status çıktısı verir. Private pack review timestamp'inin gelecekte olması da
  preflight öncesinde fail-closed reddedilir.
- Hazırlık çıktısı repository dışına resolve olmak ve yeni dosya olmak zorundadır;
  `O_EXCL`/`O_NOFOLLOW`, `0600` ve `fsync` kullanır. Bağımsız kırma testinde ilk
  patch executable biti olmayan `0600` runner'ı kabul etti ve dangling symlink yeni
  çıktı kontrolünü aştı; ikisi de düzeltildi. Stabilized bağımsız yeniden üretimde
  non-executable runner ile dangling symlink fail-closed, symlink target oluşmadı ve
  valid yeni çıktı `0600` oldu.
- Başarısızlıklar saklandı: ilk test-first 5 FAIL; ilk format kapısında bir dosya
  reformat ihtiyacı; local ara koşuda test mock sıralaması nedeniyle 2 FAIL/91 PASS;
  bağımsız concurrent edit penceresinde field-rename uyuşmazlığıyla 87 PASS/4 FAIL.
  İlk manual eksik-girdi CLI probe'u hazırlık hatasını `tier:null` diye etiketledi;
  bounded hata envelope'u `request_type` ve `provider_invoked=false` taşıyacak
  şekilde düzeltildi. Sabit kaynaklarda bütün bu bulguların regresyonları
  **94/94 PASS** oldu.
- Final exact-source doğrulaması: izole PostgreSQL/Redis/MinIO ile **710 backend
  PASS, 2 skip**, 1 Starlette/httpx uyarısı; **94 tier PASS**. Ruff format/check,
  MyPy strict 14 dosya, deterministic OpenAPI, 122-package offline lock ve
  diff-check PASS. Kullanıcı DB/MinIO/object verisi veya gerçek provider
  kullanılmadı; remote push/PR/merge/release yapılmadı.
- Bağımsız alt ajan stabilized kaynaklarda yerel operator-preparation patch'ini
  `APPROVE` etti ve somut kalan yerel admission açığı bulmadı. Bu karar owner
  approval/provider izni değildir ve A9'u kapatmaz.
- Public-safe kanıt: `artifacts/evals/2026-09-03-a9-preflight/`.
- Açık kapı değişmedi: gerçek owner-reviewed private pack, exact provider/model ve
  provider-side budget kararı, insan approval manifesti, gerçek provider koşusu,
  bağımsız request/non-transfer kanıtı ve ilk baseline insan seal'i gerekir. Bunlar
  gelmeden A11 başlatılmaz.

### 16.13 A9 dataset provenance ve public review guard çalışma kaydı — 2026-09-04

Başlangıç SHA: `e5116c441f54e79207b94bc95611dc6c4612af52`.
Durum: **yerel dataset-provenance doğrulaması PASS; public owner review ve A9 gerçek
koşu BLOCKED**.

- Salt-okunur yeniden üretimde `contract-smoke` gerçekten backend
  `tests/evals/datasets/golden.jsonl` dosyasını çalıştırdığı halde raporun
  yürütülmeyen public sentetik corpus SHA'sını fallback olarak yazdığı kanıtlandı.
  `offline-e2e` de inline pytest fixture'ları çalıştırırken aynı ilgisiz public SHA'yı
  taşıyordu. Önceki 94/94 test bu yanlış provenance iddiasını yakalamıyordu.
- Test-first eklenen kontroller ilk koşuda **12 FAIL/94 PASS** verdi. Contract tier
  artık exact çalıştırılan golden dosyanın SHA'sını ve 66 kaydını;
  offline tier `dataset_sha256=null` ile özyinelemeli keşfedilen testler, sabit
  production-pipeline hedefleri, ilgili `conftest.py` zinciri ve `pyproject.toml`
  üzerinden scoped source-bundle hash'ini raporluyor. Yalıtılmış gerçek offline
  koşu **42/42 PASS**, sekiz kaynaklı bundle SHA
  `fa6eb25e9147074e35cb41a71d03ee3e5ffd89e90b464e4db47c5c09c512c6bf`.
- Public `public-synthetic-v2` manifesti versioned JSON Schema, exact corpus SHA,
  semver, kayıt sayısı, satır sürümleri ve split setiyle fail-closed doğrulanır.
  CI bu salt-okunur kontrolü koşar. Mevcut gerçek durum `review_status=pending`,
  `review_complete=false`, `release_gate_eligible=false`, `provider_invoked=false`.
- İlk bağımsız inceleme yerel paketi `REJECT` etti: rastgele reviewer/tarih/64-hex
  receipt alanları yanlış biçimde `review_complete=true` olabiliyor; offline bundle
  pytest config/conftest ve gelecekteki nested testleri eksik bırakabiliyordu. Bu
  aşırı iddialar test-first **2 FAIL/112 PASS** ile tekrar üretildi. Düzeltmeden sonra
  schema-valid self-asserted approval yalnız
  `manifest-declared-approved-unverified` kalır; receipt/otorite doğrulanmadığı için
  `approval_evidence_verified=false` ve `review_complete=false` değişmez. UTC dışı
  offset ve gelecek tarih reddedilir. Nested test ile nested `conftest.py` keşfi ve
  byte-sensitive bundle hash'i regresyonla korunur.
- Bağımsız ikinci inceleme stabilized exact kaynakları **APPROVE** etti: **115/115
  tier PASS**, Ruff/format/diff PASS; self-asserted approval ve nested discovery
  kırma denemeleri fail-closed. Bu karar owner review, provider yetkisi, gerçek
  benchmark, baseline seal veya A9 kapanışı değildir.
- Final exact-source doğrulaması: yalıtılmış PostgreSQL/Redis/MinIO ile **731 backend
  PASS, 2 skip**, bilinen 1 Starlette/httpx uyarısı; **115 tier PASS**; contract
  66-record exact hash PASS; offline **42 PASS**; public manifest integrity PASS.
  Ruff format/check, MyPy strict 14 dosya, deterministic OpenAPI, 122-package
  offline lock ve diff-check PASS. Kullanıcı DB/MinIO/object verisi ve gerçek
  provider kullanılmadı; remote push/PR/merge/release yapılmadı.
- Public-safe kanıt: `artifacts/evals/2026-09-04-a9-dataset-provenance/`.
- Açık kapı değişmedi: public corpus için gerçek owner review/bağlı bağımsız receipt;
  A9 için owner-reviewed private pack, exact provider/model/provider-side bütçe,
  insan approval manifesti, gerçek provider koşusu, bağımsız request/non-transfer
  kanıtı ve ilk baseline insan seal'i gerekir. Bunlar gelmeden A11 başlatılmaz.

### 16.14 A9 public review receipt binding çalışma kaydı — 2026-09-04

Başlangıç SHA: `e27c1312814301bb4e321837f1c5554e94c875e4`.
Durum: **yerel external-receipt binding hazırlığı PASS; public owner review ve A9
gerçek koşu BLOCKED**.

- Bağımsız kabul denetimi §16.3'teki açık review/version/hash maddesi için son yerel
  hazırlık açığını buldu: public manifest receipt SHA istiyor, fakat CLI receipt
  dosyasını tüketip exact dataset/manifest bağını doğrulayamıyordu. Desteklenmeyen
  `--public-review-receipt` gerçek CLI probe'u argparse exit `2` ile bunu yeniden
  üretti; yeni test-first paket **12 FAIL/115 PASS** verdi.
- Versioned `public-dataset-review-receipt.schema.json`; karar, review kapsamı,
  reviewer-reference hash'i, UTC zaman, dataset SHA/version, kayıt ve split setini
  additional-field kapalı sözleşmeyle tanımlar. Otomasyon receipt üretmez veya
  onaylamaz. Receipt gerektiğinde repo dışında ilgili veri politikasında tutulur.
- No-effect status verifier receipt'in exact byte SHA'sını manifestteki
  `review_receipt_sha256` ile; dataset/version/count/splits/reviewer/time alanlarını
  gerçek manifest ve corpusla eşler. Hash veya herhangi bir bağ sapması, malformed
  JSON, ek alan, pending manifest, UTC dışı/gelecek zaman ve public-status dışındaki
  CLI kullanımı provider dispatch'ten önce fail-closed reddedilir.
- Doğru sentetik binding yalnız `receipt_binding_verified=true` üretir;
  `review_authority_verified=false`, `approval_evidence_verified=false`,
  `review_complete=false`, `release_gate_eligible=false` ve
  `provider_invoked=false` kalır. Böylece dosya bütünlüğü insan/owner otoritesi gibi
  gösterilmez. Approved manifest receipt olmadan da yalnız
  `manifest-declared-approved-unverified` olabilir.
- Final exact-source doğrulaması: **127/127 tier PASS**; yalıtılmış
  PostgreSQL/Redis/MinIO ile **743 backend PASS, 2 skip**, bilinen 1
  Starlette/httpx uyarısı. Ruff format/check, MyPy strict 14 dosya, deterministic
  OpenAPI, 122-package offline lock, diff-check ve pending/no-receipt CLI probe'u
  PASS. Gerçek provider/credential veya kullanıcı DB/object verisi kullanılmadı.
- Bağımsız alt ajan exact stabilized kaynakları **APPROVE** etti; receipt hash ve
  bütün exact bağları kırdı, malformed/additional/pending/wrong-mode yollarının
  fail-closed kaldığını doğruladı. Bu karar owner review, insan otoritesi, provider
  izni, gerçek benchmark, baseline seal veya A9 kapanışı değildir.
- Public-safe kanıt:
  `artifacts/evals/2026-09-04-a9-public-review-binding/`.
- Açık kapı değişmedi: public corpusun gerçek owner review'ü ve onun harici otorite
  kararı; A9 için owner-reviewed private pack, provider/model/provider-side bütçe,
  insan approval manifesti, gerçek provider koşusu, bağımsız request/non-transfer
  kanıtı ve ilk baseline insan seal'i gerekir. Bunlar gelmeden A11 başlatılmaz.

### 16.15 A9 yerel BGE runtime admission çalışma kaydı — 2026-09-04

Başlangıç SHA: `fb9fabef2480d3a2da5d2cf1277d01c0222d9888`.
Durum: **exact yerel BGE embedding runtime/admission PASS; generation ve A9 gerçek
benchmark kapısı AÇIK**.

- Kullanıcının MacBook üzerinde yerel BGE kullanma kararıyla cache'teki
  `BAAI/bge-m3` snapshot'ı bulundu. Exact revision
  `5617a9f61b028005a4858fdac845db406aefb181`; 11 resolved dosya,
  2.293.331.623 byte ve bundle SHA
  `d87c47601ade6251c7e0c236d4b863b797bfd280f94ac09be9cc7fbeede74668`.
- Ayrı runtime smoke'u gerçek `[2,1024]` finite ve normalize vektör üretti.
  Runtime kalıcı olarak opt-in `local-eval` extra'sına bağlandı:
  `sentence-transformers==6.0.1`, `transformers==5.15.1` ve `torch==2.14.0`;
  `uv.lock` 163 paket çözer. Extra normal `--all-groups` backend/RAG CI
  kurulumuna girmez; security CI onu açıkça kurup audit/SBOM/license kapsamına
  alır. İlk `transformers==4.57.6` adayında `pip-audit` 6 bulguyla beklendiği
  gibi FAIL oldu; yükseltilen final lock'ta bilinen zafiyet sayısı **0**.
- Test-first ilk koşu eksik verifier nedeniyle **7 FAIL** verdi. Snapshot
  identity/revision/bundle doğrulaması, model-root dışına symlink kaçışı,
  byte-sensitive hash, 1024 dimension/finite/norm kapısı ve local/remote ayrımı
  eklendi. İlk bağımsız kırma TOCTOU ve output symlink overwrite açıklarını;
  ikinci kırma aynı-bayt symlink retarget yarışını buldu. Düzeltmeden sonra
  pre/post full validation, exact bundle eşitliği, `O_EXCL`/`O_NOFOLLOW`, mode
  `0600` ve `fsync` regresyonlarla korunuyor.
- Rapor `repository_revision`, verifier SHA, dependency-lock SHA ve model bundle
  SHA taşır. Hugging Face/Transformers offline guard ile
  `local_files_only=True` kullanılır; OS seviyesinde ağ sandbox'ı kanıtlanmadığı
  dürüstçe `network_isolation_verified=false` olarak raporlanır.
- Exact snapshot locked runtime ile CPU ve Apple MPS üzerinde PASS oldu. Küçük
  smoke'ta CPU encode 3.038 saniye, MPS encode 9.012 saniye verdi; bu yalnız
  smoke gözlemidir, genel performans iddiası değildir.
- Odaklı final doğrulama **140/140 PASS**, Ruff/format, offline lock ve diff-check
  PASS. İlk full-suite koşusu yanlışlıkla eski izole `cv3_00000003` DB'sine
  bağlandığı için **741 PASS/21 FAIL/1 skip** ile schema drift'i doğru yakaladı.
  Mevcut DB migrate/reset edilmedi; yeni boş `cv3_a9_local_bge_verify` DB'si
  oluşturulup `cv3_00000006` head'e getirildi. Aynı PostgreSQL/Redis/MinIO test
  altyapısında final exact-source backend **756 PASS, 2 skip**, bir bilinen
  Starlette/httpx uyarısıdır. Kullanıcı DB/MinIO/object verisi silinmedi veya
  yeniden kurulmadı.
- Bağımsız alt ajan stabilized local verifier kaynaklarını **APPROVE** etti;
  exact hashleri eşledi ve aynı-byte symlink retarget saldırısının fail-closed
  olduğunu doğruladı. Bu karar insan/provider onayı veya A9 kapanışı değildir.
- Public-safe kanıt: `artifacts/evals/2026-09-04-a9-local-bge/`.
- Açık kapı: BGE-M3 yalnız embedding modelidir. §16.2'nin gerçek embedding **ve**
  generation şartı için approval/report/baseline sözleşmesindeki tekil
  `provider/model` kimliği iki ayrı provider/model kimliğine ayrılmalı; sonra
  owner-reviewed private pack, exact local generation modeli, gerçek production
  pipeline koşusu, bağımsız request/non-transfer kanıtı ve ilk insan baseline
  seal'i gerekir. Bunlar tamamlanmadan A11 başlatılmaz.

### 16.16 A9 embedding/generation identity split kaydı — 2026-09-05

Başlangıç SHA: `da15ce7d2bbc2093d3e18b7741fc9268378461ff`.
Durum: **versioned split approval/report/baseline contract PASS; generation ve A9
gerçek benchmark kapısı AÇIK**.

- Test-first fixture tekil `provider/model` yerine ayrı `embedding_provider`,
  `embedding_model`, `generation_provider` ve `generation_model` kimliklerini
  istediğinde ilk koşu **115 PASS/20 FAIL** verdi. Runtime yalnız v2 approval ve
  seal şemalarına bağlandı; v1 veya karma v1/v2 approval runner dispatch'ten önce
  fail-closed reddedilir.
- Provider report executable sözleşmesi `schema_version=2.0` ve dört identity
  alanını zorunlu tutar. Child process'e yalnız dört ayrı `CV_EVAL_*` identity
  değişkeni verilir; legacy `CV_EVAL_PROVIDER`/`CV_EVAL_MODEL` verilmez.
  Approval/report equality, seal/baseline ve current/baseline provenance bağları
  dört identity alanının tamamını kapsar. Preflight değerleri üretmez; yalnız
  yetkili insanın ayrı ayrı karar vermesi gereken alan adlarını listeler.
- Bağımsız kırma ilk stabilize adayı REJECT etti: v2 report veya baseline'a legacy
  alan eklenmesi, eksik zorunlu metriğe sahip sealed baseline ve v1 current
  comparator adayı kabul edilebiliyordu. Dört bulgu **4 FAIL** ile yeniden
  üretildi. `_benchmark_report` legacy alanları reddeder; sealed baseline ve
  current candidate tam v2 report sözleşmesinden geçmeden karşılaştırılmaz.
- Final tier sözleşme testi **158/158 PASS**; Ruff check/format, strict MyPy
  ratchet (14 source), deterministic OpenAPI, offline lock (163 paket) ve
  diff-check PASS. İzole PostgreSQL `cv3_00000006` head, Redis ve MinIO ile tam
  backend **787 PASS, 2 skip**, bir bilinen Starlette/httpx uyarısıdır. İlk tam
  koşudaki tek hata Redis test endpoint'inin parola bilgisini URL'de taşımamasıydı;
  kod değişikliği yapılmadan doğru izole endpoint ile yeniden koşulup geçti.
- Tarihsel generic v1 schema yolları önceki kanıt manifestlerini bozmamak için
  byte-identical korundu. Ayrı `-v1` arşiv kopyaları aynı SHA'ları taşır; runtime
  yalnız `-v2` dosyalarını kullanır. Approval v1 SHA
  `a071586bc85dcbede58185d7ba7a4b843df4a823aefd167a5d4efab5a4d2f6f6`, seal v1
  SHA `6a18222393fe66a7d62bbcaf974357aa2e60172b41c94d1d04f35f47e565b255`.
- Bağımsız alt ajan final source'u **APPROVE** etti. Bu karar insan approval'ı,
  provider çalıştırma izni, private-pack review, baseline seal veya A9 kapanışı
  değildir. Kullanıcı DB/object verisi ve model ağırlıkları değiştirilmedi;
  remote provider/push/PR/merge/release yapılmadı.
- Public-safe kanıt: `artifacts/evals/2026-09-05-a9-split-provider-contract/`.
- Açık kapı: exact yerel generation runtime, owner-reviewed private pack, gerçek
  production-path benchmark, bağımsız request/non-transfer kanıtı ve ilk insan
  baseline seal'i gerekir. Bunlar tamamlanmadan A11 başlatılmaz.

### 16.17 A9 yerel generation runtime admission kaydı — 2026-09-05

Başlangıç SHA: `54d95d4cd43f1ff9eb9b06063a67ab757c8e4bb4`.
Durum: **exact yerel generation runtime/admission PASS; gerçek kalite benchmarkı
ve A9 kapısı AÇIK**.

- MacBook için ilk gerçek aday `Qwen/Qwen2.5-0.5B-Instruct`, revision
  `7ae557604adf67be50417f59c2c2f167def9a775`, 10 dosya ve 999.604.126
  byte olarak sınandı. Mevcut kullanıcı verisini koruma sorusunda silmeyi önerdi
  ve no-answer sorusunda desteklenmeyen retention ayrıntısı üretti; bu aday
  **REJECT** edildi. İlk 1.5B doğal-dil semantik smoke'u da aynı iki guardı
  geçemedi. Başarısızlıklar kalite kanıtından çıkarılmadı.
- Kabul edilen runtime kimliği `Qwen/Qwen2.5-1.5B-Instruct`, exact revision
  `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`; 10 resolved dosya,
  3.098.973.447 byte ve symlink identity dahil bundle SHA
  `5a6a6259762fed70e38ae346763b105a7b05aa981e5ab078c8e5b033f87f0c87`.
  Ağırlıklar yalnız yerel Hugging Face cache'inde kaldı, Git'e eklenmedi.
- Final MPS koşusu gerçek model yükleme ve üç üretim yaptı: exact grounded token,
  exact `NO_CONTEXT` ve deterministic tekrar PASS; 200 prompt token, 15 generated
  token. Ham prompt/çıktı tutulmadı, yalnız case/output SHA'ları kaydedildi.
  Bu dar bir controlled prompt-adherence/runtime smoke'udur; genel grounding,
  Türkçe kalite, citation veya release başarısı değildir.
- Verifier exact owner/name/revision, Qwen2 config şekli, gerekli dosyalar,
  cache-root symlink confinement, symlink-target+byte bundle kimliği ve inference
  öncesi/sonrası tam eşitlik uygular. Snapshot zarfı 64 dosya/4 GB; inference
  zarfı 4.096 prompt/72 output token ve en fazla 600 saniye dış süreçtir.
  Direct worker CLI geçişi parent pipe capability olmadan fail-closed olur; bu
  aynı OS kullanıcısına karşı kimlik doğrulama iddiası değildir.
- İlk bağımsız inceleme rapor-envelope enjeksiyonu, permissive grounded predicate,
  malformed config ve kaynak/pin açıklarını; ikinci inceleme numeric/hash/runtime
  üst bağları ile doğrudan worker timeout bypassını; üçüncü inceleme 600 saniyelik
  sınırın 3.600'e yükseltilebilmesini `REJECT` etti. Bulgular test-first yeniden
  üretildi ve regresyonlarla kapatıldı. Final odaklı test **42/42 PASS** oldu.
- Bağımsız alt ajan stabilize exact kaynak ve artifact hashlerini yeniden eşledi,
  timeout bypasslarını providersız kırma testleriyle reddetti ve yalnız bu dar
  yerel runtime paketini **APPROVE** etti. Bu karar owner/provider onayı, private
  pack review'u, insan baseline seal'i veya A9 kapanışı değildir.
- `local-eval` extra doğrudan `sentence-transformers==6.0.1`, `torch==2.14.0`
  ve `transformers==5.15.1` pinlerini taşır; offline lock 163 pakettir. Exact
  output hashleri, runtime sürümleri, dependency lock, verifier, prompt-case ve
  repository revision rapora bağlanır. OS ağ izolasyonu kanıtlanmadığı için
  `network_isolation_verified=false` kalır.
- Cached path/revision/bundle yerel byte identity sağlar; yayıncı kökeni veya
  Apache lisansını kriptografik olarak kanıtladığı iddia edilmez. Kullanıcı DB,
  MinIO, embedding index veya proje dosyası değiştirilmedi; remote provider,
  push, PR, merge veya release yapılmadı.
- Public-safe kanıt: `artifacts/evals/2026-09-05-a9-local-generation/`.
- Açık kapı: owner-reviewed private pack, exact BGE+generation ile production
  ingestion/retrieval/answer yolunu kullanan gerçek benchmark, bağımsız
  request/non-transfer kanıtı ve ilk insan baseline seal'i gerekir. Bu yerel
  runtime admission tek başına §16.8'i veya A9'u kapatmaz; A11 başlatılmaz.

### 16.18 A9 local production-runner admission hardening kaydı — 2026-09-05

Başlangıç SHA: `4d0354ab4052071d9092ebaa4c931fe58642b148`.
Durum: **yerel runner input/source/runtime admission PASS; production benchmark
ve A9 kapısı AÇIK**.

- `scripts/run_eval.py` tool contract'ı `1.7.0` oldu. Doğrulanmış private-pack
  dataset/manifest SHA'ları, classification, record/query-type kapsamı ve exact
  runner bundle SHA'sı child'a yalnız reserved `CV_EVAL_*` alanlarıyla aktarılır.
  Onay manifesti bu adları credential olarak talep edemez.
- Opsiyonel `<runner>.sources.json` sidecar'ı repo-relative `python_roots` ve
  fixed `files` kapanımını runner bundle'a bağlar. Sidecar/üyeler için kaçış,
  duplicate, limit, eksik dosya ve symlink kontrolleri fail-closed'dur; nested
  directory symlink'leri ile `__pycache__`, `.pyc/.pyo` ve import edilebilir
  native artifact'lar kabul edilmez. Runner ve private manifest koşu sonrasında
  yeniden hashlenir.
- Credential kanalı `PATH`, locale, Python, dynamic-loader, proxy/certificate ve
  diğer execution-control ortam adlarını dispatch öncesi reddeder. Child `PATH`,
  mevcut locked interpreter dizini + `os.defpath` olarak deterministik kurulur;
  `PYTHONDONTWRITEBYTECODE=1` ve `PYTHONNOUSERSITE=1` sabittir.
- Environment identity; lexical/resolved parent interpreter yolu, binary SHA,
  `sys.prefix`, lock SHA, exact runner PATH ve bu PATH'in seçtiği `python3`/varsa
  `python` lexical+resolved yol/binary SHA kimliğini bağlar. PATH seçimi her
  kontrolde `sys.executable` ile `samefile` olmak zorundadır. Environment koşu
  öncesi ve sonrasında tekrar doğrulanır.
- Test-first kırmızı kanıtlar nested directory symlink görünmezliğini,
  execution-control credential override'ını, unchecked bytecode/native import
  artifact'larını, ambient PATH drift'ini ve `python3` symlink retarget'ini
  yeniden üretti. Tüm bulgular ayrı regresyonlarla kapatıldı.
- Final doğrulama: A9 tier contract **188/188 PASS**; doğru izole PostgreSQL,
  Redis ve MinIO ortamıyla tam backend **859 PASS, 2 SKIP, 1 bilinen uyarı**;
  Ruff, strict MyPy ratchet (14 kaynak), deterministic OpenAPI, offline lock
  (163 paket), pip-audit ve `git diff --check` PASS.
- Bağımsız alt ajan birkaç turda nested symlink, execution-control env, bytecode,
  ambient PATH ve runner-selected interpreter açıklarını ayrı ayrı `REJECT`
  etti. Stabilize güncel kaynak hashleri üzerinde yalnız bu bounded local
  admission-hardening paketini **APPROVE** etti. Bu karar insan/owner onayı,
  private-pack review'u, gerçek benchmark veya baseline seal değildir.
- Kullanıcı DB/MinIO/embedding index/proje dosyaları ve model cache'i
  değiştirilmedi. Provider çağrısı, migration/reset, push, PR, merge veya release
  yapılmadı. Public-safe kanıt:
  `artifacts/evals/2026-09-05-a9-runner-admission/`.
- Açık kapı değişmedi: gerçek owner-reviewed private pack; exact yerel BGE ve
  generation ile production ingestion/retrieval/answer benchmarkı; bağımsız
  request/non-transfer kanıtı ve ilk insan baseline seal'i gerekir. Bunlar
  tamamlanmadan A9 kapatılmaz ve A11 başlatılmaz.

---

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

- [x] Route/page bileşenleri orchestration dışında iş mantığı taşımayacak.
- [x] Server state için tek veri erişim katmanı; ad-hoc `fetch` tekrarları olmayacak.
- [x] Form/schema validation backend contract'ıyla uyumlu olacak.
- [x] Loading/error/empty/partial/permission-denied durumları ayrı gösterilecek. (Yerel reducer ve tarayıcı hata matrisi; §17.10.)
- [x] Development debug görünümü production bundle'da yetkisiz erişilebilir olmayacak.

### 17.3 Typed API sözleşmesi

- [ ] Backend OpenAPI schema CI'da deterministic üretilir.
- [x] TypeScript client/schema otomatik oluşturulur.
- [x] Generated client elle düzenlenmez.
- [ ] Backend schema değişip client güncellenmezse CI fail eder.
- [x] Problem detail/error code'lar typed UI mesajlarına eşlenir.
- [x] API base URL ve auth config build-time hardcode değil deployment config'tir.

### 17.4 Proje, kaynak ve ingestion deneyimi

- [x] Kullanıcı workspace/project seçmeden upload/chat/retrieval yapamaz.
- [x] Sessiz default proje yoktur.
- [x] Kaynak ekleme ekranı source type, data classification ve remote-processing policy kararını açık gösterir.
- [x] Raw “chunk size (karakter)” alanı kaldırılır.
- [x] Gelişmiş kullanıcıya yalnız kayıtlı immutable chunker/embedding profile seçimi sunulabilir. (Keyfî profil seçimi sunulmuyor.)
- [x] Job durumu gerçek backend event'inden gelir; sahte progress yoktur.
- [x] SSE/WebSocket veya bounded polling; reconnect ve terminal state güvenli.
- [x] Retry aynı idempotency key/iş kaydı üzerinden yapılır; duplicate upload yaratmaz.
- [x] Quarantine/policy-rejected source kullanıcıya anlaşılır ve secret göstermeyen nedenle sunulur.

### 17.5 Chat ve citation deneyimi

- [x] Conversation project scope'u görünür ve değiştirilemez bağlam olarak gösterilir.
- [x] No-answer, provider error, permission denied ve policy refusal farklı UI durumlarıdır.
- [x] Citation panel yalnız kullanılan source'ları gösterir.
- [x] Belge/version/file/page/line/bbox/symbol locator ve güvenli excerpt sunulur.
- [x] Eski citation'ın dayandığı version ile güncel version farklıysa kullanıcıya gösterilir.
- [x] Retrieval score son kullanıcı için yanıltıcı “doğruluk yüzdesi” şeklinde gösterilmez.
- [x] Admin diagnostics; stage ranks, fallback reason ve timing'i yalnız yetkili role gösterir.
- [x] Tam prompt/context veya secret içeriği UI debug'ında bulunmaz.

### 17.6 Erişilebilirlik ve güvenlik

- [x] Klavye erişimi, focus yönetimi, semantik etiket, ekran okuyucu ve kontrast testleri. (Otomatik WCAG + klavye smoke; insan ekran okuyucu incelemesi iddia edilmez.)
- [x] File upload tür/boyut/policy bilgisi erişilebilir şekilde sunulur.
- [x] XSS için model cevabı/source HTML'i güvenli render edilir; raw HTML varsayılan yasak.
- [x] Auth token localStorage'da düz metin kalıcı tutulmaz; seçilen auth mimarisine uygun güvenli yöntem kullanılır.
- [x] CSRF/session politikası auth moduna göre uygulanır.
- [x] Hassas error detail ve stack trace gösterilmez.

### 17.7 Test kapısı

- [x] Unit: state reducers/hooks/schema mapping. (Schema/API unit; hook durum geçişleri tarayıcı senaryolarında.)
- [x] Component: project selection, upload/job, no-answer, citation, permission states.
- [x] Contract: generated client ve backend fixture.
- [x] E2E: login/local auth, project create/select, upload, completed job, chat, used citation, delete/retention.
- [x] E2E negatif: cross-project URL, expired key, failed/quarantined job, provider outage.
- [x] Accessibility otomatik tarama + kritik akışta keyboard smoke.
- [x] Production build ve bundle secret/config taraması.

### 17.8 Kabul kriterleri

- [x] `page.tsx` monoliti işlevsel feature'lara ayrılmış.
- [x] Backend API tipleri elle kopyalanmıyor; generated contract kullanılıyor.
- [x] Project scope ve auth olmadan veri işlemi yapılamıyor.
- [x] UI gerçek job/citation/no-answer semantiğini doğru gösteriyor.
- [ ] Unit/component/e2e/build/a11y kapıları CI'da çalışıyor.

### 17.9 Aşama 10 yerel uygulama kaydı — 2026-09-02

Implementation SHA: `4bf56429304b33fa5b75a2eea05a6c9e918f0d2e`.

Durum: **yerel ürün/contract doğrulaması geçti; aşama/global kapanış değildir**.
A9 gerçek provider/private-pack/owner-review kapısı açık kalır. A11–A13 atlanmadı,
production-ready veya release iddiası yapılmaz. Remote CI çalışması/push yapılmadı.

- Monolit route ve chat; auth/projects/ingestion/conversations/citations/retrieval
  feature'larına ayrıldı. Generated OpenAPI → TS ve runtime schema validation eklendi.
- API base/auth deployment sırasında gelir; memory-only anahtar, açık workspace ve
  project seçimi, aynı-project conversation id ve aynı-key upload retry uygulandı.
- Sahte thinking/code ticker kaldırıldı. Ölçülmemiş progress null; yalnız kanıtlı
  completed/indexed son durumu 100 olabilir. Terminal job hataları gizlenmez.
- Yeni gerçek HTTP/browser koşusu A8'in önceki testlerinin kaçırdığı commit eksikliğini
  yeniden üretti: ekranda citation olmasına rağmen fresh DB connection 0 message/claim/
  citation gördü. `query_chat` artık answer transaction'ını commit eder ve kalıcılık
  bayrağı API'den kapatılamaz. Bu bulgu önceki A8 genel runtime kalıcılığı iddiasını
  daraltır; düzeltme ve yeni bağımsız bağlantı kanıtı A10 receipt'indedir.
- Debug endpoint log signature hatası yeniden üretildi; raw query/context dönen yol
  allowlisted rank/timing/fallback view ile değiştirildi. Member 403 ve production
  diagnostics görünmezliği test edildi. Mypy strict kapısındaki üç annotation hatası
  da yeniden üretilip tip kapsamı küçültülmeden giderildi.
- Backend: **620 passed, 2 skipped**, 1 Starlette/httpx deprecation warning.
  Frontend: **13 unit/component + 11 browser-contract + 1 connected browser PASS**;
  lint/typecheck/build/bundle-pattern scan/audit, OpenAPI drift ve MyPy strict PASS.
- Connected fixture: gerçek HTTP/auth/PG/MinIO/parser/worker-core/retrieval; model
  deterministik yerel fixture, dispatch synchronous. Gerçek sağlayıcı/queue delivery
  kalite kanıtı değildir. Fresh connection: **2 message, 1 claim, 1 encrypted citation,
  1 soft-deleted document, 3 retained encrypted object** doğrulandı.
- A10 için yeni, boş `cv3_a10_browser` DB ve `cv3-a10-browser` bucket kullanıldı;
  kullanıcı verisi veya mevcut isolated baseline silinmedi. Test kayıtları korundu.
- Remote CI YAML'ları schema/client/browser/connected durability gate'lerini içerir
  ve yerelde parse edildi; remote koşu doğrulanmadığı için ilgili kutular açık kaldı.
- Detay/komut/kanıt: ADR-012, `docs/runbooks/web-product-contract.md`,
  `artifacts/product/2026-09-02-a10/PRODUCT_RECEIPT.json`.

---

### 17.10 A10 kısmi hata ve scope invalidation ek doğrulaması — 2026-09-02

Başlangıç SHA: `02a7227f6cbed2db5b1d5887012031dbc67977c1`.
Durum: **yerel hata matrisi PASS; aşama/global kapanış değildir**.

- Geçici job-list hatasının doğrulanmış snapshot'ı kaybedip boş liste gösterdiği,
  permission hatasında yanıltıcı empty-state üretildiği iki tarayıcı testiyle
  yeniden üretildi. JSON `null` gövdeli HTTP 403'ün `TypeError` olarak kaybolduğu
  API unit testi de düzeltmeden önce başarısız oldu.
- Scope/request kimlikli document reducer, abort ve eski response reddi eklendi.
  Ağ/429/5xx hatasında yalnız son doğrulanmış snapshot uyarıyla korunur; silme
  düğmesi kapanır. İlk yükleme hatası empty veya doğrulanmış snapshot sayılmaz.
- 401/403/404 kaynak/citation snapshot'ını temizler, upload/chat'i kapatır;
  yeniden erişim yalnız başarılı revalidation sonrası açılır. Proje listesi
  permission hatası seçili scope'u da kaldırır. Başarılı delete sonrasındaki
  refresh hatası silinen satırı geri getirmez.
- **26 unit/component + 20 browser-contract + 1 connected browser PASS**.
  Lint/typecheck/generated client/build/15-chunk bounded bundle scan PASS.
  Partial-state ekranı ve mobil workspace otomatik WCAG taramasından geçti.
  `frontend-design` mevcut palette/typography'yi koruyup semantik uyarıların
  ayrıştırılmasına rehberlik etti; ayrı görsel tasarım değişikliği yapılmadı.
- Connected koşu mevcut isolated DB/bucket üzerinde yeni sentetik kayıtlarla
  tekrarlandı. Fresh connection: **2 message, 1 claim, 1 encrypted citation,
  1 soft-deleted source, 3 retained encrypted object**. İlk verifier komutu eksik
  dummy provider config nedeniyle durdu; config açık test değeriyle tamamlanınca
  salt-okunur kontrol geçti. Gerçek provider çağrısı yapılmadı.
- Bu tur backend full suite yeniden koşulmadı; önceki 620 PASS/2 skip yalnız
  §17.9 SHA'sının kanıtıdır. Uygulama DB şeması/backend kodu değiştirilmedi.
- Komutlar, source manifest ve redakte edilmiş raporlar:
  `artifacts/product/2026-09-02-a10-partial-state/`.
  Eski receipt'ler değiştirilmedi. Remote CI, A9 gerçek baseline/owner onayı,
  A11–A13 ve bağımsız kapanış doğrulaması açık kalır; push yapılmadı.

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
