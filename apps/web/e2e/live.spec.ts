import { expect, test, type Page } from "@playwright/test";

const productCsv = "external_id,sku,name,price\np1,BAG-1,Canvas bag,1000.00\n";
const inventoryCsv = "external_id,sku,location,sellable,reserved,quarantined,observed_at\nwms-1,BAG-1,Main,5,0,0,2026-10-15T09:00:00+05:30\nerp-1,BAG-1,Main,2,0,0,2026-10-15T09:00:00+05:30\n";
const orderCsv = "external_id,customer_ref,status,channel,total,placed_at\no-1,C-001,fulfilled,CSV,1000.00,2026-10-14T10:00:00+05:30\no-2,C-002,processing,CSV,750.00,2026-10-14T11:00:00+05:30\n";

async function signup(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to your business" })).toBeVisible();
  await page.getByRole("button", { name: "Create a workspace" }).click();
  await page.getByLabel("Email").fill(`browser-${Date.now()}-${Math.random().toString(36).slice(2)}@example.com`);
  await page.getByLabel("Password").fill("a-unique-browser-test-password");
  await page.getByLabel("Business name").fill("Actual Records Ltd");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
}

async function importCsv(page: Page, kind: string, content: string) {
  await page.getByRole("button", { name: "Import data" }).click();
  await page.getByLabel("Record type").selectOption(kind);
  await page.getByLabel("UTF-8 CSV").setInputFiles({ name: `${kind}.csv`, mimeType: "text/csv", buffer: Buffer.from(content) });
  await page.getByRole("button", { name: "Validate and preview" }).click();
  await expect(page.locator(".live-preview-summary")).toContainText(/rows\s*\/\s*\d+ new/);
  await page.getByRole("button", { name: "Commit validated batch" }).click();
  await expect(page.getByText(/records imported;/)).toBeVisible();
}

test("live orders import persists through refresh and opens the source row", async ({ page }) => {
  await signup(page);
  await expect(page.getByText("Your live workspace is empty")).toBeVisible();
  await importCsv(page, "orders", orderCsv);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Live Twin" })).toBeVisible();
  await page.getByRole("button", { name: "Live Twin" }).click();
  await expect(page.locator(".live-kpis .metric-label", { hasText: /^ORDERS$/ }).locator("..").locator(".metric-value")).toHaveText("2");
  await page.screenshot({ path: "../../docs/opsverse-live-operations.png", fullPage: true });
  await page.locator(".live-inspector .live-record").first().click();
  await expect(page.getByText("SOURCE RECORD", { exact: true })).toBeVisible();
  await expect(page.locator(".event-drawer pre").last()).toContainText('"channel": "CSV"');
});

test("real inventory mismatch is investigated and compared without source writes", async ({ page }) => {
  page.on("requestfailed", request => console.error("Failed request", request.method(), request.url(), request.failure()?.errorText));
  page.on("response", response => { if (response.status() >= 400) console.error("HTTP response", response.status(), response.url()); });
  page.on("pageerror", error => console.error("Browser page error", error.message));
  page.on("console", message => { if (message.type() === "error") console.error("Browser console error", message.text()); });
  await signup(page);
  await importCsv(page, "products", productCsv);
  await importCsv(page, "inventory", inventoryCsv);
  await page.getByRole("button", { name: "Live Twin" }).click();
  const sellableBefore = await page.locator(".live-kpis .metric").filter({ hasText: "SELLABLE" }).locator(".metric-value").textContent();
  await page.getByRole("button", { name: /Incidents/ }).click();
  await expect(page.getByText("Inventory Mismatch").first()).toBeVisible();
  await page.locator(".incident-row").first().click();
  await expect(page.getByText("Confirmed Inconsistency").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Evidence graph" })).toBeVisible();
  await expect(page.locator(".live-evidence-canvas .react-flow__node")).toHaveCount(5);
  await page.locator(".live-evidence-canvas .react-flow__node").filter({ hasText: "wms-1" }).click();
  await expect(page.getByText("SOURCE RECORD", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.locator(".live-evidence-workspace").evaluate(element => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 80));
  await page.screenshot({ path: "../../docs/screenshots/v2-incident-microscope.png", animations: "disabled" });
  await page.getByRole("button", { name: "Investigate with read tools" }).click();
  await expect(page.getByText("Deterministic fallback").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Proposed recovery steps" })).toBeVisible();
  await expect(page.getByText("Recount the disputed SKU at the recorded location")).toBeVisible();
  await page.getByLabel("Review note").fill("Checking both warehouse counts before replenishment");
  await page.getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.getByText("Checking both warehouse counts before replenishment")).toBeVisible();
  await expect(page.getByText("0 open")).toBeVisible();
  await page.getByRole("button", { name: "Scenario Lab" }).click();
  await page.getByRole("button", { name: "Create snapshot" }).click();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await expect(page.getByRole("heading", { name: "Hold disputed units" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pause fulfillment for verification" })).toBeVisible();
  await page.getByLabel("Restock availability · units").fill("8");
  await expect(page.getByText(/Saved results are stale/)).toBeVisible();
  await page.getByRole("button", { name: "Compare futures" }).click();
  await page.getByRole("button", { name: "Run five-seed sensitivity" }).click();
  await expect(page.getByText(/5 repetitions/)).toBeVisible();
  await page.getByRole("button", { name: "Explain difference" }).click();
  await expect(page.getByText(/A \d+ → B \d+;/)).toBeVisible();
  await page.getByLabel("Historical cutoff").fill("2026-10-16T09:00");
  await page.getByRole("button", { name: "Evaluate history" }).click();
  await expect(page.getByText("Insufficient observed history for a valid backtest.")).toBeVisible();
  await page.locator(".live-branches").evaluate(element => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 80));
  await page.screenshot({ path: "../../docs/screenshots/v2-decision-lab.png", animations: "disabled" });
  await page.getByLabel("Review note").last().fill("Holding disputed units until a recount confirms the balance");
  await page.getByRole("button", { name: "Record preferred plan" }).click();
  await page.getByRole("button", { name: "Verify sandbox replay" }).click();
  await expect(page.getByText(/Sandbox replay verified/)).toBeVisible();
  const receiptPagePromise = page.context().waitForEvent("page");
  await page.getByRole("link", { name: /Open printable Decision Receipt/ }).click();
  const receiptPage = await receiptPagePromise;
  await receiptPage.waitForLoadState("domcontentloaded");
  await expect(receiptPage.getByRole("heading", { name: "Incident to decision" })).toBeVisible();
  await expect(receiptPage.getByText("Reproducibility manifest")).toBeVisible();
  await receiptPage.screenshot({ path: "../../docs/screenshots/v2-decision-receipt.png", fullPage: true, animations: "disabled" });
  await receiptPage.close();
  await page.locator(".live-branches").screenshot({ path: "../../docs/opsverse-live-scenario.png" });
  await page.getByRole("button", { name: "Record History" }).click();
  await expect(page.getByRole("heading", { name: "Provider and import events" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Audit history" })).toBeVisible();
  await page.getByRole("button", { name: "Live Twin" }).click();
  await expect(page.locator(".live-kpis .metric").filter({ hasText: "SELLABLE" }).locator(".metric-value")).toHaveText(sellableBefore ?? "");
});
