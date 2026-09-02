"use client";
import { useEffect, useRef, useState } from "react";
import { apiRequest, problemMessage } from "../../lib/api/client";
import type { Schemas } from "../../lib/api/generated";
import { INPUT_LIMITS } from "../../lib/api/generated";
import type { ChatResponse, SourceTypeFilter } from "../../lib/types";
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;
  error?: boolean;
};
export function useConversation(projectId: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState(false);
  const [models, setModels] = useState<Schemas["ChatModelsResponse"] | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const conversation = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    apiRequest<Schemas["ChatModelsResponse"]>(
      "/chat/models",
      { signal: abort.signal },
      "ChatModelsResponse",
    )
      .then(setModels)
      .catch((cause) => {
        if (!abort.signal.aborted) setError(problemMessage(cause));
      });
    return () => {
      abort.abort();
      controller.current?.abort();
    };
  }, []);
  const send = async (
    query: string,
    model: string,
    scope: SourceTypeFilter,
  ) => {
    if (
      !projectId ||
      !query.trim() ||
      query.length > INPUT_LIMITS.query ||
      pending ||
      controller.current
    )
      return;
    const abort = new AbortController();
    controller.current = abort;
    setPending(true);
    setMessages((items) => [
      ...items,
      { id: crypto.randomUUID(), role: "user", content: query },
    ]);
    try {
      const request: Schemas["ChatQuery"] = {
        query,
        project_id: projectId,
        model: model || models?.default || null,
        scope,
        conversation_id: conversation.current,
        debug: false,
      };
      const response = await apiRequest<ChatResponse>(
        "/chat/query",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          signal: abort.signal,
        },
        "ChatResponse",
      );
      if (abort.signal.aborted) return;
      conversation.current = response.conversation_id;
      setMessages((items) => [
        ...items,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.answer,
          response,
        },
      ]);
    } catch (cause) {
      if (!abort.signal.aborted)
        setMessages((items) => [
          ...items,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: problemMessage(cause),
            error: true,
          },
        ]);
    } finally {
      if (!abort.signal.aborted) setPending(false);
      controller.current = null;
    }
  };
  return { messages, pending, models, error, send };
}
