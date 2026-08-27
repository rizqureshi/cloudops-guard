/**
 * Phase 3K isolation checks: the deployment tooling must never read
 * report-related source/data, and report-related code must never
 * reference the contact endpoint. This mirrors (without duplicating) the
 * real import-graph check already covering `src/features/contact-form/`
 * and `worker/` (`tests/unit/contact-form/isolation.test.ts`, Phase 3I) --
 * these tests instead cover the two Phase 3K deployment-tooling files
 * specifically, reading their actual source from disk.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const WEB_ROOT = fileURLToPath(new URL("../../..", import.meta.url));

const REPORT_RELATED_DIRS = [
  "src/features/report-import",
  "src/features/report-workspace",
  "src/features/local-report-explorer",
  "src/features/comparison",
  "src/features/executive-summary",
  "src/features/demo-controller",
  "src/features/check-catalogue",
];

describe("Phase 3K deployment-tooling isolation", () => {
  it("the renderer's source contains no reference to any report-related feature, data path, or the contact endpoint's own logic", () => {
    const source = readFileSync(`${WEB_ROOT}/deploy/render-wrangler-configs.mjs`, "utf8");
    for (const dir of REPORT_RELATED_DIRS) {
      expect(source).not.toContain(dir);
    }
    expect(source).not.toContain("src/data/");
    expect(source).not.toMatch(/report\.json|synthetic-|check-catalogue\.json/);
  });

  it("the renderer never imports anything other than Node built-ins", () => {
    const source = readFileSync(`${WEB_ROOT}/deploy/render-wrangler-configs.mjs`, "utf8");
    const importSpecifiers = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map((match) => match[1]);
    expect(importSpecifiers.length).toBeGreaterThan(0);
    for (const specifier of importSpecifiers) {
      expect(specifier?.startsWith("node:")).toBe(true);
    }
  });

  it("no report-related source file contains the literal string '/api/contact' (unchanged from Phase 3I's own isolation guarantee)", () => {
    for (const dir of REPORT_RELATED_DIRS) {
      const dirPath = `${WEB_ROOT}/${dir}`;
      const offenders = listFilesRecursive(dirPath).filter((file) => readFileSync(file, "utf8").includes("/api/contact"));
      expect(offenders).toEqual([]);
    }
  });

  it("deploy-web.yml never reads or references report-related directories or data", () => {
    const workflow = readFileSync(`${WEB_ROOT}/../.github/workflows/deploy-web.yml`, "utf8");
    for (const dir of REPORT_RELATED_DIRS) {
      expect(workflow).not.toContain(dir);
    }
    expect(workflow).not.toContain("src/data/");
  });
});

function listFilesRecursive(dirPath: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dirPath)) {
    if (entry === "node_modules" || entry === "dist" || entry === ".astro") continue;
    const fullPath = `${dirPath}/${entry}`;
    const stats = statSync(fullPath);
    if (stats.isDirectory()) {
      results.push(...listFilesRecursive(fullPath));
    } else {
      results.push(fullPath);
    }
  }
  return results;
}
