import { expect, test } from "@playwright/test";

test("Topics shows deterministic rankings and daily change", async ({ page }) => {
  await page.route("**/topics/overview", (route) => route.fulfill({ json: [{
    key: "agentic-systems", label: "Agentic systems", paper_count: 3, daily_change: 1,
    papers: [{ work_id: "work-1", title: "Bounded local agents", score: 81.4 }],
  }] }));
  await page.goto("/#topics");
  await expect(page.getByRole("heading", { name: "Research topics" })).toBeVisible();
  await expect(page.getByText("+1 today")).toBeVisible();
  await expect(page.getByText("81.4")).toBeVisible();
});

test("Opportunities shows only cited deep-dive output at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/opportunities", (route) => route.fulfill({ json: [{
    kind: "commercial", work_id: "work-1", title: "Bounded local agents",
    headline: "Reliability review", detail: "Engineering leads: test a local pilot",
    evidence_ids: ["evidence-page-2"], confidence: 0.72,
  }] }));
  await page.goto("/#opportunities");
  await expect(page.getByRole("heading", { name: "Opportunities", level: 2 })).toBeVisible();
  await expect(page.getByText("Evidence e-page-2")).toBeVisible();
  await expect(page.getByText("72% confidence")).toBeVisible();
});
