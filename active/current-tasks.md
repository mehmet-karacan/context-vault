# Aktif Görevler

**Son güncelleme:** 2026-08-20
**Kanonik plan:** `AKTIF_GOREV.md` (ilerleme kaydı §18)

> Önceki "MVP TAMAMLANDI — 6 aşama tümü yeşil" bloğu gerçek kodla çeliştiği için kaldırıldı. Gerçek durum: **Aşama 0–9 tamamlandı, Aşama 10 devam ediyor.**

## Aşama Durumu

- ✅ Aşama 0–9 tamamlandı (tamamlanan teslimatlar için `done/completed-tasks.md`).
- ⏳ **Aşama 10 — Dokümantasyon, Temizlik ve Son Aktivasyon** devam ediyor:
  - ✅ Durum dokümanları (`context-summary.md`, `IMPLEMENTATION_CHECKLIST.md`, `active/current-tasks.md`, `done/completed-tasks.md`) gerçek koda göre düzeltildi.
  - ✅ Feature flag aktivasyon/doğrulama kontrolü yapıldı (rapor için `IMPLEMENTATION_CHECKLIST.md` bölümüne bakın).
  - ⏳ ADR-005 (repo tarama güvenlik modeli) ve ADR-006 (OCR provider stratejisi) yazılacak.
  - ⏳ Ops runbook'ları yazılacak: re-index, embedding model/profile değişimi, OCR model + language pack kurulumu, repo scan limitleri, backup/restore.
  - ⏳ Eski chunk temizliği (yeni version + eval doğrulaması sonrası, kontrollü).
  - ⏳ Root `README.md` güncellemesi (ayrı görev atanmış olabilir).

## Bilinen / Açık Kalemler

1. **Tree-sitter AST/symbol chunking ertelendi** → kod parser/chunker satır + sembol bildirimli fallback kullanıyor (`infrastructure/chunkers/code_chunker.py`, `infrastructure/parsers/code_parser.py`).
2. **OCR engine'leri kurulmalı** — Docling/Tesseract provider'ları uygulandı; motor kurulu değilse factory fallback'e düşer veya OCR kullanılamaz raporlar.
3. **Uzak reranker** gateway rerank desteği gerektirir; varsayılan `NoopReranker` + `FEATURE_RERANKER=false`.
4. **`FEATURE_REPOSITORY_INGESTION` varsayılanı `true`** — repo/arşiv/klasör ingestion default AÇIK; isterseniz deployment env'de `false` yaparak kapatabilirsiniz.
5. **Rate limiter in-memory** — `RATE_LIMIT_ENABLED=false` (relaxed); üretim için kabul edilebilir.
6. Test venv'deki tek pytest hatası FastAPI 0.141-vs-kilitli-0.109 artefaktı; imajı etkilemiyor.

## Gerçek Sıradaki Adımlar

1. OCR motorlarını (Docling / Tesseract + `tur+eng` language pack) kur ve tarama manuel doğrula.
2. `FEATURE_REPOSITORY_INGESTION=true` ile repo/arşiv/klasör akışını uçtan uca doğrula.
3. ADR-005 ve ADR-006'yı yaz.
4. Ops runbook'larını tamamla (re-index, embedding değişimi, OCR kurulumu, repo scan limitleri, backup/restore).
5. Root `README.md`'yi gerçek durumla eşleştir.
6. Yeni version + eval doğrulamasından sonra eski chunk temizliğini kontrollü yap.
7. `AKTIF_GOREV.md` §18 ilerleme kaydını (koordinatör) final sonuçlarla güncelle.

## Referans

- `done/completed-tasks.md` — tamamlanan Aşama 0–9 teslimatları
- `context-summary.md` — mimari özet ve çalışma durumu
- `IMPLEMENTATION_CHECKLIST.md` — ayrıntılı checklist + feature flag raporu
