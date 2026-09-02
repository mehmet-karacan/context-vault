import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiRequest,
  ApiProblem,
  problemMessage,
  setAuthorization,
} from "./client";
import { matchesContract } from "./validate";
describe("fail-closed API boundary", () => {
  beforeEach(() => setAuthorization(null));
  afterEach(() => vi.unstubAllGlobals());
  it("does not send a request before workspace/auth admission", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(apiRequest("/projects")).rejects.toMatchObject({
      status: 401,
    });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("sends explicit headers without cookies and validates response", async () => {
    setAuthorization({ workspaceId: "w", apiKey: "test-memory-only-key" });
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify([])));
    vi.stubGlobal("fetch", fetch);
    await expect(
      apiRequest("/projects", undefined, "ProjectResponse[]"),
    ).resolves.toEqual([]);
    const [, init] = fetch.mock.calls[0];
    expect(init.credentials).toBe("omit");
    expect(init.headers.get("x-workspace-id")).toBe("w");
    expect(init.headers.get("x-api-key")).toBe("test-memory-only-key");
    setAuthorization(null);
    await expect(apiRequest("/projects")).rejects.toMatchObject({
      status: 401,
    });
  });
  it("rejects malformed successful payloads", async () => {
    setAuthorization({ workspaceId: "w", apiKey: "" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response('{"answer":"fake"}')),
    );
    await expect(
      apiRequest("/chat/query", undefined, "ChatResponse"),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("never displays arbitrary server exceptions", () => {
    expect(
      problemMessage(new ApiProblem(500, "internal", "SECRET stacktrace")),
    ).not.toContain("SECRET");
    expect(problemMessage(new ApiProblem(403, "denied", "SECRET"))).toContain(
      "yetkiniz",
    );
  });
  it("rejects insecure non-loopback transport before transmitting a key", async () => {
    vi.stubGlobal("window", {
      location: { origin: "https://vault.example" },
      __CONTEXT_VAULT_CONFIG__: { apiBaseUrl: "http://remote.example" },
    });
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    setAuthorization({ workspaceId: "w", apiKey: "test-key" });
    await expect(apiRequest("/session")).rejects.toMatchObject({
      code: "insecure_transport",
    });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("validates generated request limits and enum values", () => {
    expect(matchesContract("ProjectCreate", { name: "" })).toBe(false);
    expect(matchesContract("ProjectCreate", { name: "A".repeat(201) })).toBe(
      false,
    );
    expect(
      matchesContract("ChatQuery", {
        query: "x",
        project_id: "p",
        scope: "global",
      }),
    ).toBe(false);
    expect(matchesContract("ProjectCreate", { name: "Explicit project" })).toBe(
      true,
    );
  });
});
