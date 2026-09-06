"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  apiRequest,
  isTransientFailure,
  problemMessage,
} from "../../lib/api/client";
import type { ApiProject, Project } from "./types";
export function useProjects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setUpdating(true);
    try {
      const data = await apiRequest<ApiProject[]>(
        "/projects",
        { signal: controller.signal },
        "ProjectResponse[]",
      );
      if (controller.signal.aborted) return;
      const mapped = data.map((item) => ({
        id: item.id,
        name: item.name,
        documentCount: item.document_count,
      }));
      setProjects(mapped);
      setSelectedProjectId((current) =>
        current && mapped.some((item) => item.id === current) ? current : "",
      );
      setError(null);
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (!isTransientFailure(cause)) {
        setProjects(null);
        setSelectedProjectId("");
      }
      setError(problemMessage(cause));
    } finally {
      if (!controller.signal.aborted) setUpdating(false);
    }
  }, []);
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
  const create = async (name: string) => {
    const project = await apiRequest<ApiProject>(
      "/projects",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
      "ProjectResponse",
    );
    setProjects((items) => [
      ...(items ?? []).filter((item) => item.id !== project.id),
      {
        id: project.id,
        name: project.name,
        documentCount: project.document_count,
      },
    ]);
    setSelectedProjectId(project.id);
    await refresh();
  };
  return {
    projects,
    selectedProjectId,
    setSelectedProjectId,
    refresh,
    create,
    error,
    updating,
    partial: error !== null && projects !== null,
  };
}
