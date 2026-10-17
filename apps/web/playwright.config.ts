import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  workers: 1,
  use: { baseURL: "http://localhost:3000", browserName: "chromium", channel: process.env.CI ? undefined : "msedge", viewport: { width: 1440, height: 900 } },
  webServer: [
    { command: "python ../../scripts/start_test_api.py", cwd: "../api", url: "http://127.0.0.1:8000/health", reuseExistingServer: !process.env.CI && !process.env.FRESH_E2E, timeout: 120_000 },
    { command: "pnpm dev", cwd: ".", url: "http://localhost:3000", reuseExistingServer: !process.env.CI && !process.env.FRESH_E2E, timeout: 120_000 },
  ],
});
