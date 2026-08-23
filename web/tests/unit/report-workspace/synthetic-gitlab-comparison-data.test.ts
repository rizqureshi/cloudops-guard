import { describe, expect, it } from "vitest";

import { compareGitLabReports } from "../../../src/features/comparison/compare";
import { parseGitLabReport } from "../../../src/features/report-import";
import unprotectedBranchReportRaw from "../../../src/data/synthetic-gitlab-report-unprotected-branch.json";
import protectedBranchReportRaw from "../../../src/data/synthetic-gitlab-report-protected-branch.json";

describe("synthetic GitLab comparison dataset", () => {
  it("produces at least one New, one Persistent, and one Resolved result when compared", () => {
    const earlier = parseGitLabReport(unprotectedBranchReportRaw);
    const later = parseGitLabReport(protectedBranchReportRaw);
    const comparison = compareGitLabReports(earlier, later);

    expect(comparison.statusTotals.new).toBeGreaterThanOrEqual(1);
    expect(comparison.statusTotals.persistent).toBeGreaterThanOrEqual(1);
    expect(comparison.statusTotals.resolved).toBeGreaterThanOrEqual(1);
    // Exact totals, recorded so a future accidental data edit is caught.
    expect(comparison.statusTotals).toEqual({ new: 6, persistent: 1, resolved: 6 });
  });

  it("GL-BR-001 resolves and GL-BR-002/GL-BR-003 appear as new, consistent with the branch-protection narrative", () => {
    const earlier = parseGitLabReport(unprotectedBranchReportRaw);
    const later = parseGitLabReport(protectedBranchReportRaw);
    const comparison = compareGitLabReports(earlier, later);

    const branchResults = comparison.results.filter((r) => r.displayFinding.checkId.startsWith("GL-BR-"));
    const statusByCheckId = new Map(branchResults.map((r) => [r.displayFinding.checkId, r.status]));
    expect(statusByCheckId.get("GL-BR-001")).toBe("resolved");
    expect(statusByCheckId.get("GL-BR-002")).toBe("new");
    expect(statusByCheckId.get("GL-BR-003")).toBe("new");
  });

  it("GL-MR-001 is persistent across both states", () => {
    const earlier = parseGitLabReport(unprotectedBranchReportRaw);
    const later = parseGitLabReport(protectedBranchReportRaw);
    const comparison = compareGitLabReports(earlier, later);

    const mrResult = comparison.results.find((r) => r.displayFinding.checkId === "GL-MR-001");
    expect(mrResult?.status).toBe("persistent");
  });

  it("demonstrates the documented GL-CI-001 image-reference-change limitation: resolved plus new, not persistent", () => {
    const earlier = parseGitLabReport(unprotectedBranchReportRaw);
    const later = parseGitLabReport(protectedBranchReportRaw);
    const comparison = compareGitLabReports(earlier, later);

    const ciJobResults = comparison.results.filter(
      (r) => r.displayFinding.checkId === "GL-CI-001" && r.displayFinding.resourceKind === "CIJob",
    );
    const statuses = ciJobResults.map((r) => r.status).sort();
    expect(statuses).toEqual(["new", "resolved"]);
    expect(ciJobResults.some((r) => r.status === "persistent")).toBe(false);
  });
});
