import type { Citation } from "../../lib/types";
export function CitationPanel({
  citations,
  activeVersions,
}: {
  citations: Citation[];
  activeVersions: Record<string, string | null>;
}) {
  if (!citations.length) return null;
  return (
    <section
      aria-label="Kullanılan kanıtlar"
      className="w-full border-l-2 border-brass pl-3"
    >
      <h3 className="mb-2 font-mono text-xs uppercase">Kullanılan kanıtlar</h3>
      {citations.map((citation) => {
        const current = citation.document_id
          ? activeVersions[citation.document_id]
          : null;
        const changed =
          current && citation.version_id && current !== citation.version_id;
        return (
          <details
            key={citation.label}
            className="mb-2 rounded border border-ink-line bg-paper-dim p-3"
          >
            <summary className="cursor-pointer text-sm">
              <span className="font-mono text-brass-dim">{citation.label}</span>{" "}
              · {citation.document_name ?? "Kaynak"}
            </summary>
            <dl className="mt-3 grid gap-1 break-all text-xs">
              <div>
                <dt className="inline font-medium">Sürüm: </dt>
                <dd className="inline">
                  {citation.version_id ?? "Bilinmiyor"}
                </dd>
              </div>
              {citation.file_path && (
                <div>
                  <dt className="inline font-medium">Dosya: </dt>
                  <dd className="inline">{citation.file_path}</dd>
                </div>
              )}
              {citation.page_start !== null && (
                <div>
                  Sayfa: {citation.page_start}–
                  {citation.page_end ?? citation.page_start}
                </div>
              )}
              {citation.line_start !== null && (
                <div>
                  Satır: {citation.line_start}–
                  {citation.line_end ?? citation.line_start}
                </div>
              )}
              {citation.symbol_name && (
                <div>Sembol: {citation.symbol_name}</div>
              )}
              {citation.bbox && (
                <div>Konum: {JSON.stringify(citation.bbox)}</div>
              )}
            </dl>
            {changed && (
              <p role="status" className="mt-2 text-xs text-rust">
                Bu kanıt önceki sürüme dayanıyor; kaynağın aktif sürümü değişti.
              </p>
            )}
            <blockquote className="mt-3 whitespace-pre-wrap break-words text-sm">
              {citation.snippet}
            </blockquote>
          </details>
        );
      })}
    </section>
  );
}
