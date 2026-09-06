# Verified status projection

`verified-state.json`, `scripts/generate_verified_status.py` tarafından mevcut
checkout için üretilen ve Git tarafından izlenmeyen bir kanıt görünümüdür.
Kanonik görev otoritesi root `AKTIF_GOREV.md`; kanonik runtime otoritesi ilgili
PostgreSQL durumudur. Generated durum dosyası bu kaynakları değiştirmez.

```bash
python scripts/generate_verified_status.py
```

Dirty tree, eksik immutable evidence, birden çok migration head, sentetik evalin
kalite iddiası veya eksik/geçersiz remote CI/ruleset receipt'i `verified=false`
üretir. Remote dosya varlığı yeterli değildir; exact-HEAD ve semantik sözleşme
`remote-evidence-contract.md` uyarınca fail-closed doğrulanır.
