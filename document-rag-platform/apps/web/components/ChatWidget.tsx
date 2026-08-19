"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDownIcon, FileIcon, LayersIcon, PenIcon, SearchIcon, SendIcon, TargetIcon } from "./icons";
import { apiUrl } from "../lib/api";

const markdownComponents = {
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

interface Source {
  document: { name: string };
  chunk: string;
  similarity: number;
}

interface Project {
  id: string;
  name: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
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

function uniqueDocumentNames(sources: Source[]): string[] {
  return Array.from(new Set(sources.map((s) => s.document.name)));
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
        }),
      });
      const data = await response.json();

      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: data.answer || "Bu soruya yüklü belgelerde bir yanıt bulunamadı.",
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
      <div className="flex items-center px-5 py-4 border-b border-ink-line shrink-0">
        <span className="font-display italic text-lg text-ink">Sohbet</span>
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
                  {message.sources && message.sources.length > 0 && (
                    <div className="flex flex-col gap-1 pl-3.5">
                      {uniqueDocumentNames(message.sources).map((name) => (
                        <div key={name} className="flex items-center gap-1.5 font-mono text-[11px] text-ink-soft">
                          <FileIcon className="w-3 h-3 text-ink/40 shrink-0" />
                          <span className="truncate">{name}</span>
                        </div>
                      ))}
                    </div>
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
