"use client";
import { useEffect } from "react";
import { useDocuments } from "../documents/useDocuments";
export function JobBoard({ projectId }: { projectId: string }) {
  const documents = useDocuments(projectId);
  const refresh = documents.refresh;
  const hasRunning = documents.documents?.some(
    (item) =>
      item.job_id &&
      !["failed", "cancelled", "completed"].includes(item.job_status ?? ""),
  );
  useEffect(() => {
    if (!hasRunning) return;
    let polls = 0;
    let pending = false;
    const timer = setInterval(async () => {
      if (pending) return;
      if (++polls > 120) {
        clearInterval(timer);
        return;
      }
      pending = true;
      try {
        await refresh();
      } finally {
        pending = false;
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [hasRunning, refresh]);
  return (
    <section aria-label="Kalıcı iş kayıtları">
      <h2 className="font-display text-3xl">Kaynakların son işleri</h2>
      <p className="my-3 text-sm text-ink-soft">
        Her kaynağın son backend işi. Etkin işler en çok 10 dakika izlenir;
        sayfadan ayrılmak işi iptal etmez.
      </p>
      <button
        className="mb-4 underline"
        disabled={!projectId}
        onClick={() => void documents.refresh()}
      >
        Durumu yenile
      </button>
      {documents.error && (
        <p role="alert" className="text-rust">
          {documents.error}
        </p>
      )}
      {!projectId ? (
        <p>Önce bir proje seçin.</p>
      ) : documents.documents === null ? (
        <p role="status">İş kayıtları yükleniyor…</p>
      ) : documents.documents.filter((item) => item.job_id).length === 0 ? (
        <p>Bu projede iş kaydı bulunamadı.</p>
      ) : (
        <ul className="space-y-3">
          {documents.documents
            .filter((item) => item.job_id)
            .map((item) => (
              <li key={item.job_id} className="border-l-2 border-brass p-3">
                <h3>{item.name}</h3>
                <p className="font-mono text-xs">
                  {item.job_status} · {item.job_stage}
                </p>
                <p className="break-all font-mono text-xs text-ink-soft">
                  İş: {item.job_id}
                </p>
                {item.job_status === "failed" && (
                  <p className="text-sm text-rust">
                    İş başarısız; kaynak politika veya işleme incelemesi
                    bekliyor.
                  </p>
                )}
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
