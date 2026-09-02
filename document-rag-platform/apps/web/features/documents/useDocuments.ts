"use client";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { apiRequest } from "../../lib/api/client";
import {
  documentReducer,
  emptyDocumentState,
  type DocumentItem,
} from "./documentState";
export type { DocumentItem } from "./documentState";
export function useDocuments(projectId: string) {
  const [state, dispatch] = useReducer(
    documentReducer,
    projectId,
    emptyDocumentState,
  );
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const start = useCallback(() => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const request = ++generation.current;
    dispatch({ type: "start", scope: projectId, request });
    return { controller, request };
  }, [projectId]);
  const load = useCallback(
    async (controller: AbortController, request: number) => {
      try {
        const documents = projectId
          ? await apiRequest<DocumentItem[]>(
              `/documents?project_id=${projectId}`,
              { signal: controller.signal },
              "DocumentResponse[]",
            )
          : [];
        if (!controller.signal.aborted)
          dispatch({ type: "loaded", scope: projectId, request, documents });
      } catch (cause) {
        if (!controller.signal.aborted)
          dispatch({ type: "failed", scope: projectId, request, cause });
      }
    },
    [projectId],
  );
  const refresh = useCallback(async () => {
    const { controller, request } = start();
    await load(controller, request);
  }, [start, load]);
  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) void refresh();
    });
    return () => {
      controller.abort();
      pending.current?.abort();
    };
  }, [refresh]);
  const remove = async (id: string) => {
    if (!projectId || state.denied || state.updating) return;
    const { controller, request } = start();
    try {
      await apiRequest(`/documents/${id}?project_id=${projectId}`, {
        method: "DELETE",
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      dispatch({ type: "removed", scope: projectId, request, id });
      await load(controller, request);
    } catch (cause) {
      if (!controller.signal.aborted)
        dispatch({ type: "failed", scope: projectId, request, cause });
    }
  };
  // Scope changes must never display a previous snapshot while an effect starts.
  const visible =
    state.scope === projectId ? state : emptyDocumentState(projectId);
  return {
    ...visible,
    refresh,
    remove,
    partial: visible.error !== null && visible.documents !== null,
  };
}
