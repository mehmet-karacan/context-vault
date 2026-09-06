import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl } from "./api";

describe("apiUrl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses same-origin during server-side rendering", () => {
    expect(apiUrl("/health/live")).toBe("/health/live");
  });

  it("uses deployment runtime configuration", () => {
    vi.stubGlobal("window", {
      __CONTEXT_VAULT_CONFIG__: { apiBaseUrl: "https://api.example.test" },
    });
    expect(apiUrl("/api/v1/projects")).toBe(
      "https://api.example.test/api/v1/projects",
    );
  });
});
