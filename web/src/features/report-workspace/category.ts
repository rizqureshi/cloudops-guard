/**
 * Pure, stable derivation of a display category from a check ID, by prefix.
 *
 * This is display-only: the released report contract has no category field
 * (see CLAUDE.md, "web application invariants" -- the Python models are not
 * changed to accommodate the web UI), so category is always recomputed from
 * `checkId` rather than trusted from report data.
 *
 * Only the currently implemented Kubernetes check families are mapped here,
 * per Phase 3D's scope. An unrecognized prefix (e.g. a future GitLab
 * `GL-*` check ID, once Phase 3E wires up the GitLab demo) falls back to
 * "Other" rather than throwing, so this function stays safe to call on any
 * finding without the workspace needing to know which platform it belongs
 * to.
 */

export type FindingCategory = "Resource management" | "Image security" | "Reliability" | "Other";

const CATEGORY_BY_PREFIX: ReadonlyArray<readonly [prefix: string, category: FindingCategory]> = [
  ["K8S-RES-", "Resource management"],
  ["K8S-IMG-", "Image security"],
  ["K8S-REL-", "Reliability"],
];

export function deriveCategory(checkId: string): FindingCategory {
  for (const [prefix, category] of CATEGORY_BY_PREFIX) {
    if (checkId.startsWith(prefix)) {
      return category;
    }
  }
  return "Other";
}
