import { useMemo, useState } from "react";

import { compareReports } from "../comparison/compare";
import { buildComparisonExecutiveSummary, buildSingleReportExecutiveSummary } from "../executive-summary/summary";
import { ExecutiveSummary } from "../executive-summary/ExecutiveSummary";
import type { NormalizedWebReport } from "../report-import";
import { ReportWorkspace, type ReportWorkspaceProps } from "../report-workspace/ReportWorkspace";
import "./demo-controller.css";

type DemoMode = "earlier" | "later" | "comparison";
type DemoView = "findings" | "summary";

interface DemoControllerProps<TReport extends NormalizedWebReport> {
  readonly earlierReport: TReport;
  readonly laterReport: TReport;
  readonly earlierLabel: string;
  readonly laterLabel: string;
}

/**
 * The shared controller for both demo routes (`/demo/kubernetes` and
 * `/demo/gitlab`): a scan-state selector (earlier scan / later scan /
 * compare earlier to later) plus a findings/executive-summary view
 * toggle, wrapping `ReportWorkspace` and `ExecutiveSummary`. Written once,
 * generic over the concrete report type, rather than duplicated per
 * platform -- each demo page supplies its own two reports and labels;
 * the shared `compareReports` (`../comparison/compare.ts`) picks the right
 * platform-specific comparator internally. That dispatcher is also used,
 * unmodified, by the local report explorer (Phase 3G), so the
 * platform-dispatch logic exists exactly once.
 *
 * Comparison is computed lazily (`useMemo`, only when `mode === "comparison"`)
 * rather than eagerly on every render, and kept in React memory only.
 *
 * Changing `mode` resets the view back to "findings" and remounts
 * whichever of `ReportWorkspace`/`ExecutiveSummary` is currently shown via
 * `key={mode}` -- a deliberate keyed remount, so that component's own
 * internal state (search, filters, sort order, expanded `<details>`
 * elements) always starts fresh rather than carrying over stale state
 * from a previous scan state or comparison.
 */
export function DemoController<TReport extends NormalizedWebReport>({
  earlierReport,
  laterReport,
  earlierLabel,
  laterLabel,
}: DemoControllerProps<TReport>) {
  const [mode, setMode] = useState<DemoMode>("later");
  const [view, setView] = useState<DemoView>("findings");

  function handleModeChange(nextMode: DemoMode): void {
    setMode(nextMode);
    setView("findings");
  }

  const comparison = useMemo(
    () => (mode === "comparison" ? compareReports(earlierReport, laterReport) : null),
    [mode, earlierReport, laterReport],
  );

  const activeReport = mode === "earlier" ? earlierReport : laterReport;

  const workspaceProps: ReportWorkspaceProps =
    mode === "comparison" && comparison
      ? { mode: "comparison", source: "synthetic", comparison }
      : { mode: "single", source: "synthetic", report: activeReport };

  const executiveSummaryData =
    mode === "comparison" && comparison
      ? buildComparisonExecutiveSummary(comparison)
      : buildSingleReportExecutiveSummary(activeReport);

  return (
    <div className="demo-controller">
      <fieldset className="demo-controller__mode">
        <legend>Synthetic scan state</legend>
        <label className="demo-controller__radio">
          <input
            type="radio"
            name="demo-controller-mode"
            value="earlier"
            checked={mode === "earlier"}
            onChange={() => handleModeChange("earlier")}
          />
          {earlierLabel}
        </label>
        <label className="demo-controller__radio">
          <input
            type="radio"
            name="demo-controller-mode"
            value="later"
            checked={mode === "later"}
            onChange={() => handleModeChange("later")}
          />
          {laterLabel}
        </label>
        <label className="demo-controller__radio">
          <input
            type="radio"
            name="demo-controller-mode"
            value="comparison"
            checked={mode === "comparison"}
            onChange={() => handleModeChange("comparison")}
          />
          Compare earlier scan to later scan
        </label>
      </fieldset>
      <p className="demo-controller__mode-note">
        Switching the scan state or comparison resets search, filters, sorting, and any expanded
        finding details below.
      </p>

      <div className="demo-controller__view-toggle" role="group" aria-label="Findings or executive summary view">
        <button
          type="button"
          className="demo-controller__view-button"
          aria-pressed={view === "findings"}
          onClick={() => setView("findings")}
        >
          Findings
        </button>
        <button
          type="button"
          className="demo-controller__view-button"
          aria-pressed={view === "summary"}
          onClick={() => setView("summary")}
        >
          Executive summary
        </button>
      </div>

      {view === "findings" ? (
        <ReportWorkspace key={mode} {...workspaceProps} />
      ) : (
        <ExecutiveSummary key={mode} source="synthetic" summary={executiveSummaryData} />
      )}
    </div>
  );
}
