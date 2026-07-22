import { expect, test } from "@playwright/test";

test("four-destination shell reports a healthy local API", async ({ page }) => {
  await page.goto("/#discover");
  await expect(page).toHaveTitle("AI Intelligence OS");
  await expect(page.getByRole("status")).toContainText("Local API healthy");
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  for (const label of ["Daily Brief", "Discover", "Intelligence", "Settings"]) await expect(navigation.getByRole("link", { name: label })).toBeVisible();
  await expect(navigation.getByRole("link")).toHaveCount(4);
});

test("command palette is keyboard accessible and navigates", async ({ page }) => {
  await page.goto("/#daily"); await page.keyboard.press("Control+K");
  await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
  await page.getByLabel("Search commands").fill("opportunities");
  await page.getByRole("link", { name: /View opportunities/ }).press("Enter");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Opportunities" })).toHaveAttribute("aria-selected", "true");
});
