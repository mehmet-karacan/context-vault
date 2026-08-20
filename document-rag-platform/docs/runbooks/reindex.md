# Runbook — Re-index

Bu runbook, kaynağın (repository / arşiv / klasör) orijinal içeriğinden **yeniden
yükleme yapmadan** yeni bir `DocumentVersion` üretmeyi ve atomik aktivasyonu anlatır.
Kod: `src/application/reindex_service.py` (`ReindexService`), API:
`src/api/v1/repositories.py` (`POST /documents/{id}/refresh`).

Belgeler (PDF/DOCX/TXT) için re-index şu an doğrudan bir endpoint değildir;
`POST /documents/upload` her zaman yeni bir version üretir. Alttaki yönetim
(repository/arşiv/klasör) incremental re-index içindir.

## 1. Re-index ne zaman gerekir (AKTIF_GOREV.md §13)

Re-index tetikleyicileri: parser_profile değişti, chunker_profile değişti,
**embedding_profile değişti**, OCR provider/config değişti, source revision değişti,
normalize model şeması değişti. Embedding modeli değişimi için ayrıca
`docs/runbooks/embedding-model-change.md`'ye bakın.

## 2. Akış

`ReindexService.run(db, document, scan)` (reindex_service.py:302):

1. Önceki aktif version'ın dosyalarını `relative_path -> SourceFile` ve
   `source_file_id -> chunk'lar` olarak yükler.
2. Scan edilen her dosya için `content_hash` hesaplar:
   - Önceki version'daki `content_hash` aynıysa → **yeniden parse/embed etmez**,
     önceki version'ın chunk'larını yeni version'a kopyalar (`_copy_unchanged_file`).
   - Değişen/yeni dosya → parse → chunk → embed (`_process_changed_file`).
   - Silinen dosyalar yeni version'da görünmez.
3. Yeni `DocumentVersion` (`version_no = max+1`) oluşturulur; aktif embedding
   profili `_get_or_create_active_embedding_profile` ile çözülür (config'teki
   `EMBEDDING_MODEL`'den, yoksa oluşturulur).
4. **Atomik aktivasyon:** yeni version `ready` olur ve `documents.active_version_id`
   ancak **tümü hazır olduktan sonra** değiştirilir (`run` sonundaki commit). Hiçbir
   okuma yarım kalmış bir version'ı görmez.

Dönüş özeti: `files_count`, `files_processed`, `files_copied`, `ignored`,
`deleted_files`, `chunks`.

## 3. Endpoint'ler

```text
POST /repositories/ingest          → yeni repo kaynağı (ilk index)
POST /archives/upload              → yeni arşiv kaynağı
POST /directories/scan             → yeni klasör kaynağı (izinli kök altında)
POST /documents/{document_id}/refresh  → mevcut kaynağı yeniden indexle
GET  /documents/{document_id}/files    → active version dosya listesi
GET  /documents/{document_id}/versions → version geçmişi
```

Tümü `FEATURE_REPOSITORY_INGESTION` arkasındadır (config.py varsayılanı `true`);
devre dışıyken `403` döner.

`POST /documents/{id}/refresh` (repositories.py:387) kaynağın **aktif version'ındaki
scan config'ini** (`scan_config` artifact'ı — repo URL/ref, arşiv checksum veya klasör
alias/relative_path + include/exclude) okur ve kaynağı yeniden tarar:

- `repository` → `GitRepositorySource().scan(origin_uri, ref=..., ...)` ile klon,
  `source_revision=commit SHA`.
- `archive` → MinIO'daki `original` artifact'ından okur, `ArchiveSourceScanner` ile
  yeniden açar, `source_revision=SHA-256`.
- `directory` → `resolve_allowed_scan_path` ile izinli kök doğrulaması + yeniden tarar.

Her durumda `ReindexService` devreye girer; config yeni version'a `scan_config`
artifact'ı olarak saklanır.

## 4. content_hash skip mantığı

Incremental davranış `content_hash` üzerinden çalışır:

- Aynı content_hash → dosya içeriği değişmemiş → chunk'lar kopyalanır (embed'e
  gerek yok). Bu, "tek dosya değiştiğinde bütün repository yeniden embed edilmez"
  kabul kriterinin temelidir.
- `ScanResult.source_revision` (commit SHA / arşiv checksum) version'a
  `source_revision` olarak yazılır.

## 5. Doğrulama

```powershell
# Aktif version öncesi kaynak sürümü
curl.exe http://localhost:8000/documents/<document_id>/versions

# Yeniden indexle
curl.exe -X POST http://localhost:8000/documents/<document_id>/refresh

# files_processed (değişen) vs files_copied (değişmeyen) yanıtını kontrol et
curl.exe http://localhost:8000/documents/<document_id>/versions
```

Beklenti: değişmeyen dosyalar `files_copied`'a, değişenler `files_processed`'a düşer;
yeni `version_no` artar ve `is_active` yalnız yeni version'da `true` olur.

## 6. Notlar / sınırlamalar

- Re-index sırasında kod hiçbir koşulda çalıştırılmaz (`GitRepositorySource` git'i
  `shell=False` ve sabit argv ile çağırır; hook/LFS/submodule/script yok, bkz.
  `docs/runbooks/repository-scan-limits.md`).
- Eski active version korunur; rollback süresi boyunca silinmez (§16).
- Farklı boyutta embedding gerekmiyorsa mevcut aktif profil kullanılır; model/profil
  değişimi **ayrı** kontrollü bir operasyondur (embedding-model-change.md).
