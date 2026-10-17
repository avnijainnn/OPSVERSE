import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const shots = resolve(__dirname, "../../../docs/screenshots");
mkdirSync(shots, { recursive: true });

async function capture(page: Page, name: string) {
  await page.screenshot({ path: resolve(shots, `${name}.png`), animations: "disabled", fullPage: true });
  const overflow = await page.evaluate(() => ({
    width: innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    elements: Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter(element => { const rect = element.getBoundingClientRect(); return rect.right > innerWidth + 2 && rect.width > 0 && getComputedStyle(element).position !== "fixed"; })
      .slice(0, 10).map(element => ({ tag: element.tagName, className: String(element.className), right: Math.round(element.getBoundingClientRect().right), width: Math.round(element.getBoundingClientRect().width) })),
  }));
  expect(overflow.scrollWidth, `${name}: ${JSON.stringify(overflow)}`).toBeLessThanOrEqual(overflow.width + 2);
}

async function liveView(page: Page, label: string, mobile = false) {
  if (mobile) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.locator(".sidebar .nav-item").filter({ hasText: label }).click();
  await expect(page.getByRole("heading", { name: label, exact: true }).first()).toBeVisible();
}

async function demoView(page: Page, label: string, mobile = false) {
  if (mobile) await page.getByRole("button", { name: "Toggle navigation" }).click();
  await page.locator(".sidebar .nav-item").filter({ hasText: label }).click();
  await expect(page.getByRole("heading", { name: label, exact: true }).first()).toBeVisible();
}

async function importCsv(page: Page, kind: string, csv: string) {
  await liveView(page, "Integrations");
  await page.getByLabel("Record type").selectOption(kind);
  await page.getByLabel("UTF-8 CSV").setInputFiles({ name: `${kind}.csv`, mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Validate and preview" }).click();
  await expect(page.locator(".live-preview-summary")).toContainText(/\d+ new/);
  await page.getByRole("button", { name: "Commit validated batch" }).click();
  await expect(page.getByText(/records imported;/)).toBeVisible();
}

test("live commerce walkthrough and desktop/mobile screen audit on real API", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/live");
  await page.getByRole("button", { name: "Create a workspace" }).click();
  await page.getByLabel("Email").fill(`showcase-${Date.now()}@example.test`);
  await page.getByLabel("Password").fill("a-unique-showcase-test-password");
  await page.getByLabel("Business name").fill("Aaranya Commerce Live");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();

  await importCsv(page, "products", "external_id,sku,name,price\np1,BAG-1,Canvas travel bag,1000.00\np2,LAMP-2,Desk lamp,750.00\n");
  await importCsv(page, "orders", "external_id,customer_ref,status,channel,total,placed_at\no1,buyer-a,processing,Shopify export,1000.00,2026-10-15T09:00:00+05:30\no2,buyer-b,fulfilled,Shopify export,750.00,2026-10-15T09:05:00+05:30\n");
  await importCsv(page, "inventory", "external_id,sku,location,sellable,reserved,quarantined,observed_at\nwms-1,BAG-1,Main,5,1,0,2026-10-15T09:10:00+05:30\nerp-1,BAG-1,Main,2,1,0,2026-10-15T09:10:00+05:30\nlamp-1,LAMP-2,Main,12,0,0,2026-10-15T09:10:00+05:30\n");
  await importCsv(page, "payments", "external_id,order_external_id,amount,fee,fee_tax,captured_at\npay-1,o1,1000.00,20.00,3.00,2026-10-15T09:01:00+05:30\n");
  await importCsv(page, "settlements", "external_id,payment_external_id,amount,settled_at\nsettle-1,pay-1,977.00,2026-10-16T09:00:00+05:30\n");
  await importCsv(page, "bank_transactions", "external_id,settlement_external_id,amount,posted_at\nbank-1,settle-1,977.00,2026-10-16T12:00:00+05:30\n");

  await liveView(page, "Live Twin");
  await expect(page.locator(".live-kpis")).toContainText("1,750.00");
  const sourceMetrics = await page.locator(".live-kpis .metric-value").allTextContents();
  await capture(page, "live-twin-desktop");
  await liveView(page, "Incidents");
  await page.locator(".incident-row").filter({ hasText: "Inventory Mismatch" }).click();
  await expect(page.locator(".live-detail-body")).toBeVisible();
  await expect(page.locator(".live-detail-body .live-record").first()).toBeVisible();
  await page.getByRole("button", { name: "Investigate with read tools" }).click();
  await expect(page.getByText("Deterministic fallback").first()).toBeVisible();
  await capture(page, "incidents-desktop");
  await liveView(page, "Scenario Lab");
  await page.getByRole("button", { name: "Create snapshot" }).click();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await expect(page.locator(".live-branch")).toHaveCount(3);
  await page.locator(".live-branch details").first().click();
  await expect(page.locator(".live-branch .live-sim-event").first()).toBeVisible();
  await capture(page, "scenario-lab-desktop");
  await page.getByLabel("Review note").last().fill("Hold disputed units until a physical recount confirms the balance");
  await page.getByRole("button", { name: "Record preferred plan" }).click();
  await page.getByRole("button", { name: "Verify sandbox replay" }).click();
  await expect(page.getByText(/Sandbox replay verified/)).toBeVisible();
  await liveView(page, "Live Twin");
  await expect(page.locator(".live-kpis .metric-value")).toHaveText(sourceMetrics);
  for (const label of ["Money Trail", "Agent Traces", "Record History", "Benchmarks", "Integrations"]) {
    await liveView(page, label);
    await capture(page, `${label.toLowerCase().replaceAll(" ", "-")}-desktop`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  for (const label of ["Live Twin", "Incidents", "Scenario Lab", "Money Trail", "Agent Traces", "Record History", "Benchmarks", "Integrations"]) {
    await liveView(page, label, true);
    await capture(page, `${label.toLowerCase().replaceAll(" ", "-")}-mobile`);
  }
});

test("synthetic demo screen audit and live guided workflow", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/demo");
  await page.getByRole("button", { name: "Launch Demo" }).click();
  await expect(page.locator(".world-map")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".operation-banner")).toBeHidden({ timeout: 30_000 });
  await capture(page, "demo-live-twin-desktop");
  await page.getByRole("button", { name: "Inject scenario" }).click();
  await page.getByRole("button", { name: "Damaged return" }).click();
  await expect(page.locator(".incident-row").first()).toBeVisible({ timeout: 30_000 });
  await demoView(page, "Incidents");
  await expect(page.locator(".detail-panel .finding")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".operation-banner")).toBeHidden({ timeout: 30_000 });
  await expect(page.locator(".dropdown")).toBeHidden();
  await capture(page, "demo-incidents-desktop");
  await page.getByRole("button", { name: "Compare futures" }).click();
  await demoView(page, "Scenario Lab");
  await expect(page.locator(".branch-card")).toHaveCount(3);
  await expect(page.locator(".operation-banner")).toBeHidden({ timeout: 30_000 });
  await capture(page, "demo-scenario-lab-desktop");
  await page.getByRole("button", { name: "Approve in sandbox" }).click();
  await expect(page.locator(".operation-banner")).toBeHidden({ timeout: 30_000 });
  for (const label of ["Money Trail", "Agent Traces", "Replay", "Benchmarks", "Settings"]) {
    await demoView(page, label);
    if (label === "Agent Traces") await expect(page.locator(".trace-row").first()).toBeVisible();
    if (label === "Benchmarks") await expect(page.getByRole("heading", { name: "Positive and negative controls" })).toBeVisible();
    await capture(page, `demo-${label.toLowerCase().replaceAll(" ", "-")}-desktop`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  for (const label of ["Live Twin", "Incidents", "Scenario Lab", "Money Trail", "Agent Traces", "Replay", "Benchmarks", "Settings"]) {
    await demoView(page, label, true);
    if (label === "Agent Traces") await expect(page.locator(".trace-row").first()).toBeVisible();
    if (label === "Benchmarks") await expect(page.getByRole("heading", { name: "Positive and negative controls" })).toBeVisible();
    await capture(page, `demo-${label.toLowerCase().replaceAll(" ", "-")}-mobile`);
  }
});
