# Runbook — Upload ve Ingestion Job Yönetimi

Bu runbook, belge yüklemeden (`POST /documents/upload`) asenkron ingestion job'ının
tamamlanmasına kadar olan süreci, job yaşam döngüsünü, izlemeyi, retry ve worker
yeniden başlatma davranışını gerçek kod/modüllerle eşleşerek anlatır.

İlgili kod:
- `services/backend/src/api/v1/documents.py` — upload endpoint'i (`_upload_document_async`)
- `services/backend/src/workers/ingestion_tasks.py` — `process_ingestion_job` ve `run_ingestion_job`
- `services/backend/src/workers/celery_app.py` — worker-safety konfigürasyonu
- `services/backend/src/config.py` — `FEATURE_ASYNC_INGESTION`, `INGESTION_*`
- `services/backend/src/api/v1/ingestion_jobs.py` — `GET /ingestion-jobs/{id}` + `/events`

---

## 1. Asenkron yükleme nasıl çalışır

Varsayılan davranış `FEATURE_ASYNC_INGESTION=true`'dır (config.py + `.env.example`).
Bu modda `POST /documents/upload` isteği anında döner ve uzun parse/chunk/embed
sürecini beklemez:

1. `_upload_document_async` (documents.py:178) `guard_upload` ile MIME/magic/size
   doğrulaması yapar (`src/infrastructure/security/file_validation.py`).
2. Aynı transaction'da `Document` (`status=uploaded`), ilk `DocumentVersion`
   (`version_no=1`, `status=pending`) ve `IngestionJob` (`status=queued`,
   `stage=validating`) kaydı oluşturulur.
3. Orijinal dosya MinIO'ya yazılır: `object_keys.original_key(...)` →
   `projects/{project_id}/documents/{document_id}/versions/{version_id}/original/{safe_filename}`
   (src/infrastructure/storage/object_keys.py).
4. Commit sonrası `process_ingestion_job.delay(job_id)` ile Celery task'ı kuyruğa
   atılır (commit düşerse orphan job üretilmez).
5. Yanıt `job_id`, `document_id`, `version_id`, `status: "queued"` içerir ve
   `GET /documents` listesinde belge hemen görünür; `job_status`/`job_stage` alanları
   ilerlemeyi yansıtır.

Senkron fallback: `FEATURE_ASYNC_INGESTION=false` ayarlanırsa aynı istek içinde
parse → chunk → embed → index yapılır (documents.py `upload_document`'ın alt dalı).
Bu, yalnızca geçiş/rollback/debug için korunmuştur.

## 2. Job yaşam döngüsü ve aşamalar

`IngestionJob` (models.py) şu durumlara sahiptir: `queued | running | completed |
failed | cancelled`. `process_ingestion_job` aşağıdaki aşamaları sırayla işletir:

```
validating -> storing -> parsing -> chunking -> embedding -> indexing -> activating
```

(stage sırası `ingestion_tasks.py:STAGES`; `models.py`'de daha geniş enum — `ocr`,
`normalizing` — Aşama 3+ parser katmanı içindir.)

- `validating`: `DocumentVersion.storage_key` dolu mu kontrol edilir.
- `storing`: orijinal dosya MinIO'dan çekilir, `document_artifacts`'a `original`
  artifact'ı yazılır.
- `parsing`: geçici dosyadan metin çıkarılır, `normalized_md` artifact'ı saklanır.
- `chunking`: `chunk_text` ile parçalanır, credential değerleri redact edilir
  (`src/infrastructure/security/redaction.py`).
- `embedding`: `embed_texts(..., instruction=PASSAGE_INSTRUCTION)` çağrılır.
- `indexing`: version'ın chunk'ları idempotent "wipe + rewrite" ile yeniden yazılır,
  `embedding_profiles`'daki aktif profile bağlanır, `search_vector`/`identifiers`
  kurulur.
- `activating`: version `ready` olur, `documents.active_version_id` atomik değişir,
  job `completed`.

Her aşama `ingestion_events`'e bir `IngestionEvent` (stage, status, message) yazar
(`_emit_event`). Redis yalnız canlı-iletim katmanıdır; **kalıcı kayıt PostgreSQL'dedir.**

## 3. İzleme

```powershell
# Job genel durumu
curl.exe http://localhost:8000/ingestion-jobs/<job_id>

# Job olay/ilerleme geçmişi
curl.exe http://localhost:8000/ingestion-jobs/<job_id>/events

# Belge + en son job durumu
curl.exe http://localhost:8000/documents
curl.exe http://localhost:8000/documents/<document_id>/status
```

`GET /documents` yanıtı `job_id`, `job_status`, `job_stage`, `job_error` alanlarını
içerir (`documents.py:serialize_document`; yalnızca asenkron geçmişi olan belgelerde).

## 4. Worker yeniden başlatma güvenliği

`celery_app.py` şu ayarları kullanır (Aşama 2 kabul kriteri: "worker yeniden
başlatılsa job verisi kaybolmaz"):

- `task_acks_late=True` — mesaj yalnızca görev başarılı olduktan sonra ack'lenir.
- `task_reject_on_worker_lost=True` — çöken worker'ın görevi kuyruğa geri döner.
- `task_acks_on_failure_or_timeout=True` — max_retries tükenince final hata ack'lenir.
- `worker_prefetch_multiplier=1` — tek uzun job diğer kuyruktakileri kilitlemez.
- `task_soft_time_limit` / `task_time_limit` → `INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS`
  (3600) / `INGESTION_TASK_TIME_LIMIT_SECONDS` (3700).

Ayrıca `run_ingestion_job` idempotenttir: `indexing` aşaması önce version'ın eski
chunk'larını siler (`_clear_existing_chunks_for_version`), sonra yeniden yazar; bu
sayede aynı job yeniden alınsa bile duplicate chunk/embedding oluşmaz. Job zaten
`completed` gelirse no-op döner (`skipped=true`).

## 5. Retry davranışı (INGESTION_*)

`process_ingestion_job` (ingestion_tasks.py:486) `autoretry` kullanır:

- `retry_kwargs={"max_retries": INGESTION_MAX_RETRIES}` — varsayılan `3`.
- `retry_backoff=INGESTION_RETRY_BACKOFF_SECONDS` (10s) , `retry_backoff_max=300`,
  `retry_jitter=True` — üstel backoff.
- **Kalıcı hatalar** (`IngestionJobError`, `StageTransitionError`) asla yeniden
  denenmez: `validating`'deki doğrulama hatası (bulunamayan job/version/document,
  boş içerik, sıfır chunk, embedding sayısı uyuşmazlığı).
- **Geçici (transient) hatalar** (gateway timeout, MinIO/DB kıpırtısı)
  `RetryableIngestionError` ile sarılıp retry edilir. Her denemede job geçici olarak
  `failed` işaretlenir, sonra retry `validating`'e geri sarar.

## 6. Sorun giderme

- Job `failed` kaldıysa: `GET /ingestion-jobs/{id}` → `error_code`/`error_message`,
  `GET /ingestion-jobs/{id}/events` son olayı inceleyin.
- Embedding gateway'den 401/404: `LITELLM_BASE_URL`/`LITELLM_API_KEY`/`EMBEDDING_MODEL`
  kontrol edin (config.py + `docker compose logs worker`).
- Worker hiç devreye girmiyorsa: `docker compose ps`'de `worker` "Up" olmalı;
  `celery -A src.workers.celery_app worker -l info` komutunun bootstrap hatasız
  bittiğini loglardan doğrulayın.
- Esnek aşama sırası ihlali `StageTransitionError` → kod seviyesi state hatası,
  otomatik düzeltme yapmayın, rapor edin.
