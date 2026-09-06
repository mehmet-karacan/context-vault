import { expect, test } from "@playwright/test";
test("real HTTP auth → create/select → encrypted upload/worker → retrieval/citation → soft delete", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const projectName = `A10 browser ${Date.now()}`;
  await page.goto("/");
  await page
    .getByLabel("Workspace UUID")
    .fill("22222222-2222-4222-8222-222222222222");
  await page
    .getByLabel("API anahtarı")
    .fill("test-live-browser-memory-only-0000");
  await page.getByRole("button", { name: "Bağlan", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Aktif proje" })
    .selectOption("__new__");
  await page.getByLabel("Yeni proje adı").fill(projectName);
  await page.getByRole("button", { name: "Oluştur", exact: true }).click();
  await expect(page.getByLabel("Belge dosyaları")).toBeEnabled();
  await page
    .getByLabel("Belge dosyaları")
    .setInputFiles({
      name: "payment.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("PAYMENT_FLAG equals 1 when payment is complete."),
    });
  await expect(
    page.getByText("completed", { exact: false }).first(),
  ).toBeVisible({ timeout: 30_000 });
  await page
    .getByLabel("Belgen hakkında sor")
    .fill("PAYMENT_FLAG ne zaman tamamlanır?");
  await page.getByRole("button", { name: "Gönder" }).click();
  const evidence = page.getByRole("region", { name: "Kullanılan kanıtlar" });
  await expect(evidence).toBeVisible({ timeout: 30_000 });
  await evidence.locator("summary").click();
  await expect(
    evidence.getByText("PAYMENT_FLAG equals 1 when payment is complete."),
  ).toBeVisible();
  await expect(evidence.getByText("Bilinmiyor")).toHaveCount(0);
  await page.screenshot({
    path: "reports/live-citation-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "payment.txt kaynağını sil" }).click();
  await expect(
    page.getByText("Bu projede henüz kaynak yok.", { exact: false }),
  ).toBeVisible();
});
