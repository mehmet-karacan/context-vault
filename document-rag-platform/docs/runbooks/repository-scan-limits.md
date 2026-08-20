# Runbook — Repository Scan Limits ve Güvenlik

Bu runbook, repository / arşiv / klasör taramasındaki sınırları ve güvenlik
kurallarını gerçek kodla eşleşerek anlatır.

İlgili kod:
- `src/config.py` — `CODE_*` değişkenleri, `FEATURE_REPOSITORY_INGESTION`
- `src/infrastructure/repositories/discovery.py` — `ScanConfig`, `discover_directory`
- `src/infrastructure/repositories/git_source.py` — `GitRepositorySource`
- `src/infrastructure/repositories/archive_source.py` — `ArchiveSourceScanner`
- `src/infrastructure/repositories/ignore_rules.py` — `IgnoreRules`, `is_sensitive_path`
- `src/infrastructure/repositories/path_security.py` — `is_allowed_scan_path`

## 1. Scan config değişkenleri ve varsayılanlar

| Değişken | Varsayılan | Anlam |
|---|---|---|
| `CODE_ALLOWED_ROOTS` | `/imports,/workspace` | Web isteğindeki scan path'inin çözülebileceği izinli kökler (`alias=path` veya `/path`) |
| `CODE_MAX_FILES` | `20000` | Tek scan'de keşfedilecek max dosya |
| `CODE_MAX_TOTAL_BYTES` | `1073741824` (1 GB) | Tek scan'deki toplam max byte |
| `CODE_MAX_FILE_BYTES` | `2097152` (2 MB) | Tek dosya max byte; büyüğü atlanır |
| `CODE_SCAN_TIMEOUT_SECONDS` | `900` | Scan'in wall-clock bütçesi (aşılınca durdurulur) |
| `CODE_FOLLOW_SYMLINKS` | `false` | Symlink takibi kapalı (escape koruması) |
| `CODE_ALLOW_SUBMODULES` | `false` | Git submodule otomatik çekilmez |
| `CODE_ALLOW_GIT_LFS` | `false` | Git LFS büyük objeleri indirilmez |
| `CODE_SECRET_POLICY` | `skip` | Hassas dosyalar keşifte atlanır |
| `CODE_ARCHIVE_MAX_TOTAL_BYTES` | `1 GB` | Arşiv toplam max byte (zip bomb koruması) |
| `CODE_ARCHIVE_MAX_ENTRY_BYTES` | `2 MB` | Arşivde tek giriş max byte |
| `CODE_ARCHIVE_MAX_ENTRIES` | `20000` | Arşivde max giriş sayısı |
| `FEATURE_REPOSITORY_INGESTION` | `true` | Repo/arşiv/klasör izleme özellik kapısı |

`discovery.py:ScanConfig.from_settings()` bu değerleri okur; mantıkta hiçbir sayı
sabit değildir.

## 2. İzinli kökler ve path güvenliği

`repositories.py` (`resolve_allowed_scan_path`) + `path_security.is_allowed_scan_path`:

- Yalnız `CODE_ALLOWED_ROOTS` altına çözünen canonical absolute path'lere izin verilir.
- Mutlak path / `C:\...` / `..` traversal istekleri `400/403` ile reddedilir.
- Symlink ile izinli kök dışına çıkış engellenir (canonicalize hem taraf).

`.env` örneği — izinli kök tanımlama (alias=path formatı da desteklenir):

```dotenv
CODE_ALLOWED_ROOTS=/imports,/workspace
```

Örn. `/imports` kökü `allowed_root_alias=imports`, `relative_path=project-a` olarak
`POST /directories/scan`'de kullanılır.

## 3. Disk tarama sınırları

`discover_directory` (discovery.py) şu sınırları uygular:

- `max_files` → `result.truncated=True; reason="max_files"`.
- `max_total_bytes` → toplam byte aşılırsa durdurulur, `reason="total_bytes"`.
- `max_file_bytes` → daha büyük tek dosyalar `skipped_large` ile atlanır (`reason="file_too_large"`).
- `scan_timeout` → `reason="scan_timeout"`.
- `follow_symlinks=false` → symlink dizin/dosyalar atlanır.
- `secret_policy=skip` → hassas dosyalar keşifte atlanır.

Epsilon: `ScanResult` içindeki `truncated`/`reason` bu sınırların aşıldığını gösterir;
uygulama aşımı sessizce geçiştirmez.

## 4. Git repository güvenliği (kod çalıştırılmaz)

`GitRepositorySource` (git_source.py):

- `git` `shell=False` + sabit argv ile çağrılır; URL/ref hiçbir shell string'inde
  yorumlanmaz.
- Ortam: `GIT_TERMINAL_PROMPT=0` (credential için prompt'a düşmez, fail eder),
  `GIT_LFS_SKIP_SMUDGE=1` + `--config filter.lfs.required=false` (LFS indirmez),
  `GIT_CONFIG_NOSYSTEM=1` (host config/hookPath'i içeri almaz).
- `--config core.autocrlf=false` → satır sonları byte-identical (content_hash stabil).
- Submodule `git submodule update` **hiç çalıştırılmaz**; hook'lar klonlanmaz; hiçbir
  script/build/test/package-manager komutu çalıştırılmaz (§15).
- `GIT_LFS_SKIP_SMUDGE=1` varsayılan olarak LFS büyük objelerini indirmez.
- Archive tarafında `ArchiveSourceScanner`: path traversal member'ları atlanır, link
  member'ları reddedilir, entry/total limitleri `ArchiveLimitError` ile aşımı reddeder.

## 5. Ignore önceliği (yapılandırılmış precedence)

`ignore_rules.py` dört katmanı **sabit** sırayla uygular — ilk kesin karar sonucu verir:

1. **Sistem güvenlik ignore listesi** (`DEFAULT_IGNORE_PATTERNS`: `.git/`,
   `node_modules/`, `.venv/`, `dist/`, `build/`, `target/`, `*.min.js`, `*.map`,
   `*.lock`, `*.png`, `*.jpg`, `*.pdf`, `*.exe`, `*.dll`, `*.class`, `*.jar`, `*.zip`,
   `*.tar`, `*.gz`, `.env`, `*.pem`, `*.key`, `id_rsa*`, vb.) — override edilemez.
2. `.contextvaultignore`
3. Repository `.gitignore`
4. Kullanıcı include/exclude kalıpları (include → re-include, exclude → ignore)

Ayrıca `.lock`/`package-lock.json`/`go.sum` gibi üretilmiş dosyalar `is_generated`
ile işaretlenir ve secret/credential desenleri (`DEFAULT_SENSITIVE_PATTERNS`) ile
`.env`, `*.pem`, `*.key`, `id_rsa*`, `*secret*`, `*password*` vb. `is_sensitive_path`
ile atlanır.

## 6. Secret / credential yönetimi

- `CODE_SECRET_POLICY=skip` (varsayılan) → hassas dosyalar keşifte **atlanır**.
- Ingestion sırasında chunk metinleri embedding gateway'e gönderilmeden önce
  `src/infrastructure/security/redaction.py:redact_secrets` ile credential değerleri
  redact edilir (`ingestion_tasks.py`); `.env`/private-key içeriği gateway'e gitmez (§15).

## 7. Limitleri değiştirme

`.env` örneği:

```dotenv
FEATURE_REPOSITORY_INGESTION=true
CODE_ALLOWED_ROOTS=/imports,/workspace
CODE_MAX_FILES=20000
CODE_MAX_TOTAL_BYTES=1073741824
CODE_MAX_FILE_BYTES=2097152
CODE_SCAN_TIMEOUT_SECONDS=900
CODE_FOLLOW_SYMLINKS=false
CODE_ALLOW_SUBMODULES=false
CODE_ALLOW_GIT_LFS=false
CODE_SECRET_POLICY=skip
```

Değişiklik sonrası backend'i yeniden başlatın; yeni scan'lerde geçerli olur.

## 8. Örnek doğrulama

```powershell
# İzinli kök altında bir klasörü tara (mutlak path kabul edilmez)
curl.exe -X POST http://localhost:8000/directories/scan -H "Content-Type: application/json" -d '{"project_id":"<id>","allowed_root_alias":"imports","relative_path":"some-dir"}'
```
