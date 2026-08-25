import "./executive-summary.css";
import { REPORT_SOURCE_LABELS, type ReportSource } from "../report-workspace/reportSource";
import type { ExecutiveSummary as ExecutiveSummaryData } from "./types";

interface ExecutiveSummaryProps {
  readonly summary: ExecutiveSummaryData;
  /**
   * A narrow, application-controlled discriminant -- never derived from
   * report content -- selecting the visible source indicator and the
   * "synthetic"/plain "scan state" wording below via `REPORT_SOURCE_LABELS`
   * (`../report-workspace/reportSource.ts`). `DemoController` always
   * passes `"synthetic"`; the local report explorer always passes
   * `"local"`. There is no prop that accepts an arbitrary label string.
   */
  readonly source: ReportSource;
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * A deterministic, template-driven executive summary -- not AI-generated,
 * and never claiming a health, risk, maturity, or completeness score (see
 * `summary.ts` for the pure calculation this renders). Every value comes
 * from ordinary React text, `<dl>`, and semantic headings/lists -- never
 * `dangerouslySetInnerHTML`, Markdown, or HTML injection -- and no
 * report-derived string ever becomes an `href`.
 */
export function ExecutiveSummary({ summary, source }: ExecutiveSummaryProps) {
  const totalActiveFindings =
    summary.mode === "single" ? summary.summary.total : summary.affectedCategories.reduce((sum, c) => sum + c.count, 0);
  // "synthetic scan state" is only accurate for demo data; a local report
  // is a real report the visitor selected, so the scope/limitations copy
  // below must not call it "synthetic".
  const scanStateNoun = source === "synthetic" ? "synthetic scan state" : "scan state";

  return (
    <div className="executive-summary">
      <p className="status-label status-label--neutral executive-summary__badge">
        {REPORT_SOURCE_LABELS[source]}
      </p>

      <section aria-labelledby="exec-summary-target-heading">
        <h2 id="exec-summary-target-heading">Target</h2>
        <dl className="executive-summary__detail-list">
          <dt>Platform</dt>
          <dd>{summary.targetInfo.platform === "kubernetes" ? "Kubernetes" : "GitLab"}</dd>
          {summary.targetInfo.platform === "kubernetes" ? (
            <>
              <dt>Cluster context</dt>
              <dd>{summary.targetInfo.target.clusterContext}</dd>
              <dt>Namespace filter</dt>
              <dd>
                {summary.targetInfo.target.namespaceFilter === null
                  ? "All namespaces"
                  : summary.targetInfo.target.namespaceFilter}
              </dd>
            </>
          ) : (
            <>
              {/* Plain text, never a link -- see ReportWorkspace.tsx for the same rule. */}
              <dt>GitLab instance URL</dt>
              <dd>{summary.targetInfo.target.gitlabUrl}</dd>
              <dt>Project ID</dt>
              <dd>{summary.targetInfo.target.projectId}</dd>
              <dt>Project path</dt>
              <dd>{summary.targetInfo.target.projectPath}</dd>
              <dt>Default branch</dt>
              <dd>{summary.targetInfo.target.defaultBranch}</dd>
            </>
          )}
          {summary.mode === "single" ? (
            <>
              <dt>Audit timestamp</dt>
              <dd>
                <time dateTime={summary.generatedAt}>{summary.generatedAt}</time>
              </dd>
            </>
          ) : (
            <>
              <dt>Earlier scan</dt>
              <dd>
                <time dateTime={summary.olderGeneratedAt}>{summary.olderGeneratedAt}</time>
              </dd>
              <dt>Later scan</dt>
              <dd>
                <time dateTime={summary.newerGeneratedAt}>{summary.newerGeneratedAt}</time>
              </dd>
            </>
          )}
        </dl>
      </section>

      <section aria-labelledby="exec-summary-totals-heading">
        <h2 id="exec-summary-totals-heading">
          {summary.mode === "single" ? "Total and severity counts" : "Newer scan totals"}
        </h2>
        <div className="executive-summary__severity-bar" role="group" aria-label="Severity totals">
          <span className="status-label status-label--critical">
            Critical {summary.mode === "single" ? summary.summary.critical : summary.newerSummary.critical}
          </span>
          <span className="status-label status-label--high">
            High {summary.mode === "single" ? summary.summary.high : summary.newerSummary.high}
          </span>
          <span className="status-label status-label--medium">
            Medium {summary.mode === "single" ? summary.summary.medium : summary.newerSummary.medium}
          </span>
          <span className="status-label status-label--low">
            Low {summary.mode === "single" ? summary.summary.low : summary.newerSummary.low}
          </span>
          <span className="status-label status-label--neutral">
            Total {summary.mode === "single" ? summary.summary.total : summary.newerSummary.total}
          </span>
        </div>
        {summary.mode === "comparison" ? (
          <>
            <p className="executive-summary__note">
              These severity totals describe the newer/current scan only. Resolved findings from the
              earlier scan are never merged into them; comparison-status totals are shown separately
              below.
            </p>
            <div className="executive-summary__severity-bar" role="group" aria-label="Comparison status totals">
              <span className="status-label status-label--new">New {summary.statusTotals.new}</span>
              <span className="status-label status-label--persistent">
                Persistent {summary.statusTotals.persistent}
              </span>
              <span className="status-label status-label--resolved">
                Resolved {summary.statusTotals.resolved}
              </span>
            </div>
          </>
        ) : null}
      </section>

      <section aria-labelledby="exec-summary-categories-heading">
        <h2 id="exec-summary-categories-heading">Affected categories</h2>
        {summary.mode === "comparison" ? (
          <p className="executive-summary__note">
            Based on currently active findings only (new and persistent) -- resolved findings are
            excluded here, even though their totals are shown above.
          </p>
        ) : null}
        {summary.affectedCategories.length === 0 ? (
          <p className="executive-summary__empty">
            {totalActiveFindings === 0
              ? `No findings are present in this ${scanStateNoun}. This reflects only the checks that produced a finding here -- it is not a claim that the target is healthy, safe, compliant, or comprehensively audited.`
              : "No affected categories to report."}
          </p>
        ) : (
          <ul className="executive-summary__category-list">
            {summary.affectedCategories.map((entry) => (
              <li key={entry.category} className="executive-summary__category-item">
                <span className={`status-label status-label--${entry.highestSeverity}`}>
                  {capitalize(entry.highestSeverity)}
                </span>
                <span className="executive-summary__category-name">{entry.category}</span>
                <span className="executive-summary__category-count">
                  {entry.count} finding{entry.count === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="exec-summary-recommendations-heading">
        <h2 id="exec-summary-recommendations-heading">Prioritized recommendations</h2>
        {summary.recommendations.length === 0 ? (
          <p className="executive-summary__empty">
            No recommendations to prioritize for this {scanStateNoun}.
          </p>
        ) : (
          <ol className="executive-summary__recommendation-list">
            {summary.recommendations.map((item) => (
              // `recommendation` alone is a safe React key: `buildRecommendations`
              // (summary.ts) already deduplicates by exact recommendation text
              // before returning this list, so it's unique here by construction.
              // A concatenated `checkId + recommendation` key is collision-prone --
              // checkId "A" + recommendation "BC" and checkId "AB" + recommendation
              // "C" both produce "ABC" -- with no delimiter to prevent it.
              <li key={item.recommendation} className="executive-summary__recommendation-item">
                <div className="executive-summary__recommendation-meta">
                  <span className={`status-label status-label--${item.severity}`}>
                    {capitalize(item.severity)}
                  </span>
                  <span className="executive-summary__check-id">{item.checkId}</span>
                  <span className="executive-summary__category-name">{item.category}</span>
                </div>
                <p className="executive-summary__recommendation-text">{item.recommendation}</p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-labelledby="exec-summary-scope-heading">
        <h2 id="exec-summary-scope-heading">Scope and limitations</h2>
        <ul className="executive-summary__scope-list">
          <li>
            The underlying report contract records findings, not a complete per-check execution
            ledger -- the absence of a finding for a check is not proof that the check ran and
            passed.
          </li>
          <li>
            This summary reflects only the findings present in the supplied {scanStateNoun}
            {summary.mode === "comparison" ? "s" : ""}, not a live audit.
          </li>
          <li>
            The implemented Kubernetes and GitLab checks do not provide complete coverage of either
            platform.
          </li>
          <li>No live Kubernetes cluster or GitLab instance was contacted to produce this page.</li>
          <li>This summary is deterministic and template-driven -- it is not AI-generated.</li>
        </ul>
      </section>
    </div>
  );
}
