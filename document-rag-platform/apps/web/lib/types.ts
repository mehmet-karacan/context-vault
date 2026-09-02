import type { Schemas } from "./api/generated";
export type Citation = Schemas["CitationResponse"];
export type ChatResponse = Schemas["ChatResponse"];
export type IngestionJob = Schemas["IngestionJobResponse"];
export type IngestionJobEvent = Schemas["IngestionEventResponse"];
export type UploadResponse = Schemas["UploadResponse"];
export type SourceTypeFilter = NonNullable<Schemas["ChatQuery"]["scope"]>;
export const SOURCE_TYPE_FILTERS: { value: SourceTypeFilter; label: string }[] =
  [
    { value: "all", label: "Tüm kaynaklar" },
    { value: "documents", label: "Belgeler" },
    { value: "code", label: "Kod" },
    { value: "images", label: "Görseller" },
  ];
export const scopeValue = (filter: SourceTypeFilter) => filter;
