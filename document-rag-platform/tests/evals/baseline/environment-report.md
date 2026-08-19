# Ortam Doğrulama Raporu — Aşama 0

Tarih: 2026-08-19
Compose dosyası: `C:\innova\projeler\context-vault\document-rag-platform\docker-compose.yml`

## Kullanılan komutlar (tam yeniden üretim için)

```powershell
cd C:\innova\projeler\context-vault\document-rag-platform
docker compose up -d
docker compose ps -a
docker compose logs worker --tail 60
docker compose logs backend --tail 30
docker compose exec -T postgres pg_dump --schema-only -U raguser rag_platform > tests\evals\baseline\db-schema-snapshot.sql
```

Not: Container'lar bu görev başlamadan önce zaten bir kez oluşturulmuştu (7-31 saat önce "Created") ama hepsi "Exited" durumundaydı. `docker compose up -d` mevcut container'ları ve volume'ları (`postgres_data`, `minio_data`) SİLMEDEN yeniden başlattı. Hiçbir `down -v`, `prune` veya `volume rm` komutu çalıştırılmadı.

## Servis durumu

| Servis   | Compose'da tanımlı mı | Durum (bu görev sonunda) | Not |
|----------|------------------------|---------------------------|-----|
| postgres | Evet (`pgvector/pgvector:pg16`) | Up, healthy | Port 5432 |
| redis    | Evet (`redis:7-alpine`) | Up, healthy | Port 6379 |
| minio    | Evet (`minio/minio`) | Up, healthy | Port 9000/9001 |
| backend  | Evet (build: `services/backend`) | Up (running) | Port 8000, FastAPI/uvicorn, loglarda gerçek istek trafiği (`/projects`, `/documents`, `/chat/query` 200 OK) görülüyor |
| worker   | Evet (build: `services/backend`, `celery -A src.workers.celery_app worker -l info`) | **Exited (2)** | Aşağıya bakın |
| frontend | **Compose'da tanımlı değil** | — | `apps/web` dizininde Next.js kaynak kodu var ama `docker-compose.yml` içinde frontend/web servisi hiç tanımlanmamış. Konteynerize değil, muhtemelen ayrı `npm run dev` ile çalıştırılması bekleniyor. |

Container'ları bu görev sırasında ben (ajan) `docker compose up -d` ile başlattım (önceden hepsi Exited durumdaydı). Container'lar ayakta bırakıldı, kapatılmadı.

## Worker / Celery bulgusu

Worker container'ı **Exited (2)** ile çöküyor. Tam hata (docker compose logs worker):

```
Error: Invalid value for '-A' / '--app':
Unable to load celery application.
The module src.workers.celery_app was not found.
```

Kök neden doğrulandı: `services/backend/src/workers/` dizini gerçekten var, ancak içinde sadece `.gitkeep` dosyası bulunuyor — `celery_app.py` (veya eşdeğeri) fiziksel olarak yok. Bu import hatası değil, dosyanın hiç oluşturulmamış olması sorunu.

## DB şema-only dump

- Bağlantı bilgileri `.env` dosyasından okundu (gerçek şifreler bu raporda yazılmadı).
- DB adı: `rag_platform`, kullanıcı env'den okundu.
- Komut: `docker compose exec -T postgres pg_dump --schema-only -U raguser rag_platform`
- Çıktı: `tests/evals/baseline/db-schema-snapshot.sql` (97 satır)
- Bulunan tablolar: `public.chunks`, `public.documents`, `public.projects` (pgvector extension'lı bir şema).
- Dump başarılı, stderr boş.

## Dış engeller

Yok. Docker Desktop çalışıyordu (Server Version 29.6.2, Docker Compose v5.3.1), tüm adımlar sorunsuz tamamlandı. Tek not: Bash tool bu ortamda `ls -la` gibi bazı komutlarda git-bash mount hatası (`add_item ... failed, errno 1`) verdi; iş PowerShell ile tamamlandı, bu görevi engellemedi.

## Özet / Aşama 0 sonucu

- postgres, redis, minio, backend: çalışır durumda ve sağlıklı.
- worker: **çalışmıyor** — `src/workers/celery_app.py` dosyası eksik, bu net bir açık iş kalemi.
- frontend: docker-compose.yml'de hiç servis olarak tanımlanmamış — bu da ayrıca not edilmesi gereken bir boşluk (containerize edilmemiş).
- DB şeması (3 tablo) başarıyla dump edildi ve baseline olarak kaydedildi.
