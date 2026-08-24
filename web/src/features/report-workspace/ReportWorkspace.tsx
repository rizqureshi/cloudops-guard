import { useMemo, useState } from "react";

import { COMPARISON_STATUS_ORDER, type ComparisonResult, type ComparisonStatus } from "../comparison/types";
import type {
  NormalizedFinding,
  NormalizedGitLabTarget,
  NormalizedKubernetesTarget,
  NormalizedWebReport,
} from "../report-import";
import {
  DEFAULT_FILTER_STATE,
  distinctCategories,
  distinctResourceKinds,
  SEVERITY_FILTER_OPTIONS,
  type WorkspaceFilterState,
} from "./filtering";
import { FindingRow } from "./FindingRow";
import "./report-workspace.css";
import { REPORT_SOURCE_LABELS, type ReportSource } from "./reportSource";
import { SORT_OPTIONS } from "./sorting";
import {
  buildSingleReportItems,
  filterWorkspaceItems,
  sortWorkspaceItems,
  type WorkspaceItem,
  type WorkspaceSortOption,
} from "./workspaceItems";

export type ReportWorkspaceProps = { readonly source: ReportSource } & (
  | { readonly mode: "single"; readonly report: NormalizedWebReport }
  | { readonly mode: "comparison"; readonly comparison: ComparisonResult }
);

const SORT_LABELS: Readonly<Record<WorkspaceSortOption, string>> = {
  severity: "Severity",
  checkId: "Check ID",
  resource: "Resource",
  comparisonStatus: "Comparison status",
};

const COMPARISON_STATUS_LABELS: Readonly<Record<ComparisonStatus, string>> = {
  new: "New",
  persistent: "Persistent",
  resolved: "Resolved",
};

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * The target identity to display: the report's own target in single mode,
 * or the newer report's target in comparison mode (target compatibility
 * between older/newer is already enforced before a `ComparisonResult` can
 * exist -- see `../comparison/validation.ts` -- so either side would show
 * the same fields). A real discriminated union, branched explicitly on
 * `props.mode` and then on platform, so every field access below is
 * narrowed by the type checker -- no `as` cast stands in for that.
 */
type DisplayTarget =
  | { readonly platform: "kubernetes"; readonly target: NormalizedKubernetesTarget }
  | { readonly platform: "gitlab"; readonly target: NormalizedGitLabTarget };

function resolveDisplayTarget(props: ReportWorkspaceProps): DisplayTarget {
  if (props.mode === "single") {
    if (props.report.platform === "kubernetes") {
      return { platform: "kubernetes", target: props.report.target };
    }
    return { platform: "gitlab", target: props.report.target };
  }
  if (props.comparison.platform === "kubernetes") {
    return { platform: "kubernetes", target: props.comparison.newerReport.target };
  }
  return { platform: "gitlab", target: props.comparison.newerReport.target };
}

/**
 * Assigns each workspace item a stable React key derived from its
 * position in the *original*, unfiltered `items` array -- never from a
 * delimiter-joined combination of displayed fields, which would collide
 * for two otherwise-identical findings that differ only in a field not
 * included in the join. Built once per `items` array via `useMemo`, as a
 * `Map` keyed by the item's finding **object reference** (not content),
 * giving O(1) lookups during render. This works because
 * `filterWorkspaceItems`/`sortWorkspaceItems` only ever select and reorder
 * items (and, within them, findings) that originate unchanged from
 * `items` -- in comparison mode, each `ComparisonFindingResult` displays
 * exactly one finding object, and multiset matching (see
 * `../comparison/compare.ts`) never lets the same finding object appear in
 * more than one result -- so every finding encountered while rendering a
 * filtered/sorted view is reference-identical to one entry in `items`.
 */
function useFindingKeys(items: readonly WorkspaceItem[]): ReadonlyMap<NormalizedFinding, number> {
  return useMemo(() => {
    const keys = new Map<NormalizedFinding, number>();
    items.forEach((item, index) => {
      keys.set(item.finding, index);
    });
    return keys;
  }, [items]);
}

/**
 * The shared report-workspace island: everything is kept in React memory
 * only (`useState`/`useMemo`) -- no `localStorage`/`sessionStorage`/
 * IndexedDB/cookies/service worker/URL persistence, and no `fetch`/
 * `XMLHttpRequest`/`WebSocket`/`sendBeacon` is ever called here.
 *
 * Generalized in Phase 3F to also render a `ComparisonResult` (see
 * `../comparison/`), in addition to a plain `NormalizedWebReport` (Phase
 * 3E). The `mode` discriminant makes this an explicit, exhaustive choice
 * at every call site, rather than inferring which shape was passed. In
 * comparison mode: the severity-totals bar reflects the *newer* report
 * only (see the milestone document, §Phase 3F -- resolved findings are
 * never merged into it); a separate comparison-status-totals bar shows
 * New/Persistent/Resolved counts; a comparison-status filter and a
 * "Comparison status" sort option become available; and each finding row
 * shows its status as a labeled badge. None of this renders in single
 * mode, which is otherwise unchanged from Phase 3E.
 *
 * `source` (Phase 3G) is a narrow, application-controlled discriminant --
 * never derived from report content -- selecting the visible source
 * indicator via `REPORT_SOURCE_LABELS` (`./reportSource.ts`).
 * `DemoController` always passes `"synthetic"`; the local report explorer
 * always passes `"local"`. There is no prop that accepts an arbitrary
 * label string.
 */
export function ReportWorkspace(props: ReportWorkspaceProps) {
  const [filters, setFilters] = useState<WorkspaceFilterState>(DEFAULT_FILTER_STATE);
  const [sortOption, setSortOption] = useState<WorkspaceSortOption>("severity");

  const isComparison = props.mode === "comparison";

  const items = useMemo<readonly WorkspaceItem[]>(() => {
    if (props.mode === "single") {
      // Widening `props.report.findings` (typed per-branch by the
      // discriminated union) to the flat `NormalizedFinding` union once
      // here, same technique as Phase 3E -- readonly arrays are
      // covariant, so this is a safe upcast, not a runtime transformation.
      const findings: readonly NormalizedFinding[] = props.report.findings;
      return buildSingleReportItems(findings);
    }
    return props.comparison.results.map((result) => ({
      finding: result.displayFinding,
      status: result.status,
    }));
  }, [props]);

  const findingKeys = useFindingKeys(items);

  const underlyingFindings = useMemo(() => items.map((item) => item.finding), [items]);
  const resourceKindOptions = useMemo(() => distinctResourceKinds(underlyingFindings), [underlyingFindings]);
  const categoryOptions = useMemo(() => distinctCategories(underlyingFindings), [underlyingFindings]);

  const filtered = useMemo(() => filterWorkspaceItems(items, filters), [items, filters]);
  const sorted = useMemo(() => sortWorkspaceItems(filtered, sortOption), [filtered, sortOption]);

  const totalCount = items.length;
  const filteredCount = sorted.length;

  const sortOptionsForMode: readonly WorkspaceSortOption[] = isComparison
    ? [...SORT_OPTIONS, "comparisonStatus"]
    : SORT_OPTIONS;

  function clearFilters(): void {
    setFilters(DEFAULT_FILTER_STATE);
  }

  function handleSortChange(value: string): void {
    setSortOption(value as WorkspaceSortOption);
  }

  const currentSeveritySummary = props.mode === "single" ? props.report.summary : props.comparison.newerReport.summary;
  const displayTarget = resolveDisplayTarget(props);

  return (
    <div className="report-workspace">
      <p className="status-label status-label--neutral report-workspace__badge">
        {REPORT_SOURCE_LABELS[props.source]}
      </p>

      <div className="report-workspace__identity">
        {displayTarget.platform === "kubernetes" ? (
          <>
            <p>
              <span className="report-workspace__identity-label">Platform</span> Kubernetes
            </p>
            <p>
              <span className="report-workspace__identity-label">Cluster context</span>{" "}
              {displayTarget.target.clusterContext}
            </p>
            <p>
              <span className="report-workspace__identity-label">Namespace filter</span>{" "}
              {displayTarget.target.namespaceFilter === null
                ? "All namespaces"
                : displayTarget.target.namespaceFilter}
            </p>
          </>
        ) : (
          <>
            <p>
              <span className="report-workspace__identity-label">Platform</span> GitLab
            </p>
            {/* Rendered as plain text, never a link/navigation target -- the
                GitLab URL is untrusted, report-derived display data (see
                CLAUDE.md, "web application invariants"). */}
            <p>
              <span className="report-workspace__identity-label">GitLab instance URL</span>{" "}
              {displayTarget.target.gitlabUrl}
            </p>
            <p>
              <span className="report-workspace__identity-label">Project ID</span>{" "}
              {displayTarget.target.projectId}
            </p>
            <p>
              <span className="report-workspace__identity-label">Project path</span>{" "}
              {displayTarget.target.projectPath}
            </p>
            <p>
              <span className="report-workspace__identity-label">Default branch</span>{" "}
              {displayTarget.target.defaultBranch}
            </p>
          </>
        )}
        {props.mode === "single" ? (
          <p>
            <span className="report-workspace__identity-label">Report generated</span>{" "}
            <time dateTime={props.report.generatedAt}>{props.report.generatedAt}</time>
          </p>
        ) : (
          <>
            <p>
              <span className="report-workspace__identity-label">Earlier scan</span>{" "}
              <time dateTime={props.comparison.olderReport.generatedAt}>
                {props.comparison.olderReport.generatedAt}
              </time>
            </p>
            <p>
              <span className="report-workspace__identity-label">Later scan</span>{" "}
              <time dateTime={props.comparison.newerReport.generatedAt}>
                {props.comparison.newerReport.generatedAt}
              </time>
            </p>
          </>
        )}
      </div>

      <div className="report-workspace__summary" aria-label="Full report severity totals">
        <span className="status-label status-label--critical">Critical {currentSeveritySummary.critical}</span>
        <span className="status-label status-label--high">High {currentSeveritySummary.high}</span>
        <span className="status-label status-label--medium">Medium {currentSeveritySummary.medium}</span>
        <span className="status-label status-label--low">Low {currentSeveritySummary.low}</span>
        <span className="status-label status-label--neutral">Total {currentSeveritySummary.total}</span>
      </div>

      {isComparison ? (
        <div className="report-workspace__summary" aria-label="Comparison status totals">
          <span className="status-label status-label--new">New {props.comparison.statusTotals.new}</span>
          <span className="status-label status-label--persistent">
            Persistent {props.comparison.statusTotals.persistent}
          </span>
          <span className="status-label status-label--resolved">
            Resolved {props.comparison.statusTotals.resolved}
          </span>
        </div>
      ) : null}

      <form className="report-workspace__controls" onSubmit={(event) => event.preventDefault()}>
        <div className="report-workspace__field">
          <label htmlFor="workspace-search">Search findings</label>
          <input
            id="workspace-search"
            type="search"
            value={filters.search}
            onChange={(event) =>
              setFilters((previous) => ({ ...previous, search: event.target.value }))
            }
            placeholder="Search check ID, title, resource, evidence…"
          />
        </div>

        <div className="report-workspace__field">
          <label htmlFor="severity-filter">Severity</label>
          <select
            id="severity-filter"
            value={filters.severity}
            onChange={(event) =>
              setFilters((previous) => ({
                ...previous,
                severity: event.target.value as WorkspaceFilterState["severity"],
              }))
            }
          >
            <option value="all">All severities</option>
            {SEVERITY_FILTER_OPTIONS.map((severity) => (
              <option key={severity} value={severity}>
                {capitalize(severity)}
              </option>
            ))}
          </select>
        </div>

        <div className="report-workspace__field">
          <label htmlFor="category-filter">Category</label>
          <select
            id="category-filter"
            value={filters.category}
            onChange={(event) =>
              setFilters((previous) => ({
                ...previous,
                category: event.target.value as WorkspaceFilterState["category"],
              }))
            }
          >
            <option value="all">All categories</option>
            {categoryOptions.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </div>

        <div className="report-workspace__field">
          <label htmlFor="resource-kind-filter">Resource kind</label>
          <select
            id="resource-kind-filter"
            value={filters.resourceKind}
            onChange={(event) =>
              setFilters((previous) => ({ ...previous, resourceKind: event.target.value }))
            }
          >
            <option value="all">All resource kinds</option>
            {resourceKindOptions.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </div>

        {isComparison ? (
          <div className="report-workspace__field">
            <label htmlFor="comparison-status-filter">Comparison status</label>
            <select
              id="comparison-status-filter"
              value={filters.comparisonStatus}
              onChange={(event) =>
                setFilters((previous) => ({
                  ...previous,
                  comparisonStatus: event.target.value as WorkspaceFilterState["comparisonStatus"],
                }))
              }
            >
              <option value="all">All statuses</option>
              {COMPARISON_STATUS_ORDER.map((status) => (
                <option key={status} value={status}>
                  {COMPARISON_STATUS_LABELS[status]}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="report-workspace__field">
          <label htmlFor="sort-order">Sort by</label>
          <select id="sort-order" value={sortOption} onChange={(event) => handleSortChange(event.target.value)}>
            {sortOptionsForMode.map((option) => (
              <option key={option} value={option}>
                {SORT_LABELS[option]}
              </option>
            ))}
          </select>
        </div>

        <button type="button" className="report-workspace__clear" onClick={clearFilters}>
          Clear filters
        </button>
      </form>

      <p className="report-workspace__count" aria-live="polite">
        Showing {filteredCount} of {totalCount} findings.
      </p>

      {sorted.length === 0 ? (
        <p className="report-workspace__empty">
          No findings match your current search and filters. Clear them to see all {totalCount} findings.
        </p>
      ) : (
        <ul className="report-workspace__results">
          {sorted.map((item) => {
            // Every item's finding originates from `items` (filterWorkspaceItems/
            // sortWorkspaceItems only select and reorder references, never
            // clone), so this lookup always hits -- see useFindingKeys above.
            // The non-null assertion reflects that proven invariant, not an
            // unchecked assumption.
            const key = findingKeys.get(item.finding)!;
            return <FindingRow key={key} finding={item.finding} status={item.status} />;
          })}
        </ul>
      )}
    </div>
  );
}
