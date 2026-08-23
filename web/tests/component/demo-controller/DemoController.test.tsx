// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DemoController } from "../../../src/features/demo-controller/DemoController";
import {
  buildNormalizedKubernetesFinding,
  buildNormalizedKubernetesReport,
} from "../../helpers/normalizedKubernetesFixtures";

const earlierFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-004",
  severity: "high",
  namespace: "payments-demo",
  resourceName: "checkout-api",
});
const persistentFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-RES-001",
  severity: "medium",
  namespace: "payments-demo",
  resourceName: "checkout-api",
});
const laterFinding = buildNormalizedKubernetesFinding({
  checkId: "K8S-REL-001",
  severity: "high",
  namespace: "commerce-demo",
  resourceName: "cache-pod",
});

function renderController() {
  const earlierReport = buildNormalizedKubernetesReport({ generatedAt: "2026-06-01T09:00:00Z" }, [
    earlierFinding,
    persistentFinding,
  ]);
  const laterReport = buildNormalizedKubernetesReport({ generatedAt: "2026-06-15T09:00:00Z" }, [
    persistentFinding,
    laterFinding,
  ]);
  render(
    <DemoController
      earlierReport={earlierReport}
      laterReport={laterReport}
      earlierLabel="Earlier scan (2026-06-01)"
      laterLabel="Later scan (2026-06-15)"
    />,
  );
}

describe("DemoController: mode selection", () => {
  it("exposes earlier, later, and comparison modes with accessible labels", () => {
    renderController();
    expect(screen.getByLabelText("Earlier scan (2026-06-01)")).toBeInTheDocument();
    expect(screen.getByLabelText("Later scan (2026-06-15)")).toBeInTheDocument();
    expect(screen.getByLabelText("Compare earlier scan to later scan")).toBeInTheDocument();
  });

  it("shows the later scan by default", () => {
    renderController();
    expect(screen.getByLabelText("Later scan (2026-06-15)")).toBeChecked();
    expect(screen.getByText("K8S-REL-001")).toBeInTheDocument();
    expect(screen.queryByText("K8S-RES-004")).not.toBeInTheDocument();
  });

  it("switching to earlier shows the earlier report's findings only", async () => {
    const user = userEvent.setup();
    renderController();

    await user.click(screen.getByLabelText("Earlier scan (2026-06-01)"));

    expect(screen.getByText("K8S-RES-004")).toBeInTheDocument();
    expect(screen.getByText("K8S-RES-001")).toBeInTheDocument();
    expect(screen.queryByText("K8S-REL-001")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 2 findings.")).toBeInTheDocument();
  });

  it("switching to comparison shows every status and correct totals", async () => {
    const user = userEvent.setup();
    renderController();

    await user.click(screen.getByLabelText("Compare earlier scan to later scan"));

    expect(screen.getByText("New 1")).toBeInTheDocument();
    expect(screen.getByText("Persistent 1")).toBeInTheDocument();
    expect(screen.getByText("Resolved 1")).toBeInTheDocument();
    expect(screen.getByText("Showing 3 of 3 findings.")).toBeInTheDocument();
  });
});

describe("DemoController: reset on mode change", () => {
  it("resets search, filters, sorting, and the view when switching mode", async () => {
    const user = userEvent.setup();
    renderController();

    await user.type(screen.getByLabelText("Search findings"), "K8S-REL-001");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    await user.selectOptions(screen.getByLabelText("Sort by"), "checkId");
    await user.click(screen.getByRole("button", { name: "Executive summary" }));
    expect(screen.getByRole("button", { name: "Executive summary" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByLabelText("Earlier scan (2026-06-01)"));

    // View resets back to Findings.
    expect(screen.getByRole("button", { name: "Findings" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Executive summary" })).toHaveAttribute("aria-pressed", "false");
    // Filters/search/sort reset to default (a fresh ReportWorkspace mount).
    expect(screen.getByLabelText("Search findings")).toHaveValue("");
    expect(screen.getByLabelText("Severity")).toHaveValue("all");
    expect(screen.getByLabelText("Sort by")).toHaveValue("severity");
    expect(screen.getByText("Showing 2 of 2 findings.")).toBeInTheDocument();
  });

  it("resets expanded finding details when switching mode", async () => {
    const user = userEvent.setup();
    renderController();

    const summary = screen.getByText("K8S-REL-001").closest("summary")!;
    const details = summary.closest("details")!;
    await user.click(summary);
    expect(details).toHaveAttribute("open");

    await user.click(screen.getByLabelText("Earlier scan (2026-06-01)"));
    await user.click(screen.getByLabelText("Later scan (2026-06-15)"));

    const newSummary = screen.getByText("K8S-REL-001").closest("summary")!;
    const newDetails = newSummary.closest("details")!;
    expect(newDetails).not.toHaveAttribute("open");
  });
});

describe("DemoController: findings/executive-summary view", () => {
  it("switches views via keyboard-operable buttons with correct accessible names and pressed state", async () => {
    const user = userEvent.setup();
    renderController();

    const findingsButton = screen.getByRole("button", { name: "Findings" });
    const summaryButton = screen.getByRole("button", { name: "Executive summary" });
    expect(findingsButton).toHaveAttribute("aria-pressed", "true");
    expect(summaryButton).toHaveAttribute("aria-pressed", "false");

    summaryButton.focus();
    await user.keyboard("{Enter}");

    expect(summaryButton).toHaveAttribute("aria-pressed", "true");
    expect(findingsButton).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Prioritized recommendations")).toBeInTheDocument();
  });

  it("renders executive-summary scope and limitations content", async () => {
    const user = userEvent.setup();
    renderController();

    await user.click(screen.getByRole("button", { name: "Executive summary" }));

    expect(screen.getByText("Scope and limitations")).toBeInTheDocument();
    expect(screen.getByText(/deterministic and template-driven/)).toBeInTheDocument();
  });
});

describe("DemoController: excluded controls", () => {
  it("renders no explorer, upload, token, credential, contact, feedback, or cross-target override control", () => {
    renderController();
    expect(screen.queryByText(/explorer/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/choose file/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/contact/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/feedback/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/compare anyway/i)).not.toBeInTheDocument();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });
});
