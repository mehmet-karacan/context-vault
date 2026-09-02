// GENERATED from OpenAPI; do not edit.
// schema-sha256: 767665398fcb1da669afd2f0927b397d7754b48718a689ed06f5d9746aab2c7d
export interface Schemas {
  AnswerClaim: { claim_text: string; source_labels: Array<string> };
  Body_upload_archive_api_v1_archives_upload_post: {
    data_classification?: "public" | "internal" | "confidential" | "restricted";
    file: string;
    project_id: string;
  };
  Body_upload_document_api_v1_documents_upload_post: {
    data_classification?: "public" | "internal" | "confidential" | "restricted";
    file: string;
    project_id: string;
  };
  ChatModelsResponse: { default: string; models: Array<string> };
  ChatQuery: {
    conversation_id?: string | null;
    debug?: boolean;
    document_ids?: Array<string> | null;
    model?: string | null;
    project_id: string;
    query: string;
    scope?: "all" | "documents" | "images" | "code";
  };
  ChatResponse: {
    answer: string;
    answerable: boolean;
    citations: Array<Schemas["CitationResponse"]>;
    claims: Array<Schemas["AnswerClaim"]>;
    conversation_id: string;
    no_answer_reason: Schemas["NoAnswerReason"] | null;
    retrieval_debug: { [key: string]: unknown } | null;
    safety_flags: Array<string>;
    uncertainty: Array<string>;
  };
  CitationResponse: {
    bbox: { [key: string]: unknown } | Array<unknown> | null;
    document_id: string | null;
    document_name: string | null;
    file_path: string | null;
    heading_path: Array<string>;
    label: string;
    line_end: number | null;
    line_start: number | null;
    page_end: number | null;
    page_start: number | null;
    rank: number;
    snippet: string;
    source_file_id: string | null;
    source_type: string;
    symbol_name: string | null;
    version_id: string | null;
  };
  DiagnosticRank: {
    chunk_id: string;
    rank: number | null;
    score: number | null;
  };
  DiagnosticsResponse: {
    bundle_hash: string | null;
    fallback_reason: string | null;
    retrieval_run_id: string | null;
    stages: { [key: string]: Array<Schemas["DiagnosticRank"]> };
    timings_ms: { [key: string]: number };
  };
  DirectoryScanRequest: {
    allowed_root_alias: string;
    data_classification?: "public" | "internal" | "confidential" | "restricted";
    exclude_patterns?: Array<string>;
    include_patterns?: Array<string>;
    project_id: string;
    relative_path: string;
  };
  DocumentResponse: {
    active_version_id: string | null;
    chunks_count: number;
    error_message: string | null;
    id: string;
    job_error: string | null;
    job_id: string | null;
    job_stage: string | null;
    job_status: string | null;
    name: string;
    project_id: string;
    project_name: string | null;
    size: number;
    status: string;
    uploaded_at: string;
  };
  HTTPValidationError: { detail?: Array<Schemas["ValidationError"]> };
  IngestionEventResponse: {
    created_at: string | null;
    id: string;
    job_id: string;
    message: string | null;
    stage: string;
    status: string;
  };
  IngestionJobResponse: {
    attempt: number;
    created_at: string | null;
    document_id: string | null;
    error_code: string | null;
    error_message: string | null;
    finished_at: string | null;
    id: string;
    progress: number | null;
    stage: string;
    started_at: string | null;
    status: string;
    version_id: string | null;
  };
  NoAnswerReason:
    | "smalltalk"
    | "policy_refusal"
    | "insufficient_evidence"
    | "provider_failure"
    | "permission_denied"
    | "malformed_response";
  ProjectCreate: { name: string };
  ProjectResponse: {
    created_at: string;
    document_count: number;
    id: string;
    name: string;
  };
  RepoIngestRequest: {
    credential_ref?: string | null;
    data_classification?: "public" | "internal" | "confidential" | "restricted";
    exclude_patterns?: Array<string>;
    include_patterns?: Array<string>;
    project_id: string;
    ref?: string | null;
    repository_url: string;
  };
  RetrievalDebugRequest: {
    document_ids?: Array<string> | null;
    project_id: string;
    query: string;
  };
  SessionResponse: {
    auth_mode: "disabled" | "api_key" | "oidc";
    principal_id: string;
    roles: Array<string>;
    upload_max_bytes: number;
    workspace_id: string;
  };
  UploadResponse: {
    document: Schemas["DocumentResponse"];
    document_id: string;
    job_id: string;
    quarantine_reason: string | null;
    replayed: boolean;
    status: string;
    version_id: string;
  };
  ValidationError: {
    ctx?: Record<string, unknown>;
    input?: unknown;
    loc: Array<string | number>;
    msg: string;
    type: string;
  };
}
export const INPUT_LIMITS = { projectName: 200, query: 8000 } as const;
export const SCHEMA_DEFINITIONS = {
  AnswerClaim: {
    additionalProperties: false,
    properties: {
      claim_text: {
        maxLength: 8000,
        minLength: 1,
        title: "Claim Text",
        type: "string",
      },
      source_labels: {
        items: { type: "string" },
        minItems: 1,
        title: "Source Labels",
        type: "array",
      },
    },
    required: ["claim_text", "source_labels"],
    title: "AnswerClaim",
    type: "object",
  },
  Body_upload_archive_api_v1_archives_upload_post: {
    properties: {
      data_classification: {
        default: "internal",
        enum: ["public", "internal", "confidential", "restricted"],
        title: "Data Classification",
        type: "string",
      },
      file: {
        contentMediaType: "application/octet-stream",
        title: "File",
        type: "string",
      },
      project_id: { format: "uuid", title: "Project Id", type: "string" },
    },
    required: ["file", "project_id"],
    title: "Body_upload_archive_api_v1_archives_upload_post",
    type: "object",
  },
  Body_upload_document_api_v1_documents_upload_post: {
    properties: {
      data_classification: {
        default: "internal",
        enum: ["public", "internal", "confidential", "restricted"],
        title: "Data Classification",
        type: "string",
      },
      file: {
        contentMediaType: "application/octet-stream",
        title: "File",
        type: "string",
      },
      project_id: { format: "uuid", title: "Project Id", type: "string" },
    },
    required: ["file", "project_id"],
    title: "Body_upload_document_api_v1_documents_upload_post",
    type: "object",
  },
  ChatModelsResponse: {
    properties: {
      default: { title: "Default", type: "string" },
      models: { items: { type: "string" }, title: "Models", type: "array" },
    },
    required: ["models", "default"],
    title: "ChatModelsResponse",
    type: "object",
  },
  ChatQuery: {
    additionalProperties: false,
    properties: {
      conversation_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Conversation Id",
      },
      debug: { default: false, title: "Debug", type: "boolean" },
      document_ids: {
        anyOf: [
          { items: { format: "uuid", type: "string" }, type: "array" },
          { type: "null" },
        ],
        title: "Document Ids",
      },
      model: { anyOf: [{ type: "string" }, { type: "null" }], title: "Model" },
      project_id: { format: "uuid", title: "Project Id", type: "string" },
      query: { maxLength: 8000, minLength: 1, title: "Query", type: "string" },
      scope: {
        default: "all",
        enum: ["all", "documents", "images", "code"],
        title: "Scope",
        type: "string",
      },
    },
    required: ["query", "project_id"],
    title: "ChatQuery",
    type: "object",
  },
  ChatResponse: {
    additionalProperties: false,
    properties: {
      answer: { title: "Answer", type: "string" },
      answerable: { title: "Answerable", type: "boolean" },
      citations: {
        items: { $ref: "#/components/schemas/CitationResponse" },
        title: "Citations",
        type: "array",
      },
      claims: {
        items: { $ref: "#/components/schemas/AnswerClaim" },
        title: "Claims",
        type: "array",
      },
      conversation_id: { title: "Conversation Id", type: "string" },
      no_answer_reason: {
        anyOf: [
          { $ref: "#/components/schemas/NoAnswerReason" },
          { type: "null" },
        ],
      },
      retrieval_debug: {
        anyOf: [
          { additionalProperties: true, type: "object" },
          { type: "null" },
        ],
        title: "Retrieval Debug",
      },
      safety_flags: {
        items: { type: "string" },
        title: "Safety Flags",
        type: "array",
      },
      uncertainty: {
        items: { type: "string" },
        title: "Uncertainty",
        type: "array",
      },
    },
    required: [
      "conversation_id",
      "answer",
      "answerable",
      "no_answer_reason",
      "claims",
      "uncertainty",
      "safety_flags",
      "citations",
      "retrieval_debug",
    ],
    title: "ChatResponse",
    type: "object",
  },
  CitationResponse: {
    properties: {
      bbox: {
        anyOf: [
          { additionalProperties: true, type: "object" },
          { items: {}, type: "array" },
          { type: "null" },
        ],
        title: "Bbox",
      },
      document_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Document Id",
      },
      document_name: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Document Name",
      },
      file_path: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "File Path",
      },
      heading_path: {
        items: { type: "string" },
        title: "Heading Path",
        type: "array",
      },
      label: { title: "Label", type: "string" },
      line_end: {
        anyOf: [{ type: "integer" }, { type: "null" }],
        title: "Line End",
      },
      line_start: {
        anyOf: [{ type: "integer" }, { type: "null" }],
        title: "Line Start",
      },
      page_end: {
        anyOf: [{ type: "integer" }, { type: "null" }],
        title: "Page End",
      },
      page_start: {
        anyOf: [{ type: "integer" }, { type: "null" }],
        title: "Page Start",
      },
      rank: { title: "Rank", type: "integer" },
      snippet: { title: "Snippet", type: "string" },
      source_file_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Source File Id",
      },
      source_type: { title: "Source Type", type: "string" },
      symbol_name: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Symbol Name",
      },
      version_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Version Id",
      },
    },
    required: [
      "label",
      "document_id",
      "document_name",
      "version_id",
      "source_file_id",
      "source_type",
      "heading_path",
      "page_start",
      "page_end",
      "file_path",
      "symbol_name",
      "line_start",
      "line_end",
      "bbox",
      "snippet",
      "rank",
    ],
    title: "CitationResponse",
    type: "object",
  },
  DiagnosticRank: {
    properties: {
      chunk_id: { title: "Chunk Id", type: "string" },
      rank: { anyOf: [{ type: "integer" }, { type: "null" }], title: "Rank" },
      score: { anyOf: [{ type: "number" }, { type: "null" }], title: "Score" },
    },
    required: ["chunk_id", "rank", "score"],
    title: "DiagnosticRank",
    type: "object",
  },
  DiagnosticsResponse: {
    properties: {
      bundle_hash: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Bundle Hash",
      },
      fallback_reason: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Fallback Reason",
      },
      retrieval_run_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Retrieval Run Id",
      },
      stages: {
        additionalProperties: {
          items: { $ref: "#/components/schemas/DiagnosticRank" },
          type: "array",
        },
        title: "Stages",
        type: "object",
      },
      timings_ms: {
        additionalProperties: { type: "number" },
        title: "Timings Ms",
        type: "object",
      },
    },
    required: [
      "retrieval_run_id",
      "bundle_hash",
      "stages",
      "fallback_reason",
      "timings_ms",
    ],
    title: "DiagnosticsResponse",
    type: "object",
  },
  DirectoryScanRequest: {
    additionalProperties: false,
    properties: {
      allowed_root_alias: { title: "Allowed Root Alias", type: "string" },
      data_classification: {
        default: "internal",
        enum: ["public", "internal", "confidential", "restricted"],
        title: "Data Classification",
        type: "string",
      },
      exclude_patterns: {
        items: { type: "string" },
        title: "Exclude Patterns",
        type: "array",
      },
      include_patterns: {
        items: { type: "string" },
        title: "Include Patterns",
        type: "array",
      },
      project_id: { format: "uuid", title: "Project Id", type: "string" },
      relative_path: { title: "Relative Path", type: "string" },
    },
    required: ["project_id", "allowed_root_alias", "relative_path"],
    title: "DirectoryScanRequest",
    type: "object",
  },
  DocumentResponse: {
    properties: {
      active_version_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Active Version Id",
      },
      chunks_count: { title: "Chunks Count", type: "integer" },
      error_message: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Error Message",
      },
      id: { title: "Id", type: "string" },
      job_error: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Job Error",
      },
      job_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Job Id",
      },
      job_stage: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Job Stage",
      },
      job_status: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Job Status",
      },
      name: { title: "Name", type: "string" },
      project_id: { title: "Project Id", type: "string" },
      project_name: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Project Name",
      },
      size: { title: "Size", type: "integer" },
      status: { title: "Status", type: "string" },
      uploaded_at: { title: "Uploaded At", type: "string" },
    },
    required: [
      "id",
      "name",
      "size",
      "status",
      "uploaded_at",
      "chunks_count",
      "error_message",
      "project_id",
      "project_name",
      "active_version_id",
      "job_id",
      "job_status",
      "job_stage",
      "job_error",
    ],
    title: "DocumentResponse",
    type: "object",
  },
  HTTPValidationError: {
    properties: {
      detail: {
        items: { $ref: "#/components/schemas/ValidationError" },
        title: "Detail",
        type: "array",
      },
    },
    title: "HTTPValidationError",
    type: "object",
  },
  IngestionEventResponse: {
    properties: {
      created_at: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Created At",
      },
      id: { title: "Id", type: "string" },
      job_id: { title: "Job Id", type: "string" },
      message: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Message",
      },
      stage: { title: "Stage", type: "string" },
      status: { title: "Status", type: "string" },
    },
    required: ["id", "job_id", "stage", "status", "message", "created_at"],
    title: "IngestionEventResponse",
    type: "object",
  },
  IngestionJobResponse: {
    properties: {
      attempt: { title: "Attempt", type: "integer" },
      created_at: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Created At",
      },
      document_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Document Id",
      },
      error_code: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Error Code",
      },
      error_message: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Error Message",
      },
      finished_at: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Finished At",
      },
      id: { title: "Id", type: "string" },
      progress: {
        anyOf: [{ type: "integer" }, { type: "null" }],
        title: "Progress",
      },
      stage: { title: "Stage", type: "string" },
      started_at: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Started At",
      },
      status: { title: "Status", type: "string" },
      version_id: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Version Id",
      },
    },
    required: [
      "id",
      "version_id",
      "document_id",
      "status",
      "stage",
      "progress",
      "attempt",
      "error_code",
      "error_message",
      "started_at",
      "finished_at",
      "created_at",
    ],
    title: "IngestionJobResponse",
    type: "object",
  },
  NoAnswerReason: {
    enum: [
      "smalltalk",
      "policy_refusal",
      "insufficient_evidence",
      "provider_failure",
      "permission_denied",
      "malformed_response",
    ],
    title: "NoAnswerReason",
    type: "string",
  },
  ProjectCreate: {
    additionalProperties: false,
    properties: {
      name: { maxLength: 200, minLength: 1, title: "Name", type: "string" },
    },
    required: ["name"],
    title: "ProjectCreate",
    type: "object",
  },
  ProjectResponse: {
    properties: {
      created_at: { title: "Created At", type: "string" },
      document_count: { title: "Document Count", type: "integer" },
      id: { title: "Id", type: "string" },
      name: { title: "Name", type: "string" },
    },
    required: ["id", "name", "created_at", "document_count"],
    title: "ProjectResponse",
    type: "object",
  },
  RepoIngestRequest: {
    additionalProperties: false,
    properties: {
      credential_ref: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Credential Ref",
      },
      data_classification: {
        default: "internal",
        enum: ["public", "internal", "confidential", "restricted"],
        title: "Data Classification",
        type: "string",
      },
      exclude_patterns: {
        items: { type: "string" },
        title: "Exclude Patterns",
        type: "array",
      },
      include_patterns: {
        items: { type: "string" },
        title: "Include Patterns",
        type: "array",
      },
      project_id: { format: "uuid", title: "Project Id", type: "string" },
      ref: { anyOf: [{ type: "string" }, { type: "null" }], title: "Ref" },
      repository_url: { title: "Repository Url", type: "string" },
    },
    required: ["project_id", "repository_url"],
    title: "RepoIngestRequest",
    type: "object",
  },
  RetrievalDebugRequest: {
    additionalProperties: false,
    properties: {
      document_ids: {
        anyOf: [
          { items: { format: "uuid", type: "string" }, type: "array" },
          { type: "null" },
        ],
        title: "Document Ids",
      },
      project_id: { format: "uuid", title: "Project Id", type: "string" },
      query: { title: "Query", type: "string" },
    },
    required: ["query", "project_id"],
    title: "RetrievalDebugRequest",
    type: "object",
  },
  SessionResponse: {
    properties: {
      auth_mode: {
        enum: ["disabled", "api_key", "oidc"],
        title: "Auth Mode",
        type: "string",
      },
      principal_id: { title: "Principal Id", type: "string" },
      roles: { items: { type: "string" }, title: "Roles", type: "array" },
      upload_max_bytes: { title: "Upload Max Bytes", type: "integer" },
      workspace_id: { title: "Workspace Id", type: "string" },
    },
    required: [
      "principal_id",
      "workspace_id",
      "roles",
      "auth_mode",
      "upload_max_bytes",
    ],
    title: "SessionResponse",
    type: "object",
  },
  UploadResponse: {
    properties: {
      document: { $ref: "#/components/schemas/DocumentResponse" },
      document_id: { title: "Document Id", type: "string" },
      job_id: { title: "Job Id", type: "string" },
      quarantine_reason: {
        anyOf: [{ type: "string" }, { type: "null" }],
        title: "Quarantine Reason",
      },
      replayed: { title: "Replayed", type: "boolean" },
      status: { title: "Status", type: "string" },
      version_id: { title: "Version Id", type: "string" },
    },
    required: [
      "job_id",
      "document_id",
      "version_id",
      "status",
      "replayed",
      "quarantine_reason",
      "document",
    ],
    title: "UploadResponse",
    type: "object",
  },
  ValidationError: {
    properties: {
      ctx: { title: "Context", type: "object" },
      input: { title: "Input" },
      loc: {
        items: { anyOf: [{ type: "string" }, { type: "integer" }] },
        title: "Location",
        type: "array",
      },
      msg: { title: "Message", type: "string" },
      type: { title: "Error Type", type: "string" },
    },
    required: ["loc", "msg", "type"],
    title: "ValidationError",
    type: "object",
  },
} as const;
