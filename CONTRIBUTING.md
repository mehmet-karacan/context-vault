# Katki ve Commit Sahiplik Politikasi

Context Vault'a katki yaparken asagidaki kurallar baglayicidir.

## 1. Commit sahipligi

- Repository'nin commit **author** ve **committer** kimligi yalnizca
  **Mehmet KARACAN** olabilir.
- Yapay zeka, CLI, arac veya otomasyon adlari `Co-Authored-By` trailer'i
  olarak kullanilmaz.
- Her turdeki `Co-Authored-By` / `Co-authored by` trailer'i varsayilan olarak
  **reddedilir**.

## 2. Istisnalar

- Bir trailer'in kabul edilebilmesi icin Mehmet KARACAN'in acik yazili onayi
  ve istisnanin `.git-ownership-allowlist` dosyasina eklenmesi gerekir.
- Allowlist satiri, izinli trailer'in **tam satiri** ile birebir eslesmelidir.

## 3. Dogrulama

- `git log --all --format=%B` ciktisinda istenmeyen trailer bulunmamalidir.
- `git shortlog -sne --all` yalnizca gercek sahiplik kimliklerini gostermelidir.
- Otomatik denetim:

  ```bash
  python scripts/check_commit_ownership.py --all
  ```

- CI ve yerel hook bu politikayi uygular.

## 4. Yerel hook kurulumu (opsiyonel)

```bash
git config core.hooksPath .githooks
```

## 5. Arac destegi

- Yapay zeka kullanimi gerekiyorsa bu, release notu veya ic is kaydinda
  "arac destegi" olarak belirtilir; git commit sahipligi degismez.
