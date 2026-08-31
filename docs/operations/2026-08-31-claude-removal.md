# Claude Tool Icerigi Kaldirma ve Provenance Kaydi (2026-08-31)

## Amac

Arac-ozel (Claude) icerigi repository'den cikarildi. Bu kayit, neyin
kaldirildigini, nedenini ve nereye arsivlendigini belgeler (AKTIF_GOREV 7.5).

## Kaldirilan icerik

- `.claude/settings.local.json` (root, tool-local izin ayari)
- `document-rag-platform/.claude/skills/` uyeleri (16 dosya)

## Envanter ve degerlendirme

| Skill | Lisans | RAG iliskisi | Karar |
|---|---|---|---|
| frontend-design | Apache-2.0 (LICENSE.txt) | Ilgisiz (UI) | Arsivlendi; product'a tasinmadi |
| beautify-github-readme | Lisans dosyasi yok | Ilgisiz (README/UI) | Arsivlendi; lisans belirsiz, kabul edilmedi |
| find-skills | Lisans dosyasi yok | Ilgisiz (ekosistem kesfi) | Arsivlendi; dis paket yoneticisi + kurulum sayisini kanonik oneri gibi sundugu icin korunmadi |

Not: `find-skills`, `npx skills` paket yoneticisini ve kurulum sayisini kanonik
oneri mekanizmasi gibi sergiliyor bugdu; bu g�venlik, lisans ve supply-chain
kontrolu olmadan korunmayacaktir.

## Arsiv konumu

Kopya, repository disindaki kullanici calisma alaninda tutulur:

```
C:\Users\mkaracan\AppData\Local\Temp\opencode\context-vault-claude-archive-20260831T140000Z\
  root-claude\
  document-rag-platform-claude\
  SHA256SUMS.txt
```

`SHA256SUMS.txt` kaldirilan her dosyanin SHA-256 ozetini tutar.

## Sonuc

- Repository icinde `.claude/` dizini kalmadi.
- Tool-local ayar desenleri `.gitignore` altina eklendi.
- `git grep -i claude` sonucu yalniz bu gecmis/operasyon kaydiyla sinirlidir.

## Yeniden kullanim

Yararli ve acik lisansli icerik, RAG urunuyle ilgisi girdikten sonra ve
lisans/provenance dogrulamasi yapildiktan sonra kanonik `skills/` sozlesmesine
temiz uyarlama ile tasinabilir. Dogrudan arac dizininden geri alinmaz.
