import { expect, test } from "@playwright/test";

test("Topics shows deterministic rankings and daily change", async ({ page }) => {
  await page.route("**/topics/overview", (route) => route.fulfill({ json: [{
    key: "agentic-systems", label: "Agentic systems", paper_count: 3, daily_change: 1,
    papers: [{ work_id: "work-1", title: "Bounded local agents", score: 81.4 }],
  }] }));
  await page.goto("/#intelligence/topics");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agentic systems" })).toBeVisible();
  await expect(page.getByText("+1 today")).toBeVisible();
  await expect(page.getByText("81.4")).toBeVisible();
});

test("canonical Earn opportunities are non-empty in Intelligence at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/opportunities", (route) => route.fulfill({ json: [{
    kind: "commercial", work_id: "work-1", title: "Bounded local agents",
    headline: "Reliability review", detail: "Engineering leads: test a local pilot",
    label: "commercial_hypothesis", evidence_ids: ["evidence-page-2"], confidence: 0.72,
    target_customer: "Indian AI teams", painful_workflow: "Manual claim review", proposed_offer: "Validation pilot",
    provisional_pricing: "INR 50,000 pilot", validation_experiment: "Interview three buyers", india_market_relevance: "Fixed-scope buying", project_relevance: ["BidReady"], assumptions: ["Evidence is available"], risks: ["Urgency unknown"],
  }] }));
  await page.goto("/#intelligence/opportunities");
  await expect(page.getByRole("tab", { name: "Opportunities" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("1 cited spans")).toBeVisible();
  await expect(page.getByText("72% confidence")).toBeVisible();
  await expect(page.getByText("INR 50,000 pilot")).toBeVisible();
});
