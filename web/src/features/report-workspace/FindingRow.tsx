import type { ComparisonStatus } from "../comparison/types";
import type { NormalizedFinding } from "../report-import";

interface FindingRowProps {
  readonly finding: NormalizedFinding;
  /** `null` (or omitted) outside comparison mode -- see ReportWorkspace.tsx. */
  readonly status?: ComparisonStatus | null;
}

/**
 * One finding, as a keyboard-accessible native `<details>` disclosure.
 * Every report-derived string is rendered as ordinary React text -- never
 * `dangerouslySetInnerHTML`, HTML injection, or Markdown rendering. Report
 * content is untrusted display data (see CLAUDE.md, "web application
 * invariants").
 *
 * `status` (Phase 3F) renders as its own `status-label` badge alongside
 * severity, carrying its own visible text ("New"/"Persistent"/"Resolved")
 * -- comparison status is conveyed through that text, never through colour
 * alone (see the `.status-label--new`/`--persistent`/`--resolved` rules in
 * global.css).
 */
export function FindingRow({ finding, status = null }: FindingRowProps) {
  const secondaryIdentity = finding.platform === "kubernetes" ? finding.containerName : finding.jobName;
  const groupIdentity = finding.platform === "kubernetes" ? finding.namespace : finding.projectPath;

  return (
    <li className="finding-row">
      <details className="finding-row__details">
        <summary className="finding-row__summary">
          <span className="finding-row__disclosure-icon" aria-hidden="true" />
          {status ? (
            <span className={`status-label status-label--${status} finding-row__status`}>
              {statusLabel(status)}
            </span>
          ) : null}
          <span className={`status-label status-label--${finding.severity} finding-row__severity`}>
            {severityLabel(finding.severity)}
          </span>
          <span className="finding-row__check-id">{finding.checkId}</span>
          <span className="finding-row__title">{finding.title}</span>
          <span className="finding-row__resource">
            {groupIdentity} &middot; {finding.resourceKind} {finding.resourceName}
            {secondaryIdentity ? ` · ${secondaryIdentity}` : ""}
          </span>
        </summary>
        <div className="finding-row__body">
          <dl className="finding-row__detail-list">
            <dt>Evidence</dt>
            <dd>{finding.evidence}</dd>
            <dt>Impact</dt>
            <dd>{finding.impact}</dd>
            <dt>Recommendation</dt>
            <dd>{finding.recommendation}</dd>
            <dt>Auto-remediable</dt>
            <dd>{finding.autoRemediable ? "Yes" : "No"}</dd>
            <dt>Audited at</dt>
            <dd>
              <time dateTime={finding.auditedAt}>{finding.auditedAt}</time>
            </dd>
          </dl>
        </div>
      </details>
    </li>
  );
}

function severityLabel(severity: string): string {
  return severity.charAt(0).toUpperCase() + severity.slice(1);
}

function statusLabel(status: ComparisonStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}
