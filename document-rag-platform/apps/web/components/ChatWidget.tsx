"use client";
import { useState } from "react";
import { INPUT_LIMITS } from "../lib/api/generated";
import { SOURCE_TYPE_FILTERS, type SourceTypeFilter } from "../lib/types";
import { useConversation } from "../features/conversations/useConversation";
import { MarkdownContent } from "../features/conversations/MarkdownContent";
import { NoAnswerBlock } from "../features/conversations/NoAnswerBlock";
import { CitationPanel } from "../features/citations/CitationPanel";
export default function ChatWidget({
  projectId,
  projectName,
  activeVersions,
}: {
  projectId: string;
  projectName: string | null;
  activeVersions: Record<string, string | null>;
}) {
  const chat = useConversation(projectId);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("");
  const [scope, setScope] = useState<SourceTypeFilter>("all");
  return (
    <aside
      aria-label="Proje sohbeti"
      className="relative flex min-h-[40rem] w-full flex-col border-l border-ink-line bg-surface shadow-folio xl:fixed xl:right-0 xl:top-10 xl:z-40 xl:h-[calc(100vh-2.5rem)] xl:w-[640px]"
    >
      <header className="border-b border-ink-line px-5 py-4">
        <h2 className="font-display text-2xl">Kanıtla konuş</h2>
        <p className="mt-1 text-xs text-ink-soft">
          Sabit proje: {projectName ?? "Önce bir proje seçin"}
        </p>
      </header>
      <div
        className="flex-1 space-y-6 overflow-y-auto p-5"
        role="log"
        aria-label="Sohbet mesajları"
        aria-live="polite"
      >
        {chat.messages.length === 0 && (
          <p className="text-ink-soft">
            Seçtiğiniz projenin kaynakları hakkında sorun. Kullanılan kanıtlar
            yanıtın altında görünür.
          </p>
        )}
        {chat.messages.map((message) => (
          <article
            key={message.id}
            className={`space-y-3 rounded-lg p-3 ${message.role === "user" ? "bg-paper-dim" : ""}`}
          >
            <p className="font-mono text-xs uppercase text-ink-soft">
              {message.role === "user" ? "Siz" : "Yanıt"}
            </p>
            {message.error ? (
              <p role="alert" className="text-rust">
                {message.content}
              </p>
            ) : (
              <MarkdownContent content={message.content} />
            )}{" "}
            {message.response?.answerable === false && (
              <NoAnswerBlock reason={message.response.no_answer_reason} />
            )}{" "}
            {message.response?.answerable && (
              <CitationPanel
                citations={message.response.citations}
                activeVersions={activeVersions}
              />
            )}
          </article>
        ))}
        {chat.pending && (
          <p role="status" className="text-sm text-ink-soft">
            Yanıt bekleniyor. Sunucu aşama ilerlemesi bildirmiyor.
          </p>
        )}
      </div>
      {chat.error && (
        <p role="alert" className="px-5 text-sm text-rust">
          {chat.error}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!input.trim() || chat.pending || !projectId) return;
          void chat.send(input, model, scope);
          setInput("");
        }}
        className="space-y-3 border-t border-ink-line p-4"
      >
        <div className="flex gap-3">
          <label className="min-w-0 flex-1 text-xs">
            Model
            <select
              aria-label="Yanıt modeli"
              value={model || chat.models?.default || ""}
              onChange={(e) => setModel(e.target.value)}
              className="mt-1 w-full rounded border border-ink-line bg-paper-dim p-2"
            >
              {chat.models?.models.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label className="text-xs">
            Kaynak türü
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as SourceTypeFilter)}
              className="mt-1 block w-full rounded border border-ink-line bg-paper-dim p-2"
            >
              {SOURCE_TYPE_FILTERS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label htmlFor="chat-query" className="sr-only">
          Belgen hakkında sor
        </label>
        <textarea
          id="chat-query"
          disabled={!projectId || chat.pending}
          maxLength={INPUT_LIMITS.query}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={3}
          placeholder="Belgen hakkında sor…"
          className="w-full resize-y rounded border border-ink-line bg-paper-dim p-3 text-sm"
        />
        <button
          type="submit"
          disabled={!input.trim() || !projectId || chat.pending}
          className="rounded bg-brass px-5 py-2 disabled:opacity-40"
        >
          Gönder
        </button>
      </form>
    </aside>
  );
}
