// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { compareGitLabReports, compareKubernetesReports } from "../../../src/features/comparison/compare";
import { ExecutiveSummary } from "../../../src/features/executive-summary/ExecutiveSummary";
import { buildComparisonExecutiveSummary, buildSingleReportExecutiveSummary } from "../../../src/features/executive-summary/summary";
import { buildNormalizedGitLabFinding, buildNormalizedGitLabReport } from "../../helpers/normalizedGitLabFixtures";
import {
  buildNormalizedKubernetesFinding,
  buildNormalizedKubernetesReport,
} from "../../helpers/normalizedKubernetesFixtures";

describe("ExecutiveSummary: single-report Kubernetes", () => {
  it("renders target identity, totals, categories, recommendations, and scope text", () => {
    const finding = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-001",
      severity: "medium",
      namespace: "payments-demo",
      resourceName: "checkout-api",
      recommendation: "Set resources.requests.cpu based on observed usage.",
    });
    const report = buildNormalizedKubernetesReport(
      { target: { clusterContext: "demo-cluster", namespaceFilter: null } },
      [finding],
    );
    const summary = buildSingleReportExecutiveSummary(report);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    expect(screen.getByText("demo-cluster")).toBeInTheDocument();
    expect(screen.getByText("All namespaces")).toBeInTheDocument();
    expect(screen.getByText("Total 1")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Severity totals" })).toBeInTheDocument();
    // "Resource management" appears in both the affected-categories entry
    // and the recommendation's category label.
    expect(screen.getAllByText("Resource management").length).toBeGreaterThan(0);
    expect(screen.getByText("Set resources.requests.cpu based on observed usage.")).toBeInTheDocument();
    expect(screen.getByText(/deterministic and template-driven/)).toBeInTheDocument();
    expect(screen.getByText(/No live Kubernetes cluster or GitLab instance/)).toBeInTheDocument();
  });
});

describe("ExecutiveSummary: single-report GitLab", () => {
  it("renders GitLab target identity as plain text, never a link", () => {
    const finding = buildNormalizedGitLabFinding({ checkId: "GL-BR-001" });
    const report = buildNormalizedGitLabReport(
      {
        target: {
          gitlabUrl: "https://gitlab.example.com",
          projectId: 9200,
          projectPath: "platform/inventory-service",
          defaultBranch: "main",
        },
      },
      [finding],
    );
    const summary = buildSingleReportExecutiveSummary(report);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(screen.getByText("GitLab")).toBeInTheDocument();
    const urlText = screen.getByText("https://gitlab.example.com");
    expect(urlText.closest("a")).toBeNull();
    expect(screen.getByText("9200")).toBeInTheDocument();
    expect(screen.getByText("platform/inventory-service")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
  });
});

describe("ExecutiveSummary: comparison mode", () => {
  it("renders earlier/later timestamps, status totals, and newer-only severity totals", () => {
    const persistentFinding = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-001",
      severity: "medium",
      resourceName: "checkout-api",
    });
    const resolvedFinding = buildNormalizedKubernetesFinding({
      checkId: "K8S-RES-004",
      severity: "high",
      resourceName: "checkout-api",
    });
    const newFinding = buildNormalizedKubernetesFinding({
      checkId: "K8S-REL-001",
      severity: "high",
      namespace: "commerce-demo",
      resourceName: "cache-pod",
    });

    const older = buildNormalizedKubernetesReport({ generatedAt: "2026-06-01T09:00:00Z" }, [
      persistentFinding,
      resolvedFinding,
    ]);
    const newer = buildNormalizedKubernetesReport({ generatedAt: "2026-06-15T09:00:00Z" }, [
      persistentFinding,
      newFinding,
    ]);
    const comparison = compareKubernetesReports(older, newer);
    const summary = buildComparisonExecutiveSummary(comparison);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(screen.getByText("2026-06-01T09:00:00Z")).toBeInTheDocument();
    expect(screen.getByText("2026-06-15T09:00:00Z")).toBeInTheDocument();
    expect(screen.getByText("New 1")).toBeInTheDocument();
    expect(screen.getByText("Persistent 1")).toBeInTheDocument();
    expect(screen.getByText("Resolved 1")).toBeInTheDocument();
    // Newer severity totals: medium(persistent) + high(new), resolved's
    // high must not be merged in.
    expect(screen.getByText("High 1")).toBeInTheDocument();
    expect(screen.getByText("Medium 1")).toBeInTheDocument();
    expect(screen.getByText("Total 2")).toBeInTheDocument();

    // Regression: a `<div>` with no explicit role has an implicit
    // "generic" role, on which `aria-label` is prohibited by the ARIA
    // spec and is ignored by assistive technology -- found by axe-core's
    // `aria-prohibited-attr` rule during Phase 3J's accessibility scan.
    // `role="group"` makes the label take effect, exactly like
    // `getByRole` here proves.
    expect(screen.getByRole("group", { name: "Severity totals" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Comparison status totals" })).toBeInTheDocument();
  });

  it("excludes resolved findings from affected categories and recommendations", () => {
    const persistentFinding = buildNormalizedGitLabFinding({
      checkId: "GL-MR-001",
      severity: "medium",
      recommendation: "Enable 'Pipelines must succeed' in the project's merge request settings.",
    });
    const resolvedFinding = buildNormalizedGitLabFinding({
      checkId: "GL-SEC-001",
      severity: "high",
      recommendation: "Disable project-based pipeline visibility.",
    });
    const older = buildNormalizedGitLabReport({ generatedAt: "2026-07-01T08:00:00Z" }, [
      persistentFinding,
      resolvedFinding,
    ]);
    const newer = buildNormalizedGitLabReport({ generatedAt: "2026-07-15T08:00:00Z" }, [persistentFinding]);
    const comparison = compareGitLabReports(older, newer);
    const summary = buildComparisonExecutiveSummary(comparison);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(
      screen.getByText("Enable 'Pipelines must succeed' in the project's merge request settings."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Disable project-based pipeline visibility.")).not.toBeInTheDocument();
  });
});

describe("ExecutiveSummary: honesty and safety", () => {
  it("renders an honest empty state for a zero-finding report, explicitly disclaiming health/safety/compliance", () => {
    const report = buildNormalizedKubernetesReport({}, []);
    const summary = buildSingleReportExecutiveSummary(report);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    // The honest empty-state copy explicitly *names* health/safety/
    // compliance/comprehensiveness only to disclaim them ("it is not a
    // claim that..."), so this asserts the full disclaiming sentence is
    // present, not merely the absence of those words (which the sentence
    // itself necessarily contains).
    expect(
      screen.getByText(
        "No findings are present in this synthetic scan state. This reflects only the checks that produced a finding here -- it is not a claim that the target is healthy, safe, compliant, or comprehensively audited.",
      ),
    ).toBeInTheDocument();
  });

  it("never renders an unqualified health, risk, or maturity score claim", () => {
    const report = buildNormalizedKubernetesReport({}, [buildNormalizedKubernetesFinding()]);
    const summary = buildSingleReportExecutiveSummary(report);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(screen.queryByText(/health score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/risk score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/maturity/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/comprehensively audited/i)).not.toBeInTheDocument();
  });

  it("renders report-derived strings as plain text, never injected markup", () => {
    const finding = buildNormalizedKubernetesFinding({
      recommendation: "<img src=x onerror=alert(1)>plain recommendation text</img>",
    });
    const report = buildNormalizedKubernetesReport({}, [finding]);
    const summary = buildSingleReportExecutiveSummary(report);
    const { container } = render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(container.querySelector("img")).toBeNull();
    expect(
      screen.getByText("<img src=x onerror=alert(1)>plain recommendation text</img>"),
    ).toBeInTheDocument();
  });

  it("scope and limitation copy covers every required point", () => {
    const report = buildNormalizedKubernetesReport({}, [buildNormalizedKubernetesFinding()]);
    const summary = buildSingleReportExecutiveSummary(report);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(screen.getByText(/not a complete per-check execution ledger/)).toBeInTheDocument();
    expect(screen.getByText(/synthetic scan state/)).toBeInTheDocument();
    expect(screen.getByText(/do not provide complete coverage/)).toBeInTheDocument();
    expect(screen.getByText(/No live Kubernetes cluster or GitLab instance was contacted/)).toBeInTheDocument();
    expect(screen.getByText(/deterministic and template-driven/)).toBeInTheDocument();
  });
});

describe("ExecutiveSummary: recommendation React key regression (checkId + recommendation collision)", () => {
  // Reproduces the bug independently reported against the previous
  // `key={item.checkId + item.recommendation}` implementation: checkId "A"
  // + recommendation "BC" and checkId "AB" + recommendation "C" both
  // concatenate to "ABC", with no delimiter to prevent the collision.
  const collidingFindingA = buildNormalizedKubernetesFinding({
    checkId: "A",
    resourceName: "resource-a",
    recommendation: "BC",
  });
  const collidingFindingB = buildNormalizedKubernetesFinding({
    checkId: "AB",
    resourceName: "resource-b",
    recommendation: "C",
  });

  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  function hasDuplicateKeyWarning(spy: ReturnType<typeof vi.spyOn>): boolean {
    return spy.mock.calls.some((call: unknown[]) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
  }

  it("renders both recommendations and emits no React duplicate-key warning", () => {
    const report = buildNormalizedKubernetesReport({}, [collidingFindingA, collidingFindingB]);
    const summary = buildSingleReportExecutiveSummary(report);
    render(<ExecutiveSummary source="synthetic" summary={summary} />);

    expect(screen.getByText("BC")).toBeInTheDocument();
    expect(screen.getByText("C")).toBeInTheDocument();
    expect(hasDuplicateKeyWarning(consoleErrorSpy)).toBe(false);
  });
});
