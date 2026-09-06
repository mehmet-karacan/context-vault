# Runbook — Upload, ingestion job, recovery ve GC

Bu runbook belge, repository, archive ve directory kaynaklarının tek üretim
hattındaki işletim kurallarını açıklar. Kanonik servis
`src/application/ingestion_orchestrator.py`; API route'ları parse, chunk,
embedding veya index işlemi yapmaz.

## Kabul ve teslim

1. Kaynak MIME/magic/structure, boyut ve içerik politikasıyla doğrulanır.
2. Riskli credential/private-key içeriği object storage veya remote provider'a
   gönderilmeden quarantine edilir.
3. Kabul edilen object deterministic `staging/` anahtarına AES-256-GCM ile
   şifrelenerek yazılır ve okunarak checksum doğrulanır.
4. `Document`, immutable `DocumentVersion`, policy, `IngestionJob`, artifact,
   storage registry ve `OutboxEvent` aynı PostgreSQL transaction'ında yazılır.
5. Commit sonrasında dispatcher outbox kaydını queue'ya teslim eder. Publish
   hatası event'i `failed` bırakır; stale claim veya normal retry yeniden teslim
   eder.

Varsayılan ve tek ürün davranışı asenkron job'dır. Sync parser/chunker fallback
ve `FEATURE_ASYNC_INGESTION` dual path'i yoktur.

## Job ve attempt yaşam döngüsü

Job durumları:

```text
queued -> running -> completed
                  -> retrying -> running
                  -> failed
                  -> cancelled
```

Stage sırası:

```text
validating -> storing -> parsing -> chunking -> embedding -> indexing -> activating
```

Worker etki öncesi atomik claim alır. `lease_owner`, `lease_expires_at`,
heartbeat, attempt ve stage receipt PostgreSQL'dedir; Celery task id yalnız
attempt metadata'sıdır. Süresi dolan lease `reconcile_stale_leases` ile
`retrying` durumuna alınır. Aynı inbox/outbox idempotency key unique constraint
ile korunur.

Cancellation için:

```text
POST /api/v1/ingestion-jobs/{job_id}/cancel?project_id={project_id}
```

Queued/retrying job hemen terminal `cancelled` olur. Running job cancel isteğini
kaydeder ve yalnız güvenli stage sınırında durur. Completed/failed job yeniden
başlatılmaz.

## Artifact ve aktivasyon

Staging original, checksum doğrulamasından sonra version/artifact/checksum'dan
türetilen immutable final anahtara taşınır. Yeni version bağımsız candidate
olarak normalize edilir, chunk/embedding/index invariant'ları sağlanır ve
`documents.active_version_id` yalnız compare-and-swap transaction'ında değişir.
Başarısız candidate eski active version'ı değiştirmez.

Uzak embedding yalnız policy izin verirse ve job attributable actor/workspace
taşıyorsa çağrılır. Çağrıdan önce içeriksiz
`ingestion.remote_embedding_authorized` audit event'i commit edilir.

## Encryption-at-rest

Her yeni MinIO object'i uygulama katmanında AES-256-GCM envelope ile
şifrelenir. `OBJECT_STORAGE_ENCRYPTION_KEY`, deployment secret store'dan gelen
base64 kodlu 32-byte anahtardır; eksik veya bozuk anahtar startup'ı durdurur.
Nonce her write için rastgele 12 byte'tır ve storage key AAD olarak bağlanır.

`OBJECT_STORAGE_ALLOW_LEGACY_PLAINTEXT_READS` varsayılan `false` olmalıdır.
Yalnız doğrulanmış eski object geçişinde süreli olarak açılabilir; yeni write'lar
bu durumda da şifreli kalır. Anahtar rotasyonu ayrı backup/restore ve re-encrypt
planı gerektirir.

## Silme, retention ve legal hold

`DELETE /api/v1/documents/{id}` fiziksel silme yapmaz. Belgeyi soft-delete eder
ve bağlı referenced object'lere `STORAGE_RETENTION_DAYS` sonrasını
`retention_until` olarak yazar. Tekrarlanan delete idempotent success döner.

GC için üç kapı birlikte geçmelidir:

- retention süresi dolmuş olmalı;
- object `legal_hold=false` olmalı;
- version'a bağlı hiçbir `message_citation` bulunmamalı.

Her citation, konuşma/citation retention kararı kaldırılana kadar version için
referans hold sayılır. Herhangi bir version object'i hold altında kalıyorsa o
version'ın chunk/vector/artifact kayıtları korunur. Son object güvenle
silindiğinde chunk embedding'leri FK cascade ile, chunk ve artifact'ler aynı GC
transaction'ında temizlenir. `dry_run=true` byte silmez ve planned receipt
üretir; tekrar çalıştırma deleted kayıtları seçmez.

## Recovery komutları

Uygulama servisleri Python çağrısı olarak çalışır:

- `reconcile_stale_leases(db)` — expired attempt/job lease'lerini retryable yapar.
- `sweep_orphan_staging(db, storage, dry_run=True)` — grace süresi dolmuş,
  registry dışı `staging/` object'lerini raporlar.
- `storage_reconciliation_report(db, storage)` — missing/orphan anahtar farkını
  salt okunur üretir.
- `run_storage_gc(db, storage, dry_run=True)` — retention/citation/legal-hold
  kapılarını geçebilen object'leri receipt ile planlar.

Önce dry-run çalıştırılır ve yalnız receipt/key hash incelenir. Public loga raw
storage key, belge içeriği, secret veya connection string yazılmaz. Fiziksel GC
ancak doğru environment, doğrulanmış backup/restore ve açık operasyon planıyla
`dry_run=False` çalıştırılır.

## Doğrulama

A6 regression kanıtı şu davranışları kapsar: upload idempotency, outbox publish
recovery, stale lease, safe cancellation, staging orphan sweep, her kritik
stage sonrası fault/retry yakınsaması, reindex active-version koruması,
extension'sız source routing, secret quarantine, encrypted real-MinIO E2E,
delete retention, citation/legal hold ve idempotent GC.
