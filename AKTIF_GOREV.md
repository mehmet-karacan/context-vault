# Context Vault — RAG Güvenilirlik, Arama Kalitesi ve Ürünleştirme Aktif Görevi

> **Dosya adı sabittir:** `AKTIF_GOREV.md`  
> **Durum:** **ONAYLI — AŞAMA 0 İLERLEMEDE (rewrite, sahiplik policy ve .claude temizliği tamamlandı); AŞAMA 1 runtime baseline ve CI başlıyor**  
> **Onay sahibi:** Mehmet KARACAN  
> **Onay tarihi:** 2026-08-31 — Europe/Istanbul  
> **Repository:** `https://github.com/mehmet-karacan/context-vault`  
> **Hedef branch:** `main`  
> **İncelenen başlangıç commit’i:** `38c6ac5697431475351e577b2794399b349fb210`  
> **Aşama 0 rewrite-sonrası main head:** `6243b78963471d367b96df0b2a46f416096659ac`  
> **İncelenen eski aktif görev blob SHA’sı:** `1e39813d7afd0e57600e6959eeeb30d42227d4dd`  
> **Kanonik uygulama dizini:** `document-rag-platform/`  
> **Kanonik görev dosyası:** repository kökü `/AKTIF_GOREV.md`  
> **Sahiplik ilkesi:** Commit author ve committer yalnız Mehmet KARACAN; yapay zekâ veya araç adları `Co-Authored-By` trailer’ı olarak kullanılmayacak.  
> **Yürütme ilkesi:** Önce Git geçmişindeki istenmeyen ortak-yazar kaydı temizlenecek ve doğrulanacak; bundan önce hiçbir ürün geliştirmesi, refactor veya feature commit’i yapılmayacak.

---

## 0. Bu Görevin Bağlayıcı Sırası

Bu görev üç kapılıdır ve sıra değiştirilemez:

1. **Aşama 0 — Git sahiplik temizliği**
   - Git geçmişindeki `Co-Authored-By: Claude ...` trailer’ları mesajlardan çıkarılır.
   - Dosya ağaçları, commit sayısı ve gerçek Mehmet author/committer bilgileri korunur.
   - `main` güvenli `force-with-lease` ile güncellenir.
   - Contributor görünümü ve tüm ref’ler doğrulanır.
   - Araç-özel `.claude/` içerikleri ayrıca envanterlenir; yararlı içerik lisans/provenance kontrolünden sonra araçtan bağımsız yapıya taşınmeden silinmez.

2. **Aşama 1–7 — RAG doğruluk ve güvenilirlik çekirdeği**
   - Veri izolasyonu, aktif sürüm, embedding profili, retrieval, context, citation, ingestion ve gerçek eval hataları düzeltilir.
   - Her aşama gerçek entegrasyon testi ve geri dönüş planı ile tamamlanır.

3. **Aşama 8–11 — Ürünleştirme ve genişleme**
   - Gözlemlenebilirlik, UI, geri bildirim, araçtan bağımsız skill’ler ve yalnız ölçümle gerekli olduğu kanıtlanan deneysel retrieval seçenekleri eklenir.

**Aşama 0 tamamlanmadan Aşama 1 veya sonrasına ait herhangi bir repository mutasyonu yasaktır.**

---

# 1. Görevin Amacı

Context Vault’u yalnız “dense + lexical + identifier araması var” seviyesinden çıkarıp aşağıdaki nitelikleri gerçek çalışma yolunda sağlayan, ölçülebilir ve genişletilebilir bir bilgi platformuna dönüştür:

- Her sorgu zorunlu proje/erişim kapsamı altında çalışır.
- Yalnız belgenin aktif ve hazır sürümü aranır; eski veya yarım sürüm sonuçlara sızmaz.
- Sorgu embedding’i yalnız doğru ve aktif embedding profiliyle karşılaştırılır.
- Dense, lexical ve identifier adayları kayıpsız bir ortak sonuç sözleşmesine taşınır.
- Fusion, reranking, deduplication ve context genişletme gerçekten LLM’e giden kanıt paketini belirler.
- Cevapta kullanılan citation etiketleri doğrulanır; kullanılmayan adaylar otomatik citation sayılmaz.
- Ingestion’ın doküman, repository, archive, OCR, sync ve async yolları tek kanonik pipeline kullanır.
- Evaluation, golden cevaptan sentetik sonuç üretmeden gerçek indeks ve gerçek servis yolunu ölçer.
- Arama kalitesi, veri izolasyonu, citation doğruluğu, latency ve maliyet gözlemlenebilir olur.
- Skill ve ajan yönergeleri herhangi bir modele/CLI’a kilitlenmez.
- Dış projeler yalnız teknik örüntü kaynağıdır; Context Vault’un kanonik otoritesi değildir.

Bu çalışma “bir framework’e geçiş” görevi değildir. Haystack, RAGFlow, LightRAG, GraphRAG, Ragas, RAGChecker, BEIR veya ColBERT doğrudan ürün bağımlılığı yapılmayacaktır. Uygun fikirler Context Vault’un mevcut Python/FastAPI/PostgreSQL/pgvector mimarisine temiz ve kontrollü biçimde uyarlanacaktır.

---

# 2. İnceleme Yöntemi ve Kapsam

Aşağıdaki alanlar kod, migration, test, dokümantasyon ve commit geçmişi üzerinden incelendi:

- Repository sahipliği ve contributor kaynağı.
- Root ve kanonik uygulama dizini ayrımı.
- API sorgu kapsamı ve conversation sahipliği.
- Dense, lexical ve identifier retriever’lar.
- RRF, reranker, dedupe ve context builder.
- No-answer ve smalltalk ayrımı.
- Citation üretimi ve persistence.
- Document upload, worker ingestion ve repository re-index.
- Versioning ve embedding profile şeması.
- Alembic indeksleri ve constraint’ler.
- Eval dataset, runner ve mevcut rapor.
- CI/CD, dependency ve deployment yapılandırması.
- `.claude/` altındaki tool-specific skill paketleri.
- Benzer RAG ve değerlendirme projelerinin uyguladığı kalıplar.

Statik repository analizi, gerçek runtime ortamının kanıtı değildir. Veritabanı migration durumu, gerçek servis sağlığı ve üretim verisi Aşama 1’de yeniden ölçülerek doğrulanacaktır.

---

# 3. Dış Proje ve Skill İncelemesinden Alınan Kararlar

## 3.1 Haystack’ten alınacak örüntüler

Haystack’in güçlü tarafı retrieval, routing, memory, generation ve evaluation adımlarını açık bileşenler/pipeline’lar olarak ele almasıdır. Context Vault için alınacak dersler:

- Her pipeline adımı açık giriş/çıkış sözleşmesine sahip olmalı.
- Adayların hangi retriever’dan geldiği, hangi aşamada elendiği ve hangi context öğesinin LLM’e gittiği izlenebilmeli.
- Senkron ve asenkron yollar aynı uygulama servisinin adaptörleri olmalı.
- Skill açıklamaları gerektiğinde keşfedilmeli; tamamı her sorguda modele yüklenmemeli.
- Vendor/model bağımsız portlar korunmalı.

**Alınmayacak karar:** Haystack’i uygulamanın çekirdeğine doğrudan eklemek veya mevcut domain katmanını framework nesneleriyle değiştirmek.

## 3.2 RAGFlow’dan alınacak örüntüler

RAGFlow; yapı koruyan parsing, template/strateji bazlı chunking, chunk görselleştirme, çoklu recall, fused reranking, grounded citation ve veri kaynağı senkronizasyonunu ürün deneyimine taşır. Context Vault için alınacak dersler:

- Chunk’lar UI’dan incelenebilir ve kaynağa geri izlenebilir olmalı.
- Ingestion pipeline adımları ve hata noktaları görünür olmalı.
- Dense/lexical/identifier aşamaları search playground’da ayrı ayrı gösterilmeli.
- Citation yalnız metin etiketi değil; document/version/source-file/locator/quote snapshot içermeli.
- Yeni connector’lar ancak kanonik ingestion sözleşmesini kullanmalı.

**Alınmayacak karar:** RAGFlow’un servis topolojisini, UI’ını veya bağımlılıklarını kopyalamak.

## 3.3 LightRAG ve GraphRAG’dan alınacak örüntüler

Graph tabanlı retrieval; çok belgeli ilişki, varlık ve global özet sorgularında değer üretebilir. Fakat indeks maliyeti, veri güncelleme karmaşıklığı ve doğrulama yükü yüksektir.

Karar:

- Graph retrieval başlangıç çözümü değildir.
- Önce mevcut hybrid retrieval gerçek eval setinde ölçülür ve düzeltilir.
- Cross-document ilişki sorularında ölçülen ve tekrarlanabilir açık kalırsa graph/late-interaction deneyleri ayrı feature flag altında yapılır.
- GraphRAG maintenance/research konumunda olduğu için ürün çekirdeğine bağımlılık yapılmaz.
- LightRAG yaklaşımındaki incremental update, selective deletion, tracing ve context-return fikirleri referans alınabilir; proje iddiaları bağımsız benchmark kabul edilmez.

## 3.4 Ragas, RAGChecker, BEIR ve ColBERT’ten alınacak örüntüler

- Retrieval ve generation tek “başarı” skoruna indirgenmeyecek.
- Context relevance/precision, faithfulness, citation doğruluğu, answer sufficiency ve no-answer davranışı ayrı ölçülecek.
- Dense-only, lexical-only ve hybrid sonuçlar karşılaştırılacak.
- BM25/lexical-benzeri güçlü ve ucuz baseline korunacak.
- Late interaction/ColBERT yalnız baseline’ı anlamlı geliştirir ve latency/maliyet sınırını karşılar ise deneysel seçenek olacak.
- RAGChecker benzeri bileşen bazlı teşhis raporu üretilecek: retrieval failure, context construction failure, grounding failure ve citation failure birbirinden ayrılacak.

---

# 4. Repository’de Doğrulanan Mevcut Durum

## 4.1 Sahiplik ve araç bağımlılığı

- Contributor görünümündeki ikinci kişi gerçek commit author/committer değişikliğinden değil, **15 commit mesajındaki** `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` trailer’ından kaynaklanıyor.
- `.mailmap` bu trailer’ı geçmişten kaldırmaz ve contributor sorununu güvenilir biçimde çözmez.
- Root’ta `.claude/settings.local.json` bulunuyor.
- `document-rag-platform/.claude/skills/` altında:
  - `beautify-github-readme`
  - `find-skills`
  - `frontend-design`
  paketleri bulunuyor.
- Bu paketler RAG çekirdeğinin parçası değildir; tool-specific dizin altında olmaları taşınabilirlik ve provenance problemi oluşturur.
- `find-skills` içeriği dış paket yöneticisi ve popülerlik iddialarını kanonik öneri mekanizması gibi sunuyor; bu yaklaşım güvenlik, lisans ve supply-chain kontrolü olmadan korunmayacak.

## 4.2 Dokümantasyon ve gerçek kod uyuşmazlığı

Eski `AKTIF_GOREV.md`:

- Aşama 0–10’u tamamlandı işaretliyor.
- Global Definition of Done maddelerini işaretsiz bırakıyor.
- “CI testleri geçiyor” hedefini içeriyor fakat `.github/workflows/` yalnız `.gitkeep`.
- 492 test geçtiğini ve pipeline’ın aktif olduğunu söylüyor; ancak üretim çalışma yolunda parser/chunker/context/eval entegrasyonlarının bir bölümü bağlı değil.
- Bir sonraki adımı hâlâ “final durumu işaretle” olarak bırakıyor.

Karar:

- Eski aktif görev sessizce silinmeyecek.
- Yeni görev repository’ye alınırken eski dosya:
  `done/active-tasks/2026-08-19-context-vault-rag-v2.md`
  altında arşivlenecek.
- Root’ta yalnız bu dosyanın yeni sürümü `AKTIF_GOREV.md` olarak kalacak.
- Tamamlanan işler tarih ve commit ile arşivlenecek; “tamamlandı” iddiası kod ve test kanıtı olmadan yazılmayacak.

---

# 5. Kritik Teknik Bulgular

Aşağıdaki bulgular çözülmeden yeni özellik eklemek yasaktır.

| No | Seviye | Alan | Doğrulanan sorun | Zorunlu sonuç |
|---:|---|---|---|---|
| 1 | P0 | Proje izolasyonu | `project_id` verilmezse chat retrieval tüm projelere açılabiliyor. | Sorgu kapsamı zorunlu ve fail-closed olacak. |
| 2 | P0 | Conversation sahipliği | Var olan `conversation_id` proje/kimlik sahipliği doğrulanmadan kabul ediliyor. | Conversation erişimi principal + project ile doğrulanacak. |
| 3 | P0 | Retriever filtreleri | Geniş `TypeError` catch filtresiz tekrar arama yapabiliyor. | Uyum fallback’i kaldırılacak; typed protocol ve açık hata kullanılacak. |
| 4 | P0 | Aktif sürüm | Dense/lexical/identifier SQL’leri `documents.active_version_id` filtresini varsayılan olarak uygulamıyor. | Eski ve yarım sürüm sızıntısı sıfır olacak. |
| 5 | P0 | Embedding profili | Dense retrieval aktif `embedding_profile_id` ile sınırlanmıyor; farklı profiller karışabilir. | Tek aktif profil constraint’i ve sorgu filtresi eklenecek. |
| 6 | P0 | Neighbor expansion | Komşu resolver `version_id` kullanmıyor; sürümler arası chunk karışabilir. | Parent/neighbor çözümü document + version + source kapsamında yapılacak. |
| 7 | P1 | Context kullanımı | ContextBuilder sonucu hesaplanıyor fakat AnswerService LLM prompt’unu `ranked_candidates` üzerinden kuruyor. | LLM yalnız `context.items` içeriğini alacak. |
| 8 | P1 | Parent expansion | ContextBuilder’a yalnız seçilmiş chunk pool’u veriliyor; parent çoğunlukla çözülemiyor. | Eksik parent/neighbor DB’den güvenli ve toplu çözülecek. |
| 9 | P1 | Reranker skoru | Rank yeniden atanırken dinamik `rerank_score` kayboluyor. | Tüm stage skorları typed hit üzerinde korunacak. |
| 10 | P1 | Citation doğruluğu | Tüm reranked adaylar citation sayılabiliyor; cevabın gerçekten kullandığı label doğrulanmıyor. | Structured output + citation label validator uygulanacak. |
| 11 | P1 | Citation snapshot | Persistence yalnız ilişki ve locator tutuyor; kullanılan quote/digest immutable değil. | Quote snapshot, hash ve retrieval run bağı eklenecek. |
| 12 | P1 | Dedupe | Dedupe content_hash metadata’sından önce çalışıyor; retriever adaylarında hash yok ve pratikte etkisiz. | Chunk metadata attach sonrası veya SQL aşamasında gerçek dedupe yapılacak. |
| 13 | P1 | RRF | Aynı liste içindeki duplicate id’ler dokümana aykırı biçimde birden fazla katkı yapıyor. | Retriever başına unique rank uygulanacak. |
| 14 | P1 | HNSW | `ef_search` spec’e yazılıyor fakat sorguda `SET LOCAL hnsw.ef_search` uygulanmıyor. | Transaction-scoped HNSW ayarı ve benchmark eklenecek. |
| 15 | P1 | Vektör indeks | Yeni `chunk_embeddings.embedding` üzerinde HNSW/IVFFlat index migration’da yok. | Profile-aware vektör index stratejisi uygulanacak. |
| 16 | P1 | Lexical indeks | `search_vector` ve identifier alanlarının gerekli GIN/trigram indeksleri eksik. | GIN/pg_trgm indeksleri migration ile eklenecek. |
| 17 | P1 | Lexical recall | `plainto_tsquery` AND semantiği ve manuel stopword yaklaşımı çok terimli sorguları düşürebilir. | Query planner ile phrase/websearch/OR/exact yolları ölçümlü birleştirilecek. |
| 18 | P1 | Identifier | Modül açıklamasındaki trigram yeteneği SQL/index tarafında gerçek değil. | Exact, prefix ve trigram yolları açıkça ayrılacak. |
| 19 | P1 | No-answer | Sabit/heuristic threshold ve “term presence + dense floor” bazı zayıf adayları answerable yapabilir. | Gerçek eval ile query-type/profile bazlı kalibrasyon yapılacak. |
| 20 | P0 | Eval güvenilirliği | Mevcut runner golden `expected_sources` üzerinden sentetik aday üretiyor. | Release eval gerçek indeks ve servis yolunu kullanacak. |
| 21 | P0 | Eval raporu | Mevcut 1.000 Recall/MRR/nDCG sentetik fake path sonucu; generation metrikleri `n/a`. | Eski rapor “contract smoke” olarak yeniden adlandırılacak; kalite kanıtı sayılmayacak. |
| 22 | P1 | Ingestion wiring | Async worker legacy `extract_text` ve karakter bazlı `chunk_text` kullanıyor; ParserRouter/ChunkerRegistry bypass ediliyor. | Tüm kaynaklar tek yapı-koruyan ingestion servisine bağlanacak. |
| 23 | P1 | Sync/async ayrışması | Sync upload version/profile/artifact yolundan farklı çalışıyor. | Sync yalnız kanonik servisin blocking adapter’ı olacak. |
| 24 | P1 | Re-index | Repository re-index lexical vector/identifier/symbol metadata’sını tam üretmiyor. | Değişen ve kopyalanan dosyalar aynı index sözleşmesini sağlayacak. |
| 25 | P1 | Re-index embedding | Unchanged copy legacy `pc.embedding` alanına güveniyor; aktif profile satırı olmayabilir. | Profile-specific embedding row kopyalanacak veya cache’den çözülecek. |
| 26 | P1 | Event/outbox | DB commit ile Celery enqueue arasında transactional outbox yok; job stuck kalabilir. | Outbox + idempotent dispatcher eklenecek. |
| 27 | P1 | Object storage | MinIO yazımı DB commit öncesi orphan obje bırakabilir; delete GC yok. | Staged object + outbox/compensation + GC policy uygulanacak. |
| 28 | P1 | Feature flag | `FEATURE_STRUCTURED_PARSING` ve `FEATURE_HYBRID_RETRIEVAL` tanımlı fakat gerçek gate değil. | Her flag ya gerçek gate olacak ya kaldırılıp ADR ile açıklanacak. |
| 29 | P1 | Config | `RERANK_TOP_K` tekrar tanımlı; corporate endpoint ve varsayılan MinIO credential’ları public config’e gömülü. | Config sadeleştirilecek; environment overlay uygulanacak. |
| 30 | P1 | CI | Repository’de çalışan workflow yok. | Test, migration, eval, security ve build gate’leri eklenecek. |
| 31 | P2 | Şema bütünlüğü | Chunk document/version ilişkisi composite constraint ile korunmuyor; version_id nullable. | Backfill sonrası composite FK ve NOT NULL uygulanacak. |
| 32 | P2 | Zaman | Naive UTC datetime kullanımı yaygın. | Timezone-aware UTC standardı uygulanacak. |
| 33 | P2 | Dependency | Requirements pin’li fakat lock/hash, SBOM ve otomatik security scan yok. | Reproducible lock ve supply-chain gate eklenecek. |
| 34 | P2 | Root yapısı | Root’ta kanonik uygulama dışında placeholder/skeleton dizinler bulunuyor. | Kullanılmayan iskeletler arşivlenip/silinecek; tek kanonik ağaç kalacak. |

---

# 6. Hedef Mimari

## 6.1 Sorgu çalışma yolu

```text
HTTP/API
  -> Authentication / Principal
  -> ProjectScopeResolver (zorunlu, fail-closed)
  -> QueryNormalizer
  -> QueryPlanner
       -> DenseRetriever(active version + active embedding profile)
       -> LexicalRetriever(active version)
       -> IdentifierRetriever(active version)
  -> CandidateNormalizer (typed RetrievalHit)
  -> Retriever-local dedupe
  -> RRF / weighted RRF
  -> Metadata attach
  -> Cross-source content dedupe
  -> Optional Reranker
  -> ContextBuilder(parent/neighbor/version-safe + tokenizer budget)
  -> AnswerPolicy(calibrated)
  -> EvidencePack(JSON/data-only)
  -> LLM
  -> CitationValidator / GroundingValidator
  -> Persistence(retrieval run + messages + immutable citations)
  -> Response
```

## 6.2 Ingestion çalışma yolu

```text
Source Adapter
  -> Validation / Security policy
  -> Staged object storage
  -> DocumentVersion + IngestionJob
  -> ParserRouter
  -> NormalizedSource artifact
  -> ChunkerRegistry
  -> Metadata/identifier extraction
  -> EmbeddingProfile
  -> Dense + lexical + identifier indexing
  -> Version validation
  -> Atomic activation
  -> Outbox events / cleanup
```

Document upload, repository, archive, directory, OCR, sync ve async yollar bu tek sözleşmeyi kullanacaktır.

## 6.3 Kanonik sonuç modeli

Yeni `RetrievalHit`/eşdeğer typed sözleşme en az şu alanları taşımalıdır:

```text
chunk_id
document_id
version_id
source_file_id
embedding_profile_id
project_id
rank
source_retrievers[]
stage_scores {
  dense
  lexical
  identifier
  rrf
  reranker
}
content_hash
chunk_type
heading_path
locator
symbol_name
token_count
content (yalnız attach sonrası)
```

Dinamik attribute ekleme yasaktır. Rank yeniden atama hiçbir skoru veya provenance bilgisini kaybetmemelidir.

---

# 7. Aşama 0 — Git Sahiplik Temizliği ve Araçtan Bağımsızlaştırma

## 7.1 Zorunlu hazırlık

- [ ] GitHub App veya kullanılan kimlikte `Contents: Read and write` ve ref güncelleme yetkisi doğrulanır.
- [ ] `main` son SHA’sı tekrar okunur.
- [ ] Son SHA bu dosyadaki `38c6ac...` ile farklıysa işlem durur; yeni commit’ler ayrıca incelenir.
- [ ] Tüm branch/tag/ref’lerde `Co-Authored-By: ... Claude` taranır.
- [ ] Repository tam bundle yedeği alınır.
- [ ] Contributor cleanup sırasında branch protection geçici ve kontrollü biçimde yönetilir.
- [ ] Açık PR ve eski branch’lerin rewrite sonrası durumu kayıt altına alınır.

## 7.2 Geçmiş rewrite sözleşmesi

Yalnız commit mesajındaki istenmeyen ortak-yazar trailer satırları çıkarılacaktır.

Korunacaklar:

- Commit author adı/e-postası.
- Commit committer adı/e-postası.
- Author/committer tarihleri.
- Commit mesajının trailer dışındaki tüm içeriği.
- Her commit’in tree içeriği.
- Parent/merge topolojisi.
- Commit sayısı.
- Tag/branch kapsamı, önceden belirlenen ref listesi.

Değişecekler:

- Etkilenen commit SHA’ları.
- Etkilenen commitlerin tüm descendant SHA’ları.
- `main` head SHA’sı.

Yasaklar:

- Squash ederek geçmişi tek commit’e indirmek.
- Dosya içeriğini rewrite sırasında değiştirmek.
- `.mailmap` ile sorunu çözülmüş saymak.
- `--force` ile lease kontrolü olmadan push etmek.
- Yedek almadan ref değiştirmek.
- Başka kişilerin gerçek commitlerini Mehmet adına çevirmek.

## 7.3 Doğrulama kapısı

Aşağıdaki kontrollerin tamamı geçmeden Aşama 0 tamamlanmış sayılmaz:

- [ ] Rewrite öncesi ve sonrası commit sayısı eşit.
- [ ] Rewrite öncesi ve sonrası her karşılık gelen commit tree dizisi eşit.
- [ ] `main^{tree}` SHA’sı eşit.
- [ ] `git log --all --format=%B` içinde istenmeyen trailer yok.
- [ ] `git shortlog -sne --all` yalnız gerçek author’ları gösteriyor.
- [ ] Remote push `--force-with-lease=refs/heads/main:<OLD_SHA>` ile yapılmış.
- [ ] Push sonrası remote head beklenen yeni SHA.
- [ ] Fresh clone alınmış ve çalışma ağacı temiz.
- [ ] Uygulama dosya checksum manifest’i rewrite öncesiyle aynı.
- [ ] GitHub Contributors ekranı yenilenmiş; cache gecikirse 24 saat sonra yeniden kontrol kaydı açılmış.
- [ ] Tüm geliştiricilere “re-clone veya hard reset” talimatı verilmiş.
- [ ] Yedek bundle güvenli yerde saklanmış.

## 7.4 Gelecekte tekrarını önleme

Repository’ye aşağıdakiler eklenecek:

- `scripts/check_commit_ownership.py`
- `.githooks/commit-msg`
- `.github/workflows/commit-ownership.yml`
- `CONTRIBUTING.md` içinde sahiplik politikası.
- Optional local setup: `git config core.hooksPath .githooks`

Kural:

- Her türlü `Co-Authored-By:` trailer’ı varsayılan olarak reddedilir.
- İstisna ancak Mehmet’in açık yazılı onayı ve allowlist ile yapılabilir.
- Yapay zekâ kullanımı gerekiyorsa release note veya iç iş kaydında “araç desteği” olarak belirtilir; Git commit sahipliği değiştirilmez.

## 7.5 `.claude/` içeriklerinin güvenli kaldırılması

Contributor geçmişi temizlendikten sonra, ürün aşamalarından önce:

1. Root `.claude/settings.local.json` silinir ve `.gitignore`a tool-local ayar deseni eklenir.
2. `document-rag-platform/.claude/skills/` envanteri çıkarılır.
3. Her skill için:
   - lisans dosyası,
   - orijinal kaynak,
   - kullanılan script/binary,
   - network erişimi,
   - mutation yetkisi,
   - ürünle ilişkisi
   kontrol edilir.
4. Yararlı ve lisansı açık içerik **kopyalanmadan önce temiz uyarlama** ile root `skills/` sözleşmesine dönüştürülür.
5. RAG ürünüyle ilgisiz README/UI skill’leri ayrı araç repository’sine taşınır veya arşivlenir.
6. `find-skills` doğrudan korunmaz; bunun yerine provenance/checksum/license zorunlu, allowlist’li bir skill registry yaklaşımı yazılır.
7. Son durumda repository içinde `.claude/` dizini kalmaz.
8. `git grep -i "claude"` sonucu yalnız tarihsel cleanup/runbook kaydı gibi açıkça gerekli belgelerle sınırlı olmalıdır.

**Aşama 0 teslim commit’i:**  
`chore(history): repository sahiplik ve araç-bağımsızlık politikasını tamamla`

> Geçmiş rewrite push’u normal commit değildir. Yukarıdaki commit, rewrite sonrası policy/cleanup dosyalarını ekleyen ilk yeni commit olacaktır.

---

# 8. Aşama 1 — Gerçek Durum, Baseline ve CI Temeli

## 8.1 Eski görevi arşivle ve yeni görevi etkinleştir

- [ ] Eski `AKTIF_GOREV.md` arşivlenir.
- [ ] Bu dosya root’a yazılır.
- [ ] Yeni rewrite sonrası `main` SHA bu dosyaya işlenir.
- [ ] `done/completed-tasks.md` içerisine tarih, eski görev ve son gerçek commit kaydı eklenir.
- [ ] README’de kanonik durum sayfası bu aktif göreve bağlanır.

## 8.2 Runtime gerçeklik denetimi

Aşağıdaki çıktılar tarih damgalı olarak `artifacts/audit/` altında tutulur; credential veya özel veri içermez:

- Docker Compose service listesi ve health.
- Alembic current/head/history.
- Gerçek şema ve index envanteri.
- Tablo satır sayıları.
- Aktif document version bütünlük raporu.
- Aktif embedding profile raporu.
- Orphan object/chunk/version raporu.
- PostgreSQL/pgvector sürümü.
- Backend/frontend dependency lock özeti.
- Worker queue ve stuck job raporu.
- API endpoint smoke test sonucu.
- Gerçek fixture üzerinde mevcut retrieval baseline.

## 8.3 CI oluştur

Minimum workflow’lar:

```text
ci-backend.yml
  - dependency install from lock
  - ruff format/check
  - mypy
  - unit tests
  - integration tests
  - migration upgrade/downgrade/upgrade
  - coverage report

ci-frontend.yml
  - locked install
  - lint
  - typecheck
  - unit/component tests
  - production build

ci-rag-eval.yml
  - deterministic fixture ingestion
  - real retrieval service eval
  - leakage and citation gates
  - baseline comparison

ci-security.yml
  - secret scan
  - dependency vulnerability scan
  - SBOM
  - container scan
  - commit ownership check
```

Branch protection Aşama 0 rewrite sonrasında etkinleştirilir. `main`e doğrudan push kapatılır; yalnız zorunlu status check’leri geçen PR merge edilir.

## 8.4 Kabul kriterleri

- [ ] Sıfırdan clone + tek komutla dev ortamı kuruluyor.
- [ ] Migration blank DB ve mevcut fixture DB üzerinde testli.
- [ ] Unit/integration/eval ayrımı açık.
- [ ] Fake eval release gate’e giremiyor.
- [ ] CI gerçekten çalışıyor; `.gitkeep` workflow yok.
- [ ] Runtime audit ile doküman iddiaları arasındaki fark raporlanmış.
- [ ] Bu aşama hiçbir retrieval davranışını değiştirmiyor.

**Commit:**  
`chore(baseline): gerçek runtime envanteri ve CI kalite kapılarını kur`

---

# 9. Aşama 2 — Veri İzolasyonu, Aktif Sürüm ve Embedding Profili

## 9.1 API kapsamı

- `project_id` chat/retrieval için zorunlu olacak.
- Proje seçilmemişse “ilk projeyi seç” veya “default proje oluştur” davranışı kaldırılacak.
- Belge listeleme, durum, silme, conversation ve source preview endpoint’leri project scope doğrulayacak.
- Authentication katmanı yoksa minimum olarak API key/principal portu eklenecek; production mode kimliksiz başlamayacak.
- `conversation_id` mevcutsa conversation’ın project/principal ile eşleşmesi doğrulanacak.
- Model seçimi `settings.available_chat_models` allowlist’i dışında reddedilecek.
- Query/message boyutu, top_k ve filtre limitleri typed request modelinde sınırlandırılacak.

## 9.2 Fail-closed filtre sözleşmesi

`filter_spec` bilinmeyen filtreleri sessizce atmayacak.

- Filtreler Pydantic/typed model ile doğrulanacak.
- Güvenlik filtreleri kullanıcı filtresinden ayrı “mandatory predicates” olarak eklenecek.
- Retriever’ın imzası tek ve açık olacak; geniş `TypeError` fallback kaldırılacak.
- Filter uygulayamayan retriever hata verecek; filtresiz arama yapmayacak.

Zorunlu SQL predicate’leri:

```text
d.project_id = :project_id
d.deleted_at IS NULL
d.status = 'indexed'
c.version_id = d.active_version_id
dv.status = 'ready'
```

Legacy `version_id IS NULL` veri için yalnız süreli migration compatibility flag’i olabilir; normal sorguyla karıştırılamaz.

## 9.3 Şema bütünlüğü

Migration ile:

- `chunks.version_id` backfill sonrası `NOT NULL`.
- Document/version uyumunu koruyan composite unique/FK.
- `documents.active_version_id` kendi document’ına ait olmak zorunda.
- SourceFile/version ve Chunk/source-file uyumu korunur.
- Tek aktif embedding profile için partial unique index.
- Active profile dimension/model/config hash tutarlılık kontrolü.
- `Project.name` global unique gereksinimi yeniden değerlendirilir; tenant varsa composite unique yapılır.
- Soft-delete ve retention politikası yazılır.

## 9.4 Dense profile doğruluğu

- Sorgu embedding’i aktif profilin model/prefix/dimension değerleriyle üretilir.
- `chunk_embeddings` sorgusu `embedding_profile_id` ile sınırlanır.
- `chunks.embedding` legacy kolonu ana sorguda paralel taranmaz.
- Geçiş tamamlanınca legacy kolon yalnız rollback/read-only olabilir; kaldırma ayrı onay gerektirir.
- Query vector dimension yanlışsa DB sorgusundan önce açık hata verilir.
- Model/prefix değişimi yeni profile + controlled re-index gerektirir.

## 9.5 Güvenlik testleri

- project A sorgusu project B chunk’ı döndürmez.
- active version v2 iken v1 chunk’ı dönmez.
- failed/pending version dönmez.
- deleted document dönmez.
- yanlış project conversation reddedilir.
- bilinmeyen güvenlik filtresi aramayı genişletmez.
- neighbor/parent başka sürüme geçmez.
- profile A query profile B embedding’iyle karşılaştırılmaz.

**Mutlak gate:** Cross-project, cross-version ve cross-profile leakage testlerinde kabul edilen hata sayısı **0**.

**Commit:**  
`fix(scope): proje sürüm ve embedding profil izolasyonunu fail-closed yap`

---

# 10. Aşama 3 — Retrieval Pipeline Doğruluğu ve İndeksleme

## 10.1 Candidate sözleşmesi

- `RetrievalCandidate` yerine veya onun kontrollü evrimi olarak typed `RetrievalHit` uygulanır.
- Stage score’ları explicit alanlarda tutulur.
- `_assign_ranks` yalnız rank değiştirir; skoru/provenance’ı yeniden nesneleyip kaybetmez.
- Retriever kaynakları array olarak tutulur.
- Serialization/debug API aynı sözleşmeyi kullanır.

## 10.2 Dense retrieval

- `chunk_embeddings.embedding` için HNSW index stratejisi uygulanır.
- Profile filtrelemesinin approximate index recall etkisi gerçek fixture’da ölçülür.
- Seçenekler benchmark edilir:
  - profile başına partial HNSW index,
  - profile partition,
  - tek index + iterative scan.
- `SET LOCAL hnsw.ef_search = ...` transaction içinde gerçekten uygulanır.
- Gerekirse `hnsw.iterative_scan` feature/config olarak değerlendirilir.
- Query plan `EXPLAIN (ANALYZE, BUFFERS)` ile kaydedilir.
- Candidate K, ef_search ve latency/recall eğrisi eval raporunda bulunur.

## 10.3 Lexical retrieval

En az üç plan değerlendirilecek:

1. Exact phrase / quoted technical token.
2. `websearch_to_tsquery` veya kontrollü doğal dil sorgusu.
3. OR/prefix fallback ve identifier join.

Kurallar:

- Teknik token’lar case-normalized fakat kayıpsız tutulur.
- Türkçe ve İngilizce sorular ayrı fixture’larla ölçülür.
- Stopword listesi tek başına recall düzeltmesi sayılmaz.
- Query terimleri tek chunk’ta AND ile bulunmak zorunda bırakılmaz.
- `search_vector` için GIN index eklenir.
- Başlık, symbol, path ve content alanlarının ağırlıkları gerekirse ayrı TSVECTOR bileşenleriyle modellenir.
- SQL injection’a açık dinamik config/column üretimi yapılmaz.

## 10.4 Identifier retrieval

- Exact array match.
- Exact symbol match.
- Prefix match.
- `pg_trgm` similarity.
- Source path match.

Bu yollar ayrı skor ve provenance ile raporlanacak. Gerekli GIN/GiST trigram indeksleri eklenecek. “Trigram” adı yalnız gerçek SQL/index uygulandığında kullanılacak.

## 10.5 RRF ve dedupe

- Her retriever listesi chunk_id bazında unique yapılır.
- Duplicate aynı retriever’dan ikinci katkı alamaz.
- RRF deterministic tie-break kullanır.
- Dense/lexical/identifier katkıları debug çıktısında görünür.
- Weighted RRF ancak gerçek eval baseline sonrasında açılır.
- Content dedupe, content_hash gerçekten yüklendikten sonra çalışır.
- Aynı içeriğin farklı belge/sürüm kopyalarında citation provenance kaybolmaz; canonical hit + alternate sources tutulur.

## 10.6 Reranker

- Reranker giriş/çıkış sözleşmesi typed.
- Timeout, maksimum batch ve circuit breaker.
- Disabled/unavailable durumda fusion order korunur.
- `rerank_score` persistence ve debug çıktısında kaybolmaz.
- Reranker model/version/config hash retrieval run’a yazılır.
- Reranker yalnız eval kazancı kanıtlandığında production default olur.

## 10.7 Query planner

İlk sürüm deterministic ve açıklanabilir olacak:

```text
smalltalk
natural_language
technical_identifier
quoted_phrase
path_or_symbol
comparison_or_multi_document
```

LLM tabanlı query rewrite başlangıçta zorunlu değildir. Rewrite eklenirse:

- original query saklanır,
- rewrite’lar görünür,
- her rewrite ayrı retrieval katkısı taşır,
- prompt injection ve maliyet sınırı uygulanır,
- eval’de açık kazanç olmadan default yapılmaz.

**Commit:**  
`fix(retrieval): typed hit fusion dedupe indeks ve rerank hattını doğrula`

---

# 11. Aşama 4 — Context, No-Answer, Grounding ve Citation

## 11.1 ContextBuilder gerçek çalışma yoluna bağlanacak

AnswerService prompt’u yalnız şu kaynaktan kurulacak:

```text
retrieval_result.context.items
```

`ranked_candidates` doğrudan LLM evidence’i olmayacak.

ContextBuilder düzeltmeleri:

- `selected_chunk_ids` gerçekten doldurulur.
- Parent’lar batch resolver ile yüklenir.
- Neighbor’lar project/document/version/source-file kapsamında çözülür.
- Duplicate item `expanded_chunk_ids` listesine sahte eklenmez.
- Relation türleri korunur: selected, parent, adjacent, table_header, code_signature.
- Token sayımı hedef chat modelinin gerçek tokenizer’ına göre yapılır.
- Prompt overhead, history ve answer budget hesaba katılır.
- Context truncation deterministic ve debug edilebilir olur.
- Citation label her ContextItem ile birebir eşleşir.

## 11.2 Evidence packaging

Raw XML benzeri string birleştirme yerine structured data kullanılır:

```json
{
  "label": "S1",
  "document_id": "...",
  "version_id": "...",
  "source_file": "...",
  "locator": {},
  "relation": "selected",
  "content": "..."
}
```

- Evidence “untrusted data” olarak işaretlenir.
- Delimiter escape uygulanır.
- Belge içindeki “talimat” metni system instruction sayılmaz.
- Prompt injection detector yalnız engelleme değil, risk signal üretir.
- Security testleri farklı dil ve formatlarda yapılır.

## 11.3 Structured answer ve citation validation

LLM’den yapılandırılmış çıktı istenir:

```text
answer
claims[]
  text
  citation_labels[]
abstained
abstention_reason
```

Validator:

- Var olmayan label’ı reddeder.
- Citation’sız factual claim’i işaretler.
- Kullanılmayan candidate’ı citation olarak persist etmez.
- Label’ın quote içeriği claim’i desteklemiyorsa repair/abstain yoluna gider.
- Repair sınırlı sayıda ve gözlemlenebilir olur.
- Validation başarısızsa uydurma cevabı yayınlamak yerine güvenli no-answer döner.

## 11.4 Immutable citation snapshot

`message_citations` veya yeni normalized tablo en az:

```text
message_id
claim_id
retrieval_run_id
chunk_id
document_id
version_id
source_file_id
citation_label
quote_text
quote_hash
source_content_hash
locator_json
retrieval_rank
stage_scores_json
created_at
```

Kaynak sonradan re-index edilse de “o cevapta hangi quote kullanıldı” denetlenebilir kalmalıdır.

## 11.5 Conversation ve history

- User ve assistant mesajları birlikte persist edilir.
- History yalnız aynı conversation/project/principal için alınır.
- History token budget uygulanır.
- Silinen/erişimi kaldırılan source eski conversation context’ine yeni sorguda otomatik taşınmaz.
- Conversation title async yardımcı işlem olabilir; retrieval yolunu bloklamaz.

## 11.6 No-answer kalibrasyonu

- Smalltalk, belge sorusu ve unsupported task ayrılır.
- Candidate sayısı tek başına evidence count değildir.
- Exact identifier, lexical, dense ve reranker sinyalleri query type’a göre kalibre edilir.
- Threshold active embedding/reranker profile ile versiyonlanır.
- Negative, adversarial ve near-miss dataset kullanılır.
- Sistem emin değilse kaynak eksikliği ve aranan kapsamı açıkça söyler.

**Mutlak gate:**

- Invalid citation label: 0.
- Cross-version citation: 0.
- Citation’sız factual claim release setinde: 0 veya açık abstention.
- Prompt içindeki document instruction’ın system davranışını değiştirdiği test: 0.

**Commit:**  
`fix(grounding): gerçek context bütçesi no-answer ve doğrulanmış citation hattı`

---

# 12. Aşama 5 — Kapsam Birleştirilmiş Ingestion ve Re-index

## 12.1 Tek kanonik IngestionService

Yeni servis/orkestratör tüm yolları birleştirecek:

```text
Document upload
Repository URL
Archive upload
Allowed directory
Image/OCR
Re-index
Sync adapter
Async/Celery adapter
```

API veya worker içinde ayrı parsing/chunking iş kuralı kalmayacak.

## 12.2 ParserRouter ve ChunkerRegistry wiring

- DOCX/PDF/TXT/MD/Image/Code parser’ları gerçekten worker yolunda kullanılır.
- NormalizedSource kayıpsız artifact olarak saklanır.
- ChunkerRegistry content type’a göre seçilir.
- Heading, table, page, bbox, symbol, line ve parent metadata’sı DB’ye taşınır.
- `chunk_type`, `symbol_type`, `source_type` enum/validated vocabulary olur.
- Naive karakter chunker yalnız açık legacy fallback olarak, feature flag ile kalabilir.
- Fallback kullanıldığında event ve metric yazılır; sessiz degrade olmaz.

## 12.3 Embedding ve indexing

- Passage prefix profile’dan gelir; endpoint form parametresiyle keyfi instruction kabul edilmez.
- Embedding cache key:
  `content_hash + profile_config_hash + passage_prefix_hash`
- Her chunk için dense, lexical ve identifier index aynı transaction/stage sözleşmesinde tamamlanır.
- Index tamamlanmadan version `ready` ve aktif olamaz.
- Validation:
  - chunk count,
  - embedding count/dimension,
  - search_vector doluluk,
  - identifier metadata,
  - artifact checksum,
  - source-file coverage.

## 12.4 Transactional outbox

DB commit ve dış sistem işlemleri için:

- `ingestion_outbox`
- idempotency key
- retry/backoff
- dispatcher
- dead-letter state
- stuck job reconciler

uygulanır.

MinIO:

- önce staged key,
- checksum doğrulama,
- DB version/job,
- outbox,
- final key/activation
veya eşdeğer güvenli compensation tasarımı kullanır.

## 12.5 Re-index düzeltmeleri

- Version number row lock/advisory lock ile yarışa dayanıklı.
- Failure version’ı `failed` yapar; aktif version değişmez.
- Changed/new/unchanged/deleted file sayıları doğru isimlendirilir.
- Unchanged file:
  - source metadata,
  - chunk metadata,
  - profile-specific embedding,
  - lexical/identifier indeks,
  - artifact reference
  bakımından yeni version sözleşmesini karşılar.
- Deleted file yeni version’da yoktur; eski version retrieval’a sızmaz.
- Incremental re-index ile full re-index aynı sonuç setini üretir; property/integration test yapılır.

## 12.6 Storage lifecycle

- Document delete DB + object storage için tombstone/outbox kullanır.
- Orphan scan ve GC dry-run raporu.
- Retention süresi dolmadan fiziksel silme yok.
- Audit/citation’da referanslanan snapshot retention politikası ayrıdır.
- Backup/restore runbook object + DB birlikte test edilir.

**Commit:**  
`refactor(ingestion): tüm kaynakları tek versioned pipeline ve outbox altında birleştir`

---

# 13. Aşama 6 — Gerçek Evaluation ve Kalite Kapıları

## 13.1 Mevcut fake eval’in yeniden konumlandırılması

Mevcut `FakeRetriever` ve `FakeAnswerer`:

- `contract_smoke` testine taşınır.
- Golden `expected_sources` üzerinden aday üretmesi açıkça yazılır.
- Release metric raporu üretemez.
- CLI’da `--fake` varsayılan ve kapatılamaz davranışı kaldırılır.
- Release eval fake dependency algılarsa hard fail verir.
- Mevcut 1.000 skor raporu arşivlenir ve “sentetik sözleşme testi” olarak etiketlenir.

## 13.2 Deterministic gerçek fixture

`tests/evals/fixtures/` altında lisanslı/sentetik fakat gerçek pipeline’dan geçen corpus oluşturulur:

- Türkçe ve İngilizce belgeler.
- DOCX heading + table.
- Dijital PDF.
- OCR görsel/scanned PDF.
- Markdown/TXT.
- Python/TypeScript.
- Oracle PL/SQL package/spec/body.
- Aynı terimin farklı projelerde çakıştığı belgeler.
- v1/v2 çelişkili belge sürümleri.
- Aynı içeriğin duplicate kopyaları.
- Prompt injection metni içeren belge.
- Cevabı olmayan sorular.
- Çok belgeli karşılaştırma.
- Path/symbol/error-code soruları.

Fixture gerçek ParserRouter, ChunkerRegistry, embedding adapter ve PostgreSQL index yolundan geçer.

İki eval tier:

1. **Offline deterministic**
   - frozen local embedding fixture veya deterministic test embedder,
   - her PR’da,
   - retrieval/integration güvenilirliği.

2. **Live model benchmark**
   - gerçek embedding/reranker/chat gateway,
   - nightly/manual,
   - model kalite/maliyet/latency karşılaştırması.

Offline skor, gerçek model kalitesi gibi sunulmayacak; live skor da nondeterminism notu olmadan gate yapılmayacak.

## 13.3 Dataset boyutu ve kategoriler

İlk release seti minimum **120 sorgu**:

```text
20 natural-language factual
15 exact identifier / symbol / path
10 table
10 OCR
10 code/PLSQL
10 multi-document comparison
10 Turkish-English cross-lingual
10 no-answer
10 stale-version traps
10 cross-project isolation traps
5 duplicate/noise
5 prompt-injection/counterfactual
```

Her kayıt:

```text
id
query
project_fixture
scope
answerable
expected_document/version/source_file
relevant chunk/locator constraints
must_contain / must_not_contain
required answer aspects
risk category
```

Golden cevabı retrieval girişine veya candidate üretimine sızdırmak yasaktır.

## 13.4 Retrieval metrikleri

- Recall@1/3/5/10.
- MRR@10.
- nDCG@10.
- Precision@k/context precision.
- Relevant document/version/source-file recall.
- Identifier exact-hit rate.
- Table/OCR/code kategori skorları.
- Duplicate rate.
- Stale-version leakage.
- Cross-project leakage.
- No-answer precision/recall/F1 ve FP/FN.
- p50/p95/p99 stage latency.
- Candidate counts ve stage drop-off.

## 13.5 Generation/grounding metrikleri

- Citation coverage.
- Citation precision/accuracy.
- Unsupported claim rate.
- Faithfulness.
- Required-aspect coverage.
- Answer sufficiency.
- Contradictory source behavior.
- Abstention correctness.
- Prompt-injection resistance.
- Quote/claim entailment reviewer skoru.
- Token ve maliyet.

Otomatik LLM judge tek otorite olmayacak. Deterministic kontroller, curated labels ve periyodik insan review birlikte kullanılacak.

## 13.6 Baseline karşılaştırma matrisi

Aynı corpus üzerinde:

```text
dense-only
lexical-only
identifier-only
dense + lexical RRF
dense + lexical + identifier RRF
hybrid + reranker
hybrid + query planner
experimental late interaction
experimental graph
```

Yeni yöntem production default olmak için:

- Mutlak güvenlik gate’lerini geçmeli.
- Baseline Recall/nDCG/no-answer metriklerini belirlenen toleransın dışında düşürmemeli.
- Hedef kategoride istatistiksel ve tekrarlanabilir kazanç göstermeli.
- p95 latency ve maliyet bütçesi Mehmet tarafından onaylanmalı.
- Rollback feature flag’i bulunmalı.

Başlangıçta keyfi tek global kalite eşiği yazılmayacak. İlk gerçek baseline’dan sonra `quality-gates.yaml` versiyonlanacak.

**Commit:**  
`test(eval): gerçek indeks tabanlı retrieval grounding ve leakage benchmarkını kur`

---

# 14. Aşama 7 — Observability, Audit ve Operasyon

## 14.1 Retrieval run ledger

Yeni tablolar veya eşdeğer event store:

```text
retrieval_runs
retrieval_run_stages
retrieval_run_candidates
generation_runs
feedback
```

`retrieval_runs` en az:

- request_id/trace_id
- principal/project
- query hash ve gerekirse redacted query
- filters
- active version/profile snapshot
- retriever/reranker/config versions
- stage durations
- candidate counts
- selected context ids
- token counts
- answerability decision/reason
- model
- cost
- error/degradation signals

Sensitive content varsayılan olarak raw log’a yazılmaz.

## 14.2 Telemetry

- OpenTelemetry trace/span.
- Structured JSON logs.
- Prometheus/OpenMetrics:
  - ingestion queue age,
  - job failure/retry,
  - retrieval stage latency,
  - empty-result rate,
  - abstention rate,
  - citation validation failures,
  - cross-scope guard failures,
  - embedding/reranker/chat error,
  - cache hit,
  - token/cost.
- Correlation ID API → DB → worker → LLM.
- Dashboard ve alert runbook.

## 14.3 Operasyonel araçlar

- Stuck job reconciler.
- Failed version inspector.
- Orphan storage dry-run/GC.
- Re-index planner.
- Embedding profile activation/deactivation.
- Index health/ANALYZE/REINDEX runbook.
- Backup/restore drill.
- Golden eval regression report.
- Security incident export.

**Commit:**  
`feat(observability): retrieval ve ingestion run ledger telemetry ve runbook ekle`

---

# 15. Aşama 8 — Ürün Deneyimi

## 15.1 Search Playground

Kullanıcı tek sorguda şunları görebilmeli:

- effective project/scope/filter.
- normalized/planned query.
- dense top-k.
- lexical top-k.
- identifier top-k.
- RRF sonucu ve katkılar.
- rerank before/after.
- final context items ve token bütçesi.
- no-answer gerekçesi.
- kullanılan citation label’ları.
- trace id.

Production kullanıcılarına teknik detay RBAC/debug flag ile açılır.

## 15.2 Ingestion inspector

- Source/artifact/version listesi.
- Parser seçimi ve fallback.
- Normalized preview.
- Chunk listesi.
- Heading/table/page/bbox/symbol metadata.
- Embedding profile.
- Search vector/identifier summary.
- Job stage/events.
- Failed stage ve retry.
- Re-index diff: changed/copied/deleted.

## 15.3 Citation UX

- Citation tıklanınca document/version/source-file/locator açılır.
- Quote highlight.
- Old answer için immutable quote snapshot görüntülenir.
- Kaynak değiştiyse “cevap sırasında kullanılan sürüm” açıkça gösterilir.
- OCR bbox ve code line range desteklenir.

## 15.4 Feedback ve eval loop

- Helpful/not helpful.
- Wrong source.
- Missing source.
- Unsupported claim.
- Should abstain.
- Correct answer text.
- Feedback doğrudan model eğitimi sayılmaz; triage kuyruğuna girer.
- Onaylanan feedback golden dataset’e kontrollü PR ile eklenir.

## 15.5 Connector genişlemesi

S3/MinIO, Git, filesystem dışındaki connector’lar ancak:

- canonical SourceAdapter sözleşmesine,
- credential vault’a,
- incremental cursor/sync state’e,
- deletion semantics’e,
- rate limit/backoff’a,
- provenance ve versioning’e
uyuyorsa eklenebilir.

**Commit:**  
`feat(product): search playground ingestion inspector citation ve feedback akışları`

---

# 16. Aşama 9 — Araçtan Bağımsız Skill Sistemi

## 16.1 Kanonik dizin

Repository kökünde:

```text
skills/
  catalog.yaml
  schemas/
    skill-manifest.schema.json
  repository-audit/
    SKILL.md
    manifest.yaml
    tests/
  rag-ingestion-audit/
  rag-retrieval-debug/
  rag-eval-runner/
  citation-verifier/
  version-isolation-check/
  history-ownership-check/
```

Model/CLI özel `.claude`, `.opencode`, `.codex` vb. dizinler kanonik kaynak olmayacaktır.

CLI entegrasyonu gerekiyorsa:

```text
tools/skill-adapters/
```

altındaki generator/installer, kanonik manifest’ten çalışma zamanında adapter üretir; generated dosyalar repository otoritesi değildir.

## 16.2 Skill manifest sözleşmesi

Her skill:

```text
id
version
purpose
triggers
inputs
outputs
required_tools
network_policy
read_paths
write_paths
mutation_policy
approval_required
evidence_contract
rollback
tests
license
source_provenance
checksum
owner
```

alanlarını taşır.

## 16.3 Güvenlik ilkeleri

- Read-only default.
- Repository mutation için açık kullanıcı onayı.
- Force push, delete, secret, deploy gibi riskli işlemler ayrı capability.
- External skill doğrudan install edilmez:
  - source allowlist,
  - commit/tag pin,
  - checksum,
  - license,
  - script review,
  - network review,
  - sandbox test
  zorunlu.
- Install count/star tek başına güven göstergesi değildir.
- Skill kendisini otomatik “aktif” sayamaz; yalnız öneri ve evidence üretir.
- Skill output’u canonical DB/runtime gerçeğinin yerine geçmez.
- Skill değişikliği de unit/contract test ve changelog gerektirir.

## 16.4 İlk skill’ler

### `history-ownership-check`
- Commit trailer, author/committer, forbidden path ve contributor risk raporu.
- Default read-only.
- Rewrite yalnız ayrı script ve explicit approval ile.

### `rag-retrieval-debug`
- Query trace alır.
- Stage adaylarını karşılaştırır.
- Scope/version/profile leakage kontrol eder.
- SQL plan ve index kullanımını raporlar.
- Otomatik config değiştirmez.

### `rag-eval-runner`
- Gerçek fixture başlatır.
- Fake path’i release modunda reddeder.
- Baseline diff üretir.
- Golden leakage guard çalıştırır.

### `citation-verifier`
- Answer claims, citation labels, quote snapshot ve locator bütünlüğünü doğrular.
- Unsupported claim raporu üretir.

### `rag-ingestion-audit`
- Source → artifact → normalized → chunks → embeddings → indexes → activation zincirini kontrol eder.
- Orphan ve stuck job raporu üretir.

### `version-isolation-check`
- Cross-project, stale-version, failed-version, profile leakage testleri.

### `repository-audit`
- CI, migration, dependency, docs/code drift ve active task durumunu raporlar.

**Commit:**  
`feat(skills): model ve CLI bağımsız doğrulanabilir RAG skill kataloğunu kur`

---

# 17. Aşama 10 — Deneysel Retrieval Genişlemeleri

Bu aşama yalnız Aşama 6 eval raporunda açık kalan kategori problemi varsa başlar.

## 17.1 Late interaction / ColBERT deneyi

- Ayrı index.
- Ayrı feature flag.
- Existing hybrid ile A/B/offline comparison.
- Storage, indexing time, query latency ve recall ölçümü.
- Model/provider bağımsız port.
- Kazanç yoksa kaldırılır; core şema kirletilmez.

## 17.2 Graph retrieval deneyi

Uygun soru türleri:

- Çok belgeli varlık ilişkileri.
- Global tema/özet.
- Uzun zincirli bağlantı.
- Kod çağrı/bağımlılık ilişkileri.

Zorunlu koşullar:

- Entity/relation provenance chunk’a geri bağlı.
- Graph version document version ile uyumlu.
- Incremental update ve deletion.
- Extraction prompt/model versioned.
- Maliyet ve stale graph ölçümü.
- Graph sonucu da citation validator’dan geçer.
- GraphRAG/LightRAG dış servisleri kanonik otorite olmaz.

## 17.3 Deney kabul kapısı

- Hedef kategori metriği anlamlı artar.
- Diğer kategorilerde güvenlik/kalite regresyonu yok.
- Operasyon maliyeti onaylı.
- Rollback tek flag ile.
- Veri migration’ı geri alınabilir.
- Ürün UI debug görünümü eklenmiş.

**Commit:**  
`experiment(retrieval): eval ile gerekçelendirilmiş late-interaction veya graph deneyi`

---

# 18. Aşama 11 — Release, Temizlik ve Nihai Aktivasyon

## 18.1 Repository temizliği

- Root placeholder dizinler incelenir; kanonik olmayan boş iskeletler kaldırılır.
- `.claude/` kalmaz.
- Tool-local settings tracked olmaz.
- Corporate endpoint/certificate/deployment özel ayarlar public core config’ten ayrılır.
- README yalnız çalışan özellikleri yazar.
- Eski sentetik eval raporları doğru etikete taşınır.
- Generated artifact ve secret’lar `.gitignore`da.
- Dead code ve gerçek gate olmayan feature flag’ler kaldırılır veya bağlanır.
- Duplicate config alanları temizlenir.
- Dependency lock, SBOM ve license inventory commit edilir.

## 18.2 Release gate

- CI tüm workflow’lar yeşil.
- Migration upgrade/downgrade/upgrade.
- Backup/restore.
- Real fixture eval.
- Live benchmark son onay.
- Cross-project/version/profile leakage 0.
- Invalid citation 0.
- Prompt injection security suite yeşil.
- Object storage GC dry-run temiz.
- No stuck job.
- Search playground ve source preview smoke.
- Fresh clone deploy.
- Rollback drill.
- Git ownership check yalnız Mehmet author/committer politikasını doğruluyor.

## 18.3 Nihai dokümanlar

```text
README.md
docs/architecture/
docs/adr/
docs/runbooks/
docs/evaluation/
docs/security/
docs/skills/
CHANGELOG.md
CONTRIBUTING.md
AKTIF_GOREV.md
done/completed-tasks.md
```

Bu görev yalnız tüm Global Definition of Done maddeleri işaretlendiğinde arşivlenir.

**Commit:**  
`release(context-vault): doğrulanmış RAG platformu ve ürün kalite kapılarını aktive et`

---

# 19. Dosya Bazlı Zorunlu Değişiklik Haritası

| Dosya/Alan | Zorunlu değişiklik |
|---|---|
| `src/application/retrieval_service.py` | TypeError fallback kaldır; mandatory scope; active version/profile; score-preserving hit; gerçek dedupe/context. |
| `src/application/answer_service.py` | Prompt’u `context.items`tan kur; structured answer; citation validation; user message/history persistence. |
| `src/api/v1/chat.py` | project zorunlu; conversation ownership; resolver scope; model/query limitleri. |
| `src/infrastructure/retrieval/base.py` | Typed filter ve fail-closed; yeni RetrievalHit. |
| `dense.py` | Profile-aware `chunk_embeddings`; HNSW settings; legacy double scan kaldır. |
| `lexical.py` | Query planner; GIN; active version; açıklanabilir stage score. |
| `identifier.py` | Exact/prefix/trigram; gerçek index; active version. |
| `rrf.py` | In-list duplicate fix; provenance; deterministic weighted extension. |
| `context_builder.py` | selected IDs; parent/neighbor batch; model tokenizer; exact budget. |
| `no_answer.py` | Eval-calibrated policy; query type/profile version. |
| `workers/ingestion_tasks.py` | Legacy parser/chunker yerine kanonik IngestionService. |
| `api/v1/documents.py` | Sync/async adapter; form instruction kaldır; scope/auth; doğru status code. |
| `application/reindex_service.py` | Full metadata/index/profile copy; lock; failure state; correct counters. |
| `models.py` | Composite constraints; retrieval run/outbox/citation snapshot/feedback. |
| `alembic/versions/` | Index, FK, NOT NULL, unique active profile, new audit/outbox tabloları. |
| `config.py` | Duplicate kaldır; gerçek flag; environment overlay; typed limits. |
| `tests/evals/run_eval.py` | Gerçek runner; fake release’de forbidden; generation metrics gerçekten hesaplanır. |
| `tests/evals/` | Fixture, labels, baseline, leakage, category reports. |
| `.github/workflows/` | CI, eval, security, ownership. |
| `.claude/` | Provenance denetimi sonrası araçtan bağımsız migration ve tamamen kaldırma. |
| `skills/` | Kanonik manifest/skill/test yapısı. |
| README/runbooks | Yalnız doğrulanmış davranış, kesin komut ve rollback. |

---

# 20. Test Matrisi

## 20.1 Unit

- Filter schema unknown-field fail.
- Scope mandatory predicate.
- Retriever duplicate normalization.
- RRF duplicate/tie.
- Score preservation.
- Query planner classification.
- Lexical query variants.
- Identifier extraction/match.
- Context budget/parent/neighbor.
- Citation label validator.
- Answer policy calibration loader.
- Outbox state transition.
- Skill manifest/schema.
- Commit ownership parser.

## 20.2 Database/integration

- Blank migration upgrade.
- Existing fixture upgrade.
- Downgrade/upgrade.
- Composite FK violations.
- One active embedding profile.
- HNSW/GIN/trigram index usage.
- Project/version/profile isolation.
- Transactional activation.
- Concurrent re-index.
- Worker redelivery/idempotency.
- MinIO failure, DB failure, broker failure compensation.
- Citation snapshot after source update/delete.
- Real eval fixture ingestion.

## 20.3 API/E2E

- Auth/project required.
- Conversation ownership.
- Document upload → version → job → active.
- DOCX/PDF/OCR/code source preview.
- Search playground stages.
- Exact identifier.
- Cross-lingual.
- Multi-document.
- No-answer.
- Prompt injection.
- Citation open to correct version/locator.
- Re-index and stale-version absence.
- Delete/retention/GC.

## 20.4 Load/resilience

- Concurrent query and ingestion.
- 100k/1m chunk profile benchmark.
- HNSW ef_search sweep.
- Reranker timeout/circuit breaker.
- Celery retry storm.
- Redis/MinIO/Postgres restart.
- Large archive limits.
- OCR timeout.
- Graceful degradation signal.

---

# 21. Güvenlik ve Gizlilik Kuralları

- Production’da authentication zorunlu.
- Project/tenant scope DB predicate olarak zorunlu.
- Mümkünse PostgreSQL RLS defense-in-depth olarak değerlendirilir.
- Secret, private key, `.env`, credential dosyası hiçbir embedding/chat gateway’e gitmez.
- Redaction, orijinal artifact’ın güvenli saklanması ve retrieval görünürlüğü ayrı politikalardır.
- Repository ingestion kod çalıştırmaz; dependency install/build/test yapmaz.
- Symlink/archive traversal engellenir.
- External URL clone için allow/deny policy, timeout ve size limit.
- Prompt evidence untrusted data.
- Debug endpoint production’da kapalı ve auth’lu.
- Raw query/content loglama minimize ve redacted.
- Feedback PII policy.
- Artifact/citation retention.
- Dependency/skill supply-chain pin/checksum/license.
- Default MinIO credential production’da startup hard-fail.
- Public repository’de corporate endpoint ve environment-specific CA yalnız deployment overlay üzerinden yönetilir.

---

# 22. Commit ve Branch Stratejisi

Aşama 0 rewrite dışında her değişiklik PR ile yapılır.

Önerilen branch’ler:

```text
chore/history-ownership-policy
chore/real-baseline-ci
fix/project-version-profile-scope
fix/retrieval-pipeline
fix/context-grounding-citations
refactor/canonical-ingestion
test/real-rag-evaluation
feat/observability
feat/product-inspector
feat/tool-neutral-skills
experiment/advanced-retrieval
release/context-vault-v3
```

Kurallar:

- Bir PR tek aşama veya tek kabul kapısı.
- Migration ve uygulama kodu aynı PR’da, rollback ile.
- Generated snapshot hariç büyük format-only değişiklik işlevsel değişiklikle karıştırılmaz.
- Commit author/committer Mehmet.
- AI co-author trailer yok.
- Her PR:
  - amaç,
  - risk,
  - migration,
  - test,
  - eval diff,
  - rollback,
  - screenshots/traces
  bölümlerini içerir.

---

# 23. Rollback ve Veri Koruma

- Mevcut kullanıcı verisi silinmez.
- Her schema migration önce backup ve restore doğrulaması.
- Yeni retrieval feature flag eski güvenli baseline’a dönebilir.
- Version activation atomic; eski active version rollback için saklanır.
- Embedding profile fiziksel silinmez; önce pasif/retention.
- Object artifact migration rollback’te otomatik silinmez.
- Outbox ve GC idempotent.
- History rewrite bundle yedeği ayrı tutulur.
- Force push sonrası eski ref’ler GitHub’da erişilebilir bırakılmamalı; fakat bundle kontrollü offline yedektir.
- Skill migration’da orijinal içerik lisans/provenance kararı verilmeden kaybedilmez.
- Deneysel graph/late-interaction core retrieval şemasını geri döndürülemez biçimde değiştirmez.

---

# 24. Global Definition of Done

## Sahiplik

- [ ] İstenmeyen co-author trailer tüm hedef ref geçmişinden çıkarıldı.
- [ ] Contributor görünümünde yalnız gerçek katkı sahipleri var.
- [ ] Tree/commit sayısı koruma kanıtları arşivlendi.
- [ ] Commit ownership CI/hook aktif.
- [ ] `.claude/` repository’den kaldırıldı.
- [ ] Kanonik skill’ler tool-neutral.

## Güvenlik ve veri doğruluğu

- [ ] Project scope zorunlu ve fail-closed.
- [ ] Conversation sahipliği doğrulanıyor.
- [ ] Yalnız active ready version aranıyor.
- [ ] Yalnız active compatible embedding profile aranıyor.
- [ ] Cross-project/version/profile leakage 0.
- [ ] Unknown filter sessizce düşmüyor.
- [ ] Prompt injection suite geçiyor.

## Retrieval

- [ ] Typed hit tüm stage skorlarını koruyor.
- [ ] Dense profile-aware ve indeksli.
- [ ] Lexical GIN/query planner gerçek.
- [ ] Identifier exact/prefix/trigram gerçek.
- [ ] RRF duplicate bug yok.
- [ ] Content dedupe etkili.
- [ ] Reranker fallback ve score persistence doğru.
- [ ] HNSW ayarı uygulanıyor ve benchmarklı.

## Context ve cevap

- [ ] LLM yalnız ContextBuilder çıktısını kullanıyor.
- [ ] Parent/neighbor version-safe.
- [ ] Model tokenizer budget aşılmıyor.
- [ ] Structured answer/citation validator aktif.
- [ ] Invalid citation 0.
- [ ] Immutable quote snapshot var.
- [ ] No-answer gerçek eval ile kalibre.
- [ ] User/assistant history scope’lu persist.

## Ingestion

- [ ] Tüm kaynaklar tek IngestionService kullanıyor.
- [ ] ParserRouter/ChunkerRegistry gerçek worker yolunda.
- [ ] Metadata kayıpsız.
- [ ] Sync/async aynı contract.
- [ ] Transactional outbox.
- [ ] Re-index full/incremental eşdeğer.
- [ ] Version activation atomic.
- [ ] Storage GC/retention/runbook testli.

## Evaluation ve operasyon

- [ ] Fake eval yalnız contract smoke.
- [ ] Minimum 120 gerçek fixture sorgusu.
- [ ] Retrieval ve generation metrikleri ayrı.
- [ ] Baseline comparison ve quality gates versioned.
- [ ] CI workflow’ları gerçekten çalışıyor.
- [ ] Retrieval/ingestion run ledger.
- [ ] Trace/metrics/dashboard/alert.
- [ ] Backup/restore ve rollback drill.
- [ ] Fresh clone deployment geçiyor.
- [ ] README ve runbook kodla uyumlu.

## Ürün

- [ ] Search playground.
- [ ] Ingestion/chunk inspector.
- [ ] Doğru sürüme açılan citation UX.
- [ ] Feedback triage.
- [ ] Tool-neutral skill kataloğu ve testleri.
- [ ] Deneysel retrieval yalnız eval gerekçesiyle.

---

# 25. İlerleme Kaydı

Bu bölüm her commit/PR sonrası güncellenir; geçmiş satırlar silinmez.

```text
Son güncelleme: 2026-08-31
Sahip: Mehmet KARACAN
İncelenen remote HEAD: 38c6ac5697431475351e577b2794399b349fb210
Aktif aşama: Aşama 0 — Git Sahiplik Temizliği
Durum: ENGELLİ
Engel: Mevcut GitHub App kurulumu repository write/Git Data yetkisi vermedi; create/update işlemleri HTTP 403 "Resource not accessible by integration" ile reddedildi.
Repository mutasyonu: YAPILMADI
Hazırlanan çıktı:
- Yeni AKTIF_GOREV.md
- Güvenli history cleanup script’i
Bir sonraki kesin adım:
1. GitHub bağlantısına repository contents/ref write yetkisi ver veya cleanup script’ini Mehmet’in yetkili yerel terminalinde çalıştır.
2. Aşama 0 doğrulama manifest’ini commit et.
3. Yalnız bundan sonra Aşama 1’e geç.
```

## Aşama durumları

- [ ] Aşama 0 — Git sahipliği ve tool-neutral cleanup
- [ ] Aşama 1 — Gerçek baseline ve CI
- [ ] Aşama 2 — Project/version/profile izolasyonu
- [ ] Aşama 3 — Retrieval pipeline ve indeks
- [ ] Aşama 4 — Context/grounding/citation
- [ ] Aşama 5 — Kanonik ingestion/re-index
- [ ] Aşama 6 — Gerçek evaluation
- [ ] Aşama 7 — Observability/operasyon
- [ ] Aşama 8 — Ürün deneyimi
- [ ] Aşama 9 — Tool-neutral skill sistemi
- [ ] Aşama 10 — Ölçümlü deneysel retrieval
- [ ] Aşama 11 — Release ve nihai aktivasyon

---

# 26. İlk Uygulama Komut Sırası

Aşama 0 için repository’den bağımsız iki eşdeğer araç hazırlanmıştır:

```text
remove_claude_coauthor_history.ps1   # Windows PowerShell
remove_claude_coauthor_history.sh    # Git Bash / WSL / Linux / macOS
```

Mehmet’in çalışma ortamına uygun olan script kullanılacaktır.

Script çalıştırılmadan önce:

- GitHub hesabının `main`e force-with-lease push yetkisi doğrulanır.
- Açık PR/branch durumu kontrol edilir.
- `EXPECTED_HEAD` bu dosyadaki başlangıç SHA ile eşleşir.
- `CONFIRM_FORCE_PUSH=YES` açık olarak verilmeden remote ref değiştirilmez.
- Script çıktısı ve manifest `artifacts/audit/history-cleanup/` altında saklanır.
- Contributor ekranı doğrulanır.
- Yeni SHA bu dosyaya yazılır.

---

# 27. Referanslar

Teknik örüntü kaynağı olarak incelenen başlıca projeler:

- Haystack — `https://github.com/deepset-ai/haystack`
- RAGFlow — `https://github.com/infiniflow/ragflow`
- Microsoft GraphRAG — `https://github.com/microsoft/graphrag`
- LightRAG — `https://github.com/HKUDS/LightRAG`
- pgvector — `https://github.com/pgvector/pgvector`
- Ragas paper — `https://arxiv.org/abs/2309.15217`
- RAGChecker paper — `https://arxiv.org/abs/2408.08067`
- BEIR paper — `https://arxiv.org/abs/2104.08663`
- ColBERTv2 paper — `https://arxiv.org/abs/2112.01488`
- RGB benchmark — `https://arxiv.org/abs/2307.01463`

Bu referanslar kanonik dependency veya otorite değildir. Her fikir Context Vault’un kendi gerçek eval seti, veri güvenliği ve operasyon sınırlarıyla doğrulanmadan production’a alınmayacaktır.
