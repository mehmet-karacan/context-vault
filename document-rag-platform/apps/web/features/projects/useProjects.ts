"use client";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, problemMessage } from "../../lib/api/client";
import type { ApiProject, Project } from "./types";
export function useProjects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const data = await apiRequest<ApiProject[]>(
        "/projects",
        undefined,
        "ProjectResponse[]",
      );
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
      setProjects([]);
      setError(problemMessage(cause));
    }
  }, []);
  useEffect(() => {
    queueMicrotask(() => void refresh());
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
    await refresh();
    setSelectedProjectId(project.id);
  };
  return {
    projects,
    selectedProjectId,
    setSelectedProjectId,
    refresh,
    create,
    error,
  };
}
