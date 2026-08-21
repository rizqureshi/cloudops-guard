import { defineConfig } from "vitest/config";

// Minimal Vitest configuration for the report-import unit tests (Phase
// 3C). These tests exercise pure TypeScript logic against plain JSON --
// no Astro components, no React components, no DOM -- so the Node
// environment is used directly rather than a jsdom/happy-dom emulation.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/unit/**/*.test.ts"],
    passWithNoTests: false,
  },
});
