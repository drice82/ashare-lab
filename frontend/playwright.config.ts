import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: 0,
  timeout: 60000,
  expect: { timeout: 30000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080",
    viewport: { width: 1440, height: 1050 },
    trace: "retain-on-failure",
  },
});
