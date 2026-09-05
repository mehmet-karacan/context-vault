# Katkı Politikası

Context Vault'a katki yaparken asagidaki kurallar baglayicidir.

## 1. Çalışma akışı

- Önce root `AKTIF_GOREV.md` tamamen okunur; bu dosya kapsam ve sıra otoritesidir.
- `main` üzerinde geliştirme yapılmaz. Güncel `origin/main` tabanından konu branch'i
  açılır ve değişiklik pull request ile sunulur.
- Döngü `inspect → reproduce → plan → implement → test → verify → document`
  şeklindedir. Eski Markdown iddiası test veya runtime kanıtı sayılmaz.
- Kullanıcı verisi, PostgreSQL, MinIO veya index içeriği sıfırlanmaz. Migration
  öncesi yedek ve restore denemesi gerekir; lineage ayrışması otomatik
  merge/rebase/stamp ile kapatılmaz.
- Public ağaca secret, kişisel veri, ham transcript, kurum içi endpoint, özel CA
  veya runtime dump eklenmez.

## 2. Yerel kapılar

Desteklenen runtime kaynakları repo kökündeki `.python-version` (Python
3.12) ve `document-rag-platform/apps/web/.nvmrc` (Node 24.18.0) dosyalarıdır.
Yerel ortam, Docker ve CI farklı bir sürüm tahmin etmemelidir.

Backend komutları `document-rag-platform/services/backend/` içinde çalışır:

```bash
uv lock --check
uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

Frontend komutları `document-rag-platform/apps/web/` içinde çalışır:

```bash
npm ci
npm run lint
npm run typecheck
npm run unit
npm run build
npm run e2e-smoke
```

Ortak güvenlik ve doğrulama kapıları:

```bash
python scripts/check_commit_ownership.py --all
python scripts/check_public_tree.py
python scripts/verify_migrations.py
python scripts/generate_verified_status.py
```

Formatter değişikliği davranış değişikliğinden ayrı commit edilir. Generated
dosyanın üreticisi ve drift kontrolü aynı değişiklikte yer alır.

## 3. Commit sahipliği

- Repository'nin commit **author** ve **committer** kimligi yalnizca
  **Mehmet KARACAN** olabilir.
- Yapay zeka, CLI, arac veya otomasyon adlari `Co-Authored-By` trailer'i
  olarak kullanilmaz.
- Her turdeki `Co-Authored-By` / `Co-authored by` trailer'i varsayilan olarak
  **reddedilir**.

## 4. İstisnalar

- Bir trailer'in kabul edilebilmesi icin Mehmet KARACAN'in acik yazili onayi
  ve istisnanin `.git-ownership-allowlist` dosyasina eklenmesi gerekir.
- Allowlist satiri, izinli trailer'in **tam satiri** ile birebir eslesmelidir.

## 5. Doğrulama

- `git log --all --format=%B` ciktisinda istenmeyen trailer bulunmamalidir.
- `git shortlog -sne --all` yalnizca gercek sahiplik kimliklerini gostermelidir.
- Otomatik denetim:

  ```bash
  python scripts/check_commit_ownership.py --all
  ```

- CI ve yerel hook bu politikayi uygular.

## 6. Pull request ve review

Pull request şablonundaki risk, migration, rollback, veri sınıflandırması ve
kanıt alanları doldurulur. Migration, güvenlik, workflow ve governance
değişiklikleri CODEOWNERS incelemesi ister. Otomatik dependency PR'ları otomatik
merge edilmez.

## 7. Yerel hook kurulumu (opsiyonel)

```bash
git config core.hooksPath .githooks
```

## 8. Araç desteği

- Yapay zeka kullanimi gerekiyorsa bu, release notu veya ic is kaydinda
  "arac destegi" olarak belirtilir; git commit sahipligi degismez.
