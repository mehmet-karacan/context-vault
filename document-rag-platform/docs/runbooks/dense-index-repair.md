# Runbook — Dense index repair / backfill

Bu runbook, `chunk_embeddings` (canonical dense kaynak, AKTIF_GOREV.md §8.9) boş
kalan dokümanlar için **telafi amaçlı** DB backfill'ini ve arka plandaki sertleşmeyi
anlatır. Kod tarafında düzeltme yapılmaz; yalnızca eksik dense vektörlerin
`chunk_embeddings`'e taşınmasıdır.

## 1. Ne zaman gerekir

Legacy / sync-path ingestion, dense vektörü yalnızca HNSW-indexed
`chunks.embedding` sütununa yazar ve o chunk için `chunk_embeddings` tablosunu
**boş** bırakır. Query-time dense retriever'ı eski haliyle yalnızca
`chunk_embeddings` boşken `chunks.embedding`'e düşüyordu; canonical kaynaktaki bir
chunk varken `chunk_embeddings`'de satırı olmayan dokümanlar bu yüzden **no-answer**
verir. Tespit: `chunks.embedding` dolu fakat aynı `chunk_id` için `chunk_embeddings`
satırı olmayan chunk'ların varlığı (aşağıdaki backfill'in `WHERE` koşulu).

## 2. İdempotent backfill SQL

Kanıtlanmış tek seferlik backfill, eksik satırları canonical tabloya kopyalar:

```sql
INSERT INTO chunk_embeddings (chunk_id, embedding_profile_id, embedding, created_at)
SELECT c.id, <active_profile_id>, c.embedding, now()
FROM chunks c
LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id
WHERE ce.chunk_id IS NULL;
```

- `<active_profile_id>`: `embedding_profiles` tablosundaki **aktif** satırın id'si
  (bge-m3, dim 1024). config.py'deki `EMBEDDING_MODEL` ile eşleşen aktif profili
  kullanın.
- **Güvenli tekrar çalıştırılabilir:** `LEFT JOIN ... WHERE ce.chunk_id IS NULL`
  yalnızca `chunk_embeddings`'de karşılığı olmayan chunk'ları ekler; daha önce
  backfill edilmiş satırlar etkilenmez.
- `c.embedding` NULL olan (sync-path) chunk'lar yalnızca `chunks.embedding`'e
  yazılmadığı için bu sorgu onları atlar; doküman düzeyinde değil, chunk satırı
  düzeyinde çalışır.

## 3. Query-time retriever sertleşmesi (commit 63daf72)

Kod, `src/infrastructure/retrieval/dense.py` içindeki `DenseVectorRetriever.search`
ile sertleştirildi (commit `63daf72`): retriever artık yalnızca boş-fallback yapmak
yerine hem `chunk_embeddings` **hem de** HNSW-indexed `chunks.embedding`'den aday
toplar, `chunk_id` ile de-dupe eder, daha yüksek skoru korur ve hangi fiziksel
kaynaktan geldiğini `metadata["source"]` olarak etiketler. Böylece `chunk_embeddings`
kapsamı kısmi olsa bile `chunks.embedding` içeriği maskelenmez.

Bu runtime sertleşmesi sayesinde yukarıdaki backfill **düzeltici/bakım** adımıdır;
yeni async-ingested dokümanlar zaten doğrudan `chunk_embeddings`'e yazar. Canonical
kaynak `chunk_embeddings` olmaya devam eder (AKTIF_GOREV.md §8.9).
