# Tamamlanan Görevler

## Aşama 0 — Kapsam ve Mimari Kararlar ✅

- ✅ Repository oluştur
- ✅ Monorepo dizin yapısını hazırla
- ✅ Mimari karar kayıtları (ADR'ler) oluşturuldu (6 adet)
- ✅ Desteklenen dosya türleri belirlendi (PDF, DOCX, TXT, MD)
- ✅ Dosya boyutu limiti: 10MB
- ✅ Başarı metrikleri tanımlandı

## Aşama 1 — Temel Altyapı ✅

- ✅ Next.js projesi oluşturuldu
- ✅ FastAPI uygulaması oluşturuldu
- ✅ PostgreSQL + pgvector container tanımlandı
- ✅ Redis ve MinIO container tanımlandı
- ✅ Migration altyapısı (Alembic) kuruldu
- ✅ Temel tablolar (13 tablo) oluşturuldu
- ✅ Health-check endpoint'leri eklendi
- ✅ .env.example oluşturuldu
- ✅ CI/CD pipeline (GitHub Actions) tanımlandı
- ✅ Makefile oluşturuldu

## Aşama 2 — Dosya Yükleme ve Canlı Durum ✅

- ✅ Document oluşturma endpoint'i
- ✅ Presigned URL üretimi
- ✅ Upload ekranı (drag & drop)
- ✅ Upload ilerleme göstergesi
- ✅ Upload complete endpoint'i
- ✅ Metadata doğrulaması
- ✅ ingestion_jobs ve ingestion_events tabloları
- ✅ Celery worker yapısı
- ✅ State machine (12 durum)
- ✅ SSE endpoint'i
- ✅ Retry endpoint'i
- ✅ Kullanıcı dostu hata ekranları

## Aşama 3 — Gerçek Belge İşleme ✅

- ✅ Parser adapter'ı (Docling)
- ✅ PDF/DOCX/TXT/MD parser'ları
- ✅ Normalize belge çıktısı
- ✅ Yapısal chunker (500-800 token, %10-15 overlap)
- ✅ Token sayımı (tiktoken)
- ✅ Chunk metadata'sı
- ✅ Embedding provider adapter'ı (BGE-M3)
- ✅ Batch embedding
- ✅ pgvector kayıtları (HNSW index)
- ✅ Full-text search kolonu (GIN index)
- ✅ Gerekli indeksler
- ✅ Idempotent retry
- ✅ Parse ve embedding hatalarının ayrılması
- ✅ Belge sürümleme

## Aşama 4 — Sohbet ve Kaynak Gösterme ✅

- ✅ Conversation ve message tabloları
- ✅ Sohbet oluşturma ekranı
- ✅ Belge seçimi
- ✅ Vector retrieval
- ✅ Lexical retrieval
- ✅ RRF fusion
- ✅ Context builder
- ✅ Prompt şablonu (kaynak zorunluluğu)
- ✅ Chat model adapter'ı (Qwen3.5-27B-FP8)
- ✅ Streaming cevap
- ✅ message_citations
- ✅ Kaynak kartları
- ✅ Sayfa numarası gösterimi
- ✅ Abstention davranışı
- ✅ Sohbet geçmişi
- ✅ İptal edilebilir generation

## Aşama 5 — Kalite ve Değerlendirme ✅

- ✅ Golden dataset (52 Türkçe soru-cevap)
- ✅ Recall@K ölçümü
- ✅ Retrieval evaluator
- ✅ Generation evaluator
- ✅ Test runner + JSON rapor

## Aşama 6 — Güvenlik ve Üretim Hazırlığı (Kısmen)

- ✅ MIME ve magic-byte doğrulaması (kodda)
- ✅ Dosya ve sayfa limitleri (kodda)
- ✅ Parser kaynak limitleri (kodda)
- ⏳ Kimlik doğrulama (TODO)
- ⏳ Workspace ve rol yönetimi (TODO)
- ⏳ PostgreSQL RLS (TODO)
- ⏳ Rate limiting (TODO)
- ⏳ Antivirüs entegrasyonu (TODO)

---

## Proje İstatistikleri

- **Toplam dosya sayısı:** 100+
- **Backend endpoints:** 12
- **Frontend components:** 20+
- **Database tabloları:** 13
- **Migration'lar:** 2
- **Testler:** 50+ golden question
- **ADR'ler:** 6

## Sonraki Adımlar

1. `docker-compose up` ile tüm servisleri başlat
2. Manuel test (PDF yükle → soru sor)
3. Kimlik doğrulama ekle
4. Staging ortamına deploy
5. Gerçek kullanıcı testi