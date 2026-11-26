import { defineConfig } from "@playwright/test";

// Система уже запущена: LLM_STUB=true docker compose up (модель — заглушка, сеть не нужна).
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    locale: "ru-RU",
    trace: "retain-on-failure",
  },
});
