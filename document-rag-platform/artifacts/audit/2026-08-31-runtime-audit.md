# Context Vault — Runtime Gerçeklik Denetimi (2026-08-31)

Aşama 1 §8.2 kapsamında canlı çalışan stack üzerinde toplanan gerçek durum.
Credential veya özel veri içermez. Tarih damgalı bu kayıt, doküman iddiaları ile
gerçek runtime arasındaki farkı raporlar.

## 1. Docker Compose servis durumu

| Servis | Durum |
|---|---|
| rag-postgres | Up (healthy) |
| rag-redis | Up (healthy) |
| rag-minio | Up (healthy) |
| rag-backend | Up |
| rag-worker | Up |

Uyarı: `rag-migrate` isimli orphan container bulundu (compose'da servis olarak
tanımlı değil). Temizlik: `docker compose ... up --remove-orphans`.

## 2. Alembic migration durumu — DRIFT (kritik)

- Veritabanı `alembic_version` = **`b2f1c0a10005`**
- Deploy edilen (main) kod `alembic heads` = **`b2f1c0a10003`**

Sonuç: **DB, mevcut main kodunun migration script'lerinden ileride.** `alembic
current` "Can't locate revision b2f1c0a10005" hatası veriyor. Bu, fix
dalındaki ilerlemenin (daha yeni migration'lar) DB'ye uygulandığını fakat main
kodunda bulunmadığını gösterir. DB'yi sıfırdan yeniden kuramaz / denetleyemez.

## 3. Şema ve index envanteri

14 tablo mevcut. pgvector `0.8.6`, pg_trgm `1.6`.
Aktif embedding profilinde unique partial index var:
`uq_embedding_profiles_single_active`.

Tablolar: alembic_version, chunk_embeddings, chunks, conversations,
document_artifacts, document_versions, documents, embedding_profiles,
ingestion_events, ingestion_jobs, message_citations, messages, projects,
source_files.

## 4. Tablo satır sayıları (pg_stat_user_tables)

| Tablo | n_live_tup |
|---|---|
| chunks | 337 |
| chunk_embeddings | 337 |
| documents | 9 |
| document_versions | 13 |
| document_artifacts | 35 |
| embedding_profiles | 2 (pg_stat n_live_tup=6 eski istatistik; gerçek `select` 2 satır döndürdü) |
| ingestion_jobs | 5 |
| ingestion_events | 34 |
| projects | 9 |
| source_files | 11 |
| conversations / messages / message_citations | 0 |

Not: health/endpoint `documents_count=6, indexed_count=5`.

## 5. Aktif document version bütünlüğü

| Belge | Proje | Durum | Active version | ready versions |
|---|---|---|---|---|
| fixture.zip | ARCHIVE-TEST | indexed | cf77e09c… | 1/1 |
| Hello-World | hello-world-live | indexed | a5a5c216… | 1/1 |
| Hello-World | REPO-TEST | **error** | yok | 0/1 |
| iso.md | iso-3f9d77 | indexed | c16ccd54… | 2/2 |
| ocr_demo.png | OCR-Live-Test… | indexed | 72025328… | 1/1 |
| TTVPN Genel GTD v2.0.docx | TTVPN | indexed | b88388c2… | 1/1 |

## 6. Aktif embedding profili

- `53de0ca0-fe87-4f06-a91f-62ac3643b850` — provider `t`, model
  `openai/BAAI/bge-m3`, 1024, cosine, `is_active=true`, `config_hash=NULL`
- `dbd6744d-…` — model `m2`, 1024, cosine, inaktif

Not: `config_hash` boş; profil kimlik/hash denetimi (AKTIF bulgu #5 / Aşama 2)
henüz tam değil.

## 7. Orphan / tutarlılık raporu

- **2 chunk'ın `version_id`'si dokümanın `active_version_id`'sinden farklı.**
  Çapraz-sürüm sızıntısı riski (AKTIF bulgu #4). Aşama 2'de ele alınmalı.
- 1 belge `error` durumda: `fd40181a` — `UnsupportedFileTypeError:
  unsupported file: extension='' mime=None` (repository ingest sırasında
  uzantısız dosya).
- Stuck job yok: ingestion_jobs %4 completed, %1 failed.

## 8. Bağımlılık lock özeti

- Backend: `services/backend/requirements.txt` + `requirements-dev.txt`
- Frontend: `apps/web/package-lock.json`
- Hash/SBOM/otomatik security scan yok (AKTIF bulgu #33 → Aşama 11 / §8.3).

## 9. API smoke testi

- `GET /health` → `{status: healthy, documents_count: 6, indexed_count: 5}`
- `GET /document` → 200 (belge listesi)
- `POST /chat/query` → 200, `answerable=false` (no-answer yolu çalışıyor,
  uydurma yanıt üretilmedi)

Not: API route'ları kök seviyede (`/documents`, `/projects`, `/chat/query`);
`/api/v1` öneki yok. `/chat/query` şemasında `project_id` opsiyonel — AKTIF
bulgu #1 (fail-closed proje izolasyonu) bağlamında dikkat.

## 10. Retrieval baseline

Gerçek eval rampası (Aşama 6) henüz bağlı değil; bu kayıt yalnız canlı
no-answer smoke'udur. Recall/nDCG/leakage baseline'ı Aşama 6'da üretilecek.
Fake runner kalite kanıtı sayılmaz.

## Öncelikli aksiyonlar

1. Alembic drift (`b2f1c0a10005` vs `b2f1c0a10003`) çöz: fix dalı migration
   setini main ile uzlaştır.
2. Cross-version sızıntıya yol açan 2 orphan chunk'ı temizle/doğrula.
3. `UnsupportedFileTypeError` (uzantısız dosya) ingest davranışını netleştir.
4. `config_hash` doldur; profil sözleşmesini Aşama 2'de sıkılaştır.
5. `rag-migrate` orphan container'ı temizle.
