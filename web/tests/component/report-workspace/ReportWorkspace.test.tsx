// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportWorkspace } from "../../../src/features/report-workspace";
import {
  buildNormalizedKubernetesFinding,
  buildNormalizedKubernetesReport,
} from "../../helpers/normalizedKubernetesFixtures";

const cpuRequestFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-001",
  title: "Container has no CPU request",
  severity: "medium",
  namespace: "payments-demo",
  resourceKind: "Deployment",
  resourceName: "checkout-api",
  containerName: "api",
  evidence: "Container 'api' (image: example.com/checkout-api:2.4.0) does not set resources.requests.cpu",
  impact: "Without a CPU request the scheduler cannot reserve capacity for this container.",
  recommendation: "Set resources.requests.cpu based on observed usage.",
});

const memoryLimitFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-004",
  title: "Container has no memory limit",
  severity: "high",
  namespace: "payments-demo",
  resourceKind: "Deployment",
  resourceName: "checkout-api",
  containerName: "api",
  evidence: "Container 'api' (image: example.com/checkout-api:2.4.0) does not set resources.limits.memory",
  impact: "Without a memory limit this container can consume unbounded memory.",
  recommendation: "Set resources.limits.memory to cap worst-case memory usage.",
});

const mutableImageFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-IMG-001",
  title: "Container image uses a mutable tag",
  severity: "high",
  namespace: "commerce-demo",
  resourceKind: "Pod",
  resourceName: "checkout-batch",
  containerName: "worker",
  evidence: "Container 'worker' uses image 'example.com/checkout-batch:latest' (tag: latest)",
  impact: "A mutable tag undermines reproducibility and rollback safety.",
  recommendation: "Pin the image to a specific version tag at minimum.",
});

const restartFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-REL-001",
  title: "Container has an excessive restart count",
  severity: "high",
  namespace: "commerce-demo",
  resourceKind: "Pod",
  resourceName: "cache-pod",
  containerName: "redis",
  evidence: "Container 'redis' in pod 'cache-pod' has restarted 9 time(s)",
  impact: "Frequent restarts typically indicate crash looping.",
  recommendation: "Inspect this container's events and state for the root cause.",
});

const allFindings = [cpuRequestFinding, memoryLimitFinding, mutableImageFinding, restartFinding];

function renderWorkspace() {
  const report = buildNormalizedKubernetesReport(
    {
      generatedAt: "2026-06-01T09:00:00Z",
      target: { clusterContext: "cloudops-guard-demo-cluster", namespaceFilter: null },
    },
    allFindings,
  );
  return render(<ReportWorkspace mode="single" source="synthetic" report={report} />);
}

describe("ReportWorkspace", () => {
  it("renders the synthetic indicator, target identity, timestamp, and full summary", () => {
    renderWorkspace();

    expect(screen.getByText("Synthetic demonstration")).toBeInTheDocument();
    expect(screen.getByText("cloudops-guard-demo-cluster")).toBeInTheDocument();
    expect(screen.getByText("All namespaces")).toBeInTheDocument();

    const time = screen.getByText("2026-06-01T09:00:00Z");
    expect(time.tagName.toLowerCase()).toBe("time");
    expect(time).toHaveAttribute("datetime", "2026-06-01T09:00:00Z");

    expect(screen.getByText("Critical 0")).toBeInTheDocument();
    expect(screen.getByText("High 3")).toBeInTheDocument();
    expect(screen.getByText("Medium 1")).toBeInTheDocument();
    expect(screen.getByText("Low 0")).toBeInTheDocument();
    expect(screen.getByText("Total 4")).toBeInTheDocument();
  });

  it('initially says "Showing 4 of 4 findings."', () => {
    renderWorkspace();
    expect(screen.getByText("Showing 4 of 4 findings.")).toBeInTheDocument();
  });

  it("search is case-insensitive and updates the displayed findings", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    const search = screen.getByLabelText("Search findings");
    await user.type(search, "CHECKOUT-BATCH");

    expect(screen.getByText("Showing 1 of 4 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-IMG-001")).toBeInTheDocument();
    expect(screen.queryByText("K8S-RES-001")).not.toBeInTheDocument();
  });

  it("filters by severity", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Severity"), "medium");

    expect(screen.getByText("Showing 1 of 4 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-RES-001")).toBeInTheDocument();
    expect(screen.queryByText("K8S-RES-004")).not.toBeInTheDocument();
  });

  it("filters by category", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Category"), "Reliability");

    expect(screen.getByText("Showing 1 of 4 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-REL-001")).toBeInTheDocument();
  });

  it("filters by resource kind", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Resource kind"), "Pod");

    expect(screen.getByText("Showing 2 of 4 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-IMG-001")).toBeInTheDocument();
    expect(screen.getByText("K8S-REL-001")).toBeInTheDocument();
    expect(screen.queryByText("K8S-RES-001")).not.toBeInTheDocument();
  });

  it("combines search and filters", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Resource kind"), "Pod");
    await user.type(screen.getByLabelText("Search findings"), "redis");

    expect(screen.getByText("Showing 1 of 4 findings.")).toBeInTheDocument();
    expect(screen.getByText("K8S-REL-001")).toBeInTheDocument();
  });

  it("sorts by severity (critical/high first), then check ID, then resource", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Sort by"), "severity");
    const checkIds = screen.getAllByText(/^K8S-/).map((el) => el.textContent);
    expect(checkIds).toEqual(["K8S-IMG-001", "K8S-REL-001", "K8S-RES-004", "K8S-RES-001"]);
  });

  it("sorts by check ID", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Sort by"), "checkId");
    const checkIds = screen.getAllByText(/^K8S-/).map((el) => el.textContent);
    expect(checkIds).toEqual(["K8S-IMG-001", "K8S-REL-001", "K8S-RES-001", "K8S-RES-004"]);
  });

  it("sorts by resource", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.selectOptions(screen.getByLabelText("Sort by"), "resource");
    const checkIds = screen.getAllByText(/^K8S-/).map((el) => el.textContent);
    // Resource identity is namespace-first: commerce-demo findings (IMG-001,
    // REL-001) sort before payments-demo findings (RES-001, RES-004); within
    // commerce-demo, "cache-pod" sorts before "checkout-batch".
    expect(checkIds).toEqual(["K8S-REL-001", "K8S-IMG-001", "K8S-RES-004", "K8S-RES-001"]);
  });

  it("clearing controls restores all findings", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.type(screen.getByLabelText("Search findings"), "checkout-batch");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    expect(screen.getByText("Showing 1 of 4 findings.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(screen.getByText("Showing 4 of 4 findings.")).toBeInTheDocument();
    expect(screen.getByLabelText("Search findings")).toHaveValue("");
    expect(screen.getByLabelText("Severity")).toHaveValue("all");
  });

  it("shows an empty-results message when nothing matches", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.type(screen.getByLabelText("Search findings"), "no-such-finding-xyz");

    expect(screen.getByText("Showing 0 of 4 findings.")).toBeInTheDocument();
    expect(
      screen.getByText(/No findings match your current search and filters/),
    ).toBeInTheDocument();
  });

  it("opens finding details via normal user interaction and renders evidence/impact/recommendation as text", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    const summary = screen.getByText("K8S-RES-001").closest("summary");
    expect(summary).not.toBeNull();
    const details = summary!.closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");

    await user.click(summary!);

    expect(details).toHaveAttribute("open");
    const withinDetails = within(details!);
    expect(withinDetails.getByText(cpuRequestFinding.evidence)).toBeInTheDocument();
    expect(withinDetails.getByText(cpuRequestFinding.impact)).toBeInTheDocument();
    expect(withinDetails.getByText(cpuRequestFinding.recommendation)).toBeInTheDocument();
    expect(withinDetails.getByText("No")).toBeInTheDocument(); // auto-remediable: false
  });

  it("keeps full severity totals unchanged while the filtered-result count changes", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    expect(screen.getByText("High 3")).toBeInTheDocument();
    expect(screen.getByText("Showing 4 of 4 findings.")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Severity"), "medium");

    // The full-report High total must still read 3, even though only the
    // Medium finding is now shown.
    expect(screen.getByText("High 3")).toBeInTheDocument();
    expect(screen.getByText("Medium 1")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 4 findings.")).toBeInTheDocument();
  });

  it("renders no comparison, executive-summary, upload, or explorer control", () => {
    const { container } = renderWorkspace();

    expect(screen.queryByText(/comparison/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/executive summary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^new$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/persistent/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/resolved/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/choose file/i)).not.toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });
});

describe("ReportWorkspace: React key regression (identical findings in different namespaces)", () => {
  // Reproduces the bug independently reported against the previous
  // findingKey() implementation: same check ID, resource kind, resource
  // name, and container name, but different namespaces. A key derived only
  // from those first four fields would collide.
  const alphaFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-001",
    severity: "medium",
    namespace: "alpha",
    resourceKind: "Deployment",
    resourceName: "api",
    containerName: "api",
  });
  const betaFinding = buildNormalizedKubernetesFinding({
    checkId: "K8S-RES-001",
    severity: "medium",
    namespace: "beta",
    resourceKind: "Deployment",
    resourceName: "api",
    containerName: "api",
  });

  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  function renderDuplicateCandidateWorkspace() {
    const report = buildNormalizedKubernetesReport({}, [alphaFinding, betaFinding]);
    return render(<ReportWorkspace mode="single" source="synthetic" report={report} />);
  }

  function hasDuplicateKeyWarning(spy: ReturnType<typeof vi.spyOn>): boolean {
    return spy.mock.calls.some((call: unknown[]) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
  }

  it("renders both findings, with their namespaces distinguishable", () => {
    renderDuplicateCandidateWorkspace();

    expect(screen.getByText(/alpha/)).toBeInTheDocument();
    expect(screen.getByText(/beta/)).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 2 findings.")).toBeInTheDocument();
  });

  it("emits no React duplicate-key warning to console.error", () => {
    renderDuplicateCandidateWorkspace();

    expect(hasDuplicateKeyWarning(consoleErrorSpy)).toBe(false);
  });

  it("preserves both occurrences after sorting by resource (where they are otherwise adjacent)", async () => {
    const user = userEvent.setup();
    renderDuplicateCandidateWorkspace();

    await user.selectOptions(screen.getByLabelText("Sort by"), "resource");

    expect(screen.getByText(/alpha/)).toBeInTheDocument();
    expect(screen.getByText(/beta/)).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 2 findings.")).toBeInTheDocument();
    expect(hasDuplicateKeyWarning(consoleErrorSpy)).toBe(false);
  });

  it("preserves both occurrences after filtering to the shared severity", async () => {
    const user = userEvent.setup();
    renderDuplicateCandidateWorkspace();

    await user.selectOptions(screen.getByLabelText("Severity"), "medium");

    expect(screen.getByText(/alpha/)).toBeInTheDocument();
    expect(screen.getByText(/beta/)).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 2 findings.")).toBeInTheDocument();
  });
});

describe("ReportWorkspace: disclosure indicator", () => {
  it("renders a decorative disclosure indicator alongside each finding, and the details element opens normally", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    const summary = screen.getByText("K8S-RES-001").closest("summary");
    expect(summary).not.toBeNull();

    const indicator = summary!.querySelector(".finding-row__disclosure-icon");
    expect(indicator).not.toBeNull();
    expect(indicator).toHaveAttribute("aria-hidden", "true");

    const details = summary!.closest("details");
    expect(details).not.toHaveAttribute("open");

    await user.click(summary!);

    expect(details).toHaveAttribute("open");
  });
});
