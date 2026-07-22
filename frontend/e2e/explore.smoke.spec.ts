import { expect, test } from "@playwright/test";

test("Discover loads stored SQLite papers, filters, opens detail, and reports sync", async ({
  page,
}) => {
  await page.route("**/sources/arxiv/sync", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ingestion: {
          status: "succeeded",
          records_seen: 5,
          records_created: 0,
          duplicate_records: 5,
        },
        records_normalized: 0,
        records_rejected: 0,
      }),
    });
  });
  await page.goto("/#discover");

  await expect(page.getByRole("heading", { name: "Discover research worth understanding." })).toBeVisible();
  await expect(page.locator(".paper-card").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: /stored papers?/i })).toBeVisible();
  await expect(page.getByLabel("Source type")).toBeVisible();
  await expect(page.getByLabel("Minimum authority")).toBeVisible();
  await expect(page.getByLabel("Linked evidence")).toBeVisible();
  await expect(page.getByLabel("Associated events only")).toBeVisible();

  const firstTitle = page.locator(".paper-title").first();
  const title = await firstTitle.textContent();
  await firstTitle.click();
  await expect(page.getByRole("complementary", { name: "Paper detail" })).toContainText(
    title ?? "",
  );
  await expect(page.getByText("Deterministic ranking")).toBeVisible();
  await expect(page.getByText("Cited document evidence")).toBeVisible();
  await expect(page.getByText(/evidence spans/).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /View canonical paper/ })).toHaveAttribute(
    "href",
    /^https:\/\/arxiv\.org\/abs\//,
  );

  await page.getByRole("button", { name: "Close paper detail" }).click();
  await page.getByRole("searchbox", { name: "Search stored papers" }).fill("video");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.locator(".paper-card").first()).toBeVisible();

  await page.getByRole("button", { name: "Sync latest research" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Sync complete" })).toContainText(
    "5 fetched",
  );
});

test("Discover remains usable without horizontal overflow at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#discover");

  await expect(page.getByRole("searchbox", { name: "Search stored papers" })).toBeVisible();
  await expect(page.getByLabel("Topic")).toBeVisible();
  await expect(page.locator(".paper-card").first()).toBeVisible();
  await page.locator(".paper-title").first().click();
  await expect(page.getByRole("complementary", { name: "Paper detail" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("Discover communicates loading, empty, and API failure states", async ({ page }) => {
  await page.route("**/items?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, limit: 5, offset: 0, has_more: false }),
    });
  });
  await page.goto("/#discover");

  await expect(page.getByLabel("Loading research papers")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No papers match this view" })).toBeVisible();

  await page.unroute("**/items?**");
  await page.route("**/items?**", async (route) => {
    await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
  });
  await page.getByRole("button", { name: "Reset search" }).click();
  await expect(page.getByRole("heading", { name: "Catalog unavailable" })).toBeVisible();
});
