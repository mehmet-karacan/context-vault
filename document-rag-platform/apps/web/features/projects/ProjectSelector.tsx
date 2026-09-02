"use client";
import { useState } from "react";
import { CloseIcon } from "../../components/icons";
import type { Project } from "./types";
import { problemMessage } from "../../lib/api/client";
import { INPUT_LIMITS } from "../../lib/api/generated";
export function ProjectSelector({
  projects,
  value,
  onChange,
  onCreate,
}: {
  projects: Project[] | null;
  value: string;
  onChange: (id: string) => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (creating) {
    return (
      <div className="flex gap-2">
        <input
          autoFocus
          maxLength={INPUT_LIMITS.projectName}
          aria-label="Yeni proje adı"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="min-w-0 flex-1 rounded-md border border-ink-line bg-surface px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brass"
        />
        <button
          disabled={!name.trim() || busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await onCreate(name.trim());
              setName("");
              setCreating(false);
            } catch (cause) {
              setError(problemMessage(cause));
            } finally {
              setBusy(false);
            }
          }}
          className="rounded-md bg-brass px-3 py-2 disabled:opacity-40"
        >
          Oluştur
        </button>
        <button aria-label="İptal" onClick={() => setCreating(false)}>
          <CloseIcon className="h-4 w-4" />
        </button>
        {error && (
          <p role="alert" className="text-xs text-rust">
            {error}
          </p>
        )}
      </div>
    );
  }
  return (
    <select
      aria-label="Aktif proje"
      value={value}
      onChange={(e) =>
        e.target.value === "__new__"
          ? setCreating(true)
          : onChange(e.target.value)
      }
      className="w-full rounded-md border border-ink-line bg-surface px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brass"
    >
      <option value="">Proje seçin</option>
      {projects?.map((project) => (
        <option key={project.id} value={project.id}>
          {project.name} ({project.documentCount})
        </option>
      ))}
      <option value="__new__">+ Yeni proje oluştur…</option>
    </select>
  );
}
