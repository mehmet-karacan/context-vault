"use client";

import { useEffect, useState } from "react";
import Nav from "../components/Nav";
import {
  UploadIcon,
  ChatIcon,
  FileIcon,
  CheckIcon,
  SpinnerIcon,
  CloseIcon,
  SettingsIcon,
  ChevronDownIcon,
} from "../components/icons";
import { apiUrl } from "../lib/api";

interface Document {
  id: string;
  name: string;
  size: number;
  status: "uploaded" | "processing" | "indexed" | "error";
  uploadedAt: string;
  projectId: string;
  projectName: string | null;
}

interface Project {
  id: string;
  name: string;
  documentCount: number;
}

const NEW_PROJECT_VALUE = "__new__";

const DEFAULT_INSTRUCTION = "Bu metni bir belge arama sisteminde bulunmak üzere temsil et: ";
const DEFAULT_CHUNK_SIZE = 500;

const STATUS_LABEL: Record<Document["status"], { text: string; className: string }> = {
  uploaded: { text: "Yüklendi", className: "bg-paper-dim text-ink/70" },
  processing: { text: "Vektörleniyor", className: "bg-brass/15 text-brass-dim" },
  indexed: { text: "Hazır", className: "bg-moss/15 text-moss" },
  error: { text: "Hata", className: "bg-rust/15 text-rust" },
};

function StyledSelect({
  value,
  onChange,
  className = "",
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`relative ${className}`}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full appearance-none bg-surface border border-ink-line rounded-md pl-3 pr-8 py-2 text-sm text-ink truncate focus:outline-none focus:border-brass cursor-pointer"
      >
        {children}
      </select>
      <ChevronDownIcon className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-soft pointer-events-none" />
    </div>
  );
}

function SettingsModal({
  extraInstruction,
  setExtraInstruction,
  chunkSize,
  setChunkSize,
  onClose,
}: {
  extraInstruction: string;
  setExtraInstruction: (v: string) => void;
  chunkSize: number;
  setChunkSize: (v: number) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[60] bg-ink/40 flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="folio bg-surface text-ink shadow-folio w-full max-w-lg max-h-[85vh] overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <span className="font-display italic text-xl text-ink">Gelişmiş ayarlar</span>
          <button onClick={onClose} aria-label="Kapat" className="text-ink-soft hover:text-ink transition-colors">
            <CloseIcon className="w-5 h-5" />
          </button>
        </div>

        <p className="text-ink-soft text-sm leading-relaxed mb-6">
          Yüklediğin belge küçük parçalara bölünür. Her parça, anlamını temsil eden bir sayı
          dizisine (vektöre) çevrilir. Soru sorduğunda senin sorun da aynı şekilde bir vektöre
          çevrilir ve en yakın anlamdaki parçalar bulunup yanıt onlardan üretilir. Aşağıdaki
          ayarlar bu bölme ve vektörleme işleminin nasıl yapılacağını belirler.
        </p>

        <div className="flex flex-col gap-5">
          <div>
            <label className="block text-xs font-mono uppercase tracking-wide text-ink-soft mb-1.5">
              Sabit vektörleme talimatı
            </label>
            <div className="bg-paper-dim border border-ink-line rounded-md px-3 py-2 text-sm text-ink/70 italic">
              {DEFAULT_INSTRUCTION}
            </div>

            <label className="block text-xs font-mono uppercase tracking-wide text-ink-soft mt-3 mb-1.5">
              Ek talimat (isteğe bağlı)
            </label>
            <textarea
              value={extraInstruction}
              onChange={(e) => setExtraInstruction(e.target.value)}
              rows={2}
              placeholder="Örn. bu belge bir sözleşmedir, madde numaralarına dikkat et…"
              className="w-full bg-surface border border-ink-line rounded-md px-3 py-2 text-sm text-ink placeholder-ink-soft focus:outline-none focus:border-brass resize-none"
            />
            <p className="text-ink-soft/60 text-[11px] mt-1">
              Aşağıya yazacakların, yukarıdaki sabit talimata eklenerek kullanılır.
            </p>
          </div>

          <div>
            <label className="block text-xs font-mono uppercase tracking-wide text-ink-soft mb-1.5">
              Parça boyutu (karakter)
            </label>
            <input
              type="number"
              min={50}
              max={4000}
              value={chunkSize}
              onChange={(e) => setChunkSize(Number(e.target.value) || DEFAULT_CHUNK_SIZE)}
              className="w-32 bg-surface border border-ink-line rounded-md px-3 py-2 text-sm text-ink focus:outline-none focus:border-brass"
            />
            <p className="text-ink-soft/60 text-[11px] mt-1.5 leading-relaxed">
              Küçük parça: daha net ama bağlamdan kopabilir. Büyük parça: daha fazla bağlam
              taşır ama birden fazla konuyu karıştırıp aramayı bulanıklaştırabilir. Varsayılan{" "}
              {DEFAULT_CHUNK_SIZE} çoğu belge için dengeli bir seçimdir.
            </p>
          </div>
        </div>

        <button
          onClick={onClose}
          className="mt-6 w-full bg-brass text-ink px-5 py-2.5 rounded-md font-medium text-sm hover:bg-brass-dim transition-colors"
        >
          Tamam
        </button>
      </div>
    </div>
  );
}

export default function Home() {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [documentsFilterProjectId, setDocumentsFilterProjectId] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [extraInstruction, setExtraInstruction] = useState("");
  const [chunkSize, setChunkSize] = useState(DEFAULT_CHUNK_SIZE);

  useEffect(() => {
    fetchProjects();
  }, []);

  useEffect(() => {
    fetchDocuments(documentsFilterProjectId);
  }, [documentsFilterProjectId]);

  const fetchProjects = async () => {
    try {
      const response = await fetch(apiUrl("/projects"));
      const data = await response.json();
      const mapped: Project[] = data.map((p: any) => ({
        id: p.id,
        name: p.name,
        documentCount: p.document_count,
      }));
      setProjects(mapped);
      if (!selectedProjectId && mapped.length > 0) {
        setSelectedProjectId(mapped[0].id);
      }
    } catch (error) {
      console.error("Failed to fetch projects:", error);
      setProjects([]);
    }
  };

  const fetchDocuments = async (projectId?: string) => {
    try {
      const url = projectId ? `/documents?project_id=${projectId}` : "/documents";
      const response = await fetch(apiUrl(url));
      const data = await response.json();
      setDocuments(
        data.map((doc: any) => ({
          id: doc.id,
          name: doc.name,
          size: doc.size,
          status: doc.status as Document["status"],
          uploadedAt: doc.uploaded_at,
          projectId: doc.project_id,
          projectName: doc.project_name,
        }))
      );
    } catch (error) {
      console.error("Failed to fetch documents:", error);
      setDocuments([]);
    }
  };

  const createProject = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    try {
      const response = await fetch(apiUrl("/projects"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (response.ok) {
        const created = await response.json();
        setNewProjectName("");
        setIsCreatingProject(false);
        await fetchProjects();
        setSelectedProjectId(created.id);
      }
    } catch (error) {
      console.error("Create project error:", error);
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (!selectedProjectId) return;
    setIsUploading(true);
    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("project_id", selectedProjectId);
      const finalInstruction = extraInstruction.trim()
        ? `${DEFAULT_INSTRUCTION}${extraInstruction.trim()} `
        : DEFAULT_INSTRUCTION;
      formData.append("chunk_size", String(chunkSize));
      formData.append("instruction", finalInstruction);
      try {
        const response = await fetch(apiUrl("/documents/upload"), {
          method: "POST",
          body: formData,
        });
        if (response.ok) {
          await fetchDocuments(documentsFilterProjectId);
          await fetchProjects();
        }
      } catch (error) {
        console.error("Upload error:", error);
      }
    }
    setIsUploading(false);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => setIsDragging(false);

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    await uploadFiles(Array.from(e.dataTransfer.files));
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      await uploadFiles(Array.from(e.target.files));
    }
  };

  const deleteDocument = async (id: string) => {
    try {
      const response = await fetch(apiUrl(`/documents/${id}/delete`), { method: "POST" });
      if (response.ok) {
        await fetchDocuments(documentsFilterProjectId);
        await fetchProjects();
      }
    } catch (error) {
      console.error("Delete error:", error);
    }
  };

  const visibleDocs = documents ? (showAll ? documents : documents.slice(0, 4)) : [];

  return (
    <div className="min-h-screen bg-paper flex flex-col pr-[640px]">
      <Nav />

      <main className="flex-1 max-w-6xl px-8 lg:px-14 w-full">
        {/* Hero */}
        <section className="grid md:grid-cols-2 gap-12 items-center py-16">
          <div>
            <p className="font-mono text-xs tracking-widest uppercase text-brass-dim mb-4">
              Belge Arşivi
            </p>
            <h1 className="font-display text-5xl md:text-6xl leading-[1.1] text-ink mb-6 [text-wrap:balance]">
              Belgeni yükle, <span className="italic text-brass-dim">yalnızca içeriğinden</span> yanıt al.
            </h1>
            <p className="text-ink-soft text-lg leading-relaxed max-w-md">
              Yüklediğin PDF, DOCX veya metin dosyaları parçalara ayrılır ve vektörlenir.
              Sorunu sorduğunda, cevap yalnızca senin belgelerinden gelir — uydurma yok.
            </p>
          </div>

          {/* Real, functional dropzone — right where the eye lands first */}
          <div>
            <div className="mb-3">
              <label className="block text-xs font-mono uppercase tracking-wide text-ink-soft mb-1.5">
                Proje
              </label>
              {isCreatingProject ? (
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    autoFocus
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && createProject()}
                    placeholder="Yeni proje adı"
                    className="flex-1 bg-surface border border-ink-line rounded-md px-3 py-2 text-sm text-ink placeholder-ink-soft focus:outline-none focus:border-brass"
                  />
                  <button
                    onClick={createProject}
                    disabled={!newProjectName.trim()}
                    className="bg-brass text-ink px-3 py-2 rounded-md text-sm font-medium hover:bg-brass-dim disabled:opacity-40 transition-colors"
                  >
                    Oluştur
                  </button>
                  <button
                    onClick={() => setIsCreatingProject(false)}
                    aria-label="İptal"
                    className="text-ink-soft hover:text-ink transition-colors"
                  >
                    <CloseIcon className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <StyledSelect
                  value={selectedProjectId}
                  onChange={(value) => {
                    if (value === NEW_PROJECT_VALUE) {
                      setIsCreatingProject(true);
                    } else {
                      setSelectedProjectId(value);
                    }
                  }}
                >
                  {(!projects || projects.length === 0) && <option value="">Önce bir proje oluştur</option>}
                  {projects?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.documentCount})
                    </option>
                  ))}
                  <option value={NEW_PROJECT_VALUE}>+ Yeni proje oluştur…</option>
                </StyledSelect>
              )}
            </div>

            <div
              className={`rounded-lg p-6 text-center transition-colors border-2 border-dashed ${
                isDragging ? "border-brass bg-brass/5" : "border-ink-line bg-paper-dim"
              } ${!selectedProjectId ? "opacity-50 pointer-events-none" : ""}`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
            <div className="w-11 h-11 rounded-md bg-brass/15 text-brass-dim flex items-center justify-center mx-auto mb-3">
              <UploadIcon className="w-5 h-5" />
            </div>
            <h3 className="text-ink font-medium text-base mb-1">Belgeleri buraya sürükle</h3>
            <p className="text-ink-soft text-sm mb-4">veya aşağıdan dosya seç</p>
            <label className="inline-flex items-center gap-2 bg-brass text-ink px-5 py-2.5 rounded-md font-medium text-base cursor-pointer hover:bg-brass-dim transition-colors">
              <UploadIcon className="w-4 h-4" />
              {isUploading ? "Yükleniyor..." : "Dosya seç"}
              <input
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelect}
                disabled={isUploading || !selectedProjectId}
              />
            </label>
            <p className="text-ink-soft/60 text-xs mt-3 font-mono">PDF · DOCX · TXT</p>

            <div className="mt-4 pt-4 border-t border-ink-line/60">
              <button
                type="button"
                onClick={() => setShowSettings(true)}
                className="inline-flex items-center gap-1.5 text-xs font-mono uppercase tracking-wide text-ink-soft hover:text-ink transition-colors"
              >
                <SettingsIcon className="w-3.5 h-3.5" />
                Gelişmiş ayarlar
              </button>
            </div>
            </div>
          </div>
        </section>

        {showSettings && (
          <SettingsModal
            extraInstruction={extraInstruction}
            setExtraInstruction={setExtraInstruction}
            chunkSize={chunkSize}
            setChunkSize={setChunkSize}
            onClose={() => setShowSettings(false)}
          />
        )}

        {/* Real, live document list */}
        <section className="border-t border-ink-line py-10">
          <div className="flex items-center justify-between mb-8 gap-4 flex-wrap">
            <p className="font-mono text-xs tracking-widest uppercase text-ink-soft">
              Arşivdeki belgeler {documents ? `(${documents.length})` : ""}
            </p>
            <div className="flex items-center gap-4">
              {projects && projects.length > 0 && (
                <StyledSelect value={documentsFilterProjectId} onChange={setDocumentsFilterProjectId} className="w-48">
                  <option value="">Tüm projeler</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </StyledSelect>
              )}
              {documents && documents.length > 4 && (
                <button
                  onClick={() => setShowAll((v) => !v)}
                  className="text-sm text-brass-dim hover:text-ink transition-colors"
                >
                  {showAll ? "← Daha az göster" : "Tümünü gör →"}
                </button>
              )}
            </div>
          </div>

          {documents === null ? (
            <p className="text-ink-soft text-base">Yükleniyor...</p>
          ) : documents.length === 0 ? (
            <p className="text-ink-soft text-base">Arşiv henüz boş. Yukarıdan ilk belgeni yükle.</p>
          ) : (
            <div className="grid sm:grid-cols-2 md:grid-cols-4 gap-4">
              {visibleDocs.map((doc) => {
                const status = STATUS_LABEL[doc.status];
                return (
                  <div key={doc.id} className="folio bg-surface text-ink shadow-folio p-4 flex flex-col gap-3">
                    <div className="flex items-start gap-2.5">
                      <FileIcon className="w-4 h-4 text-ink/50 shrink-0 mt-0.5" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate">{doc.name}</div>
                        <div className="font-mono text-[11px] text-ink/50">
                          {(doc.size / 1024).toFixed(1)} KB · {doc.uploadedAt}
                        </div>
                        {doc.projectName && (
                          <div className="font-mono text-[10px] text-brass-dim truncate mt-0.5">
                            {doc.projectName}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded font-mono text-[10px] uppercase tracking-wide ${status.className}`}
                      >
                        {doc.status === "processing" && <SpinnerIcon className="w-3 h-3 animate-spin" />}
                        {doc.status === "indexed" && <CheckIcon className="w-3 h-3" />}
                        {status.text}
                      </span>
                      <button
                        onClick={() => deleteDocument(doc.id)}
                        aria-label={`${doc.name} belgesini sil`}
                        className="text-ink/40 hover:text-rust transition-colors"
                      >
                        <CloseIcon className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* How it works */}
        <section className="border-t border-ink-line py-14">
          <p className="font-mono text-xs tracking-widest uppercase text-ink-soft mb-8">
            Nasıl çalışır
          </p>
          <div className="grid sm:grid-cols-3 gap-8">
            <div className="flex gap-4">
              <span className="font-display italic text-3xl text-brass-dim shrink-0">1</span>
              <div>
                <h2 className="text-ink text-lg font-medium mb-1.5 flex items-center gap-2">
                  <UploadIcon className="w-4 h-4 text-brass-dim" /> Yükle
                </h2>
                <p className="text-ink-soft text-base leading-relaxed">
                  Belgen ayrıştırılır, parçalara bölünür ve arka planda bir embedding
                  modeliyle vektörlenerek dizine eklenir.
                </p>
              </div>
            </div>
            <div className="flex gap-4">
              <span className="font-display italic text-3xl text-brass-dim shrink-0">2</span>
              <div>
                <h2 className="text-ink text-lg font-medium mb-1.5 flex items-center gap-2">
                  <ChatIcon className="w-4 h-4 text-brass-dim" /> Sor
                </h2>
                <p className="text-ink-soft text-base leading-relaxed">
                  Sağdaki sohbetten sor; en ilgili parçalar aranır ve yanıt, hangi
                  belgeden geldiği belli olacak şekilde önüne konur.
                </p>
              </div>
            </div>
            <div className="flex gap-4">
              <span className="font-display italic text-3xl text-brass-dim shrink-0">3</span>
              <div>
                <h2 className="text-ink text-lg font-medium mb-1.5 flex items-center gap-2">
                  <FileIcon className="w-4 h-4 text-brass-dim" /> Doğrula
                </h2>
                <p className="text-ink-soft text-base leading-relaxed">
                  Her yanıtın altında hangi belgeden geldiği görünür; istersen
                  tıklayıp ilgili pasajı açabilirsin.
                </p>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-ink-line">
        <div className="max-w-6xl px-8 lg:px-14 py-6 flex items-center justify-between">
          <span className="font-display italic text-sm text-ink-soft">Arşiv</span>
          <span className="font-mono text-[11px] text-ink-soft uppercase tracking-widest">
            PostgreSQL + pgvector · yerel
          </span>
        </div>
      </footer>
    </div>
  );
}
