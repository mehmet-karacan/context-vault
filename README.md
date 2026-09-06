# Context Vault

Bu repository, RAG (Retrieval-Augmented Generation) tabanlı bir belge/kod sohbet platformu üzerinde çalışıyor.

## Kanonik uygulama dizini

Uygulamanın gerçek kaynak kodu, Docker Compose yapılandırması ve kurulum talimatları **`document-rag-platform/`** altındadır. Kurulum ve çalıştırma için oraya bakın:

```bash
cd document-rag-platform
```

Ayrıntılı talimatlar için `document-rag-platform/README.md` dosyasına bakın. Operasyonel
runbook'lar (upload/ingestion, re-index, embedding değişimi, OCR, repository scan
limitleri, backup/restore) `document-rag-platform/docs/runbooks/` altındadır.

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

Repo kökündeki **`AKTIF_GOREV.md`** tek kanonik görev ve ilerleme kaydıdır. Eski
checklist, roadmap ve özetlerdeki tamamlanma iddiaları kanıt değildir.

## Lisans durumu

Copyright © 2026 Mehmet Karacan. Tüm hakları saklıdır.

Bu public repository için açık kaynak veya başka bir kullanım lisansı
verilmemektedir. Kaynak kodun görüntülenebilir olması; kopyalama, değiştirme,
dağıtma, alt lisanslama ya da ticari kullanım izni verildiği anlamına gelmez.
Yazılı izin alınmadan bu hakların hiçbiri kullanılamaz. Üçüncü taraf bileşenler
kendi lisanslarına tabidir.
