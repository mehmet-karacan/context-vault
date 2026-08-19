# Aşama 2 — Alembic Migration Runbook

Bu doküman, `alembic/versions/` altındaki üç migration'ı gerçek Postgres
container'ına uygulamak için **kullanıcının kendi terminalinde sırayla**
çalıştırması gereken komutları içerir. Bu dosyayı yazan otomasyon adımı
hiçbir DB komutu çalıştırmadı — aşağıdaki her komut sizin tarafınızdan,
kontrollü biçimde, sırayla çalıştırılmalıdır.

Tüm komutlar repository kökünden değil, **`document-rag-platform/` dizininden**
çalıştırılmak üzere yazılmıştır (docker-compose.yml'nin bulunduğu yer).
Komutlar **yalnızca Windows PowerShell** için yazılmıştır (PS ile başlayan
mavi terminal). Her komutu TEK BAŞINA, tek satır halinde kopyalayıp
Enter'a basın — birden fazla satıra yayılan hiçbir komut yok, bu yüzden
kopyala-yapıştırda satır sonu/backtick sorunu yaşamamalısınız. Aynı
PowerShell penceresini adım 1'den 8'e kadar KAPATMADAN kullanın (Adım 1'de
tanımlanan `$stamp` değişkeni sonraki adımlarda da kullanılıyor).

Migration zinciri (`alembic/versions/`):

```text
b2f1c0a10001  baseline (DDL yok, marker)
      ↓
b2f1c0a10002  şema migration'ı (yeni tablolar + nullable kolonlar, additive)
      ↓
b2f1c0a10003  backfill (document_versions / embedding_profiles / chunk_embeddings verisi)
```

---

## 0. Ön koşul: backend image'ını yeniden build et

`requirements.txt`'ye `alembic==1.13.1` eklendi. Container içinde `alembic`
komutunun çalışabilmesi için image yeniden build edilmeli.

```powershell
docker compose build backend worker
docker compose up -d backend worker
```

**Beklenen çıktı:** build başarıyla biter, `rag-backend` ve `rag-worker`
container'ları `Up` durumuna geçer.

**Sorun olursa:** `pip install` hatası varsa `requirements.txt`'yi kontrol
edin; `alembic==1.13.1`, mevcut `sqlalchemy==2.0.25` ile uyumludur.

---

## 1. Yedek al (pg_dump)

Repo dışı/gitignore'lu bir konuma tam backup alın. `.local-backups/` zaten
`document-rag-platform/.gitignore`'a eklendi (bu görev kapsamında), yani bu
klasöre yazılan dosyalar yanlışlıkla commit edilmez.

```powershell
New-Item -ItemType Directory -Force -Path .local-backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F p' > ".local-backups/pre-phase2-$stamp.sql"
```

**Beklenen çıktı:** `.local-backups/pre-phase2-<tarih>.sql` dosyası oluşur;
boyutu mevcut veri miktarına göre değişir ama 0 byte OLMAMALI. Kontrol edin:

```powershell
Get-Item ".local-backups/pre-phase2-$stamp.sql" | Select-Object Length
```

**Sorun olursa:** Dosya boşsa veya `pg_dump` hata verdiyse **devam etmeyin**;
`docker compose ps` ile `postgres` container'ının `healthy` olduğunu
doğrulayın ve tekrar deneyin.

---

## 2. Migration öncesi satır sayılarını kaydet

```powershell
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT
  (SELECT count(*) FROM projects)                         AS projects,
  (SELECT count(*) FROM documents)                         AS documents,
  (SELECT count(*) FROM chunks)                            AS chunks,
  (SELECT count(*) FROM chunks WHERE embedding IS NOT NULL) AS chunks_with_embedding;
"'
```

Bu dört sayıyı bir kenara not edin (`N_projects`, `N_documents`, `N_chunks`,
`N_chunks_embedded`) — Adım 5'te backfill sonucu bunlarla karşılaştırılacak.

**Beklenen çıktı:** tek satırlık bir sonuç tablosu, hata yok.

---

## 3. Baseline'ı stamp'le

Mevcut DB zaten `projects` / `documents` / `chunks` şemasına sahip (eski
`init_db()` → `Base.metadata.create_all` yoluyla oluşmuş). Alembic'e bunun
`b2f1c0a10001` (baseline) noktasında olduğunu söyleyin:

```powershell
docker compose exec backend alembic stamp b2f1c0a10001
```

**ÖNEMLİ:** `alembic stamp head` KULLANMAYIN. `versions/` klasöründe zaten
üç revizyon var; "head" bunların sonuncusu (`b2f1c0a10003`) demektir ve DB'yi
sanki şema+backfill zaten uygulanmış gibi işaretler — bu YANLIŞ ve gerçek
migration'ların sessizce atlanmasına yol açar. Her zaman açık revizyon id'si
kullanın: `b2f1c0a10001`.

**Beklenen çıktı:** `INFO [alembic.runtime.migration] Running stamp_revision
... -> b2f1c0a10001`. Hata yok.

**Doğrulama:**

```powershell
docker compose exec backend alembic current
```

Çıktı `b2f1c0a10001 (head)` DEĞİL, sadece `b2f1c0a10001` göstermeli (henüz
head'de değilsiniz, bu doğru).

**Sorun olursa:** `relation "alembic_version" already exists` gibi bir hata
alırsanız, DB daha önce başka bir alembic kurulumuyla stamp'lenmiş olabilir;
`docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT * FROM alembic_version;"'`
ile mevcut durumu kontrol edip devam etmeden önce durumu anlayın.

---

## 4. Şema ve backfill'i uygula

```powershell
docker compose exec backend alembic upgrade head
```

Bu tek komut sırayla `b2f1c0a10002` (şema) ve `b2f1c0a10003` (backfill)
migration'larını uygular.

**Beklenen çıktı:** İki adet `Running upgrade ... -> ...` satırı, sonda hata
yok. `alembic current` artık `b2f1c0a10003 (head)` göstermeli.

**Sorun olursa:** Bir hata alırsanız DB muhtemelen kısmi bir durumda kalır
(Postgres DDL genelde transactional olduğundan tek migration içindeki adımlar
ya tamamen uygulanır ya da hiç uygulanmaz, ama iki migration arasında
durabilir). Hata mesajını tam olarak kaydedin, **downgrade komutlarını
deneyin (Adım 6)**; downgrade da başarısız olursa Adım 1'deki backup'tan
restore edin:

```powershell
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
Get-Content ".local-backups/pre-phase2-$stamp.sql" | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

---

## 5. Backfill'i doğrula

```powershell
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT
  (SELECT count(*) FROM document_versions)                                   AS versions,
  (SELECT count(*) FROM documents WHERE active_version_id IS NULL)           AS docs_without_active_version,
  (SELECT count(*) FROM chunks WHERE version_id IS NULL)                     AS chunks_without_version,
  (SELECT count(*) FROM embedding_profiles WHERE is_active = true)           AS active_profiles,
  (SELECT count(*) FROM chunk_embeddings)                                    AS chunk_embeddings;
"'
```

**Beklenen çıktı (Adım 2'de not ettiğiniz sayılarla karşılaştırın):**

| Kolon | Beklenen değer |
|---|---|
| `versions` | `N_documents` ile aynı |
| `docs_without_active_version` | `0` |
| `chunks_without_version` | `0` |
| `active_profiles` | `1` |
| `chunk_embeddings` | `N_chunks_embedded` ile aynı |

**Sorun olursa:** Sayılar eşleşmiyorsa migration'ı tekrar çalıştırmak
güvenlidir — her iki backfill sorgusu da `WHERE NOT EXISTS` / `IS NULL`
korumalı, yani `alembic upgrade head`'i tekrar çalıştırmak (zaten head'deyken
no-op olur) veri çoğaltmaz. Sayılar hâlâ eşleşmiyorsa Adım 1'deki backup'tan
restore edip hatayı bu dosyaya/`AKTIF_GOREV.md`'ye not ederek durun.

---

## 6. Downgrade testi

Geri alma yolunun gerçekten çalıştığını doğrulayın (önce backfill'i, sonra
şemayı geri alın):

```powershell
docker compose exec backend alembic downgrade b2f1c0a10002
```

**Doğrulama (backfill geri alındı, şema hâlâ duruyor olmalı):**

```powershell
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT
  (SELECT count(*) FROM document_versions) AS versions,
  (SELECT count(*) FROM chunk_embeddings)   AS chunk_embeddings,
  (SELECT count(*) FROM documents WHERE active_version_id IS NOT NULL) AS docs_with_active_version;
"'
```

**Beklenen çıktı:** üçü de `0`. Şema tabloları (`document_versions` vb.) hâlâ
var, sadece boş.

Şimdi baseline'a kadar tamamen geri alın:

```powershell
docker compose exec backend alembic downgrade b2f1c0a10001
```

**Doğrulama (şema tamamen kaldırıldı):** interaktif `psql` oturumuna girip
tabloları gözle kontrol edin (string literal içeren tek satırlık sorguları
PowerShell/sh çift tırnak iç içeliğinden kaçınmak için bilerek burada
interaktif yapıyoruz):

```powershell
docker compose exec -it postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

`psql` içinde:

```sql
\dt
\d documents
\d chunks
\q
```

**Beklenen çıktı:** `\dt` listesinde `document_versions`, `source_files`,
`document_artifacts`, `ingestion_jobs`, `ingestion_events`,
`embedding_profiles`, `chunk_embeddings`, `conversations`, `messages`,
`message_citations` tablolarından HİÇBİRİ görünmemeli — yalnızca `projects`,
`documents`, `chunks` kalmalı. `\d documents` ve `\d chunks` çıktısında
`source_type`, `active_version_id`, `version_id`, `search_vector` gibi Aşama
2 kolonları görünmemeli (orijinal kolonlara dönmüş olmalı).

**Sorun olursa:** Downgrade bir `ForeignKeyViolation` veya benzeri hata
verirse, aradaki süreçte migration dışından (elle) veri eklenmiş olabilir.
Adım 1'deki backup'tan restore edin.

---

## 7. Tekrar ileri al

```powershell
docker compose exec backend alembic upgrade head
```

**Beklenen çıktı:** Adım 4 ile aynı — iki `Running upgrade` satırı, hata
yok. Adım 5'teki doğrulama sorgularını tekrar çalıştırıp aynı sonuçları
aldığınızı teyit edin.

---

## 8. Backend'i yeniden başlat ve uçtan uca test et

```powershell
docker compose up -d --build backend
```

**Beklenen çıktı:** `rag-backend` yeniden build olur ve `Up` durumuna geçer.
Loglarda başlangıç hatası olmamalı:

```powershell
docker compose logs --tail=50 backend
```

Ardından uçtan uca endpoint testleri:

Windows PowerShell'de `curl` komutu aslında `Invoke-WebRequest` takma adıdır
ve `-X`/`-H`/`-d` bayraklarını desteklemez — bu yüzden aşağıda **gerçek
curl programını** (`curl.exe`) açıkça çağırıyoruz, `curl` YAZMAYIN:

```powershell
curl.exe http://localhost:8000/health
```

```powershell
curl.exe http://localhost:8000/projects
```

```powershell
curl.exe http://localhost:8000/documents
```

```powershell
curl.exe -X POST http://localhost:8000/chat/query -H "Content-Type: application/json" -d '{"query": "merhaba"}'
```

**Beklenen çıktı:**
- `/health` → `200 OK`, DB bağlantısını doğrulayan bir gövde.
- `/projects` → mevcut projelerin listesi (migration öncesiyle aynı sayıda).
- `/documents` → mevcut belgelerin listesi (migration öncesiyle aynı sayıda,
  `status`/`name` alanları değişmemiş olmalı — sadece yeni nullable alanlar
  eklendi).
- `/chat/query` → `answer` ve `sources` içeren bir JSON gövde (gerçek bir
  belge sorusuyla da tekrar deneyip kaynakların döndüğünü doğrulayın).

**Sorun olursa:** Backend başlamıyorsa `docker compose logs backend`'i
inceleyin. `src/models.py`'deki yeni ORM sınıfları mevcut endpoint'lerin
davrandığı hiçbir alanı değiştirmedi (sadece ekledi); `/health`,
`/projects`, `/documents`, `/chat/query` yanıt şemaları migration öncesiyle
birebir aynı kalmalı. Şema farklıysa bu bir regresyon işaretidir — durun ve
raporlayın, kendi başınıza ek düzeltme yapmayın.

---

## Özet: tam komut sırası

```powershell
docker compose build backend worker
docker compose up -d backend worker

New-Item -ItemType Directory -Force -Path .local-backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F p' > ".local-backups/pre-phase2-$stamp.sql"

# (satır sayılarını not edin - Adım 2)

docker compose exec backend alembic stamp b2f1c0a10001
docker compose exec backend alembic upgrade head

# (backfill doğrulama - Adım 5)

docker compose exec backend alembic downgrade b2f1c0a10002
docker compose exec backend alembic downgrade b2f1c0a10001
docker compose exec backend alembic upgrade head

docker compose up -d --build backend
```
