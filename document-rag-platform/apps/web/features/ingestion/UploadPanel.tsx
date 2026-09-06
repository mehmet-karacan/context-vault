"use client";
import { useState } from "react";
import type { Classification, UploadJob } from "./useIngestion";
import { useSession } from "../auth/SessionGate";
export function UploadPanel({
  projectId,
  jobs,
  busy,
  onUpload,
  onDismiss,
  onRetry,
}: {
  projectId: string;
  jobs: UploadJob[];
  busy: boolean;
  onUpload: (files: File[], classification: Classification) => Promise<void>;
  onDismiss: (id: string) => void;
  onRetry: (id: string) => Promise<void>;
}) {
  const session = useSession();
  const [classification, setClassification] =
    useState<Classification>("internal");
  return (
    <section
      aria-labelledby="upload-title"
      className="folio bg-surface p-5 shadow-folio"
    >
      <h2 id="upload-title" className="font-display text-2xl">
        Kaynak ekle
      </h2>
      <p id="upload-policy" className="my-3 text-sm text-ink-soft">
        Kaynak türü: belge. PDF, DOCX veya TXT; en çok{" "}
        {session ? (session.upload_max_bytes / 1024 / 1024).toFixed(0) : "—"}{" "}
        MiB. Sunucu tür, boyut ve içerik politikasını tekrar kontrol eder.
      </p>
      <label className="mb-1 block text-xs" htmlFor="classification">
        Veri sınıfı
      </label>
      <select
        id="classification"
        value={classification}
        onChange={(e) => setClassification(e.target.value as Classification)}
        className="mb-3 w-full rounded border border-ink-line bg-surface p-2"
      >
        {["public", "internal", "confidential", "restricted"].map((value) => (
          <option key={value}>{value}</option>
        ))}
      </select>
      <p className="mb-4 text-xs text-ink-soft">
        Confidential/restricted kaynaklar uzak sağlayıcıya gönderilmez. Diğer
        sınıflarda uzak işleme, sunucunun veri politikası izin verirse
        kullanılabilir.
      </p>
      <label htmlFor="upload-files" className="mb-2 block text-sm">
        Belge dosyaları
      </label>
      <input
        id="upload-files"
        aria-describedby="upload-policy"
        type="file"
        accept=".pdf,.docx,.txt"
        multiple
        disabled={!projectId || busy}
        onChange={(e) => {
          if (e.target.files)
            void onUpload(Array.from(e.target.files), classification);
          e.target.value = "";
        }}
        className="w-full text-xs file:mr-3 file:rounded file:border-0 file:bg-brass file:px-3 file:py-2"
      />
      {jobs.map((job) => (
        <div
          role="status"
          key={job.id}
          className="mt-4 border-l-2 border-brass pl-3"
        >
          <p className="break-all text-sm">{job.name}</p>
          <p className="mt-1 font-mono text-xs text-ink-soft">
            {job.status} · {job.stage}
            {job.progress !== null
              ? ` · ${job.progress}%`
              : " · ilerleme bildirilmedi"}
          </p>
          {job.error && <p className="my-2 text-xs text-rust">{job.error}</p>}
          {job.retryable && (
            <button
              disabled={busy}
              onClick={() => void onRetry(job.id)}
              className="mr-3 text-xs underline"
            >
              Aynı işlemi yeniden dene
            </button>
          )}
          {job.settled && (
            <button
              className="text-xs underline"
              onClick={() => onDismiss(job.id)}
            >
              İş durumunu gizle
            </button>
          )}
        </div>
      ))}
    </section>
  );
}
