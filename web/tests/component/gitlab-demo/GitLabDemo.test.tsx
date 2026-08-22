// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { GitLabDemo, type GitLabScenario } from "../../../src/features/gitlab-demo";
import {
  buildNormalizedGitLabFinding,
  buildNormalizedGitLabReport,
} from "../../helpers/normalizedGitLabFixtures";

const ALL_ELEVEN_GITLAB_CHECK_IDS = [
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
] as const;

const scenarioAFindings = [
  buildNormalizedGitLabFinding({ checkId: "GL-BR-001", severity: "high", resourceName: "main" }),
  buildNormalizedGitLabFinding({ checkId: "GL-MR-001", severity: "medium", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({ checkId: "GL-SEC-001", severity: "high", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({ checkId: "GL-SEC-002", severity: "high", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({ checkId: "GL-COST-001", severity: "low", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({ checkId: "GL-REL-001", severity: "medium", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({
    checkId: "GL-CI-001",
    severity: "high",
    resourceKind: "CIJob",
    resourceName: "registry.example.com/demo/build:latest",
    jobName: "build",
  }),
];

const scenarioBFindings = [
  buildNormalizedGitLabFinding({ checkId: "GL-BR-002", severity: "high", resourceName: "main" }),
  buildNormalizedGitLabFinding({ checkId: "GL-BR-003", severity: "medium", resourceName: "main" }),
  buildNormalizedGitLabFinding({ checkId: "GL-SEC-003", severity: "high", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({ checkId: "GL-COST-002", severity: "low", resourceKind: "Project" }),
  buildNormalizedGitLabFinding({
    checkId: "GL-CI-001",
    severity: "high",
    resourceKind: "CIService",
    resourceName: "dynamic image reference",
    jobName: "test",
  }),
];

const scenarios: readonly [GitLabScenario, GitLabScenario] = [
  {
    id: "unprotected-default-branch",
    label: "Scenario A — default branch unprotected",
    report: buildNormalizedGitLabReport(
      { generatedAt: "2026-07-01T08:00:00Z" },
      scenarioAFindings,
    ),
  },
  {
    id: "protected-with-permissive-rules",
    label: "Scenario B — default branch protected, but with permissive rules",
    report: buildNormalizedGitLabReport(
      { generatedAt: "2026-07-15T08:00:00Z" },
      scenarioBFindings,
    ),
  },
];

function renderDemo() {
  return render(<GitLabDemo scenarios={scenarios} />);
}

describe("GitLabDemo scenario selector", () => {
  it("has an accessible label on the scenario selector", () => {
    renderDemo();
    expect(screen.getByLabelText("Synthetic scan state")).toBeInTheDocument();
  });

  it("renders exactly one report workspace at a time", () => {
    renderDemo();
    expect(screen.getAllByText(/^Showing \d+ of \d+ findings\.$/)).toHaveLength(1);
  });

  it("shows both scenario labels as selectable options", () => {
    renderDemo();
    const select = screen.getByLabelText("Synthetic scan state") as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map((option) => option.textContent);
    expect(optionLabels).toEqual([
      "Scenario A — default branch unprotected",
      "Scenario B — default branch protected, but with permissive rules",
    ]);
  });

  it("switching scenarios displays the newly selected report and its correct summary", async () => {
    const user = userEvent.setup();
    renderDemo();

    expect(screen.getByText("Showing 7 of 7 findings.")).toBeInTheDocument();
    expect(screen.getByText("High 4")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText("Synthetic scan state"),
      "protected-with-permissive-rules",
    );

    expect(screen.getByText("Showing 5 of 5 findings.")).toBeInTheDocument();
    expect(screen.getByText("High 3")).toBeInTheDocument();
    expect(screen.queryByText("GL-BR-001")).not.toBeInTheDocument();
    expect(screen.getByText("GL-BR-002")).toBeInTheDocument();
  });

  it("resets search, filters, and sorting when the scenario changes", async () => {
    const user = userEvent.setup();
    renderDemo();

    await user.type(screen.getByLabelText("Search findings"), "GL-BR-001");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    expect(screen.getByText("Showing 1 of 7 findings.")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText("Synthetic scan state"),
      "protected-with-permissive-rules",
    );

    // A fresh ReportWorkspace mount always starts from its own default
    // filter state, so the previous scenario's search/severity selections
    // must not carry over and silently hide findings in the new scenario.
    expect(screen.getByLabelText("Search findings")).toHaveValue("");
    expect(screen.getByLabelText("Severity")).toHaveValue("all");
    expect(screen.getByText("Showing 5 of 5 findings.")).toBeInTheDocument();
  });

  it("resets expanded finding details when the scenario changes", async () => {
    const user = userEvent.setup();
    renderDemo();

    const summary = screen.getByText("GL-BR-001").closest("summary")!;
    const details = summary.closest("details")!;
    await user.click(summary);
    expect(details).toHaveAttribute("open");

    await user.selectOptions(
      screen.getByLabelText("Synthetic scan state"),
      "protected-with-permissive-rules",
    );

    const newSummary = screen.getByText("GL-BR-002").closest("summary")!;
    const newDetails = newSummary.closest("details")!;
    expect(newDetails).not.toHaveAttribute("open");
  });

  it("makes all eleven implemented GitLab check IDs explorable across the two scenarios", () => {
    const presentCheckIds = new Set([
      ...scenarioAFindings.map((f) => f.checkId),
      ...scenarioBFindings.map((f) => f.checkId),
    ]);
    for (const checkId of ALL_ELEVEN_GITLAB_CHECK_IDS) {
      expect(presentCheckIds.has(checkId)).toBe(true);
    }
  });

  it("switching scenarios renders no comparison status or comparison controls in the workspace itself", async () => {
    const user = userEvent.setup();
    renderDemo();

    await user.selectOptions(
      screen.getByLabelText("Synthetic scan state"),
      "protected-with-permissive-rules",
    );

    // Scoped to the report-workspace results list, not the whole page --
    // the selector's own explanatory note (asserted separately below)
    // legitimately uses words like "persistent" to describe what the
    // selector does *not* do.
    const resultsList = document.querySelector(".report-workspace__results")!;
    const withinResults = within(resultsList as HTMLElement);
    expect(withinResults.queryByText(/^new$/i)).not.toBeInTheDocument();
    expect(withinResults.queryByText(/persistent/i)).not.toBeInTheDocument();
    expect(withinResults.queryByText(/resolved/i)).not.toBeInTheDocument();
    expect(withinResults.queryByText(/comparison/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /compare/i })).not.toBeInTheDocument();
  });

  it("explains that the selector switches examples and does not compare them", () => {
    renderDemo();
    expect(screen.getByText(/does not compare the two states against each other/i)).toBeInTheDocument();
  });
});
