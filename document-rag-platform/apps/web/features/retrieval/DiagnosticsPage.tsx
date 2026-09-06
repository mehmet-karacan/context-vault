"use client";
import { useState } from "react";
import Nav from "../../components/Nav";
import { useSession } from "../auth/SessionGate";
import { useProjects } from "../projects/useProjects";
import { ProjectSelector } from "../projects/ProjectSelector";
import { apiRequest, problemMessage } from "../../lib/api/client";
import type { Schemas } from "../../lib/api/generated";
export function DiagnosticsPage() {
  const session = useSession();
  if (!session?.roles.includes("admin"))
    return <p role="alert">Bu görünüm yalnız yöneticilere açıktır.</p>;
  return <AdminDiagnostics />;
}
function AdminDiagnostics() {
  const projects = useProjects();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Schemas["DiagnosticsResponse"] | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-3xl space-y-5 px-6 py-10">
        <h1 className="font-display text-4xl">Retrieval tanısı</h1>
        <p>
          Yalnız yönetici ve development ortamı. Bu işlem veri politikası
          kapsamında embedding sağlayıcısını çağırabilir. Ham prompt veya
          context gösterilmez.
        </p>
        <ProjectSelector
          projects={projects.projects}
          value={projects.selectedProjectId}
          onChange={(id) => {
            setResult(null);
            projects.setSelectedProjectId(id);
          }}
          onCreate={projects.create}
        />
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!projects.selectedProjectId || busy) return;
            setBusy(true);
            setError(null);
            setResult(null);
            try {
              setResult(
                await apiRequest<Schemas["DiagnosticsResponse"]>(
                  "/debug/retrieval",
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      project_id: projects.selectedProjectId,
                      query,
                    }),
                  },
                  "DiagnosticsResponse",
                ),
              );
            } catch (cause) {
              setError(problemMessage(cause));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label htmlFor="diagnostic-query">Tanı sorgusu</label>
          <input
            id="diagnostic-query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="mx-3 rounded border border-ink-line p-2"
          />
          <button
            disabled={busy || !query.trim() || !projects.selectedProjectId}
            className="rounded bg-brass p-2"
          >
            Tanıyı çalıştır
          </button>
        </form>
        {(error || projects.error) && (
          <p role="alert">{error ?? projects.error}</p>
        )}
        {result && (
          <section>
            <p>Fallback: {result.fallback_reason ?? "yok"}</p>
            <p>
              Toplam: {result.timings_ms.total?.toFixed(1) ?? "bildirilmedi"} ms
            </p>
            {Object.entries(result.stages).map(([stage, hits]) => (
              <div key={stage}>
                <h2 className="mt-4 font-display text-xl">{stage}</h2>
                <ol>
                  {hits.map((hit) => (
                    <li
                      key={hit.chunk_id}
                      className="break-all font-mono text-xs"
                    >
                      {hit.rank}. {hit.chunk_id} · ham skor {hit.score ?? "yok"}
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </section>
        )}
      </main>
    </>
  );
}
