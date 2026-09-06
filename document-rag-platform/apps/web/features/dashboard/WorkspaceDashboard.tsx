"use client";
import Nav from "../../components/Nav";
import ChatWidget from "../../components/ChatWidget";
import { DocumentGrid } from "../documents/DocumentGrid";
import { useDocuments } from "../documents/useDocuments";
import { ResourceNotice } from "../documents/ResourceNotice";
import { UploadPanel } from "../ingestion/UploadPanel";
import { useIngestion } from "../ingestion/useIngestion";
import { ProjectSelector } from "../projects/ProjectSelector";
import { useProjects } from "../projects/useProjects";
import { JobBoard } from "../ingestion/JobBoard";
type View = "all" | "projects" | "sources" | "chat" | "jobs";
function ProjectWorkspace({
  projectId,
  projectName,
  refreshProjects,
  view,
}: {
  projectId: string;
  projectName: string | null;
  refreshProjects: () => Promise<void>;
  view: View;
}) {
  const documents = useDocuments(projectId);
  const ingestion = useIngestion(projectId, async () => {
    await Promise.all([documents.refresh(), refreshProjects()]);
  });
  const versions = Object.fromEntries(
    (documents.documents ?? []).map((item) => [
      item.id,
      item.active_version_id,
    ]),
  );
  return (
    <>
      {view === "jobs" && <JobBoard projectId={projectId} />}
      {(view === "all" || view === "sources" || view === "projects") && (
        <div className="grid gap-8 2xl:grid-cols-[20rem_minmax(0,1fr)]">
          <div>
            <UploadPanel
              projectId={documents.denied ? "" : projectId}
              jobs={documents.denied ? [] : ingestion.jobs}
              busy={ingestion.busy}
              onUpload={ingestion.upload}
              onDismiss={ingestion.dismiss}
              onRetry={ingestion.retry}
            />
            {ingestion.error && (
              <p role="alert" className="mt-3 text-sm text-rust">
                {ingestion.error}
              </p>
            )}
          </div>
          <section aria-labelledby="documents-title">
            <h2
              id="documents-title"
              className="mb-5 border-b border-ink-line pb-3 font-display text-3xl"
            >
              Proje kaynakları
            </h2>
            <button
              disabled={!projectId || documents.updating}
              onClick={() => void documents.refresh()}
              className="mb-4 text-sm underline"
            >
              Kaynakları yenile
            </button>
            <ResourceNotice
              error={documents.error}
              partial={documents.partial}
              updating={documents.updating && documents.documents !== null}
            />
            {projectId && (!documents.error || documents.partial) ? (
              <DocumentGrid
                documents={documents.documents}
                onDelete={documents.remove}
                stale={documents.partial}
                disabled={documents.updating || documents.partial}
              />
            ) : !projectId ? (
              <p className="text-ink-soft">
                Kaynakları görmek için bir proje seçin.
              </p>
            ) : null}
          </section>
        </div>
      )}
      {(view === "all" || view === "chat") && (
        <ChatWidget
          key={documents.denied ? "access-denied" : "admitted"}
          projectId={documents.denied ? "" : projectId}
          projectName={projectName}
          activeVersions={versions}
        />
      )}
    </>
  );
}
export default function WorkspaceDashboard({ view = "all" }: { view?: View }) {
  const projects = useProjects();
  const selected = projects.projects?.find(
    (item) => item.id === projects.selectedProjectId,
  );
  return (
    <div
      className={`min-h-screen bg-paper ${view === "all" || view === "chat" ? "xl:pr-[640px]" : ""}`}
    >
      <Nav />
      <main className="mx-auto max-w-6xl px-6 py-10 lg:px-10">
        <header className="mb-10 space-y-6">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-brass-dim">
              Kanıt kasası
            </p>
            <h1 className="mt-3 max-w-2xl font-display text-4xl leading-tight xl:text-5xl">
              Kaynağı yükle.
              <br />
              İddiayı izle.
              <br />
              Kanıtı doğrula.
            </h1>
            <p className="mt-4 max-w-xl text-ink-soft">
              Her cevap aktif proje, belge sürümü ve gerçekten kullanılan
              kaynaklarla sınırlıdır.
            </p>
          </div>
          <div className="max-w-sm">
            <p className="mb-1 text-xs text-ink-soft">Çalışma kapsamı</p>
            <ProjectSelector
              projects={projects.projects}
              value={projects.selectedProjectId}
              onChange={projects.setSelectedProjectId}
              onCreate={projects.create}
            />
          </div>
        </header>
        <ResourceNotice
          error={projects.error}
          partial={projects.partial}
          updating={projects.updating}
        />
        {!projects.error &&
          !projects.updating &&
          projects.projects?.length === 0 && (
            <p className="mb-4 text-sm text-ink-soft">
              Bu workspace içinde henüz proje yok. Yeni proje
              oluşturabilirsiniz.
            </p>
          )}
        {projects.error && (
          <button
            className="mb-4 text-sm underline"
            disabled={projects.updating}
            onClick={() => void projects.refresh()}
          >
            Projeleri yeniden yükle
          </button>
        )}
        <ProjectWorkspace
          key={projects.selectedProjectId || "unscoped"}
          projectId={projects.selectedProjectId}
          projectName={selected?.name ?? null}
          refreshProjects={projects.refresh}
          view={view}
        />
      </main>
    </div>
  );
}
