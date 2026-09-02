import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl } from "./api";

describe("apiUrl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses localhost during server-side rendering", () => {
    expect(apiUrl("/health/live")).toBe("http://localhost:8000/health/live");
  });

  it("uses the browser hostname without trusting a caller-provided origin", () => {
    vi.stubGlobal("window", { location: { hostname: "vault.example.test" } });
    expect(apiUrl("/api/v1/projects")).toBe(
      "http://vault.example.test:8000/api/v1/projects",
    );
  });
});
