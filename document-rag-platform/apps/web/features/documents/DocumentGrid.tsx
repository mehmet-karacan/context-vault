"use client";
import {
  CheckIcon,
  CloseIcon,
  FileIcon,
  SpinnerIcon,
} from "../../components/icons";
import type { DocumentItem } from "./useDocuments";
export function DocumentGrid({
  documents,
  onDelete,
  stale = false,
  disabled = false,
}: {
  documents: DocumentItem[] | null;
  onDelete: (id: string) => Promise<void>;
  stale?: boolean;
  disabled?: boolean;
}) {
  if (documents === null)
    return (
      <p role="status" className="text-ink-soft">
        Belgeler yükleniyor…
      </p>
    );
  if (!documents.length)
    return (
      <p className="text-ink-soft">
        {stale
          ? "Son doğrulanmış listede kaynak bulunmuyordu."
          : "Bu projede henüz kaynak yok. İlk kaynağı yukarıdan ekleyin."}
      </p>
    );
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,14rem),1fr))] gap-4">
      {documents.map((doc) => (
        <article
          key={doc.id}
          className="folio flex flex-col gap-3 bg-surface p-4 shadow-folio"
        >
          <div className="flex gap-2">
            <FileIcon className="mt-1 h-4 w-4 shrink-0" />
            <div className="min-w-0">
              <h3 className="truncate font-medium">{doc.name}</h3>
              <p className="font-mono text-[11px] text-ink-soft">
                {(doc.size / 1024).toFixed(1)} KB · {doc.uploaded_at}
              </p>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-1 font-mono text-[10px] uppercase">
              {doc.status === "processing" && (
                <SpinnerIcon className="h-3 w-3 animate-spin" />
              )}
              {doc.status === "indexed" && <CheckIcon className="h-3 w-3" />}
              {doc.status}
            </span>
            <button
              disabled={disabled}
              aria-label={`${doc.name} kaynağını sil`}
              onClick={() => void onDelete(doc.id)}
              className="text-ink-soft hover:text-rust focus-visible:outline focus-visible:outline-2 focus-visible:outline-brass"
            >
              <CloseIcon className="h-4 w-4" />
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}
