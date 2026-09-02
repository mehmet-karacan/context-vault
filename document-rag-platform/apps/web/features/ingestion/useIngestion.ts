"use client";
import { useEffect, useRef, useState } from "react";
import { apiRequest, problemMessage } from "../../lib/api/client";
import type {
  IngestionJob,
  IngestionJobEvent,
  UploadResponse,
} from "../../lib/types";
import { useSession } from "../auth/SessionGate";
export type Classification =
  "public" | "internal" | "confidential" | "restricted";
export interface UploadJob {
  id: string;
  name: string;
  jobId: string | null;
  status: string;
  stage: string;
  progress: number | null;
  error: string | null;
  settled: boolean;
  retryable: boolean;
}
const terminal = new Set(["completed", "failed", "cancelled"]);
export function jobFailure(job: IngestionJob): string | null {
  if (job.status === "cancelled")
    return "İş iptal edildi; kaynak etkinleştirilmedi.";
  if (job.status !== "failed") return null;
  if (/policy|quarantine|secret/i.test(job.error_code ?? ""))
    return "Kaynak güvenlik politikası nedeniyle karantinada; işleme açılmadı.";
  return "İşleme başarısız. İş kaydı korundu; yönetici incelemesi gerekiyor.";
}
export function useIngestion(
  projectId: string,
  afterTerminal: () => Promise<void>,
) {
  const session = useSession();
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operations = useRef(
    new Map<
      string,
      { file: File; classification: Classification; jobId: string | null }
    >(),
  );
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const update = (id: string, values: Partial<UploadJob>) =>
    setJobs((items) =>
      items.map((job) => (job.id === id ? { ...job, ...values } : job)),
    );
  const run = async (id: string, signal: AbortSignal) => {
    const operation = operations.current.get(id);
    if (!operation) return;
    update(id, { retryable: false, error: null });
    try {
      if (!operation.jobId) {
        const form = new FormData();
        form.append("file", operation.file);
        form.append("project_id", projectId);
        form.append("data_classification", operation.classification);
        const accepted = await apiRequest<UploadResponse>(
          "/documents/upload",
          {
            method: "POST",
            headers: { "Idempotency-Key": id },
            body: form,
            signal,
          },
          "UploadResponse",
        );
        operation.jobId = accepted.job_id;
        update(id, { jobId: accepted.job_id, status: accepted.status });
      }
      const started = Date.now();
      while (Date.now() - started < 10 * 60_000 && !signal.aborted) {
        const query = `?project_id=${projectId}`;
        const [job] = await Promise.all([
          apiRequest<IngestionJob>(
            `/ingestion-jobs/${operation.jobId}${query}`,
            { signal },
            "IngestionJobResponse",
          ),
          apiRequest<IngestionJobEvent[]>(
            `/ingestion-jobs/${operation.jobId}/events${query}`,
            { signal },
            "IngestionEventResponse[]",
          ),
        ]);
        update(id, {
          status: job.status,
          stage: job.stage,
          progress: job.progress,
          error: jobFailure(job),
          settled: terminal.has(job.status),
        });
        if (terminal.has(job.status)) {
          operations.current.delete(id);
          await afterTerminal();
          return;
        }
        await new Promise<void>((resolve) => {
          const finish = () => {
            clearTimeout(timer);
            signal.removeEventListener("abort", finish);
            resolve();
          };
          const timer = setTimeout(finish, 1500);
          signal.addEventListener("abort", finish, { once: true });
        });
      }
      if (!signal.aborted)
        update(id, {
          retryable: true,
          error:
            "İzleme süresi doldu. Aynı iş kaydını yeniden sorgulayabilirsiniz.",
        });
    } catch (cause) {
      if (!signal.aborted)
        update(id, { error: problemMessage(cause), retryable: true });
    }
  };
  const upload = async (files: File[], classification: Classification) => {
    if (!projectId || active.current) return;
    if (
      !session ||
      files.some(
        (file) =>
          file.size > session.upload_max_bytes ||
          !/\.(pdf|docx|txt)$/i.test(file.name),
      )
    ) {
      setError("Dosya türü veya boyutu izin verilen sınırı aşıyor.");
      return;
    }
    setError(null);
    setBusy(true);
    const controller = new AbortController();
    active.current = controller;
    try {
      for (const file of files) {
        if (controller.signal.aborted) break;
        const id = crypto.randomUUID();
        operations.current.set(id, { file, classification, jobId: null });
        setJobs((items) => [
          ...items,
          {
            id,
            name: file.name,
            jobId: null,
            status: "request_pending",
            stage: "Sunucu kabulü bekleniyor",
            progress: null,
            error: null,
            settled: false,
            retryable: false,
          },
        ]);
        await run(id, controller.signal);
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      active.current = null;
    }
  };
  const retry = async (id: string) => {
    if (active.current || !operations.current.has(id)) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    try {
      await run(id, controller.signal);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      active.current = null;
    }
  };
  return {
    jobs,
    busy,
    error,
    upload,
    retry,
    dismiss: (id: string) => {
      operations.current.delete(id);
      setJobs((items) => items.filter((item) => item.id !== id));
    },
  };
}
