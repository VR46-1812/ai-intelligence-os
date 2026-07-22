import { expect, test } from "@playwright/test";

test("application shell reports a healthy local API", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/health") {
      const response = await page.request.get("http://127.0.0.1:8000/health");
      await route.fulfill({ response });
      return;
    }
    await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
  });
  await page.goto("/");

  await expect(page).toHaveTitle("AI Intelligence OS");
  await expect(page.getByRole("status")).toContainText("Local API healthy");
});
