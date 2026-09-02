"use client";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, problemMessage } from "../../lib/api/client";
import type { Schemas } from "../../lib/api/generated";
export type DocumentItem = Schemas["DocumentResponse"];
export function useDocuments(projectId: string) {
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    if (!projectId) {
      setDocuments([]);
      setError(null);
      return;
    }
    try {
      setDocuments(
        await apiRequest<DocumentItem[]>(
          `/documents?project_id=${projectId}`,
          undefined,
          "DocumentResponse[]",
        ),
      );
      setError(null);
    } catch (cause) {
      setDocuments([]);
      setError(problemMessage(cause));
    }
  }, [projectId]);
  useEffect(() => {
    queueMicrotask(() => void refresh());
  }, [refresh]);
  const remove = async (id: string) => {
    try {
      await apiRequest(`/documents/${id}?project_id=${projectId}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (cause) {
      setError(problemMessage(cause));
    }
  };
  return { documents, refresh, remove, error };
}
