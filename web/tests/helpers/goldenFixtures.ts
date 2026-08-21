/**
 * Loads the two real repository-root golden fixtures directly from their
 * existing location (`tests/fixtures/` at the repository root) -- they are
 * never copied into `web/`, regenerated, or modified. The path is derived
 * from this module's own `import.meta.url` rather than assuming any
 * particular shell working directory, so these tests work the same way
 * whether Vitest is invoked from `web/` or elsewhere.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// This file lives at web/tests/helpers/goldenFixtures.ts. The repository
// root is three directories up: helpers -> tests -> web -> repository root.
const THIS_DIR = fileURLToPath(new URL(".", import.meta.url));
const REPO_ROOT = join(THIS_DIR, "..", "..", "..");

function loadFixtureFile(repoRelativePath: string): unknown {
  const absolutePath = join(REPO_ROOT, repoRelativePath);
  const raw = readFileSync(absolutePath, "utf-8");
  return JSON.parse(raw) as unknown;
}

export function loadGoldenKubernetesReport(): unknown {
  return loadFixtureFile(join("tests", "fixtures", "golden_kubernetes_report.json"));
}

export function loadGoldenGitLabReport(): unknown {
  return loadFixtureFile(join("tests", "fixtures", "golden_gitlab_report.json"));
}
