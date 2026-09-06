import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarkdownContent } from "./MarkdownContent";
import { NoAnswerBlock, NO_ANSWER_LABELS } from "./NoAnswerBlock";
import { CitationPanel } from "../citations/CitationPanel";
import { UploadPanel } from "../ingestion/UploadPanel";
import { ProjectSelector } from "../projects/ProjectSelector";
import type { Citation } from "../../lib/types";
const citation: Citation = {
  label: "S1",
  document_id: "d",
  document_name: "Evidence.txt",
  version_id: "v1",
  source_file_id: null,
  source_type: "document",
  heading_path: [],
  page_start: 2,
  page_end: 3,
  file_path: null,
  symbol_name: null,
  line_start: null,
  line_end: null,
  bbox: null,
  snippet: "<script>untrusted</script>",
  rank: 1,
};
describe("product semantic components", () => {
  it("does not silently preselect the first project", () => {
    const html = renderToStaticMarkup(
      <ProjectSelector
        projects={[{ id: "p", name: "Project", documentCount: 0 }]}
        value=""
        onChange={() => {}}
        onCreate={async () => {}}
      />,
    );
    expect(html).toContain('value="" selected=""');
    expect(html).not.toContain('value="p" selected');
  });
  it("renders each no-answer reason distinctly", () => {
    expect(new Set(Object.values(NO_ANSWER_LABELS)).size).toBe(6);
    for (const [reason, label] of Object.entries(NO_ANSWER_LABELS))
      expect(
        renderToStaticMarkup(
          <NoAnswerBlock reason={reason as keyof typeof NO_ANSWER_LABELS} />,
        ),
      ).toContain(label);
  });
  it("renders only supplied used citations and warns on changed version", () => {
    const html = renderToStaticMarkup(
      <CitationPanel citations={[citation]} activeVersions={{ d: "v2" }} />,
    );
    expect(html).toContain("önceki sürüme");
    expect(html).toContain("Sayfa:");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(
      renderToStaticMarkup(
        <CitationPanel citations={[]} activeVersions={{}} />,
      ),
    ).toBe("");
  });
  it("does not render raw HTML, scripts, javascript links or remote images", () => {
    const html = renderToStaticMarkup(
      <MarkdownContent
        content={
          "<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n![tracking](https://tracker.invalid/pixel)"
        }
      />,
    );
    expect(html).not.toMatch(/<script|javascript:|<img|tracker.invalid/);
  });
  it("keeps failed terminal jobs visible and never fabricates percent", () => {
    const html = renderToStaticMarkup(
      <UploadPanel
        projectId="p"
        busy={false}
        jobs={[
          {
            id: "x",
            name: "bad.txt",
            jobId: "j",
            status: "failed",
            stage: "quarantined",
            progress: null,
            error: "Karantinada",
            settled: true,
            retryable: false,
          },
        ]}
        onUpload={async () => {}}
        onDismiss={() => {}}
        onRetry={async () => {}}
      />,
    );
    expect(html).toContain("Karantinada");
    expect(html).toContain("ilerleme bildirilmedi");
    expect(html).not.toContain("0%");
  });
});
