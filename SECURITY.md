# Security Policy

## Supported version

Güvenlik düzeltmeleri yalnız `main` üzerindeki en güncel sürüm ve aktif release
candidate için hazırlanır. Eski commit ve tarihsel görev snapshot'ları desteklenen
release değildir.

## Reporting a vulnerability

Secret, kişisel veri, exploit ayrıntısı veya müşteri içeriğini public issue'a
yazmayın. GitHub repository Security sekmesindeki private reporting kanalı
kullanılabiliyorsa onu; değilse repository sahibi Mehmet KARACAN ile bilinen özel
bir kanal kullanın. İlk bildirime yalnız yeniden üretim adımları, etkilenen sürüm
ve etki sınıfını ekleyin; gerçek credential veya ham kullanıcı verisi göndermeyin.

## Public/private boundary

Public Git ağacına gerçek `.env`, credential, token, private key, kurum içi
endpoint, özel trust bundle, ham DB/object dump, kullanıcı belgesi, PII veya ham
model transcript'i girmez. Private deployment değerleri secret store veya
repo-dışı overlay ile sağlanır. Test fixture'ları sentetik ve açıkça etiketlidir.

## Handling

- Credential şüphesinde önce erişim kesilir ve anahtar rotate edilir.
- History rewrite veri kaybettiren ve koordinasyon gerektiren ayrı bir karardır;
  repository sahibinin açık onayı olmadan yapılmaz.
- Migration veya restore güvenlik olayı mevcut veriyi silerek kapatılmaz.
- Düzeltme için regression testi, public-safe receipt ve ilgili runbook gerekir.
- High/critical dependency veya container açığı belgelenmiş istisna olmadan
  release'i durdurur.

Güvenlik politikası ve hassas yolların review sahipliği `.github/CODEOWNERS`
dosyasındadır.
