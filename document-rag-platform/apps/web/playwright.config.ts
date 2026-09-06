import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testIgnore: process.env.CV3_BROWSER_LIVE === "1" ? [] : ["**/live.spec.ts"],
  reporter: [["line"], ["junit", { outputFile: "reports/e2e-junit.xml" }]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    env: {
      CONTEXT_VAULT_API_BASE_URL: "http://127.0.0.1:44999",
      CONTEXT_VAULT_AUTH_MODE: "disabled",
    },
    command: "npm run start -- --hostname 127.0.0.1",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
