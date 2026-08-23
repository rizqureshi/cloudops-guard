// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ReportWorkspace } from "../../../src/features/report-workspace";
import {
  buildNormalizedGitLabFinding,
  buildNormalizedGitLabReport,
} from "../../helpers/normalizedGitLabFixtures";

const branchFinding = buildNormalizedGitLabFinding({
  checkId: "GL-BR-001",
  title: "Default branch is not protected",
  severity: "high",
  projectPath: "platform/inventory-service",
  resourceKind: "ProtectedBranch",
  resourceName: "main",
  jobName: null,
  evidence: "No exact, wildcard, or inherited protected-branch rule matched the default branch.",
  impact: "An unprotected default branch permits force-push and unreviewed direct pushes.",
  recommendation: "Create a protected-branch rule whose name matches the default branch.",
});

const mrFinding = buildNormalizedGitLabFinding({
  checkId: "GL-MR-001",
  title: "Successful pipelines are not required before merge",
  severity: "medium",
  projectPath: "platform/inventory-service",
  resourceKind: "Project",
  resourceName: "platform/inventory-service",
  jobName: null,
  evidence: "The 'Pipelines must succeed' setting is disabled.",
  impact: "Merge requests can be merged even when a pipeline fails.",
  recommendation: "Enable 'Pipelines must succeed' in the project's merge request settings.",
});

const secFinding = buildNormalizedGitLabFinding({
  checkId: "GL-SEC-002",
  title: "CI job tokens are permitted to push to the repository",
  severity: "high",
  projectPath: "platform/inventory-service",
  resourceKind: "Project",
  resourceName: "platform/inventory-service",
  jobName: null,
  evidence: "CI job token repository push is enabled.",
  impact: "A compromised or malicious job gains a repository-write escalation path.",
  recommendation: "Disable this permission unless specifically reviewed automation requires it.",
});

const costFinding = buildNormalizedGitLabFinding({
  checkId: "GL-COST-001",
  title: "Redundant pipelines are not automatically cancelled",
  severity: "low",
  projectPath: "platform/inventory-service",
  resourceKind: "Project",
  resourceName: "platform/inventory-service",
  jobName: null,
  evidence: "Automatic cancellation of redundant pending pipelines is disabled.",
  impact: "Superseded pending pipelines may continue consuming runner capacity.",
  recommendation: "Enable automatic cancellation of redundant, pending pipelines.",
});

const ciJobFinding = buildNormalizedGitLabFinding({
  checkId: "GL-CI-001",
  title: "CI job or service container image uses a mutable tag or no tag",
  severity: "high",
  projectPath: "platform/inventory-service",
  resourceKind: "CIJob",
  resourceName: "registry.example.com/inventory/build:latest",
  jobName: "build",
  evidence: "CI job 'build' uses image 'registry.example.com/inventory/build:latest' (tag: latest).",
  impact: "A mutable tag undermines reproducibility and rollback safety.",
  recommendation: "Pin the image to a specific version tag at minimum.",
});

const ciServiceFinding = buildNormalizedGitLabFinding({
  checkId: "GL-CI-001",
  title: "CI job or service container image uses a mutable tag or no tag",
  severity: "high",
  projectPath: "platform/inventory-service",
  resourceKind: "CIService",
  resourceName: "dynamic image reference",
  jobName: "test",
  evidence: "The CI image reference is dynamic and could not be statically verified.",
  impact: "A dynamic image reference cannot be evaluated from the CI configuration alone.",
  recommendation: "Replace or constrain the CI/CD variable expression.",
});

const allFindings = [branchFinding, mrFinding, secFinding, costFinding, ciJobFinding, ciServiceFinding];

function renderGitLabWorkspace() {
  const report = buildNormalizedGitLabReport(
    {
      generatedAt: "2026-07-15T08:00:00Z",
      target: {
        gitlabUrl: "https://gitlab.example.com",
        projectId: 9200,
        projectPath: "platform/inventory-service",
        defaultBranch: "main",
      },
    },
    allFindings,
  );
  return render(<ReportWorkspace mode="single" report={report} />);
}

describe("ReportWorkspace: GitLab platform", () => {
  it("renders the GitLab platform label and all five target identity fields", () => {
    renderGitLabWorkspace();

    expect(screen.getByText("GitLab")).toBeInTheDocument();
    expect(screen.getByText("https://gitlab.example.com")).toBeInTheDocument();
    expect(screen.getByText("9200")).toBeInTheDocument();
    expect(screen.getByText("platform/inventory-service")).toBeInTheDocument();
    // "main" appears both as the default-branch identity field and as a
    // resource name within finding rows -- assert at least one occurrence.
    expect(screen.getAllByText("main").length).toBeGreaterThan(0);

    const time = screen.getByText("2026-07-15T08:00:00Z");
    expect(time.tagName.toLowerCase()).toBe("time");
    expect(time).toHaveAttribute("datetime", "2026-07-15T08:00:00Z");
  });

  it("renders the GitLab URL as plain text, never as a link", () => {
    renderGitLabWorkspace();

    const urlText = screen.getByText("https://gitlab.example.com");
    expect(urlText.closest("a")).toBeNull();
  });

  it("renders the full GitLab severity summary and total", () => {
    renderGitLabWorkspace();

    expect(screen.getByText("Critical 0")).toBeInTheDocument();
    expect(screen.getByText("High 4")).toBeInTheDocument();
    expect(screen.getByText("Medium 1")).toBeInTheDocument();
    expect(screen.getByText("Low 1")).toBeInTheDocument();
    expect(screen.getByText("Total 6")).toBeInTheDocument();
  });

  it('initially says "Showing 6 of 6 findings."', () => {
    renderGitLabWorkspace();
    expect(screen.getByText("Showing 6 of 6 findings.")).toBeInTheDocument();
  });

  it("search matches check ID, title, project path, resource, job name, evidence, impact, and recommendation, case-insensitively", async () => {
    const user = userEvent.setup();

    renderGitLabWorkspace();
    await user.type(screen.getByLabelText("Search findings"), "GL-BR-001");
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.type(screen.getByLabelText("Search findings"), "mutable tag or no tag");
    expect(screen.getByText("Showing 2 of 6 findings.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.type(screen.getByLabelText("Search findings"), "PLATFORM/INVENTORY-SERVICE");
    expect(screen.getByText("Showing 6 of 6 findings.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.type(screen.getByLabelText("Search findings"), "build");
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.type(screen.getByLabelText("Search findings"), "escalation path");
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.type(screen.getByLabelText("Search findings"), "runner capacity");
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.type(screen.getByLabelText("Search findings"), "specific version tag");
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
  });

  it("filters GitLab findings by severity", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.selectOptions(screen.getByLabelText("Severity"), "low");

    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    expect(screen.getByText("GL-COST-001")).toBeInTheDocument();
  });

  it("filters GitLab findings by category", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.selectOptions(screen.getByLabelText("Category"), "Branch protection");

    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    expect(screen.getByText("GL-BR-001")).toBeInTheDocument();
  });

  it("filters GitLab findings by resource kind", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.selectOptions(screen.getByLabelText("Resource kind"), "Project");

    expect(screen.getByText("Showing 3 of 6 findings.")).toBeInTheDocument();
    expect(screen.getByText("GL-MR-001")).toBeInTheDocument();
    expect(screen.getByText("GL-SEC-002")).toBeInTheDocument();
    expect(screen.getByText("GL-COST-001")).toBeInTheDocument();
  });

  it("combines search and filters for GitLab findings", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.selectOptions(screen.getByLabelText("Resource kind"), "CIJob");
    await user.type(screen.getByLabelText("Search findings"), "build");

    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
    expect(screen.getByText("GL-CI-001")).toBeInTheDocument();
  });

  it("sorts GitLab findings deterministically by severity, check ID, and resource", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.selectOptions(screen.getByLabelText("Sort by"), "checkId");
    let checkIds = screen.getAllByText(/^GL-/).map((el) => el.textContent);
    expect(checkIds).toEqual([
      "GL-BR-001",
      "GL-CI-001",
      "GL-CI-001",
      "GL-COST-001",
      "GL-MR-001",
      "GL-SEC-002",
    ]);

    await user.selectOptions(screen.getByLabelText("Sort by"), "severity");
    checkIds = screen.getAllByText(/^GL-/).map((el) => el.textContent);
    // High-severity findings (BR-001, SEC-002, both CI-001s) sort before
    // medium (MR-001), which sorts before low (COST-001); within the "high"
    // group, ties break by check ID, then resource identity.
    expect(checkIds.slice(0, 4)).toEqual(["GL-BR-001", "GL-CI-001", "GL-CI-001", "GL-SEC-002"]);
    expect(checkIds[4]).toBe("GL-MR-001");
    expect(checkIds[5]).toBe("GL-COST-001");
  });

  it("clearing controls restores the complete GitLab report", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.type(screen.getByLabelText("Search findings"), "build");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(screen.getByText("Showing 6 of 6 findings.")).toBeInTheDocument();
    expect(screen.getByLabelText("Search findings")).toHaveValue("");
    expect(screen.getByLabelText("Severity")).toHaveValue("all");
  });

  it("shows an empty-results message when nothing matches a GitLab search", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    await user.type(screen.getByLabelText("Search findings"), "no-such-finding-xyz");

    expect(screen.getByText("Showing 0 of 6 findings.")).toBeInTheDocument();
    expect(
      screen.getByText(/No findings match your current search and filters/),
    ).toBeInTheDocument();
  });

  it("opens a GitLab finding's details via normal user interaction and renders project path, resource, and job name", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    // Two findings share the "GL-CI-001" check ID (the CIJob and CIService
    // examples); disambiguate by the job-specific resource text ("build"),
    // scoped to the summary line (the same substring also appears in the
    // finding's own evidence text further down, so an unscoped query would
    // match twice).
    const summaryEl = screen
      .getByText(/registry\.example\.com\/inventory\/build:latest/, { selector: ".finding-row__resource" })
      .closest("summary")!;
    const details = summaryEl.closest("details")!;
    expect(details).not.toHaveAttribute("open");

    await user.click(summaryEl);

    expect(details).toHaveAttribute("open");
    const withinDetails = within(details);
    expect(withinDetails.getByText(ciJobFinding.evidence)).toBeInTheDocument();
    expect(withinDetails.getByText(ciJobFinding.impact)).toBeInTheDocument();
    expect(withinDetails.getByText(ciJobFinding.recommendation)).toBeInTheDocument();
    expect(withinDetails.getByText("No")).toBeInTheDocument(); // auto-remediable: false
  });

  it("renders project path, resource kind/name, and job name in the finding row summary", () => {
    renderGitLabWorkspace();

    const resourceText = screen.getByText(/registry\.example\.com\/inventory\/build:latest/, {
      selector: ".finding-row__resource",
    });
    expect(resourceText.textContent).toContain("platform/inventory-service");
    expect(resourceText.textContent).toContain("CIJob");
    expect(resourceText.textContent).toContain("registry.example.com/inventory/build:latest");
    expect(resourceText.textContent).toContain("build");
  });

  it("keeps full GitLab severity totals unchanged while the filtered-result count changes", async () => {
    const user = userEvent.setup();
    renderGitLabWorkspace();

    expect(screen.getByText("High 4")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Severity"), "low");

    expect(screen.getByText("High 4")).toBeInTheDocument();
    expect(screen.getByText("Low 1")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 6 findings.")).toBeInTheDocument();
  });

  it("renders report-derived GitLab strings as plain text, never injected markup", () => {
    const markupLikeFinding = buildNormalizedGitLabFinding({
      checkId: "GL-BR-001",
      evidence: "<img src=x onerror=alert(1)>plain evidence text</img>",
    });
    const report = buildNormalizedGitLabReport({}, [markupLikeFinding]);
    const { container } = render(<ReportWorkspace mode="single" report={report} />);

    expect(container.querySelector("img")).toBeNull();
    expect(
      screen.getByText("<img src=x onerror=alert(1)>plain evidence text</img>"),
    ).toBeInTheDocument();
  });

  it("renders no comparison, executive-summary, upload, explorer, token, or credential control", () => {
    const { container } = renderGitLabWorkspace();

    expect(screen.queryByText(/comparison/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/executive summary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^new$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/persistent/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/resolved/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/choose file/i)).not.toBeInTheDocument();
    // "token" legitimately appears within GL-SEC-002's own finding text (it
    // is a check about CI job tokens), so the relevant invariant is that no
    // *input control* asks for one -- never that the word is absent.
    expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument();
    expect(container.querySelectorAll("input").length).toBe(1); // search only
    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });
});
