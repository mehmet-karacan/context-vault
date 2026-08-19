# Baseline Retrieval Metrikleri (Aşama 0)

- Golden dataset: `example/example-golden.jsonl`
- Retrieval sonuçları: `example/example-retrieval-top10.jsonl`
- Toplam soru sayısı: 3
- Recall/MRR hesabına dahil edilen soru sayısı: 2
- Hesap dışı bırakılan (answerable=false veya expected_sources boş) soru sayısı: 1

## Toplam Metrikler

| Metrik | Değer |
|---|---|
| Recall@1 | 0.000 |
| Recall@3 | 0.500 |
| Recall@5 | 0.500 |
| MRR@10 | 0.250 |

## Soru Bazlı Sonuçlar

| id | Recall@1 | Recall@3 | Recall@5 | İlk alakalı rank | MRR katkısı | query |
|---|---|---|---|---|---|---|
| SYNTHETIC-EXAMPLE-001 | False | True | True | 2 | 0.500 | (SENTETIK ORNEK - gercek belge sorusu degildir) PAYMENT_FLAG 1 oldugunda ne olur? |
| SYNTHETIC-EXAMPLE-002 | False | False | False | None | 0.000 | (SENTETIK ORNEK - gercek belge sorusu degildir) Sistemin yedekleme periyodu nedir? |

## Recall/MRR Hesabına Dahil Edilmeyen Sorular (answerable=false / no-answer)

- SYNTHETIC-EXAMPLE-003
