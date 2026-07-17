import { expect, test } from "@playwright/test";

test("application shell reports a healthy local API", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("AI Intelligence OS");
  await expect(page.getByRole("status")).toContainText("Local API healthy");
});
