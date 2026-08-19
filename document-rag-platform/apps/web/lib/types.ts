// Shared client-side types for the chat / retrieval / ingestion responses.
// Mirrors the backend schemas documented in AKTIF_GOREV.md §12.4 (chat) and
// §12.1 (upload) plus §Aşama 2.4 (ingestion job status/events).

export type SourceType = "document" | "code" | "image";

export interface Citation {
  label: string;
  document_id: string;
  document_name: string;
  source_type: SourceType | string;
  heading_path: string[] | null;
  page_start: number | null;
  page_end: number | null;
  file_path: string | null;
  symbol_name: string | null;
  line_start: number | null;
  line_end: number | null;
  snippet: string;
  rank: number;
}

export interface RetrievalDebugEntry {
  label?: string;
  chunk_id?: string;
  rank?: number;
  score?: number | null;
  source?: string;
  document_name?: string | null;
  rerank_score?: number | null;
}

// Matches the backend `RetrievalResult.debug_payload()` shape: the entries the
// debug panel renders live under the `stages` key, each holding an ordered
// ranked list (dense / lexical / identifier / fusion / rerank).
export interface RetrievalDebug {
  stages?: Record<string, RetrievalDebugEntry[]>;
}

export interface LegacySource {
  document: { name: string };
  chunk: string;
  similarity: number;
}

export interface ChatResponse {
  answer: string;
  answerable: boolean;
  citations: Citation[];
  retrieval_debug: RetrievalDebug | null;
  // Old-stage backend still returns `sources`; keep accepting it so the
  // frontend degrades gracefully until the backend is migrated.
  sources?: LegacySource[];
}

export type SourceTypeFilter = "all" | "documents" | "code" | "images";

export interface IngestionJob {
  id: string;
  version_id: string | null;
  document_id: string | null;
  status: string;
  stage: string;
  progress: number | null;
  attempt: number;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
}

export interface IngestionJobEvent {
  id: string;
  job_id: string;
  stage: string;
  status: string;
  message: string | null;
  created_at: string | null;
}

export interface UploadResponse {
  job_id?: string;
  document_id?: string;
  version_id?: string;
  status?: string;
}

export const SOURCE_TYPE_FILTERS: { value: SourceTypeFilter; label: string }[] = [
  { value: "all", label: "Tümü" },
  { value: "documents", label: "Belgeler" },
  { value: "code", label: "Kod" },
  { value: "images", label: "Görseller" },
];

// Maps the chat query filter (<-> backend scope values).
export function scopeValue(filter: SourceTypeFilter): string {
  if (filter === "documents") return "document";
  if (filter === "code") return "code";
  if (filter === "images") return "image";
  return "all";
}
