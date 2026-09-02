import { expect, test } from "@playwright/test";

test("home shell renders and backend failures remain contained", async ({ page }) => {
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "test backend unavailable" }),
    });
  });

  await page.goto("/");
  await expect(page).toHaveTitle("Belge Arşivi");
  await expect(page.getByRole("link", { name: "Arşiv", exact: true })).toBeVisible();
});
