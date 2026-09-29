# Context Vault

Context Vault; belge, görsel, Git deposu, arşiv ve izinli klasörleri indeksleyen,
kaynak göstererek yanıt üreten bir RAG platformudur. FastAPI ve Celery tabanlı
servisler, PostgreSQL/pgvector, Redis ve MinIO ile çalışır; web arayüzü Next.js
üzerindedir.

## Kanonik uygulama dizini

Uygulamanın gerçek kaynak kodu, Docker Compose yapılandırması ve kurulum talimatları **`document-rag-platform/`** altındadır. Kurulum ve çalıştırma için oraya bakın:

```bash
cd document-rag-platform
```

Ayrıntılı kurulum, yapılandırma ve API bilgisi için
[`document-rag-platform/README.md`](document-rag-platform/README.md) dosyasına
bakın. Geliştirme ortamında servisler Docker Compose ile, web arayüzü ise
`document-rag-platform/apps/web/` dizininden ayrı başlatılır. Yükleme, yeniden
indeksleme, OCR, depo tarama ve yedekleme işlemlerinin yönergeleri
[`document-rag-platform/docs/runbooks/`](document-rag-platform/docs/runbooks/)
altındadır.

Kullanılmayan kök uygulama iskeletleri kaldırılmıştır. Tarihsel görev ve durum
dosyaları yalnız insan görünümüdür; çalışan sistemin kanıtı değildir.

## Doğrulanmış durum

Mevcut checkout için machine-readable durum üretmek üzere:

```bash
python scripts/generate_verified_status.py
```

Çıktı `status/verified-state.json` dosyasına yazılır. Bu dosya exact checkout
SHA'sını ölçen yerel/CI artifact'idir ve Git'e eklenmez. Eksik uzak CI/ruleset
kanıtı veya dirty tree varsa `verified` değeri `false` olur.

## Aktif görev planı

Repo kökündeki [`AKTIF_GOREV.md`](AKTIF_GOREV.md) tek kanonik görev ve ilerleme kaydıdır. Eski
checklist, roadmap ve özetlerdeki tamamlanma iddiaları kanıt değildir.
