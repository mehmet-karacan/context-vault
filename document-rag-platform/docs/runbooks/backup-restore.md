# Runbook — Backup / Restore

Bu runbook, Context Vault'un iki kalıcı katmanının yedeklenmesini ve geri
yüklenmesini anlatır: **PostgreSQL** (metadata, chunk, vektör, job, citation,
profil) ve **MinIO** (orijinal dosyalar + normalize/OCR artifact'ları).

> Önemli: Tam bir geri yükleme için **iki katman birden** gerekir. Yalnız DB'yi
> geri yükleyip MinIO'yu geri yüklemezseniz `DocumentVersion.storage_key` /
> artifact storage_key'leri boşta kalır; yalnız MinIO'yu geri yüklerseniz DB'deki
> referanslar eksik kalır.

Tüm komutlar `document-rag-platform/` dizininden, Windows PowerShell için yazılmıştır
(docker-compose.yml orada). DB kullanıcısı/veritabanı `.env`'deki `POSTGRES_USER`
(`raguser`) ve `POSTGRES_DB` (`rag_platform`) değerleridir — farklıysa değiştirin.

## 1. Yerel backup konvansiyonu

`.gitignore` (document-rag-platform/.gitignore) `/.local-backups/` içerir — bu
klasöre yazılan pg_dump çıktıları **commit edilmez**. Backup'ı buraya alın:

```powershell
New-Item -ItemType Directory -Force -Path .local-backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
```

## 2. PostgreSQL dump

Fotoğraf (plain SQL, tek dosya):

```powershell
docker compose exec -T postgres pg_dump -U raguser -d rag_platform -F p > ".local-backups/backup-$stamp.sql"
```

Doğrulama (0 byte OLMAMALI):

```powershell
Get-Item ".local-backups/backup-$stamp.sql" | Select-Object Length
```

İsteğe bağlı — sıkıştırılmış custom format (pg_restore ile geri yükleme için):

```powershell
docker compose exec -T postgres pg_dump -U raguser -d rag_platform -F c > ".local-backups/backup-$stamp.dump"
```

MIGRATION_RUNBOOK.md ayrıca migration öncesi belirli satır sayılarını (`projects`,
`documents`, `chunks`, `chunks_with_embedding`) kaydedip sonra karşılaştırmayı
önerir; bu, veri kaybını erken yakalayan iyi bir alışkanlıktır.

## 3. MinIO nesne deposu backup

Orijinal dosyalar ve artifact'lar MinIO'da `MINIO_BUCKET` (varsayılan `context-vault`)
altında. İki yol:

### a) mc (MinIO Client)

```powershell
# Tek seferlik kurulum/konfigürasyon
mc alias set rag http://localhost:9000 "$env:MINIO_ROOT_USER" "$env:MINIO_ROOT_PASSWORD"

# Bucket'ı yerel bir dizine mirror'la
New-Item -ItemType Directory -Force -Path .local-backups/minio-$stamp | Out-Null
mc mirror rag/context-vault ".local-backups/minio-$stamp"
```

### b) S3 uyumlu CLI (aws s3 / rclone)

```powershell
# aws --endpoint-url örneği
aws --endpoint-url http://localhost:9000 s3 sync s3://context-vault ".local-backups/minio-$stamp" --profile context-vault
```

(mc'yi henüz kurmadıysanız ve s3 cli'niz varsa `aws s3 sync` kullanılabilir; amaç
aynı: bucket içeriğini gitignore'lu yerel klasöre çekmek.)

## 4. Restore prosedürü

### 4.1 PostgreSQL restore

Önce şemayı temizle (yalnızca gerçekten geri yüklemek istediğinizde; kullanıcı
verisini kapsam dışı düşünmeden yapın):

```powershell
docker compose exec -T postgres psql -U raguser -d rag_platform -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Sonra dump'ı uygula (plain SQL):

```powershell
Get-Content ".local-backups/backup-$stamp.sql" | docker compose exec -T postgres psql -U raguser -d rag_platform
```

Custom format kullandıysanız:

```powershell
docker compose exec -T postgres pg_restore -U raguser -d rag_platform --clean --if-exists ".local-backups/backup-$stamp.dump"
```

> Not: `pg_dump`/`pg_restore` invocation'ında container-içi `sh -c '...'` ile
> PowerShell'den iç içe tırnak genişletmekten kaçının (MIGRATION_RUNBOOK.md'de
> `option requires an argument -- 'c'` hatasına yol açtığı kayıtlıdır). `-T` ile
> `exec` üzerinden ve .env'deki net `raguser`/`rag_platform` adlarını kullanın.

### 4.2 MinIO restore

```powershell
mc alias set rag http://localhost:9000 "$env:MINIO_ROOT_USER" "$env:MINIO_ROOT_PASSWORD"
mc mirror ".local-backups/minio-$stamp" rag/context-vault
```

veya S3 uyumlu:

```powershell
aws --endpoint-url http://localhost:9000 s3 sync ".local-backups/minio-$stamp" s3://context-vault --profile context-vault
```

### 4.3 Restore sonrası

1. Servisleri yeniden başlat ve durumları doğrula:

   ```powershell
   docker compose restart backend worker
   curl.exe http://localhost:8000/health
   curl.exe http://localhost:8000/health/readiness
   ```

2. Migration'ın en son haliyle uyumlu olduğundan emin ol (daha eski bir dump
   geri yüklenirse migration gerekebilir):

   ```powershell
   docker compose exec backend alembic current
   docker compose exec backend alembic upgrade head   # gerekirse
   ```

3. Örnek bir belge/repo üzerinden retrieval ve chat'i smoke-test et
   (`POST /chat/query` → `answer` + `citations`).

## 5. Ne zaman yedek alınır

- Her migration öncesi (bkz. MIGRATION_RUNBOOK.md Adım 1 — `pre-phase2-$stamp.sql`).
- Embedding modeli/profil değişimi öncesi (bkz. `docs/runbooks/embedding-model-change.md`).
- Periyodik operasyonel yedek olarak hem DB hem MinIO birlikte.

## 6. Notlar

- `.local-backups/` gitignore'lıdır — gerçek müşteri/belge verisini commit etmeyin.
- Backup/restore sırasında kod değiştirilmez; yalnızca veri katmanı dokunulur.
- `int`/`UUID` karışıklığına takılmayın — restore tam snapshot tabanlıdır, deltasız.
