import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { Schemas } from "../../lib/api/generated";
const workspace = "22222222-2222-4222-8222-222222222222";
const projectId = "33333333-3333-4333-8333-333333333333";
const secondId = "44444444-4444-4444-8444-444444444444";
const testKey = "test-browser-memory-only-00000000";
const document: Schemas["DocumentResponse"] = {
  id: "d1",
  name: "Evidence.txt",
  size: 34,
  status: "indexed",
  uploaded_at: "2026-09-02",
  chunks_count: 1,
  error_message: null,
  project_id: projectId,
  project_name: "Birinci proje",
  active_version_id: "v1",
  job_id: "j1",
  job_status: "completed",
  job_stage: "completed",
  job_error: null,
};
const answer: Schemas["ChatResponse"] = {
  conversation_id: "c1",
  answer: "Kanıtlı yanıt [S1]",
  answerable: true,
  no_answer_reason: null,
  claims: [{ claim_text: "Kanıtlı yanıt", source_labels: ["S1"] }],
  uncertainty: [],
  safety_flags: [],
  citations: [
    {
      label: "S1",
      document_id: "d1",
      document_name: "Evidence.txt",
      version_id: "v1",
      source_file_id: null,
      source_type: "document",
      heading_path: [],
      page_start: 1,
      page_end: 1,
      file_path: null,
      symbol_name: null,
      line_start: null,
      line_end: null,
      bbox: null,
      snippet: "Kaynak alıntısı",
      rank: 1,
    },
  ],
  retrieval_debug: null,
};
async function setup(
  page: Page,
  mode: "ok" | "quarantine" | "outage" | "expired" | "retry" = "ok",
) {
  const calls: {
    path: string;
    method: string;
    body: string | null;
    key: string | undefined;
  }[] = [];
  let uploaded = false;
  let uploads = 0;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    calls.push({
      path,
      method: request.method(),
      body: request.postData(),
      key: request.headers()["idempotency-key"],
    });
    expect(request.headers()["x-workspace-id"]).toBe(workspace);
    expect(request.headers()["x-api-key"]).toBe(testKey);
    let body: unknown = {};
    let status = 200;
    if (mode === "expired") {
      status = 401;
      body = { detail: "SECRET server exception must not render" };
    } else if (path === "/session")
      body = {
        principal_id: "p1",
        workspace_id: workspace,
        roles: ["member"],
        auth_mode: "api_key",
        upload_max_bytes: 20971520,
      };
    else if (path === "/projects" && request.method() === "POST")
      body = {
        id: secondId,
        name: "Yeni proje",
        document_count: 0,
        created_at: "2026-09-02",
      };
    else if (path === "/projects")
      body = [
        {
          id: projectId,
          name: "Birinci proje",
          document_count: uploaded ? 1 : 0,
          created_at: "2026-09-02",
        },
        {
          id: secondId,
          name: "İkinci proje",
          document_count: 0,
          created_at: "2026-09-02",
        },
      ];
    else if (path === "/chat/models")
      body = { models: ["test-offline"], default: "test-offline" };
    else if (path === "/documents/upload") {
      uploads++;
      if (mode === "retry" && uploads === 1) {
        status = 503;
        body = { detail: "offline" };
      } else {
        uploaded = true;
        body = {
          document_id: "d1",
          version_id: "v1",
          job_id: "j1",
          status: "queued",
          replayed: uploads > 1,
          quarantine_reason: null,
          document,
        };
      }
    } else if (path === "/documents")
      body =
        uploaded &&
        new URL(request.url()).searchParams.get("project_id") === projectId
          ? [document]
          : [];
    else if (path === "/documents/d1" && request.method() === "DELETE") {
      uploaded = false;
      body = { success: true };
    } else if (path.endsWith("/events"))
      body = [
        {
          id: "e1",
          job_id: "j1",
          stage: mode === "quarantine" ? "quarantined" : "completed",
          status: mode === "quarantine" ? "failed" : "completed",
          message: null,
          created_at: "2026-09-02",
        },
      ];
    else if (path === "/ingestion-jobs/j1")
      body = {
        id: "j1",
        document_id: "d1",
        version_id: "v1",
        status: mode === "quarantine" ? "failed" : "completed",
        stage: mode === "quarantine" ? "quarantined" : "completed",
        progress: null,
        attempt: 1,
        error_code: mode === "quarantine" ? "policy_rejected" : null,
        error_message: "SECRET should never render",
        started_at: null,
        finished_at: null,
        created_at: "2026-09-02",
      };
    else if (path === "/chat/query")
      body =
        mode === "outage"
          ? {
              ...answer,
              answer: "",
              answerable: false,
              no_answer_reason: "provider_failure",
              claims: [],
              citations: [],
            }
          : answer;
    else {
      status = 404;
      body = { detail: "not found" };
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/");
  return calls;
}
async function login(page: Page) {
  expect(
    await page.evaluate(() => window.__CONTEXT_VAULT_CONFIG__?.apiBaseUrl),
  ).toBe("http://127.0.0.1:44999");
  await page.getByLabel("Workspace UUID").fill(workspace);
  await page.getByLabel("API anahtarı").fill(testKey);
  await page.getByRole("button", { name: "Bağlan", exact: true }).click();
}
async function selectProject(page: Page) {
  await page
    .getByRole("combobox", { name: "Aktif proje" })
    .selectOption(projectId);
}
async function upload(page: Page) {
  await page.getByLabel("Belge dosyaları").setInputFiles({
    name: "Evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Public synthetic fixture only."),
  });
}

test("auth/project/upload/job/chat/citation/delete contract flow and keyboard access", async ({
  page,
}) => {
  const calls = await setup(page);
  expect(calls).toHaveLength(0);
  await login(page);
  await expect(page.getByLabel("Belge dosyaları")).toBeDisabled();
  await expect(page.getByLabel("Belgen hakkında sor")).toBeDisabled();
  await selectProject(page);
  await upload(page);
  await expect(
    page.getByText("completed · completed · ilerleme bildirilmedi"),
  ).toBeVisible();
  await page.getByLabel("Belgen hakkında sor").fill("Kaynak ne diyor?");
  await page.getByLabel("Belgen hakkında sor").press("Tab");
  await expect(page.getByRole("button", { name: "Gönder" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Kanıtlı yanıt [S1]")).toBeVisible();
  await page.getByText("S1 · Evidence.txt", { exact: false }).click();
  await expect(page.getByText("Kaynak alıntısı")).toBeVisible();
  expect(calls.find((call) => call.path === "/chat/query")?.body).toContain(
    projectId,
  );
  await page
    .getByRole("button", { name: "Evidence.txt kaynağını sil" })
    .click();
  await expect(
    page.getByText("Bu projede henüz kaynak yok.", { exact: false }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      JSON.stringify({ local: localStorage, session: sessionStorage }),
    ),
  ).not.toContain(testKey);
});

test("same-project follow-up keeps conversation; switching project resets it", async ({
  page,
}) => {
  const calls = await setup(page);
  await login(page);
  await selectProject(page);
  for (const question of ["İlk soru", "Devam sorusu"]) {
    await page.getByLabel("Belgen hakkında sor").fill(question);
    await page.getByRole("button", { name: "Gönder" }).click();
    await expect(page.getByLabel("Belgen hakkında sor")).toBeEnabled();
  }
  const sent = calls.filter((item) => item.path === "/chat/query");
  expect(JSON.parse(sent[1].body!).conversation_id).toBe("c1");
  await page
    .getByRole("combobox", { name: "Aktif proje" })
    .selectOption(secondId);
  await expect(page.getByText("İlk soru", { exact: true })).toHaveCount(0);
  await page.getByLabel("Belgen hakkında sor").fill("Diğer proje");
  await page.getByRole("button", { name: "Gönder" }).click();
  await expect
    .poll(() => calls.filter((item) => item.path === "/chat/query").length)
    .toBe(3);
  const last = JSON.parse(
    calls.filter((item) => item.path === "/chat/query").at(-1)!.body!,
  );
  expect(last.project_id).toBe(secondId);
  expect(last.conversation_id).toBeNull();
});

test("quarantine remains visible with no raw error or fake progress", async ({
  page,
}) => {
  await setup(page, "quarantine");
  await login(page);
  await selectProject(page);
  await upload(page);
  await expect(
    page.getByText(
      "Kaynak güvenlik politikası nedeniyle karantinada; işleme açılmadı.",
    ),
  ).toBeVisible();
  await expect(page.getByText("SECRET", { exact: false })).toHaveCount(0);
  await expect(page.getByText("0%", { exact: false })).toHaveCount(0);
});

test("transport retry reuses upload idempotency key", async ({ page }) => {
  const calls = await setup(page, "retry");
  await login(page);
  await selectProject(page);
  await upload(page);
  await page.getByRole("button", { name: "Aynı işlemi yeniden dene" }).click();
  await expect(
    page.getByText("completed · completed · ilerleme bildirilmedi"),
  ).toBeVisible();
  const uploads = calls.filter((call) => call.path === "/documents/upload");
  expect(uploads).toHaveLength(2);
  expect(uploads[0].key).toBeTruthy();
  expect(uploads[0].key).toBe(uploads[1].key);
});

test("provider outage is distinct from insufficient evidence and has no citation", async ({
  page,
}) => {
  await setup(page, "outage");
  await login(page);
  await selectProject(page);
  await page.getByLabel("Belgen hakkında sor").fill("Bir soru");
  await page.getByRole("button", { name: "Gönder" }).click();
  await expect(
    page.getByText("Yanıt sağlayıcısı isteği güvenli biçimde tamamlayamadı."),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Kullanılan kanıtlar" }),
  ).toHaveCount(0);
});

test("expired credential does not open the workspace", async ({ page }) => {
  const calls = await setup(page, "expired");
  await login(page);
  await expect(
    page.getByText("Oturum süresi doldu. Yeniden giriş yapın."),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Aktif proje" })).toHaveCount(
    0,
  );
  expect(calls.some((item) => item.path === "/projects")).toBe(false);
  await expect(page.getByText("SECRET", { exact: false })).toHaveCount(0);
});

test("login and selected workspace pass automated WCAG scan on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await login(page);
  await selectProject(page);
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  expect(
    await page.evaluate(
      () => window.document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "reports/workspace-mobile.png",
    fullPage: true,
  });
});

test("project create is explicit and a URL cannot inject another project scope", async ({
  page,
}) => {
  const calls = await setup(page);
  await login(page);
  await page
    .getByRole("combobox", { name: "Aktif proje" })
    .selectOption("__new__");
  await page.getByLabel("Yeni proje adı").fill("Yeni proje");
  await page.getByRole("button", { name: "Oluştur", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Aktif proje" })).toHaveValue(
    secondId,
  );
  expect(
    calls.find((call) => call.path === "/projects" && call.method === "POST")
      ?.body,
  ).toContain("Yeni proje");
  await page.goto(`/sources?project_id=${projectId}`);
  await login(page);
  await expect(page.getByRole("combobox", { name: "Aktif proje" })).toHaveValue(
    "",
  );
  await expect(page.getByLabel("Belge dosyaları")).toBeDisabled();
});

test("permission failure is visible without server detail", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await page.route("**/api/v1/documents?**", (route) =>
    route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "SECRET private workspace" }),
    }),
  );
  await selectProject(page);
  await expect(page.getByText("Bu işlem için yetkiniz yok.")).toBeVisible();
  await expect(page.getByText("SECRET", { exact: false })).toHaveCount(0);
});

test("local mode still requires explicit workspace selection and server confirmation", async ({
  page,
}) => {
  const paths: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    expect(route.request().headers()["x-workspace-id"]).toBe(workspace);
    expect(route.request().headers()["x-api-key"]).toBeUndefined();
    const body = path.endsWith("/session")
      ? {
          principal_id: "local",
          workspace_id: workspace,
          roles: ["admin"],
          auth_mode: "disabled",
          upload_max_bytes: 20971520,
        }
      : path.endsWith("/projects")
        ? []
        : { models: [], default: "" };
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/");
  expect(paths).toEqual([]);
  await page.getByRole("button", { name: "Yerel workspace seç" }).click();
  await expect(page.getByText("Yalnız yerel oturum")).toBeVisible();
  await expect(page.getByLabel("Belge dosyaları")).toBeDisabled();
});

test("production diagnostics route has no interactive admin surface", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await page.goto("/admin/diagnostics");
  await login(page);
  await expect(page.getByText("404", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tanıyı çalıştır" }),
  ).toHaveCount(0);
});

test("job refresh outage retains a labeled stale snapshot, not an empty project", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await selectProject(page);
  await upload(page);
  await expect(
    page.getByText("completed · completed · ilerleme bildirilmedi"),
  ).toBeVisible();
  await page.getByRole("link", { name: "İşler", exact: true }).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await selectProject(page);
  const board = page.getByRole("region", { name: "Kalıcı iş kayıtları" });
  await expect(
    board.getByRole("heading", { name: "Evidence.txt" }),
  ).toBeVisible();
  await page.route("**/api/v1/documents?**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "SECRET internal failure" }),
    }),
  );
  await board.getByRole("button", { name: "Durumu yenile" }).click();
  await expect(
    board.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
  ).toBeVisible();
  await expect(
    board.getByRole("heading", { name: "Evidence.txt" }),
  ).toBeVisible();
  await expect(board.getByText("Bu projede iş kaydı bulunamadı.")).toHaveCount(
    0,
  );
  await expect(page.getByText("SECRET", { exact: false })).toHaveCount(0);
});

test("job refresh permission loss clears snapshot without claiming an empty project", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await selectProject(page);
  await upload(page);
  await expect(
    page.getByText("completed · completed · ilerleme bildirilmedi"),
  ).toBeVisible();
  await page.getByRole("link", { name: "İşler", exact: true }).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await selectProject(page);
  const board = page.getByRole("region", { name: "Kalıcı iş kayıtları" });
  await expect(
    board.getByRole("heading", { name: "Evidence.txt" }),
  ).toBeVisible();
  await page.route("**/api/v1/documents?**", (route) =>
    route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "denied" }),
    }),
  );
  await board.getByRole("button", { name: "Durumu yenile" }).click();
  await expect(board.getByText("Bu işlem için yetkiniz yok.")).toBeVisible();
  await expect(
    board.getByRole("heading", { name: "Evidence.txt" }),
  ).toHaveCount(0);
  await expect(board.getByText("Bu projede iş kaydı bulunamadı.")).toHaveCount(
    0,
  );
});

test("source snapshot survives outage with disabled deletion and recovers", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await selectProject(page);
  await upload(page);
  const remove = page.getByRole("button", {
    name: "Evidence.txt kaynağını sil",
  });
  await expect(remove).toBeEnabled();
  await page.route("**/api/v1/documents?**", (route) =>
    route.fulfill({ status: 503, json: null }),
  );
  await page.getByRole("button", { name: "Kaynakları yenile" }).click();
  await expect(
    page.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
  ).toBeVisible();
  await expect(remove).toBeVisible();
  await expect(remove).toBeDisabled();
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await page.screenshot({
    path: "reports/source-partial-state.png",
    fullPage: true,
  });
  await page.unroute("**/api/v1/documents?**");
  await page.getByRole("button", { name: "Kaynakları yenile" }).click();
  await expect(remove).toBeEnabled();
  await expect(
    page.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
  ).toHaveCount(0);
});

for (const status of [401, 403, 404]) {
  test(`HTTP ${status} clears sources and citation context and blocks upload/chat`, async ({
    page,
  }) => {
    await setup(page);
    await login(page);
    await selectProject(page);
    await upload(page);
    await expect(
      page.getByRole("button", { name: "Evidence.txt kaynağını sil" }),
    ).toBeEnabled();
    await page.getByLabel("Belgen hakkında sor").fill("Kanıt?");
    await page.getByRole("button", { name: "Gönder" }).click();
    await expect(page.getByText("Kanıtlı yanıt [S1]")).toBeVisible();
    await page.route("**/api/v1/documents?**", (route) =>
      route.fulfill({ status, json: null }),
    );
    await page.getByRole("button", { name: "Kaynakları yenile" }).click();
    await expect(
      page.getByRole("button", { name: "Evidence.txt kaynağını sil" }),
    ).toHaveCount(0);
    await expect(page.getByText("Kanıtlı yanıt [S1]")).toHaveCount(0);
    await expect(page.getByLabel("Belge dosyaları")).toBeDisabled();
    await expect(page.getByLabel("Belgen hakkında sor")).toBeDisabled();
    await expect(
      page.getByText("Bu projede henüz kaynak yok.", { exact: false }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
    ).toHaveCount(0);
  });
}

test("first source load failure is neither an empty result nor a stale snapshot", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await page.route("**/api/v1/documents?**", (route) =>
    route.fulfill({ status: 503, json: null }),
  );
  await selectProject(page);
  await expect(
    page.getByText("Sunucu isteği tamamlayamadı. Daha sonra tekrar deneyin."),
  ).toBeVisible();
  await expect(
    page.getByText("Bu projede henüz kaynak yok.", { exact: false }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
  ).toHaveCount(0);
});

test("successful deletion followed by failed refresh never resurrects the source", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await selectProject(page);
  await upload(page);
  const remove = page.getByRole("button", {
    name: "Evidence.txt kaynağını sil",
  });
  await expect(remove).toBeEnabled();
  await page.route("**/api/v1/documents?**", (route) =>
    route.fulfill({ status: 503, json: null }),
  );
  await remove.click();
  await expect(
    page.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
  ).toBeVisible();
  await expect(remove).toHaveCount(0);
  await expect(
    page.getByText("Son doğrulanmış listede kaynak bulunmuyordu."),
  ).toBeVisible();
});

test("project refresh outage retains scope, then permission loss clears it", async ({
  page,
}) => {
  await setup(page);
  await login(page);
  await selectProject(page);
  await page.route("**/api/v1/projects", (route) =>
    route.fulfill({ status: 503, json: null }),
  );
  await upload(page);
  await expect(
    page.getByText("Son doğrulanmış liste gösteriliyor; güncel olmayabilir."),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Aktif proje" })).toHaveValue(
    projectId,
  );
  await expect(
    page.getByRole("button", { name: "Evidence.txt kaynağını sil" }),
  ).toBeVisible();
  await page.route("**/api/v1/projects", (route) =>
    route.fulfill({ status: 403, json: null }),
  );
  await page.getByRole("button", { name: "Projeleri yeniden yükle" }).click();
  await expect(page.getByText("Bu işlem için yetkiniz yok.")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Aktif proje" })).toHaveValue(
    "",
  );
  await expect(
    page.getByRole("button", { name: "Evidence.txt kaynağını sil" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Belge dosyaları")).toBeDisabled();
  await expect(page.getByLabel("Belgen hakkında sor")).toBeDisabled();
});
