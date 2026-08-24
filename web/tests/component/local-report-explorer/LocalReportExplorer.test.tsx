// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { LocalReportExplorer } from "../../../src/features/local-report-explorer/LocalReportExplorer";
import goldenGitlabReport from "../../../../tests/fixtures/golden_gitlab_report.json";
import goldenKubernetesReport from "../../../../tests/fixtures/golden_kubernetes_report.json";
import syntheticGitlabProtected from "../../../src/data/synthetic-gitlab-report-protected-branch.json";
import syntheticGitlabUnprotected from "../../../src/data/synthetic-gitlab-report-unprotected-branch.json";
import syntheticKubernetesEarlier from "../../../src/data/synthetic-kubernetes-report.json";
import syntheticKubernetesLater from "../../../src/data/synthetic-kubernetes-report-later.json";

function jsonFile(name: string, content: unknown): File {
  const body = typeof content === "string" ? content : JSON.stringify(content);
  return new File([body], name, { type: "application/json" });
}

const earlierLabel = "Earlier or primary report";
const laterLabel = "Later report for comparison (optional)";

describe("LocalReportExplorer: initial state", () => {
  it("starts with no report and shows the privacy explanation prominently", () => {
    render(<LocalReportExplorer />);

    expect(screen.getByText("Your files stay in this browser tab")).toBeInTheDocument();
    expect(screen.getByText(/never uploaded anywhere/i)).toBeInTheDocument();
    expect(screen.getByText(/Reloading or closing this tab clears/i)).toBeInTheDocument();
    expect(screen.getByText(/only a CloudOps Guard report\.json file/i)).toBeInTheDocument();
    expect(screen.getByText(/never accepts a kubeconfig file/i)).toBeInTheDocument();
    expect(screen.getByText("No report imported yet.", { exact: false })).toBeInTheDocument();
  });

  it("exposes exactly two labeled file inputs", () => {
    render(<LocalReportExplorer />);
    const fileInputs = document.querySelectorAll('input[type="file"]');
    expect(fileInputs).toHaveLength(2);
    expect(screen.getByLabelText(earlierLabel)).toBe(fileInputs[0]);
    expect(screen.getByLabelText(laterLabel)).toBe(fileInputs[1]);
  });

  it("both file inputs use the correct accept attribute and omit multiple/directory selection", () => {
    render(<LocalReportExplorer />);
    for (const label of [earlierLabel, laterLabel]) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("accept", ".json,application/json");
      expect(input).not.toHaveAttribute("multiple");
      expect(input).not.toHaveAttribute("webkitdirectory");
      expect(input).not.toHaveAttribute("directory");
    }
  });

  it("never renders more than two file inputs -- no history, no add-another control", () => {
    render(<LocalReportExplorer />);
    expect(document.querySelectorAll('input[type="file"]')).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /add/i })).not.toBeInTheDocument();
  });
});

describe("LocalReportExplorer: valid imports", () => {
  it("imports a valid Kubernetes report and shows it in findings view with the local-report indicator", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticKubernetesEarlier));

    expect(await screen.findByText("Report loaded.")).toBeInTheDocument();
    expect(screen.getByText("Local report")).toBeInTheDocument();
    expect(screen.getByLabelText("Search findings")).toBeInTheDocument();
    expect(screen.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeInTheDocument();
  });

  it("imports a valid GitLab report", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticGitlabUnprotected));

    expect(await screen.findByText("Report loaded.")).toBeInTheDocument();
    expect(screen.getByText("GitLab", { exact: true })).toBeInTheDocument();
  });

  it("imports the golden Kubernetes fixture", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("golden.json", goldenKubernetesReport));

    expect(await screen.findByText("Report loaded.")).toBeInTheDocument();
    expect(screen.getByText("Kubernetes", { exact: true })).toBeInTheDocument();
  });

  it("imports the golden GitLab fixture", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("golden.json", goldenGitlabReport));

    expect(await screen.findByText("Report loaded.")).toBeInTheDocument();
    expect(screen.getByText("GitLab", { exact: true })).toBeInTheDocument();
  });

  it("shows the local-report indicator in the executive-summary view too", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");

    await user.click(screen.getByRole("button", { name: "Executive summary" }));
    expect(screen.getByText("Local report")).toBeInTheDocument();
    expect(screen.getByText("Prioritized recommendations")).toBeInTheDocument();
  });
});

describe("LocalReportExplorer: findings-view interaction", () => {
  async function importEarlier(user: ReturnType<typeof userEvent.setup>) {
    render(<LocalReportExplorer />);
    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
  }

  it("search, filters, sorting, and disclosure all work through the real ReportWorkspace", async () => {
    const user = userEvent.setup();
    await importEarlier(user);

    await user.type(screen.getByLabelText("Search findings"), "checkout-api");
    expect(screen.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search findings"));

    await user.selectOptions(screen.getByLabelText("Severity", { selector: "select" }), "high");
    expect(screen.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Severity", { selector: "select" }), "all");

    await user.selectOptions(screen.getByLabelText("Sort by"), "checkId");
    await user.selectOptions(screen.getByLabelText("Sort by"), "severity");

    const firstDetails = document.querySelector(".finding-row__details")!;
    await user.click(within(firstDetails as HTMLElement).getByText(/^[A-Z0-9-]+$/));
    expect(firstDetails).toHaveAttribute("open");
  });

  it("switches to the executive-summary view and back", async () => {
    const user = userEvent.setup();
    await importEarlier(user);

    await user.click(screen.getByRole("button", { name: "Executive summary" }));
    expect(screen.getByText("Scope and limitations")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Findings" }));
    expect(screen.getByLabelText("Search findings")).toBeInTheDocument();
  });
});

describe("LocalReportExplorer: two-report comparison", () => {
  it("offers earlier/later/comparison modes once both slots hold compatible reports", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("earlier.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("later.json", syntheticKubernetesLater));
    await screen.findAllByText("Report loaded.");

    expect(screen.getByLabelText("Earlier report")).toBeInTheDocument();
    expect(screen.getByLabelText("Later report")).toBeInTheDocument();
    const compareRadio = screen.getByLabelText("Compare earlier to later");
    expect(compareRadio).toBeEnabled();

    await user.click(compareRadio);
    expect(screen.getByText(/^New \d+$/)).toBeInTheDocument();
    expect(screen.getByText(/^Persistent \d+$/)).toBeInTheDocument();
    expect(screen.getByText(/^Resolved \d+$/)).toBeInTheDocument();
  });

  it("renders new/persistent/resolved status badges and the comparison-status filter/sort", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("earlier.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("later.json", syntheticKubernetesLater));
    await screen.findAllByText("Report loaded.");
    await user.click(screen.getByLabelText("Compare earlier to later"));

    expect(document.querySelectorAll(".finding-row__status").length).toBeGreaterThan(0);

    const statusFilter = screen.getByLabelText("Comparison status", { selector: "select" });
    await user.selectOptions(statusFilter, "new");
    expect(screen.getByText(/^Showing \d+ of \d+ findings\.$/)).toBeInTheDocument();
    await user.selectOptions(statusFilter, "all");

    await user.selectOptions(screen.getByLabelText("Sort by"), "comparisonStatus");
  });
});

describe("LocalReportExplorer: comparison rejection, still viewable individually", () => {
  it("rejects a mixed-platform pair, explains why, and still allows viewing each individually", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("k8s.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("gitlab.json", syntheticGitlabUnprotected));
    await screen.findAllByText("Report loaded.");

    expect(screen.getByText(/can.t be compared/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Compare earlier to later")).toBeDisabled();

    // Still viewable individually.
    expect(screen.getByLabelText("Earlier report")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Later report"));
    expect(screen.getByText("GitLab", { exact: true })).toBeInTheDocument();
  });

  it("rejects a Kubernetes target mismatch (different clusterContext)", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);
    const mismatched = { ...syntheticKubernetesLater, cluster_context: "some-other-cluster" };

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("earlier.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("later.json", mismatched));
    await screen.findAllByText("Report loaded.");

    expect(screen.getByText(/can.t be compared/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Compare earlier to later")).toBeDisabled();
  });

  it("rejects a GitLab target mismatch (different projectId)", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);
    const mismatched = { ...syntheticGitlabProtected, project_id: 424242 };

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("earlier.json", syntheticGitlabUnprotected));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("later.json", mismatched));
    await screen.findAllByText("Report loaded.");

    expect(screen.getByText(/can.t be compared/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Compare earlier to later")).toBeDisabled();
  });

  it("rejects equal timestamps", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("a.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("b.json", syntheticKubernetesEarlier));
    await screen.findAllByText("Report loaded.");

    expect(screen.getByText(/can.t be compared/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Compare earlier to later")).toBeDisabled();
  });

  it("rejects reversed timestamps (later report placed in the earlier slot)", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("later.json", syntheticKubernetesLater));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("earlier.json", syntheticKubernetesEarlier));
    await screen.findAllByText("Report loaded.");

    expect(screen.getByText(/can.t be compared/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Compare earlier to later")).toBeDisabled();
  });
});

describe("LocalReportExplorer: local-import rejections", () => {
  it("rejects a wrong file extension", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.txt", syntheticKubernetesEarlier));

    expect(await screen.findByRole("alert")).toHaveTextContent(/\.json/i);
    expect(screen.queryByText("Report loaded.")).not.toBeInTheDocument();
  });

  it("rejects an actual HTML file", async () => {
    // `applyAccept: false`: a real browser's `accept` attribute is only a
    // picker *hint* -- a visitor can still choose "All Files" and select a
    // .html file despite it, so the component's own extension check (not
    // the browser) must be what actually rejects it. `user-event` filters
    // non-matching files by default; this test deliberately bypasses that
    // filtering to exercise the app's own rejection path.
    const user = userEvent.setup({ applyAccept: false });
    render(<LocalReportExplorer />);
    const htmlFile = new File(["<html><body>not a report</body></html>"], "report.html", { type: "text/html" });

    await user.upload(screen.getByLabelText(earlierLabel), htmlFile);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Report loaded.")).not.toBeInTheDocument();
  });

  it("rejects malformed JSON", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", "{ not valid json"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("rejects an unsupported schema", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", { unrelated: true }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("rejects a summary/findings mismatch", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);
    const tampered = { ...syntheticKubernetesEarlier, summary: { critical: 50, high: 50, medium: 50, low: 50 } };

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", tampered));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("never reproduces the selected filename in the displayed error", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);
    const secretName = "top-secret-internal-codename.txt";

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile(secretName, syntheticKubernetesEarlier));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toContain(secretName);
    expect(alert.textContent).not.toContain("top-secret-internal-codename");
  });
});

describe("LocalReportExplorer: clearing", () => {
  it("clears a single slot: resets its input value, error, and any dependent view", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");

    const clearButtons = screen.getAllByRole("button", { name: "Clear" });
    await user.click(clearButtons[0]!);

    expect(screen.getByText("No report imported yet.", { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText(earlierLabel)).toHaveValue("");
  });

  it("clears both slots via Clear all: resets inputs, filters, mode, and view", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("earlier.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    await user.upload(screen.getByLabelText(laterLabel), jsonFile("later.json", syntheticKubernetesLater));
    await screen.findAllByText("Report loaded.");
    await user.click(screen.getByLabelText("Compare earlier to later"));
    await user.type(screen.getByLabelText("Search findings"), "cache-pod");
    await user.click(screen.getByRole("button", { name: "Executive summary" }));

    await user.click(screen.getByRole("button", { name: "Clear all" }));

    expect(screen.getByText("No report imported yet.", { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText(earlierLabel)).toHaveValue("");
    expect(screen.getByLabelText(laterLabel)).toHaveValue("");

    // Re-importing a single report afterward proves the view/mode state
    // was truly reset, not merely hidden -- it comes back in Findings view.
    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");
    expect(screen.getByLabelText("Search findings")).toBeInTheDocument();
    expect(screen.getByLabelText("Search findings")).toHaveValue("");
  });
});

describe("LocalReportExplorer: report safety", () => {
  it("renders HTML/Markdown/URL-shaped report strings as literal text, never injected markup", async () => {
    const user = userEvent.setup();
    // The first synthetic finding is severity "medium" -- the summary
    // below must match that exactly, or the import is (correctly) rejected
    // as a summary mismatch before this test ever reaches rendering.
    const maliciousFinding = {
      ...syntheticKubernetesEarlier.findings[0],
      evidence: "<img src=x onerror=alert(1)>[a link](javascript:alert(1))",
    };
    const report = {
      ...syntheticKubernetesEarlier,
      findings: [maliciousFinding],
      summary: { critical: 0, high: 0, medium: 1, low: 0 },
    };
    render(<LocalReportExplorer />);

    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", report));
    await screen.findByText("Report loaded.");

    expect(document.querySelector("img")).toBeNull();
    expect(
      screen.getAllByText("<img src=x onerror=alert(1)>[a link](javascript:alert(1))").length,
    ).toBeGreaterThan(0);
  });

  it("renders no contact, feedback, upload-to-server, credential, kubeconfig, token, analytics, or deployment control", async () => {
    const user = userEvent.setup();
    render(<LocalReportExplorer />);
    await user.upload(screen.getByLabelText(earlierLabel), jsonFile("report.json", syntheticKubernetesEarlier));
    await screen.findByText("Report loaded.");

    expect(screen.queryByText(/contact/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/feedback/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/kubeconfig/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/analytics/i)).not.toBeInTheDocument();
    // Not a bare /deploy/i check: the synthetic report's own resource kind
    // "Deployment" legitimately contains that substring as report content,
    // so this checks specifically for deployment-*configuration* language
    // instead, scoped to buttons/links (an actual control), not report text.
    expect(screen.queryByRole("button", { name: /deploy/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /deploy/i })).not.toBeInTheDocument();
  });
});
