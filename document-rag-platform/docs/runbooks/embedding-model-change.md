# Runbook — Embedding Model / Profil Değişimi

Bu runbook, `EMBEDDING_MODEL` değiştirirken veya yeni bir `EmbeddingProfile`
oluştururken izlenecek kontrollü süreci anlatır. Kod: `src/config.py`,
`src/models.py` (`EmbeddingProfile` / `ChunkEmbedding`),
`src/infrastructure/embeddings/cache.py`, `src/application/reindex_service.py`.

> Kritik kural (AKTIF_GOREV.md §15 / models.py): farklı boyutlu embedding'ler aynı
> indeksli kolonda karıştırılmaz; `chunks.embedding` ve `chunk_embeddings.embedding`
> `Vector(1024)`'dir. Yalnızca `.env` değiştirip eski vektörlerle devam **edilmez** —
> bu kontrollü bir re-index operasyonudur.

## 1. Embedding cache anahtarı

`src/infrastructure/embeddings/cache.py`:

- `profile_config_hash(profile)` — `EmbeddingProfile`'un embedding çıktısını
  etkileyen alanlarından (`provider`, `model`, `dimension`, `distance_metric`,
  `query_prefix`, `passage_prefix`, `profile_version`) deterministik SHA-256 üretir.
- `embedding_cache_key(content_hash, profile_config_hash)` = `{content_hash}:{config_hash}`.

Sonuç: aynı içerik + aynı profil → aynı key → embedding yeniden hesaplanmaz; içerik
veya profil değişirse key değişir ve yeniden embed edilir. Bu, cache'i otomatik olarak
"eski model vektörü" ile karışmaktan korur ve model değişiminde zorunlu yeniden
embed'i doğru şekilde tetikler.

## 2. Değişim öncesi kontrol listesi (AKTIF §13)

1. Postgres backup al (`docs/runbooks/backup-restore.md`).
2. Yeni embedding modelini/gateway'i doğrula (adres + API anahtarı + dimensyon).
3. **Dimensyon değişmiyorsa** yeni profilin aynı `Vector(1024)` kolonuna yazıldığından
   emin ol; değişiyorsa fiziksel ayrılığı şemada sağla / uzmanlaş — kolonda karıştırma.
4. Yeni `EmbeddingProfile` kaydı oluştur (`is_active` dahil).
5. Kontrollü re-index ile yeni vektörleri üret ve doğrula.
6. Golden eval + manuel doğrulama geçerse aktif sürümü değiştir.
7. Rollback süresi boyunca eski profili pasif tut, fiziksel veriyi inceleme bitmeden silme (§16).

## 3. Yeni profil nasıl oluşur (aktif profil çözümü)

`workers/ingestion_tasks.py:_get_or_create_active_embedding_profile` ve
`reindex_service.py:_get_or_create_active_embedding_profile`:

- Aktif `EmbeddingProfile` (DB'de `is_active=true`) varsa **onu** kullanır — mevcut
  kaydın `model`/`dimension` değeri config'ten farklı olsa bile **unilateral değiştirmez**
  ("model-change kontrollü re-index işidir, ingestion sırasında karar verilmez").
- Aktif profil yoksa `EMBEDDING_MODEL`'den `provider=openai-compatible`,
  `dimension=1024`, `distance_metric=cosine`, `profile_version=1`, `is_active=true`
  olarak oluşturur.
- `config_hash` profil oluşturulurken/kaydedilirken cache alan setinden hesaplanır.

Yeni modeli devreye almak için genellikle önce eski profil `is_active=false` yapılıp
yeni bir `EmbeddingProfile` `is_active=true` olarak eklenir, sonra re-index çalıştırılır.

## 4. Uygulama adımları

Örnek — model değiştir ve kontrollü yeniden indexle:

```powershell
# 1) Backup (her zaman önce)
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
docker compose exec -T postgres pg_dump -U raguser -d rag_platform -F p > ".local-backups/pre-embed-$stamp.sql"

# 2) Ortamda yeni modeli ayarla ve worker/backend'i yeniden başlat
#    .env: EMBEDDING_MODEL=<yeni-model-id>
docker compose up -d --build backend worker
```

Emir sırası (her ortama göre):
1. `EMBEDDING_MODEL`'i `.env`'de güncelle.
2. `Config_hash`'ı değişen profil için ya yeni embedding kullanan bir re-index
   tetikle ya da embedding_profiles'ta yeni aktif profili oluştur.
3. Sorgu tarafı aynı `EMBEDDING_MODEL`'i kullanır (`src/llm.py` → `embed_text` query,
   `embed_texts` passage) — model tutarsızlığında dense tarama yanlış sonuç verir.
4. Re-index edilmemiş belgeler eski vektörleriyle kalır; bu yüzden **tüm** aktif
   kaynakları (repo/arşiv/klasör + belgeler) yeniden indexleyerek tek modelde topla.
5. Eval (golden set) ve manuel doğrulama sonrası eski profili pasif yap.

## 5. İzin verilmeyen davranışlar (AKTIF §15)

- Model değişmeden yalnız `.env` değiştirip **eski vektörlerle devam etmek** — hayır.
- Farklı prefix'lerle üretilmiş belgeleri aynı profil altında karıştırmak — hayır
  (profil, query/passage prefix'lerini `config_hash` içinde taşır).
- Farklı boyutlu embedding'leri aynı `Vector(1024)` kolonuna yazmak — hayır.
- Hatalı profili inceleme tamamlanmadan fiziksel olarak silmek — hayır; önce pasif yap.

## 6. Doğrulama

- Yeni profilin `dimension` değeri dönen vektörlerle eşleşmelidir (uyumsuzsa ingestion
  `ReindexError("Embedding count does not match chunk count")` veya boyut hatasıyla fail).
- `GET /debug/retrieval` ile aynı sorgu yeni profilde anlamlı dense sonuçlar dönmeli.
- `chunk_embeddings`'te yeni `embedding_profile_id` altında satırların arttığını SQL ile
  doğrulayın (bkz. backup-restore runbook'undaki psql erişimi).
