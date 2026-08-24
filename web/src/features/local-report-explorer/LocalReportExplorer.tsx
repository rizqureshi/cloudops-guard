import { useMemo, useRef, useState, type ChangeEvent } from "react";

import { compareReports } from "../comparison/compare";
import { ComparisonError } from "../comparison/errors";
import type { ComparisonResult } from "../comparison/types";
import { buildComparisonExecutiveSummary, buildSingleReportExecutiveSummary } from "../executive-summary/summary";
import { ExecutiveSummary } from "../executive-summary/ExecutiveSummary";
import type { NormalizedWebReport } from "../report-import";
import { ReportWorkspace, type ReportWorkspaceProps } from "../report-workspace/ReportWorkspace";
import "./local-report-explorer.css";
import { useReportSlot } from "./useReportSlot";

type ViewMode = "slot1" | "slot2" | "comparison";
type DisplayView = "findings" | "summary";

const GENERIC_COMPARISON_MESSAGE = "These two reports cannot be compared.";

type ComparisonAttempt = { readonly ok: true; readonly result: ComparisonResult } | { readonly ok: false; readonly message: string };

/**
 * The local report explorer island (`/explorer`, Phase 3G): two labeled
 * file inputs, reusing the real `ReportWorkspace`/`ExecutiveSummary` and
 * the shared `compareReports` dispatcher (`../comparison/compare.ts`) --
 * no filtering/sorting/matching/summary logic is reimplemented here.
 *
 * Everything stays in React memory only. No `fetch`/`XMLHttpRequest`/
 * `WebSocket`/`sendBeacon`, no `localStorage`/`sessionStorage`/IndexedDB/
 * cookies/service worker, no object URL, and no URL parameter/fragment is
 * ever used for report state. The initial state (no props, no default
 * data) contains no report -- see `explorer.astro`, which renders this
 * component with no report-shaped prop at all.
 *
 * `viewMode` is the visitor's own slot1/slot2/comparison selection, but
 * `effectiveMode` (derived below, every render) is what actually decides
 * what renders: if only one slot holds a report, that slot's report shows
 * regardless of `viewMode`; if both slots hold reports but they cannot be
 * compared (see `comparisonAttempt`), `"comparison"` silently falls back
 * to `"slot1"` rather than rendering nothing. This means clearing a slot,
 * or replacing it with an incompatible file, never needs an effect to
 * "resync" `viewMode` -- the derivation is correct by construction on
 * every render.
 *
 * `contentKey` combines `effectiveMode` with both slots' generation
 * counters (see `useReportSlot.ts`) so `ReportWorkspace`/`ExecutiveSummary`
 * remount -- resetting their internal search/filter/sort/expanded-details
 * state -- whenever the view changes *or* either slot's report changes,
 * including on every clear.
 */
export function LocalReportExplorer() {
  const slot1 = useReportSlot();
  const slot2 = useReportSlot();
  const input1Ref = useRef<HTMLInputElement>(null);
  const input2Ref = useRef<HTMLInputElement>(null);

  const [viewMode, setViewMode] = useState<ViewMode>("slot1");
  const [displayView, setDisplayView] = useState<DisplayView>("findings");

  const hasSlot1 = slot1.state.report !== null;
  const hasSlot2 = slot2.state.report !== null;
  const bothPresent = hasSlot1 && hasSlot2;

  const comparisonAttempt: ComparisonAttempt | null = useMemo(() => {
    const report1 = slot1.state.report;
    const report2 = slot2.state.report;
    if (!report1 || !report2) {
      return null;
    }
    try {
      return { ok: true, result: compareReports(report1, report2) };
    } catch (error) {
      const message = error instanceof ComparisonError ? error.message : GENERIC_COMPARISON_MESSAGE;
      return { ok: false, message };
    }
  }, [slot1.state.report, slot2.state.report]);

  let effectiveMode: ViewMode;
  if (hasSlot1 && !hasSlot2) {
    effectiveMode = "slot1";
  } else if (!hasSlot1 && hasSlot2) {
    effectiveMode = "slot2";
  } else if (bothPresent) {
    effectiveMode = viewMode === "comparison" && !(comparisonAttempt && comparisonAttempt.ok) ? "slot1" : viewMode;
  } else {
    effectiveMode = "slot1";
  }

  function handleModeChange(nextMode: ViewMode): void {
    setViewMode(nextMode);
    setDisplayView("findings");
  }

  function handleFile1Change(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0];
    if (file) {
      slot1.select(file);
    }
  }

  function handleFile2Change(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0];
    if (file) {
      slot2.select(file);
    }
  }

  function handleClear1(): void {
    slot1.clear();
    if (input1Ref.current) {
      input1Ref.current.value = "";
    }
    setViewMode("slot1");
    setDisplayView("findings");
  }

  function handleClear2(): void {
    slot2.clear();
    if (input2Ref.current) {
      input2Ref.current.value = "";
    }
    setViewMode("slot1");
    setDisplayView("findings");
  }

  function handleClearAll(): void {
    slot1.clear();
    slot2.clear();
    if (input1Ref.current) {
      input1Ref.current.value = "";
    }
    if (input2Ref.current) {
      input2Ref.current.value = "";
    }
    setViewMode("slot1");
    setDisplayView("findings");
  }

  const activeReport: NormalizedWebReport | null =
    effectiveMode === "slot1" ? slot1.state.report : effectiveMode === "slot2" ? slot2.state.report : null;

  const workspaceProps: ReportWorkspaceProps | null =
    effectiveMode === "comparison" && comparisonAttempt?.ok
      ? { mode: "comparison", source: "local", comparison: comparisonAttempt.result }
      : activeReport
        ? { mode: "single", source: "local", report: activeReport }
        : null;

  const executiveSummaryData =
    effectiveMode === "comparison" && comparisonAttempt?.ok
      ? buildComparisonExecutiveSummary(comparisonAttempt.result)
      : activeReport
        ? buildSingleReportExecutiveSummary(activeReport)
        : null;

  const contentKey = `${effectiveMode}:${slot1.state.generation}:${slot2.state.generation}`;

  return (
    <div className="local-report-explorer">
      <div className="local-report-explorer__privacy">
        <h2>Your files stay in this browser tab</h2>
        <ul>
          <li>Files stay in this browser tab and are never uploaded anywhere.</li>
          <li>Reloading or closing this tab clears any imported report.</li>
          <li>This page accepts only a CloudOps Guard report.json file.</li>
          <li>
            It never accepts a kubeconfig file, a GitLab access token, CI/CD YAML, any other
            credential, or an HTML report.
          </li>
        </ul>
      </div>

      <fieldset className="local-report-explorer__slots">
        <legend>Import report files</legend>

        <div className="local-report-explorer__slot">
          <label htmlFor="local-report-slot-1">Earlier or primary report</label>
          <input
            id="local-report-slot-1"
            ref={input1Ref}
            type="file"
            accept=".json,application/json"
            aria-describedby="local-report-slot-1-help local-report-slot-1-status"
            onChange={handleFile1Change}
          />
          <p id="local-report-slot-1-help" className="local-report-explorer__slot-help">
            A CloudOps Guard report.json file produced by the CLI.
          </p>
          <p id="local-report-slot-1-status" className="local-report-explorer__slot-status" aria-live="polite">
            {slot1.state.isLoading ? "Reading file…" : slot1.state.report ? "Report loaded." : ""}
          </p>
          {slot1.state.errorMessage ? (
            <p className="local-report-explorer__slot-error" role="alert">
              {slot1.state.errorMessage}
            </p>
          ) : null}
          <div className="local-report-explorer__slot-actions">
            <button type="button" className="local-report-explorer__button" onClick={handleClear1}>
              Clear
            </button>
          </div>
        </div>

        <div className="local-report-explorer__slot">
          <label htmlFor="local-report-slot-2">Later report for comparison (optional)</label>
          <input
            id="local-report-slot-2"
            ref={input2Ref}
            type="file"
            accept=".json,application/json"
            aria-describedby="local-report-slot-2-help local-report-slot-2-status"
            onChange={handleFile2Change}
          />
          <p id="local-report-slot-2-help" className="local-report-explorer__slot-help">
            Optional -- select a second report.json to compare it against the report above.
          </p>
          <p id="local-report-slot-2-status" className="local-report-explorer__slot-status" aria-live="polite">
            {slot2.state.isLoading ? "Reading file…" : slot2.state.report ? "Report loaded." : ""}
          </p>
          {slot2.state.errorMessage ? (
            <p className="local-report-explorer__slot-error" role="alert">
              {slot2.state.errorMessage}
            </p>
          ) : null}
          <div className="local-report-explorer__slot-actions">
            <button type="button" className="local-report-explorer__button" onClick={handleClear2}>
              Clear
            </button>
          </div>
        </div>
      </fieldset>

      <div className="local-report-explorer__actions">
        <button type="button" className="local-report-explorer__button" onClick={handleClearAll}>
          Clear all
        </button>
      </div>

      {!hasSlot1 && !hasSlot2 ? (
        <p className="local-report-explorer__empty">
          No report imported yet. Select a report.json file above to begin -- nothing is imported
          automatically.
        </p>
      ) : (
        <>
          {bothPresent ? (
            <>
              <fieldset className="local-report-explorer__mode">
                <legend>View</legend>
                <label className="local-report-explorer__radio">
                  <input
                    type="radio"
                    name="local-report-explorer-mode"
                    value="slot1"
                    checked={effectiveMode === "slot1"}
                    onChange={() => handleModeChange("slot1")}
                  />
                  Earlier report
                </label>
                <label className="local-report-explorer__radio">
                  <input
                    type="radio"
                    name="local-report-explorer-mode"
                    value="slot2"
                    checked={effectiveMode === "slot2"}
                    onChange={() => handleModeChange("slot2")}
                  />
                  Later report
                </label>
                <label className="local-report-explorer__radio">
                  <input
                    type="radio"
                    name="local-report-explorer-mode"
                    value="comparison"
                    checked={effectiveMode === "comparison"}
                    disabled={!comparisonAttempt?.ok}
                    onChange={() => handleModeChange("comparison")}
                  />
                  Compare earlier to later
                </label>
              </fieldset>
              {!comparisonAttempt?.ok ? (
                <p className="local-report-explorer__comparison-note" role="status">
                  These two reports can’t be compared ({comparisonAttempt?.message}). Each can still be
                  viewed individually.
                </p>
              ) : null}
            </>
          ) : null}

          <div
            className="local-report-explorer__view-toggle"
            role="group"
            aria-label="Findings or executive summary view"
          >
            <button
              type="button"
              className="local-report-explorer__view-button"
              aria-pressed={displayView === "findings"}
              onClick={() => setDisplayView("findings")}
            >
              Findings
            </button>
            <button
              type="button"
              className="local-report-explorer__view-button"
              aria-pressed={displayView === "summary"}
              onClick={() => setDisplayView("summary")}
            >
              Executive summary
            </button>
          </div>

          {displayView === "findings" && workspaceProps ? (
            <ReportWorkspace key={contentKey} {...workspaceProps} />
          ) : executiveSummaryData ? (
            <ExecutiveSummary key={contentKey} source="local" summary={executiveSummaryData} />
          ) : null}
        </>
      )}
    </div>
  );
}
