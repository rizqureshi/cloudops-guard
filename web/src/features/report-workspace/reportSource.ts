/**
 * A narrow, application-controlled indicator of where a displayed report
 * came from -- never derived from report content itself, and never an
 * arbitrary caller-supplied string (there is no way to pass a label other
 * than through this fixed union/lookup). `DemoController` (Phase 3F)
 * always passes `"synthetic"`; the local report explorer (Phase 3G)
 * always passes `"local"`. `ReportWorkspace` and `ExecutiveSummary` both
 * consume this so the visible source indicator, and any source-aware
 * copy, come from exactly one place.
 */
export type ReportSource = "synthetic" | "local";

export const REPORT_SOURCE_LABELS: Readonly<Record<ReportSource, string>> = {
  synthetic: "Synthetic demonstration",
  local: "Local report",
};
