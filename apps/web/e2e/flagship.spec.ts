import { expect, test } from "@playwright/test";

test("damaged return: investigate, compare, approve, replay", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
  await expect(page.getByText("Business network")).toBeVisible();
  const previousRun = await page.locator(".run-chip span").first().textContent();
  await page.getByRole("button", { name: "Launch Demo" }).click();
  await expect.poll(() => page.locator(".run-chip span").first().textContent(), { timeout: 30_000 }).not.toBe(previousRun);
  await expect(page.getByText("Business network")).toBeVisible();
  await page.screenshot({ path: "../../docs/opsverse-live.png", fullPage: true });

  await page.getByRole("button", { name: "Inject scenario" }).click();
  await page.getByRole("button", { name: "Damaged return" }).click();
  await expect(page.getByRole("heading", { name: "Operational incidents" })).toBeVisible();
  await expect(page.getByText("Premature external stock sync before quality inspection")).toBeVisible();
  await expect(page.locator(".causal-graph .react-flow__node")).toHaveCount(2);
  await page.screenshot({ path: "../../docs/opsverse-incident.png", fullPage: true });
  await page.locator(".causal-graph .react-flow__node").last().click();
  await expect(page.getByText("SOURCE EVENT")).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await expect(page.getByRole("heading", { name: "No intervention" })).toBeVisible();
  await expect(page.getByText("POLICY-VALIDATED PLAN")).toBeVisible();
  await page.getByRole("button", { name: "Replay all futures" }).click();
  await expect(page.getByRole("heading", { name: "Recovery timeline" })).toBeVisible();
  await page.screenshot({ path: "../../docs/opsverse-scenarios.png", fullPage: true });
  await page.getByRole("button", { name: "Approve in sandbox" }).click();
  await page.getByRole("button", { name: "Incidents" }).click();
  await expect(page.getByText("Verification passed")).toBeVisible();
  await page.getByRole("button", { name: "Live Twin" }).click();
  await page.screenshot({ path: "../../docs/opsverse-recovered.png", fullPage: true });
  await page.getByRole("button", { name: "Agent Traces" }).click();
  await expect(page.getByText("VALIDATED TOOL CALLS")).toBeVisible();
  await expect(page.getByText("get_related_events")).toBeVisible();
  await page.screenshot({ path: "../../docs/opsverse-traces.png", fullPage: true });
  await page.getByRole("button", { name: "Replay", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Event sequence" })).toBeVisible();
  await page.getByRole("button", { name: "Step back" }).click();
  await expect(page.getByText("Projection rebuilt by replaying recorded events through this sequence.")).toBeVisible();
});

test("guided demo executes the sandbox workflow", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
  await page.getByRole("button", { name: "Guided tour" }).click();
  await expect(page.getByRole("heading", { name: "A living business" })).toBeVisible();
  await page.getByRole("button", { name: "Trigger return fault" }).click();
  await expect(page.getByRole("heading", { name: "Evidence before action" })).toBeVisible();
  await page.getByRole("button", { name: "Test three futures" }).click();
  await expect(page.getByRole("heading", { name: "Compare decisions" })).toBeVisible();
  await page.getByRole("button", { name: "Test review delay" }).click();
  await expect(page.getByRole("heading", { name: "Sensitivity to delay" })).toBeVisible();
  await page.getByRole("button", { name: "Approve quarantine" }).click();
  await expect(page.getByText("Verification passed")).toBeVisible();
  const receiptPromise = page.context().waitForEvent("page");
  await page.getByRole("button", { name: "Open Decision Receipt" }).click();
  const receipt = await receiptPromise;
  await receipt.waitForLoadState("domcontentloaded");
  await expect(receipt.getByRole("heading", { name: "Decision Receipt" })).toBeVisible();
  await receipt.close();
  await page.getByRole("button", { name: "Replay the chain" }).click();
  await expect(page.getByRole("heading", { name: "Event sequence" })).toBeVisible();
  await page.getByRole("button", { name: "Finish tour" }).click();
  await expect(page.getByText("GUIDED DEMO · REPRODUCIBLE")).toHaveCount(0);
});

test("settlement false alarm reconciles to the bank credit", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
  await page.getByRole("button", { name: "Launch Demo" }).click();
  await page.getByRole("button", { name: "Inject scenario" }).click();
  await page.getByRole("button", { name: "Settlement mismatch" }).click();
  await expect(page.getByText("prior-period refund adjustment", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await expect(page.getByRole("heading", { name: "No intervention" })).toBeVisible();
  await page.getByRole("button", { name: "Approve in sandbox" }).click();
  await page.getByRole("button", { name: "Incidents" }).click();
  await expect(page.getByText("Verification passed")).toBeVisible();
  await page.getByRole("button", { name: "Money Trail" }).click();
  await expect(page.getByText("₹876.40").first()).toBeVisible();
});

test("supplier recovery pays its cost and prevents lost demand", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
  await page.getByRole("button", { name: "Launch Demo" }).click();
  await page.getByRole("button", { name: "Inject scenario" }).click();
  await page.getByRole("button", { name: "Supplier delay" }).click();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await expect(page.getByRole("heading", { name: "No intervention" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".branch-card.recovery .branch-metric").filter({ hasText: "Intervention cost" }).locator("strong")).toHaveText("₹15,000.00");
  const baselineLost = await page.locator(".branch-card").first().locator(".branch-metric").filter({ hasText: "Lost demand" }).locator("strong").textContent();
  const recoveredLost = await page.locator(".branch-card").last().locator(".branch-metric").filter({ hasText: "Lost demand" }).locator("strong").textContent();
  expect(Number(baselineLost)).toBeGreaterThan(Number(recoveredLost));
  await page.getByRole("button", { name: "Approve in sandbox" }).click();
  await page.getByRole("button", { name: "Incidents" }).click();
  await expect(page.getByText("Verification passed")).toBeVisible();
});

test("genuine missing payout remains open after a dispute", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
  await page.getByRole("button", { name: "Launch Demo" }).click();
  await page.getByRole("button", { name: "Inject scenario" }).click();
  await page.getByRole("button", { name: "Missing payout" }).click();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await expect(page.getByRole("heading", { name: "No intervention" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".branch-card.recovery .branch-metric").filter({ hasText: "Payout disputes" }).locator("strong")).toHaveText("1");
  await page.getByRole("button", { name: "Approve in sandbox" }).click();
  await page.getByRole("button", { name: "Incidents" }).click();
  await expect(page.getByText("Dispute opened; payout remains unreconciled")).toBeVisible();
  await page.getByRole("button", { name: "Money Trail" }).click();
  await expect(page.locator(".finance-summary .metric").filter({ hasText: "UNRECONCILED" }).locator(".metric-value")).toHaveText("₹110.00");
});
