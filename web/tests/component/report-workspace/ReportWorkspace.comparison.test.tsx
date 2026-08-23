// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { compareKubernetesReports } from "../../../src/features/comparison/compare";
import { ReportWorkspace } from "../../../src/features/report-workspace";
import {
  buildNormalizedKubernetesFinding,
  buildNormalizedKubernetesReport,
} from "../../helpers/normalizedKubernetesFixtures";

const persistentFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-001",
  severity: "medium",
  namespace: "payments-demo",
  resourceName: "checkout-api",
  evidence: "persistent evidence (newer occurrence)",
});
const persistentFindingOlder = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-001",
  severity: "medium",
  namespace: "payments-demo",
  resourceName: "checkout-api",
  evidence: "persistent evidence (older occurrence)",
});
const resolvedFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-004",
  severity: "high",
  namespace: "payments-demo",
  resourceName: "checkout-api",
});
const newFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-REL-001",
  severity: "high",
  namespace: "commerce-demo",
  resourceName: "cache-pod",
});

function renderComparisonWorkspace() {
  const older = buildNormalizedKubernetesReport(
    { generatedAt: "2026-06-01T09:00:00Z" },
    [persistentFindingOlder, resolvedFinding],
  );
  const newer = buildNormalizedKubernetesReport(
    { generatedAt: "2026-06-15T09:00:00Z" },
    [persistentFinding, newFinding],
  );
  const comparison = compareKubernetesReports(older, newer);
  render(<ReportWorkspace mode="comparison" comparison={comparison} />);
  return comparison;
}

describe("ReportWorkspace: comparison mode", () => {
  it("displays every status and correct comparison-status totals", () => {
    renderComparisonWorkspace();

    expect(screen.getByText("New 1")).toBeInTheDocument();
    expect(screen.getByText("Persistent 1")).toBeInTheDocument();
    expect(screen.getByText("Resolved 1")).toBeInTheDocument();
    expect(screen.getByText("Showing 3 of 3 findings.")).toBeInTheDocument();
  });

  it("shows the newer report's severity totals, not merged with resolved findings", () => {
    renderComparisonWorkspace();
    // newer.summary: persistentFinding (medium) + newFinding (high) only --
    // resolvedFinding (high) must not be counted, so High stays 1.
    expect(screen.getByText("Critical 0")).toBeInTheDocument();
    expect(screen.getByText("High 1")).toBeInTheDocument();
    expect(screen.getByText("Medium 1")).toBeInTheDocument();
    expect(screen.getByText("Low 0")).toBeInTheDocument();
    expect(screen.getByText("Total 2")).toBeInTheDocument();
  });

  it("shows earlier/later scan timestamps instead of a single 'Report generated' line", () => {
    renderComparisonWorkspace();
    expect(screen.getByText("Earlier scan")).toBeInTheDocument();
    expect(screen.getByText("Later scan")).toBeInTheDocument();
    expect(screen.queryByText("Report generated")).not.toBeInTheDocument();

    const earlierTime = screen.getByText("2026-06-01T09:00:00Z");
    expect(earlierTime.tagName.toLowerCase()).toBe("time");
    const laterTime = screen.getByText("2026-06-15T09:00:00Z");
    expect(laterTime.tagName.toLowerCase()).toBe("time");
  });

  it("renders each status as visible text on its finding row, not colour alone", () => {
    renderComparisonWorkspace();
    const statusBadges = document.querySelectorAll(".finding-row__status");
    expect(statusBadges).toHaveLength(3);
    const statusTexts = Array.from(statusBadges).map((el) => el.textContent);
    expect(statusTexts.sort()).toEqual(["New", "Persistent", "Resolved"]);
  });

  it("persistent findings display the newer occurrence's evidence, not the older one's", () => {
    renderComparisonWorkspace();
    expect(screen.getByText("persistent evidence (newer occurrence)")).toBeInTheDocument();
    expect(screen.queryByText("persistent evidence (older occurrence)")).not.toBeInTheDocument();
  });

  it("filters by every comparison status", async () => {
    const user = userEvent.setup();
    renderComparisonWorkspace();

    const statusFilter = screen.getByLabelText("Comparison status");

    await user.selectOptions(statusFilter, "new");
    expect(screen.getByText("Showing 1 of 3 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-REL-001")).toBeInTheDocument();

    await user.selectOptions(statusFilter, "persistent");
    expect(screen.getByText("Showing 1 of 3 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-RES-001")).toBeInTheDocument();

    await user.selectOptions(statusFilter, "resolved");
    expect(screen.getByText("Showing 1 of 3 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-RES-004")).toBeInTheDocument();

    await user.selectOptions(statusFilter, "all");
    expect(screen.getByText("Showing 3 of 3 findings.")).toBeInTheDocument();
  });

  it("sorts by comparison status in new -> persistent -> resolved order", async () => {
    const user = userEvent.setup();
    renderComparisonWorkspace();

    await user.selectOptions(screen.getByLabelText("Sort by"), "comparisonStatus");
    const checkIds = screen.getAllByText(/^K8S-/).map((el) => el.textContent);
    expect(checkIds).toEqual(["K8S-REL-001", "K8S-RES-001", "K8S-RES-004"]);
  });

  it("combines comparison-status filtering with search and severity filters", async () => {
    const user = userEvent.setup();
    renderComparisonWorkspace();

    await user.selectOptions(screen.getByLabelText("Comparison status"), "new");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    expect(screen.getByText("Showing 1 of 3 findings.")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Severity"), "medium");
    expect(screen.getByText("Showing 0 of 3 findings.")).toBeInTheDocument();
  });

  it("clearing filters resets the comparison-status filter too", async () => {
    const user = userEvent.setup();
    renderComparisonWorkspace();

    await user.selectOptions(screen.getByLabelText("Comparison status"), "resolved");
    expect(screen.getByText("Showing 1 of 3 findings.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(screen.getByLabelText("Comparison status")).toHaveValue("all");
    expect(screen.getByText("Showing 3 of 3 findings.")).toBeInTheDocument();
  });

  it("existing search and other filters continue working in comparison mode", async () => {
    const user = userEvent.setup();
    renderComparisonWorkspace();

    await user.type(screen.getByLabelText("Search findings"), "cache-pod");
    expect(screen.getByText("Showing 1 of 3 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-REL-001")).toBeInTheDocument();
  });
});

describe("ReportWorkspace: single mode does not render comparison controls", () => {
  it("renders no comparison-status filter, sort option, badges, or totals", () => {
    const report = buildNormalizedKubernetesReport({}, [persistentFinding]);
    render(<ReportWorkspace mode="single" report={report} />);

    expect(screen.queryByLabelText("Comparison status")).not.toBeInTheDocument();
    expect(screen.queryByText("Comparison status")).not.toBeInTheDocument();
    expect(document.querySelector(".finding-row__status")).toBeNull();
    expect(screen.queryByLabelText("Comparison status totals")).not.toBeInTheDocument();
    expect(screen.queryByText(/^New \d/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Persistent \d/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Resolved \d/)).not.toBeInTheDocument();
  });
});
