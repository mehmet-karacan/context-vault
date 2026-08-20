"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronDownIcon,
  FileIcon,
  LayersIcon,
  PenIcon,
  SearchIcon,
  SendIcon,
  TargetIcon,
  CloseIcon,
} from "./icons";
import { apiUrl } from "../lib/api";
import {
  Citation,
  ChatResponse,
  LegacySource,
  RetrievalDebug,
  SOURCE_TYPE_FILTERS,
  SourceTypeFilter,
  scopeValue,
} from "../lib/types";

const markdownComponents = {
  text: ({ children }: any) =>
    typeof children === "string" ? renderTextTokens(children) : children,
  p: ({ children }: any) => <p className="mb-3 last:mb-0">{children}</p>,
  strong: ({ children }: any) => <strong className="font-semibold text-ink">{children}</strong>,
  ul: ({ children }: any) => <ul className="list-disc pl-5 mb-3 space-y-1 last:mb-0">{children}</ul>,
  ol: ({ children }: any) => <ol className="list-decimal pl-5 mb-3 space-y-1 last:mb-0">{children}</ol>,
  li: ({ children }: any) => <li>{children}</li>,
  h1: ({ children }: any) => <h1 className="font-display text-lg text-ink mb-2 mt-3 first:mt-0">{children}</h1>,
  h2: ({ children }: any) => <h2 className="font-display text-base text-ink mb-2 mt-3 first:mt-0">{children}</h2>,
  h3: ({ children }: any) => <h3 className="font-display text-[15px] text-ink mb-1.5 mt-3 first:mt-0">{children}</h3>,
  a: ({ children, href }: any) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-brass-dim underline hover:text-ink">
      {children}
    </a>
  ),
  code: ({ children }: any) => (
    <code className="bg-paper-dim px-1.5 py-0.5 rounded font-mono text-[13px]">{children}</code>
  ),
  pre: ({ children }: any) => (
    <pre className="bg-paper-dim rounded-md p-3 overflow-x-auto font-mono text-[13px] mb-3 last:mb-0">{children}</pre>
  ),
  blockquote: ({ children }: any) => (
    <blockquote className="border-l-2 border-brass pl-3 italic text-ink/80 mb-3 last:mb-0">{children}</blockquote>
  ),
  table: ({ children }: any) => (
    <div className="overflow-x-auto mb-3 last:mb-0">
      <table className="w-full text-[13px] border-collapse">{children}</table>
    </div>
  ),
  th: ({ children }: any) => (
    <th className="border border-ink-line px-2 py-1 text-left font-mono text-[11px] uppercase bg-paper-dim">
      {children}
    </th>
  ),
  td: ({ children }: any) => <td className="border border-ink-line px-2 py-1">{children}</td>,
};

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
      {content}
    </ReactMarkdown>
  );
}

const THINKING_STAGES = [
  { label: "Belgelerinde aranıyor…", icon: SearchIcon },
  { label: "En alakalı yerler bulunuyor…", icon: TargetIcon },
  { label: "Bulunanlar bir araya getiriliyor…", icon: LayersIcon },
  { label: "Yanıt yazılıyor…", icon: PenIcon },
];

const THINKING_STAGE_INTERVAL_MS = 900;

const CODE_TICKER_LINES = [
  "tokenize(query) → 18 tokens",
  "embed(query) → float32[1024]",
  "ann_search(index=hnsw, ef_search=64)",
  "SELECT chunk_id, content FROM chunks",
  "ORDER BY embedding <=> $1 LIMIT 3",
  "chunk_083 similarity=0.842",
  "chunk_017 similarity=0.791",
  "chunk_204 similarity=0.763",
  "context_window: 3 chunks · 612 tokens",
  "POST /v1/chat/completions",
  "model=Kimi-K2.7-Code stream=true",
  "generating… 128/512 tokens",
  "generating… 256/512 tokens",
  "generating… 384/512 tokens",
];

const CODE_TICKER_INTERVAL_MS = 140;
const CODE_TICKER_VISIBLE_LINES = 4;

function CodeTicker() {
  const [lines, setLines] = useState<string[]>([]);
  const indexRef = useRef(0);

  useEffect(() => {
    const interval = setInterval(() => {
      const next = CODE_TICKER_LINES[indexRef.current % CODE_TICKER_LINES.length];
      indexRef.current += 1;
      setLines((prev) => [...prev, next].slice(-CODE_TICKER_VISIBLE_LINES));
    }, CODE_TICKER_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="rounded-md bg-paper-dim/60 border border-ink-line px-3 py-2 font-mono text-[10.5px] leading-relaxed overflow-hidden">
      {lines.map((line, i) => (
        <div
          key={`${line}-${i}`}
          className="text-ink-soft whitespace-nowrap"
          style={{ opacity: 0.3 + (0.7 * (i + 1)) / lines.length }}
        >
          {line}
        </div>
      ))}
    </div>
  );
}

interface Project {
  id: string;
  name: string;
}

type CitationPayload = Partial<Citation> &
  Pick<Citation, "label" | "document_name"> & {
    snippet?: string | null;
  };

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: CitationPayload[];
  retrievalDebug?: RetrievalDebug | null;
  answerable?: boolean;
  sources?: LegacySource[];
  durationMs?: number;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} sn`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes} dk ${seconds} sn`;
}

function Avatar({ role }: { role: Message["role"] }) {
  const isUser = role === "user";
  return (
    <div
      className={`w-8 h-8 rounded-full shrink-0 ${
        isUser ? "bg-paper-dim border border-ink-line" : "bg-brass"
      }`}
    />
  );
}

function ThinkingIndicator({ stageIndex }: { stageIndex: number }) {
  return (
    <div className="flex flex-col gap-2 px-3.5 py-2.5 max-w-[320px] w-full">
      <div className="flex flex-col gap-1.5 font-mono text-[12px]">
        {THINKING_STAGES.slice(0, stageIndex + 1).map((stage, i) => {
          const isCurrent = i === stageIndex;
          const Icon = stage.icon;
          return (
            <div
              key={stage.label}
              className={`flex items-center gap-2 ${isCurrent ? "text-ink-soft" : "text-ink-soft/40"}`}
            >
              <Icon className={`w-3.5 h-3.5 shrink-0 ${isCurrent ? "text-brass animate-pulse" : "text-brass-dim"}`} />
              <span>{stage.label}</span>
            </div>
          );
        })}
      </div>
      <CodeTicker />
    </div>
  );
}

interface GroupedSource {
  name: string;
  labels: string[];
}

function groupSources(citations: CitationPayload[], sources?: LegacySource[]): GroupedSource[] {
  const map = new Map<string, string[]>();
  for (const c of citations) {
    const name = c.document_name;
    if (!name) continue;
    const labels = map.get(name) ?? [];
    const label = c.label;
    if (label && !labels.includes(label)) labels.push(label);
    map.set(name, labels);
  }
  if (sources) {
    for (const s of sources) {
      const name = s.document.name;
      if (!name) continue;
      if (!map.has(name)) map.set(name, []);
    }
  }
  return Array.from(map.entries())
    .map(([name, labels]) => ({
      name,
      labels: labels.sort(
        (a, b) =>
          (parseInt(a.replace(/\D/g, ""), 10) || 0) - (parseInt(b.replace(/\D/g, ""), 10) || 0) ||
          a.localeCompare(b)
      ),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

const CITATION_TOKEN = /^\[S\d+\]$/;

// Renders ordinary inline text, splitting out `[S\d+]` citation markers into
// smaller, muted reference tokens so they read as metadata rather than body.
function renderTextTokens(text: string): React.ReactNode {
  const parts = text.split(/(\[S\d+\])/g);
  return parts.map((part, i) =>
    CITATION_TOKEN.test(part) ? (
      <span key={i} className="text-[10.5px] text-ink-soft/70 align-super">
        {part}
      </span>
    ) : (
      part
    )
  );
}


function RetrievalDebugPanel({ retrievalDebug }: { retrievalDebug: RetrievalDebug | null }) {
  const [open, setOpen] = useState(true);
  const stages = retrievalDebug?.stages ?? {};
  const entries = Object.entries(stages).filter(([, list]) => Array.isArray(list));

  if (entries.length === 0) {
    return <p className="font-mono text-[10px] text-ink-soft/50">retrieval_debug boş</p>;
  }

  return (
    <div className="rounded-md border border-ink-line bg-paper-dim/40 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-ink-soft hover:text-ink transition-colors"
      >
        <span>Retrieval sıraları</span>
        <ChevronDownIcon className={`w-3 h-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="border-t border-ink-line px-3 py-2 flex flex-col gap-2.5">
          {entries.map(([stage, ranks]) => (
            <div key={stage}>
              <div className="font-mono text-[10px] uppercase tracking-wide text-brass-dim mb-1">{stage}</div>
              <div className="flex flex-col gap-0.5">
                {ranks.slice(0, 10).map((r, i) => (
                  <div key={`${stage}-${i}`} className="flex items-center gap-2 font-mono text-[10px]">
                    <span className="w-4 text-ink-soft/60 shrink-0">{(r.rank ?? i + 1)}.</span>
                    <span className="text-ink truncate flex-1">{r.label || r.document_name || r.chunk_id || "—"}</span>
                    {r.score != null && <span className="text-ink-soft/70 shrink-0">{Number(r.score).toFixed(3)}</span>}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NoAnswerBlock() {
  return (
    <div className="rounded-md border border-ink-line bg-paper-dim/50 px-3.5 py-2.5 flex items-start gap-2">
      <CloseIcon className="w-3.5 h-3.5 text-rust shrink-0 mt-0.5" />
      <p className="text-[13px] text-ink-soft leading-relaxed">
        Kaynaklarda bilgi bulunamadı. Bu soruya yüklediğin belgelerden doğrulanabilir bir yanıt üretilemedi.
      </p>
    </div>
  );
}

function StyledSelect({
  value,
  onChange,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex-1 min-w-0">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full appearance-none bg-paper-dim border border-ink-line rounded-md pl-2.5 pr-7 py-1.5 text-[11px] font-mono text-ink truncate focus:outline-none focus:border-brass cursor-pointer"
      >
        {children}
      </select>
      <ChevronDownIcon className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-ink-soft pointer-events-none" />
    </div>
  );
}

const SOURCE_TYPE_LABEL: Record<SourceTypeFilter, string> = {
  all: "Tüm kaynaklar",
  documents: "Belgeler",
  code: "Kod",
  images: "Görseller",
};

const GREETING: Message = {
  id: "greeting",
  role: "assistant",
  content: "Merhaba. Yüklediğin belgelerle ilgili bir soru sorabilirsin.",
};

export default function ChatWidget() {
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [thinkingStage, setThinkingStage] = useState(0);
  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceTypeFilter>("all");
  const [devMode, setDevMode] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [chatModels, setChatModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const thinkingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetch(apiUrl("/projects"))
      .then((r) => r.json())
      .then((data) => setProjects(data.map((p: any) => ({ id: p.id, name: p.name }))))
      .catch((error) => console.error("Failed to fetch projects:", error));

    fetch(apiUrl("/chat/models"))
      .then((r) => r.json())
      .then((data) => {
        setChatModels(data.models || []);
        setSelectedModel(data.default || "");
      })
      .catch((error) => console.error("Failed to fetch chat models:", error));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isTyping, thinkingStage]);

  useEffect(() => {
    return () => {
      if (thinkingIntervalRef.current) clearInterval(thinkingIntervalRef.current);
    };
  }, []);

  const stopThinking = () => {
    if (thinkingIntervalRef.current) {
      clearInterval(thinkingIntervalRef.current);
      thinkingIntervalRef.current = null;
    }
    setIsTyping(false);
  };

  const handleSend = async () => {
    if (!input.trim() || isTyping) return;

    const userMessage: Message = { id: Date.now().toString(), role: "user", content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsTyping(true);
    setThinkingStage(0);
    thinkingIntervalRef.current = setInterval(() => {
      setThinkingStage((prev) => Math.min(prev + 1, THINKING_STAGES.length - 1));
    }, THINKING_STAGE_INTERVAL_MS);

    const startedAt = Date.now();

    try {
      const response = await fetch(apiUrl("/chat/query"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: userMessage.content,
          project_id: selectedProjectId || null,
          model: selectedModel || null,
          scope: scopeValue(sourceTypeFilter),
          source_type: sourceTypeFilter === "all" ? null : scopeValue(sourceTypeFilter),
          debug: devMode,
        }),
      });
      const data: Partial<ChatResponse> = await response.json();

      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: data.answer || "",
          answerable: data.answerable ?? true,
          citations: data.citations ?? [],
          retrievalDebug: data.retrieval_debug ?? null,
          sources: data.sources,
          durationMs: Date.now() - startedAt,
        },
      ]);
    } catch (error) {
      console.error("Chat error:", error);
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: "Sunucuya bağlanılamadı. Backend'in çalıştığından emin ol.",
        },
      ]);
    }

    stopThinking();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <aside className="fixed top-0 right-0 h-screen w-[640px] bg-surface border-l border-ink-line shadow-folio flex flex-col z-40">
      <div className="flex items-center justify-between px-5 py-4 border-b border-ink-line shrink-0">
        <span className="font-display italic text-lg text-ink">Sohbet</span>
        <button
          type="button"
          onClick={() => setDevMode((v) => !v)}
          title="Geliştirici modu: retrieval sıralarını göster"
          className={`flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide px-2 py-1 rounded border transition-colors ${
            devMode
              ? "bg-brass/15 text-brass-dim border-brass/30"
              : "text-ink-soft border-ink-line hover:text-ink"
          }`}
        >
          <TargetIcon className="w-3 h-3" />
          Dev
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-6 space-y-7 min-h-0">
        {messages.map((message) => (
          <div key={message.id} className={`flex gap-3 ${message.role === "user" ? "flex-row-reverse" : ""}`}>
            <Avatar role={message.role} />
            <div className={`flex flex-col gap-3 max-w-[88%] ${message.role === "user" ? "items-end" : "items-start"}`}>
              <div
                className={`rounded-lg px-3.5 py-2.5 text-[15px] leading-relaxed ${
                  message.role === "user"
                    ? "bg-paper-dim text-ink border border-ink-line"
                    : "bg-transparent text-ink"
                }`}
              >
                {message.role === "assistant" ? (
                  <MarkdownContent content={message.content} />
                ) : (
                  message.content
                )}
              </div>

              {message.role === "assistant" && message.answerable === false && <NoAnswerBlock />}

              {message.role === "assistant" &&
                (() => {
                  const grouped = groupSources(message.citations ?? [], message.sources);
                  if (grouped.length === 0) return null;
                  return (
                    <div className="flex flex-col gap-1 pl-3.5 w-full">
                      <span className="font-mono text-[10px] uppercase tracking-wide text-ink-soft/70">
                        Kaynaklar:
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {grouped.map((group) => (
                          <span
                            key={group.name}
                            className="inline-flex items-center gap-1.5 font-mono text-[11px] text-ink-soft bg-paper-dim/60 border border-ink-line rounded px-2 py-0.5"
                          >
                            <FileIcon className="w-3 h-3 text-ink/40 shrink-0" />
                            <span className="truncate">{group.name}</span>
                            {group.labels.length > 0 && (
                              <span className="inline-flex items-center gap-0.5">
                                {group.labels.map((label) => (
                                  <span
                                    key={label}
                                    className="text-[9.5px] text-brass-dim bg-brass/10 rounded px-1 py-px"
                                  >
                                    {label}
                                  </span>
                                ))}
                              </span>
                            )}
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })()}

              {message.role === "assistant" && devMode && message.retrievalDebug && (
                <RetrievalDebugPanel retrievalDebug={message.retrievalDebug} />
              )}

              {message.role === "assistant" && message.durationMs !== undefined && (
                <span className="font-mono text-[10px] text-ink-soft/50 pl-3.5">
                  {formatDuration(message.durationMs)}
                </span>
              )}
            </div>
          </div>
        ))}

        {isTyping && (
          <div className="flex gap-3">
            <Avatar role="assistant" />
            <ThinkingIndicator stageIndex={thinkingStage} />
          </div>
        )}
      </div>

      <div className="border-t border-ink-line p-3 shrink-0 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <StyledSelect value={selectedProjectId} onChange={setSelectedProjectId}>
            <option value="">Tüm projeler</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </StyledSelect>
          {chatModels.length > 0 && (
            <StyledSelect value={selectedModel} onChange={setSelectedModel}>
              {chatModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </StyledSelect>
          )}
          <StyledSelect value={sourceTypeFilter} onChange={(v) => setSourceTypeFilter(v as SourceTypeFilter)}>
            {SOURCE_TYPE_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {SOURCE_TYPE_LABEL[f.value]}
              </option>
            ))}
          </StyledSelect>
        </div>
        <div className="flex items-center gap-2.5">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Belgen hakkında sor..."
            className="flex-1 bg-paper-dim border border-ink-line rounded-lg px-4 py-2.5 text-[15px] text-ink placeholder-ink-soft focus:outline-none focus:border-brass"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isTyping}
            aria-label="Gönder"
            className="bg-brass text-ink w-10 h-10 shrink-0 rounded-lg flex items-center justify-center hover:bg-brass-dim disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <SendIcon className="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
