import { fileURLToPath, URL } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const backendDirectory = fileURLToPath(new URL("../backend", import.meta.url));
const localUvCache = fileURLToPath(new URL("../.cache/uv", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.smoke.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 15_000,
  expect: {
    timeout: 5000,
  },
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run uvicorn app.main:app --host 127.0.0.1 --port 8010",
      cwd: backendDirectory,
      env: {
        UV_CACHE_DIR: localUvCache,
      },
      url: "http://127.0.0.1:8010/health",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: "npm run dev",
      cwd: fileURLToPath(new URL(".", import.meta.url)),
      env: {
        VITE_API_PROXY_TARGET: "http://127.0.0.1:8010",
      },
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
  ],
});
