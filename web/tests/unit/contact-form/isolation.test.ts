/**
 * Architectural-isolation test (Phase 3I, §9): proves, from the *real*
 * import graph of the actual source files on disk -- never a
 * hand-maintained expected list -- that the contact/Worker feature and
 * every report-related feature stay mutually unreachable.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { buildImportGraph, listSourceFiles, reachableFrom } from "../../helpers/importGraph";

const WEB_ROOT = fileURLToPath(new URL("../../../", import.meta.url));
const SRC_DIR = `${WEB_ROOT}src`;
const WORKER_DIR = `${WEB_ROOT}worker`;

const graph = buildImportGraph([SRC_DIR, WORKER_DIR]);

const CONTACT_ROOTS = [
  ...listSourceFiles(`${SRC_DIR}/features/contact-form`),
  ...listSourceFiles(WORKER_DIR),
  `${SRC_DIR}/pages/request-demo.astro`,
  `${SRC_DIR}/pages/feedback.astro`,
];

const REPORT_RELATED_DIRS = [
  `${SRC_DIR}/features/report-import`,
  `${SRC_DIR}/features/report-workspace`,
  `${SRC_DIR}/features/local-report-explorer`,
  `${SRC_DIR}/features/comparison`,
  `${SRC_DIR}/features/executive-summary`,
  `${SRC_DIR}/features/demo-controller`,
  `${SRC_DIR}/features/check-catalogue`,
];

const REPORT_RELATED_ROOTS = [
  ...REPORT_RELATED_DIRS.flatMap((dir) => listSourceFiles(dir)),
  `${SRC_DIR}/pages/demo/kubernetes.astro`,
  `${SRC_DIR}/pages/demo/gitlab.astro`,
  `${SRC_DIR}/pages/explorer.astro`,
  `${SRC_DIR}/pages/checks/index.astro`,
  `${SRC_DIR}/pages/checks/[id].astro`,
  `${SRC_DIR}/pages/index.astro`,
  `${SRC_DIR}/data/check-catalogue.json`,
];

function isUnderAnyDir(path: string, dirs: readonly string[]): boolean {
  return dirs.some((dir) => path === dir || path.startsWith(`${dir}/`));
}

describe("contact-form/Worker architectural isolation (Phase 3I)", () => {
  it("has a non-empty, real set of contact/Worker source files to test (sanity check)", () => {
    expect(CONTACT_ROOTS.length).toBeGreaterThan(5);
  });

  it("has a non-empty, real set of report-related source files to test (sanity check)", () => {
    expect(REPORT_RELATED_ROOTS.length).toBeGreaterThan(20);
  });

  it("no contact/Worker module transitively reaches a report-related module", () => {
    const reachable = reachableFrom(CONTACT_ROOTS, graph);
    const violations = [...reachable].filter((path) => isUnderAnyDir(path, REPORT_RELATED_DIRS));
    expect(violations).toEqual([]);
  });

  it("no report-related module transitively reaches the contact feature or the Worker", () => {
    const reachable = reachableFrom(REPORT_RELATED_ROOTS, graph);
    const violations = [...reachable].filter(
      (path) => path.startsWith(`${SRC_DIR}/features/contact-form/`) || path.startsWith(`${WORKER_DIR}/`),
    );
    expect(violations).toEqual([]);
  });

  it("no report-related source file contains the literal string '/api/contact'", () => {
    const offenders = REPORT_RELATED_ROOTS.filter((path) => readFileSync(path, "utf-8").includes("/api/contact"));
    expect(offenders).toEqual([]);
  });

  it("no report-related source file references submitContactForm", () => {
    const offenders = REPORT_RELATED_ROOTS.filter((path) => readFileSync(path, "utf-8").includes("submitContactForm"));
    expect(offenders).toEqual([]);
  });

  it("the contact form component offers no file input, report attachment, or finding-export control", () => {
    // The component's own disclosure prose legitimately mentions "report"
    // (to explain that this form is unrelated to the local report
    // explorer) -- so this checks for the concrete *mechanisms* an
    // attachment/export/transfer control would require, not the word
    // "report" itself.
    const contactFormSource = readFileSync(`${SRC_DIR}/features/contact-form/ContactForm.tsx`, "utf-8");
    expect(contactFormSource).not.toMatch(/type=["']file["']/);
    expect(contactFormSource).not.toMatch(/\bfinding/i);
    expect(contactFormSource).not.toMatch(/\battach(ment)?\b/i);
    expect(contactFormSource).not.toMatch(/NormalizedWebReport|NormalizedFinding/);
  });
});
