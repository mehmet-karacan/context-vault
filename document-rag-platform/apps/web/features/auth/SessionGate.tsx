"use client";
import { createContext, useContext, useState } from "react";
import {
  apiRequest,
  problemMessage,
  setAuthorization,
} from "../../lib/api/client";
import type { Schemas } from "../../lib/api/generated";
type Session = Schemas["SessionResponse"];
const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);
const LOCAL_WORKSPACE = "22222222-2222-4222-8222-222222222222";
export function SessionGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [workspace, setWorkspace] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const login = async (local = false) => {
    const workspaceId = local ? LOCAL_WORKSPACE : workspace.trim();
    if (
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        workspaceId,
      )
    ) {
      setError("Geçerli workspace UUID girin.");
      return;
    }
    setBusy(true);
    setError(null);
    setAuthorization({ workspaceId, apiKey: local ? "" : key });
    try {
      const confirmed = await apiRequest<Session>(
        "/session",
        undefined,
        "SessionResponse",
      );
      if (
        confirmed.workspace_id !== workspaceId ||
        (local && confirmed.auth_mode !== "disabled")
      )
        throw new Error("Scope mismatch");
      setKey("");
      setSession(confirmed);
    } catch (cause) {
      setAuthorization(null);
      setError(problemMessage(cause));
    } finally {
      setBusy(false);
    }
  };
  if (session)
    return (
      <SessionContext.Provider value={session}>
        <div className="relative z-50 flex flex-wrap items-center gap-3 border-b border-ink-line bg-paper px-6 py-2 text-xs">
          <span>Workspace: {session.workspace_id}</span>
          <span>
            {session.auth_mode === "disabled"
              ? "Yalnız yerel oturum"
              : "API anahtarı ile doğrulandı"}
          </span>
          <button
            className="underline"
            onClick={() => {
              setAuthorization(null);
              setSession(null);
            }}
          >
            Oturumu kapat
          </button>
        </div>
        {children}
      </SessionContext.Provider>
    );
  return (
    <main className="mx-auto max-w-lg px-6 py-20">
      <p className="font-mono text-xs uppercase tracking-widest text-brass-dim">
        Context Vault
      </p>
      <h1 className="mt-3 font-display text-4xl">Çalışma alanına bağlan</h1>
      <p className="my-5 text-ink-soft">
        Kaynaklar ve sohbetler doğrulanan workspace ile sınırlıdır. Anahtar
        yalnız bu sekmenin belleğinde tutulur.
      </p>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void login();
        }}
      >
        <div>
          <label htmlFor="workspace" className="block text-sm">
            Workspace UUID
          </label>
          <input
            id="workspace"
            required
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
            className="mt-1 w-full rounded border border-ink-line bg-surface p-3"
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor="api-key" className="block text-sm">
            API anahtarı
          </label>
          <input
            id="api-key"
            type="password"
            required
            minLength={24}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="mt-1 w-full rounded border border-ink-line bg-surface p-3"
            autoComplete="off"
          />
        </div>
        <button
          disabled={busy}
          className="rounded bg-brass px-5 py-3 disabled:opacity-40"
        >
          {busy ? "Doğrulanıyor…" : "Bağlan"}
        </button>
        <button
          type="button"
          disabled={busy}
          className="ml-4 underline"
          onClick={() => {
            if (window.__CONTEXT_VAULT_CONFIG__?.authMode === "disabled")
              void login(true);
            else
              setError("Bu deployment yerel anahtarsız oturuma izin vermiyor.");
          }}
        >
          Yerel workspace seç
        </button>
      </form>
      {error && (
        <p role="alert" className="mt-4 text-rust">
          {error}
        </p>
      )}
    </main>
  );
}
