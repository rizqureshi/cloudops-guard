import { useMemo, useState } from "react";

import type { NormalizedFinding, NormalizedWebReport } from "../report-import";
import {
  DEFAULT_FILTER_STATE,
  distinctCategories,
  distinctResourceKinds,
  filterFindings,
  SEVERITY_FILTER_OPTIONS,
  type WorkspaceFilterState,
} from "./filtering";
import { FindingRow } from "./FindingRow";
import "./report-workspace.css";
import { sortFindings, SORT_OPTIONS, type SortOption } from "./sorting";

interface ReportWorkspaceProps {
  readonly report: NormalizedWebReport;
}

const SORT_LABELS: Readonly<Record<SortOption, string>> = {
  severity: "Severity",
  checkId: "Check ID",
  resource: "Resource",
};

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * Assigns each finding a stable React key derived from its position in the
 * *original*, unfiltered `report.findings` array -- never from a
 * delimiter-joined combination of displayed fields, which would collide
 * for two otherwise-identical findings that differ only in a field not
 * included in the join (e.g. two findings for the same check ID, resource
 * kind, resource name, and container name, but in different namespaces).
 *
 * Built once per `findings` array via `useMemo`, as a `Map` keyed by
 * **object reference** (not content), giving O(1) lookups during render --
 * never `indexOf` (which would re-scan the array on every finding, every
 * render, an O(n^2) cost that matters as reports approach the 10,000-
 * finding limit). This works because `filterFindings`/`sortFindings` only
 * ever select and reorder references into the original array -- they never
 * clone or recreate finding objects -- so every finding encountered while
 * rendering a filtered/sorted view is reference-identical to one entry in
 * `report.findings`, and the key it's given here remains stable no matter
 * how the user filters or re-sorts. Generalized over `NormalizedFinding`
 * (the Kubernetes/GitLab union) rather than the Kubernetes-only finding
 * type, since Phase 3E generalized `ReportWorkspace` to render both
 * platforms; the property (stable per-occurrence, reference-keyed, O(1)
 * lookup, no deduplication) is unaffected by which platform's findings are
 * passed in.
 */
function useFindingKeys(
  findings: readonly NormalizedFinding[],
): ReadonlyMap<NormalizedFinding, number> {
  return useMemo(() => {
    const keys = new Map<NormalizedFinding, number>();
    findings.forEach((finding, index) => {
      keys.set(finding, index);
    });
    return keys;
  }, [findings]);
}

/**
 * The shared report-workspace island: everything is kept in React memory
 * only (`useState`/`useMemo`) -- no `localStorage`/`sessionStorage`/
 * IndexedDB/cookies/service worker/URL persistence, and no `fetch`/
 * `XMLHttpRequest`/`WebSocket`/`sendBeacon` is ever called here. `report`
 * is already a validated `NormalizedWebReport` (see
 * `../report-import/parsers.ts`) -- this component only filters, sorts,
 * and displays it.
 *
 * Generalized to the full `NormalizedWebReport` union in Phase 3E: Phase
 * 3D deliberately narrowed this prop to `NormalizedKubernetesReport`
 * because, at the time, the component only rendered a Kubernetes target
 * identity correctly. Phase 3E adds a GitLab-specific identity branch (see
 * the `report.platform === "gitlab"` block below) so both discriminated
 * report variants now render their own correct target fields -- this is a
 * deliberate generalization, not a claim that rendering "already worked"
 * for GitLab before this phase.
 */
export function ReportWorkspace({ report }: ReportWorkspaceProps) {
  const [filters, setFilters] = useState<WorkspaceFilterState>(DEFAULT_FILTER_STATE);
  const [sortOption, setSortOption] = useState<SortOption>("severity");

  // Widening `report.findings` (typed per-branch by the discriminated union)
  // to the flat `NormalizedFinding` union once here lets every helper below
  // work uniformly across both platforms; readonly arrays are covariant, so
  // this is a safe upcast, not a runtime transformation.
  const findings: readonly NormalizedFinding[] = report.findings;

  const findingKeys = useFindingKeys(findings);

  const resourceKindOptions = useMemo(() => distinctResourceKinds(findings), [findings]);
  const categoryOptions = useMemo(() => distinctCategories(findings), [findings]);

  const filtered = useMemo(() => filterFindings(findings, filters), [findings, filters]);
  const sorted = useMemo(() => sortFindings(filtered, sortOption), [filtered, sortOption]);

  const totalCount = findings.length;
  const filteredCount = sorted.length;

  function clearFilters(): void {
    setFilters(DEFAULT_FILTER_STATE);
  }

  return (
    <div className="report-workspace">
      <p className="status-label status-label--neutral report-workspace__badge">Synthetic demonstration</p>

      <div className="report-workspace__identity">
        {report.platform === "kubernetes" ? (
          <>
            <p>
              <span className="report-workspace__identity-label">Platform</span> Kubernetes
            </p>
            <p>
              <span className="report-workspace__identity-label">Cluster context</span>{" "}
              {report.target.clusterContext}
            </p>
            <p>
              <span className="report-workspace__identity-label">Namespace filter</span>{" "}
              {report.target.namespaceFilter === null ? "All namespaces" : report.target.namespaceFilter}
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
              {report.target.gitlabUrl}
            </p>
            <p>
              <span className="report-workspace__identity-label">Project ID</span> {report.target.projectId}
            </p>
            <p>
              <span className="report-workspace__identity-label">Project path</span>{" "}
              {report.target.projectPath}
            </p>
            <p>
              <span className="report-workspace__identity-label">Default branch</span>{" "}
              {report.target.defaultBranch}
            </p>
          </>
        )}
        <p>
          <span className="report-workspace__identity-label">Report generated</span>{" "}
          <time dateTime={report.generatedAt}>{report.generatedAt}</time>
        </p>
      </div>

      <div className="report-workspace__summary" aria-label="Full report severity totals">
        <span className="status-label status-label--critical">Critical {report.summary.critical}</span>
        <span className="status-label status-label--high">High {report.summary.high}</span>
        <span className="status-label status-label--medium">Medium {report.summary.medium}</span>
        <span className="status-label status-label--low">Low {report.summary.low}</span>
        <span className="status-label status-label--neutral">Total {report.summary.total}</span>
      </div>

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

        <div className="report-workspace__field">
          <label htmlFor="sort-order">Sort by</label>
          <select
            id="sort-order"
            value={sortOption}
            onChange={(event) => setSortOption(event.target.value as SortOption)}
          >
            {SORT_OPTIONS.map((option) => (
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
          {sorted.map((finding) => {
            // Every element of `sorted` originates from `report.findings`
            // (filterFindings/sortFindings only select and reorder
            // references, never clone), so this lookup always hits -- see
            // useFindingKeys above. The non-null assertion reflects that
            // proven invariant, not an unchecked assumption.
            const key = findingKeys.get(finding)!;
            return <FindingRow key={key} finding={finding} />;
          })}
        </ul>
      )}
    </div>
  );
}
