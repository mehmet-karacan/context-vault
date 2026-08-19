# Context Vault

Bu repository, RAG (Retrieval-Augmented Generation) tabanlı bir belge/kod sohbet platformu üzerinde çalışıyor.

## Kanonik uygulama dizini

Uygulamanın gerçek kaynak kodu, Docker Compose yapılandırması ve kurulum talimatları **`document-rag-platform/`** altındadır. Kurulum ve çalıştırma için oraya bakın:

```bash
cd document-rag-platform
```

Ayrıntılı talimatlar için `document-rag-platform/README.md` dosyasına bakın.

Repo kökündeki `apps/`, `services/`, `docs/`, `tests/`, `packages/`, `infra/` dizinleri iskelet/boş placeholder'lardır; kullanılan gerçek kod bunların içinde değil, `document-rag-platform/` altındadır (bkz. `document-rag-platform/docs/cleanup-candidates.md`).

## Aktif görev planı

Repo kökündeki **`AKTIF_GOREV.md`**, uygulamanın kapsamlı dönüşüm planını (ingestion, parsing, chunking, hybrid retrieval, citation, OCR, repo/kod ingestion vb.) ve mevcut ilerleme durumunu tanımlar. Bu proje üzerinde çalışan herkes önce o dosyayı okumalıdır.
