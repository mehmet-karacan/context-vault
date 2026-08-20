# Runbook — OCR Modelleri ve Language Pack Kurulumu

Bu runbook, OCR provider seçimini, dil profilini, language pack kurulumunu ve
`needs_review` davranışını gerçek kodla eşleşerek anlatır.

İlgili kod:
- `src/config.py` — `OCR_PROVIDER`, `OCR_FALLBACK_PROVIDER`, `OCR_LANGUAGES`,
  `OCR_MIN_TEXT_COVERAGE`, `OCR_MIN_CONFIDENCE`, `FEATURE_OCR`, `OCR_ENABLED`
- `src/infrastructure/ocr/factory.py` — `build_ocr_provider`
- `src/infrastructure/ocr/docling_provider.py` , `tesseract_provider.py`
- `src/infrastructure/parsers/ocr_routing.py` — `should_ocr` / `route_pdf_pages_to_ocr`
- `src/infrastructure/parsers/image_parser.py` — `needs_review` metadata'sı
- `src/infrastructure/parsers/ocr_artifact.py` — `ocr_json` artifact'ı

## 1. Config ve varsayılanlar

| Değişken | Varsayılan | Anlam |
|---|---|---|
| `FEATURE_OCR` | `true` | OCR özelliği ana giri (rollback) kapısı |
| `OCR_ENABLED` | `true` | Aktif OCR çağrısını ayrıca kapılar |
| `OCR_PROVIDER` | `docling` | Birincil provider |
| `OCR_FALLBACK_PROVIDER` | `tesseract` | Birincil motor yoksa fallback |
| `OCR_LANGUAGES` | `tur+eng` | Dil profili (provider tarafında çözümlenir) |
| `OCR_MIN_TEXT_COVERAGE` | `0.02` | PDF metin kaplama eşiği (OCR yönlendirme) |
| `OCR_MIN_CONFIDENCE` | `0.5` | Altında gelen sonuç `needs_review` işaretlenir |

## 2. Provider seçimi

`build_ocr_provider` (factory.py):

- `FEATURE_OCR` ve `OCR_ENABLED` kapalıysa `OcrUnavailableError`.
- Registry: `docling` → `DoclingOcrProvider`, `tesseract` → `TesseractOcrProvider`,
  `paddleocr` → extension point (henüz uygulanmamış, `PaddleUnavailableError`).
- Önce `OCR_PROVIDER`, sonra `OCR_FALLBACK_PROVIDER` denenir; her provider'ın
  `available` bayrağı engine'in kurulu olup olmadığını gösterir:
  - `DoclingOcrProvider.available` → `docling` paketi import edilebiliyor mu
    (`import docling` / `get_tesseract_version` benzeri).
  - `TesseractOcrProvider.available` → `pytesseract` + tesseract binary var mı
    (`pytesseract.get_tesseract_version()`).
- Hiçbiri kullanılabilir değilse `OcrUnavailableError` ("Install an ocr profile
  dependency or fix OCR_PROVIDER/OCR_FALLBACK_PROVIDER").

Yani ağır bağımlılıklar API image'ına zorunlu değildir; kuruluysa devreye girer,
değilse fallback'e/degrade'ye geçer.

## 3. Dil profili (OCR_LANGUAGES)

- Config `OCR_LANGUAGES=tur+eng`. Provider'lar dili plus-joined profil olarak
  çözümlenir (`_resolve_language`): Docling'te doğal `tur+eng`, Tesseract'ta
  `lang="tur+eng"`.
- `TesseractOcrProvider.extract` `tur` desteklenmiyorsa otomatik `eng`'e düşer.

### Tesseract language pack kurulumu

`tur+eng` profili için yerel Tesseract kurulumunda **Türkçe traineddata** gerekir
(değilse Tesseract `eng`'e degrade olur ve Türkçe karakterler iyi tanınmaz):

- Windows (Tesseract install altı `tessdata/`): `tur.traineddata` dosyasını
  `https://github.com/tesseract-ocr/tessdata` depolarından `tessdata` klasörüne,
  veya `TESSDATA_PREFIX` ortam değişkeninin işaret ettiği dizine koyun.
- Linux (Debian/Ubuntu): `apt-get install tesseract-ocr tesseract-ocr-tur`.
- Doğrulama: `tesseract --list-langs` çıktısında `tur` görünmeli.

Not: Provider dili, `pytesseract.image_to_data(image, lang="tur+eng")` ile tesseract'ın
`LANG+LANG` profili olarak yorumlanır.

## 4. OCR yönlendirme (ne zaman OCR gerekir)

`should_ocr` / `route_pdf_pages_to_ocr` (ocr_routing.py), `pdf_parser`'ın kaydettiği
`text_coverage`/`text_present` metadata'sını kullanır:

- `source_type == "image"` (PNG/JPEG/TIFF) → her zaman OCR.
- PDF sayfasında yeterli metin varsa (coverage ≥ digital eşiği `0.75`) → OCR **yapılmaz**.
- Coverage ≤ `0.25` → "scanned" → tüm sayfalar OCR.
- Arada/mixed → düşük coverage sayfalar OCR'a, dijital sayfalar OCR'a girmez.

Amaç: dijital PDF'i boş yere OCR'a göndermemek (AKTIF §15: "OCR'ı tüm dijital PDF
sayfalarına koşulsuz çalıştırma").

## 5. needs_review (düşük güven)

`image_parser.py` ve `ocr/base.py`:

- OCR sonucunun `confidence` değeri `OCR_MIN_CONFIDENCE` (`0.5`) altındaysa
  normalize içerik metadata'sında `needs_review=True` işaretlenir (kabul kriteri
  "OCR confidence düşükse needs_review metadata'sı üret").
- Ham OCR sonucu `document_artifacts`'a `ocr_json` artifact'ı olarak saklanır
  (`ocr_artifact.py`, `OCR_ARTIFACT_TYPE = "ocr_json"`); bbox citation'ları korunur.
- `needs_review=True` olan parçalar ayrıca husus/uyarı olarak davranışı bozmadan
  işlenir.

## 6. OCR aç/kapa

- `FEATURE_OCR=false` → OCR özelliği kapalı; `should_ocr` her şey için `false` döner.
- `OCR_PROVIDER`'ı `tesseract`'e çevirip `OCR_FALLBACK_PROVIDER`'ı boş bırakabilir,
  ya da default tütünü koruyabilirsiniz.

`.env` örneği:

```dotenv
FEATURE_OCR=true
OCR_ENABLED=true
OCR_PROVIDER=docling
OCR_FALLBACK_PROVIDER=tesseract
OCR_LANGUAGES=tur+eng
OCR_MIN_TEXT_COVERAGE=0.02
OCR_MIN_CONFIDENCE=0.50
```

Değişiklik sonrası ilgili servisi yeniden başlatın. Not: OCR config değişimi re-index
tetikleyicilerinden biridir (bkz. `docs/runbooks/reindex.md`).
