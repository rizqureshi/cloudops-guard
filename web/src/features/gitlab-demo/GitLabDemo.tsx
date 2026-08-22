import { useState } from "react";

import type { NormalizedGitLabReport } from "../report-import";
import { ReportWorkspace } from "../report-workspace";
import "./gitlab-demo.css";

/**
 * Stable, application-defined scenario identifiers -- never derived from
 * report content. Kept as a fixed union (not `string`) so an unrecognized
 * id cannot be selected or silently coerced.
 */
export type GitLabScenarioId = "unprotected-default-branch" | "protected-with-permissive-rules";

export interface GitLabScenario {
  readonly id: GitLabScenarioId;
  readonly label: string;
  readonly report: NormalizedGitLabReport;
}

interface GitLabDemoProps {
  readonly scenarios: readonly [GitLabScenario, GitLabScenario];
}

/**
 * One React island containing both the scenario selector and the report
 * workspace, so `/demo/gitlab` hydrates exactly one island (see
 * gitlab.astro). This is deliberately *not* comparison functionality: only
 * one scenario's `NormalizedGitLabReport` is ever passed to
 * `ReportWorkspace` at a time, nothing is diffed, and no finding is ever
 * labeled new/persistent/resolved. Switching the selector changes which
 * fixed, independent synthetic example is shown -- see the note rendered
 * below the selector.
 *
 * Passing `key={activeScenario.id}` to `ReportWorkspace` forces React to
 * unmount and remount it on every scenario change, rather than patching
 * the existing instance in place. That guarantees the workspace's internal
 * `useState` (search, filters, sort option) and any expanded `<details>`
 * DOM state from the previous scenario cannot leak into the newly selected
 * one -- a fresh mount always starts from `ReportWorkspace`'s own default
 * state.
 */
export function GitLabDemo({ scenarios }: GitLabDemoProps) {
  const [scenarioId, setScenarioId] = useState<GitLabScenarioId>(scenarios[0].id);
  const activeScenario = scenarios.find((scenario) => scenario.id === scenarioId) ?? scenarios[0];

  return (
    <div className="gitlab-demo">
      <div className="gitlab-demo__selector">
        <label htmlFor="gitlab-scenario-select">Synthetic scan state</label>
        <select
          id="gitlab-scenario-select"
          value={scenarioId}
          onChange={(event) => setScenarioId(event.target.value as GitLabScenarioId)}
        >
          {scenarios.map((scenario) => (
            <option key={scenario.id} value={scenario.id}>
              {scenario.label}
            </option>
          ))}
        </select>
        <p className="gitlab-demo__selector-note">
          This selector switches between two independent, fixed synthetic examples of the same
          fictional project. It does not compare the two states against each other, and no finding
          is ever labeled new, persistent, or resolved.
        </p>
      </div>
      <ReportWorkspace report={activeScenario.report} key={activeScenario.id} />
    </div>
  );
}
