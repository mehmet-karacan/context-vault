# Baseline Retrieval Metrikleri (Aşama 0)

- Golden dataset: `tests/evals/golden/questions.jsonl`
- Retrieval sonuçları: `tests/evals/baseline/retrieval-top10.jsonl`
- Toplam soru sayısı: 25
- Recall/MRR hesabına dahil edilen soru sayısı: 21
- Hesap dışı bırakılan (answerable=false veya expected_sources boş) soru sayısı: 4

## Toplam Metrikler

| Metrik | Değer |
|---|---|
| Recall@1 | 0.905 |
| Recall@3 | 0.905 |
| Recall@5 | 1.000 |
| MRR@10 | 0.929 |

## Soru Bazlı Sonuçlar

| id | Recall@1 | Recall@3 | Recall@5 | İlk alakalı rank | MRR katkısı | query |
|---|---|---|---|---|---|---|
| ttnetsis-001 | True | True | True | 1 | 1.000 | TTNETSIS sisteminde 302 mesajının Contact alanına hangi parametrik hata kodu eklenir? |
| ttnetsis-002 | True | True | True | 1 | 1.000 | BTK taşınmış numaralara ait dosyaları hangi sıklıkla yayınlamaktadır? |
| ttnetsis-003 | True | True | True | 1 | 1.000 | UC-8 Dosya Sorgulama use case'i TTNETSIS ekranından hangi işlemi yapmayı sağlar? |
| ttnetsis-004 | True | True | True | 1 | 1.000 | TTNETSIS ekranından taşıma durumundaki numaraların TTNETSIS sistemine işlenme durumu sorgulanabilir mi? |
| ttnetsis-005 | True | True | True | 1 | 1.000 | TTNETSIS projesi kapsamında hangi mevcut sistemin işlevleri TTNETSIS sistemine aktarılmaktadır? |
| ttnetsis-006 | True | True | True | 1 | 1.000 | UC-6 İşletmeci Numara Yönetimi hangi ekran üzerinden RN bilgisiyle ilgili işlem yapılmasını sağlar? |
| ttsis-001 | True | True | True | 1 | 1.000 | STP'de Tek Numara Servisi'ne geçen bir müşteri numarasını en fazla kaç farklı numaraya yönlendirebilir? |
| ttsis-002 | False | False | True | 4 | 0.250 | Anchor user tablosu örneğinde VOIP Access Code Index değeri kaçtır? |
| ttsis-003 | True | True | True | 1 | 1.000 | MNPLOCNRNG:MODE=number,NUMBER="3123136050" sorgusu hangi Routing number index (RN) değerini döner? |
| ttsis-004 | True | True | True | 1 | 1.000 | MNPFRGNRNG tablosunda 2162160543 numarası sorgulandığında sonuç hangi işletmeciye ait çıkar? |
| ttsis-005 | True | True | True | 1 | 1.000 | UC-1 Format Kontrolü hangi numara üzerinde format kontrolü yapılmasını kapsar? |
| ttsis-006 | True | True | True | 1 | 1.000 | Tek Numara Servisine geçen müşteriler STP'de hangi tabloya tanımlanır ve hangi RN değerini alır? |
| ttsis-007 | True | True | True | 1 | 1.000 | MNPFRGNRNG tablosu ile MNPLOCNRNG tablosu hangi numara bloklarını sorgulamak için kullanılır? |
| ttvpn-001 | True | True | True | 1 | 1.000 | TTVPN projesi kapsamında YSCPE Envanter Sistemi ile ilgili doküman genel olarak neyi anlatmaktadır? |
| ttvpn-002 | True | True | True | 1 | 1.000 | Markalar ekranında sisteme yeni marka tanımlamak için hangi buton kullanılır? |
| ttvpn-003 | True | True | True | 1 | 1.000 | Sistemde tanımlı bir markayı silmek için hangi ekrandaki hangi buton kullanılmalıdır? |
| ttvpn-004 | True | True | True | 1 | 1.000 | Demontaj Türü 'Nakil' ve Demontaj Statüsü 'Açık' olan kayıtlarda hangi tür ürünler talepte birden fazla kez kullanılabilir? |
| ttvpn-005 | False | False | True | 4 | 0.250 | Talep_Urun_Seri_Nolari isimli excel dosyası hangi buton ile indirilir? |
| ttvpn-006 | True | True | True | 1 | 1.000 | Müşteriden Alınamayan Depo Stoğu Raporu filtrelerinde Temos No alanında nasıl arama yapılabilir? |
| ttvpn-007 | True | True | True | 1 | 1.000 | Bölge ekranından tanımlı bir bölgeyi silmek için hangi buton kullanılır? |
| cross-001 | True | True | True | 1 | 1.000 | TTNETSIS ve TTSIS projelerinin her ikisi de hangi mevcut sistemin (STP) bazı servislerini yeni sistemlere aktarmayı hedeflemektedir? |

## Recall/MRR Hesabına Dahil Edilmeyen Sorular (answerable=false / no-answer)

- noans-001
- noans-002
- noans-003
- chitchat-001
