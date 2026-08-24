import { describe, expect, it } from "vitest";

import { CHECK_CATALOGUE } from "../../../src/features/check-catalogue/catalogue";
import {
  DEFAULT_CATALOGUE_FILTER_STATE,
  distinctCatalogueCategories,
  filterCatalogueEntries,
  matchesCatalogueFilters,
  matchesCatalogueSearch,
} from "../../../src/features/check-catalogue/filtering";

const k8sImg001 = CHECK_CATALOGUE.find((entry) => entry.checkId === "K8S-IMG-001")!;
const glCi001 = CHECK_CATALOGUE.find((entry) => entry.checkId === "GL-CI-001")!;

describe("matchesCatalogueSearch", () => {
  it("matches on check ID, case-insensitively", () => {
    expect(matchesCatalogueSearch(k8sImg001, "k8s-img-001")).toBe(true);
  });

  it("matches on title, case-insensitively", () => {
    expect(matchesCatalogueSearch(k8sImg001, "MUTABLE tag")).toBe(true);
  });

  it("does not match on fields outside check ID and title, such as impact text", () => {
    expect(matchesCatalogueSearch(k8sImg001, "reproducibility")).toBe(false);
  });

  it("an empty or whitespace-only query matches everything", () => {
    expect(matchesCatalogueSearch(k8sImg001, "")).toBe(true);
    expect(matchesCatalogueSearch(k8sImg001, "   ")).toBe(true);
  });

  it("a non-matching query matches nothing", () => {
    expect(matchesCatalogueSearch(k8sImg001, "this string appears in no catalogue entry")).toBe(false);
  });
});

describe("matchesCatalogueFilters", () => {
  it("filters by platform", () => {
    expect(matchesCatalogueFilters(k8sImg001, { ...DEFAULT_CATALOGUE_FILTER_STATE, platform: "kubernetes" })).toBe(
      true,
    );
    expect(matchesCatalogueFilters(k8sImg001, { ...DEFAULT_CATALOGUE_FILTER_STATE, platform: "gitlab" })).toBe(
      false,
    );
  });

  it("filters by severity", () => {
    expect(matchesCatalogueFilters(k8sImg001, { ...DEFAULT_CATALOGUE_FILTER_STATE, severity: "high" })).toBe(true);
    expect(matchesCatalogueFilters(k8sImg001, { ...DEFAULT_CATALOGUE_FILTER_STATE, severity: "low" })).toBe(false);
  });

  it("filters by category", () => {
    expect(
      matchesCatalogueFilters(k8sImg001, { ...DEFAULT_CATALOGUE_FILTER_STATE, category: "Image security" }),
    ).toBe(true);
    expect(
      matchesCatalogueFilters(k8sImg001, { ...DEFAULT_CATALOGUE_FILTER_STATE, category: "Branch protection" }),
    ).toBe(false);
  });

  it("combines search and filters: all must match", () => {
    const filters = { search: "mutable", platform: "kubernetes" as const, category: "all" as const, severity: "high" as const };
    expect(matchesCatalogueFilters(k8sImg001, filters)).toBe(true);
    expect(matchesCatalogueFilters(glCi001, filters)).toBe(false); // GitLab, not Kubernetes
  });
});

describe("filterCatalogueEntries", () => {
  it("returns every entry when filters are default", () => {
    expect(filterCatalogueEntries(CHECK_CATALOGUE, DEFAULT_CATALOGUE_FILTER_STATE)).toHaveLength(17);
  });

  it("returns an empty array when nothing matches", () => {
    expect(
      filterCatalogueEntries(CHECK_CATALOGUE, { ...DEFAULT_CATALOGUE_FILTER_STATE, search: "no entry contains this exact phrase" }),
    ).toEqual([]);
  });

  it("returns only entries for the selected platform", () => {
    const kubernetesOnly = filterCatalogueEntries(CHECK_CATALOGUE, {
      ...DEFAULT_CATALOGUE_FILTER_STATE,
      platform: "kubernetes",
    });
    expect(kubernetesOnly).toHaveLength(6);
    expect(kubernetesOnly.every((entry) => entry.platform === "kubernetes")).toBe(true);
  });

  it("returns only entries for the selected severity", () => {
    const lowOnly = filterCatalogueEntries(CHECK_CATALOGUE, { ...DEFAULT_CATALOGUE_FILTER_STATE, severity: "low" });
    expect(lowOnly.every((entry) => entry.severity === "low")).toBe(true);
    expect(lowOnly.length).toBeGreaterThan(0);
  });
});

describe("distinctCatalogueCategories", () => {
  it("returns a stable, deterministic, ordinally-sorted list of every category present", () => {
    const categories = distinctCatalogueCategories(CHECK_CATALOGUE);
    const ordinalSorted = [...categories].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    expect(categories).toEqual(ordinalSorted);
    expect(categories.length).toBeGreaterThan(0);
    expect(new Set(categories).has("Other")).toBe(false);
  });
});
