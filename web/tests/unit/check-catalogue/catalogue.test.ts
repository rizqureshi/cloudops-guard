import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { CHECK_CATALOGUE, findCatalogueEntry } from "../../../src/features/check-catalogue/catalogue";
import { deriveCategory } from "../../../src/features/report-workspace/category";

const EXPECTED_CHECK_IDS = [
  "K8S-RES-001",
  "K8S-RES-002",
  "K8S-RES-003",
  "K8S-RES-004",
  "K8S-IMG-001",
  "K8S-REL-001",
  "GL-BR-001",
  "GL-BR-002",
  "GL-BR-003",
  "GL-MR-001",
  "GL-SEC-001",
  "GL-SEC-002",
  "GL-SEC-003",
  "GL-COST-001",
  "GL-COST-002",
  "GL-REL-001",
  "GL-CI-001",
];

const VALID_PLATFORMS = new Set(["kubernetes", "gitlab"]);
const VALID_SEVERITIES = new Set(["critical", "high", "medium", "low"]);
const REQUIRED_TEXT_FIELDS = [
  "title",
  "triggerCondition",
  "evidenceDescription",
  "impact",
  "recommendation",
] as const;

describe("CHECK_CATALOGUE: identity", () => {
  it("has exactly 17 entries", () => {
    expect(CHECK_CATALOGUE).toHaveLength(17);
  });

  it("has 17 unique check IDs", () => {
    const ids = new Set(CHECK_CATALOGUE.map((entry) => entry.checkId));
    expect(ids.size).toBe(17);
  });

  it("contains exactly the 17 currently implemented check IDs, no more and no fewer", () => {
    const actualIds = new Set(CHECK_CATALOGUE.map((entry) => entry.checkId));
    expect(actualIds).toEqual(new Set(EXPECTED_CHECK_IDS));
  });

  it("is sorted in deterministic, plain ordinal check-ID order (never locale-aware)", () => {
    const ids = CHECK_CATALOGUE.map((entry) => entry.checkId);
    // Plain code-unit comparison, matching `compareOrdinal` -- deliberately
    // not `localeCompare`, which is environment-dependent.
    const ordinalSorted = [...ids].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    expect(ids).toEqual(ordinalSorted);
  });
});

describe("CHECK_CATALOGUE: field validity", () => {
  it("every entry has a valid platform", () => {
    for (const entry of CHECK_CATALOGUE) {
      expect(VALID_PLATFORMS.has(entry.platform)).toBe(true);
    }
  });

  it("every entry has a valid severity", () => {
    for (const entry of CHECK_CATALOGUE) {
      expect(VALID_SEVERITIES.has(entry.severity)).toBe(true);
    }
  });

  it("every required text field is non-empty on every entry", () => {
    for (const entry of CHECK_CATALOGUE) {
      for (const field of REQUIRED_TEXT_FIELDS) {
        expect(entry[field].trim().length).toBeGreaterThan(0);
      }
    }
  });

  it("limitations, when present, is non-empty", () => {
    for (const entry of CHECK_CATALOGUE) {
      if (entry.limitations !== undefined) {
        expect(entry.limitations.trim().length).toBeGreaterThan(0);
      }
    }
  });

  it("no currently implemented check reaches Critical severity", () => {
    // Matches the milestone document's own §H invariant: the highest
    // severity any implemented check produces today is High.
    for (const entry of CHECK_CATALOGUE) {
      expect(entry.severity).not.toBe("critical");
    }
  });
});

describe("CHECK_CATALOGUE: category derivation", () => {
  it("every entry's category is derived by the shared deriveCategory utility, and is never the 'Other' fallback", () => {
    for (const entry of CHECK_CATALOGUE) {
      expect(deriveCategory(entry.checkId)).not.toBe("Other");
    }
  });
});

describe("CHECK_CATALOGUE: scope", () => {
  it("contains no AKS/EKS-specific or otherwise unimplemented check", () => {
    const disallowedIdPrefixes = ["AKS-", "EKS-", "AZURE-", "AWS-"];
    for (const entry of CHECK_CATALOGUE) {
      for (const prefix of disallowedIdPrefixes) {
        expect(entry.checkId.startsWith(prefix)).toBe(false);
      }
    }
  });

  it("findCatalogueEntry returns undefined for an unknown or not-yet-implemented check ID", () => {
    expect(findCatalogueEntry("AKS-RES-001")).toBeUndefined();
    expect(findCatalogueEntry("K8S-RES-999")).toBeUndefined();
    expect(findCatalogueEntry("")).toBeUndefined();
  });

  it("findCatalogueEntry returns the matching entry for every real check ID", () => {
    for (const id of EXPECTED_CHECK_IDS) {
      expect(findCatalogueEntry(id)?.checkId).toBe(id);
    }
  });
});

describe("CHECK_CATALOGUE: rendering safety", () => {
  it("the catalogue island component contains no dangerouslySetInnerHTML or Markdown/HTML-parsing usage", () => {
    const path = fileURLToPath(new URL("../../../src/features/check-catalogue/CheckCatalogue.tsx", import.meta.url));
    const source = readFileSync(path, "utf-8");
    expect(source).not.toContain("dangerouslySetInnerHTML");
    expect(source.toLowerCase()).not.toMatch(/marked|markdown-it|react-markdown/);
  });

  it("the check-detail page contains no dangerouslySetInnerHTML, set:html, or Markdown-parsing usage", () => {
    const path = fileURLToPath(new URL("../../../src/pages/checks/[id].astro", import.meta.url));
    const source = readFileSync(path, "utf-8");
    expect(source).not.toContain("dangerouslySetInnerHTML");
    expect(source).not.toContain("set:html");
    expect(source.toLowerCase()).not.toMatch(/marked|markdown-it|react-markdown/);
  });
});
