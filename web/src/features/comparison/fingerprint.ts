/**
 * Collision-safe finding fingerprints for comparison matching.
 *
 * Each fingerprint is `JSON.stringify` applied to a fixed-length tuple of
 * exactly the approved identity fields (see the per-platform functions
 * below) -- never a delimiter-joined string. Report content is untrusted:
 * a plain join (e.g. `namespace + "/" + resourceName`) risks two different
 * field combinations producing the same joined string if either field can
 * contain the delimiter. `JSON.stringify` has no such risk, because a
 * string element is always wrapped in quotes with its own quotes/
 * backslashes escaped as part of the JSON grammar itself, so the tuple's
 * structure (element count and boundaries) survives regardless of what
 * characters any field contains.
 *
 * Deliberately excludes severity, title, evidence, impact, recommendation,
 * auto-remediation status, and audit timestamp -- none of those identify
 * *which* underlying condition a finding is about; including any of them
 * would make a real, unrelated wording/timestamp difference look like a
 * different finding.
 */

import type { NormalizedFinding, NormalizedGitLabFinding, NormalizedKubernetesFinding } from "../report-import";

export type Fingerprint = string;

function kubernetesFingerprint(finding: NormalizedKubernetesFinding): Fingerprint {
  return JSON.stringify([
    finding.checkId,
    finding.clusterContext,
    finding.namespace,
    finding.resourceKind,
    finding.resourceName,
    finding.containerName,
  ]);
}

/**
 * `GL-CI-001` uses the image reference itself as `resourceName`, so a
 * changed image reference for the same job produces a different
 * fingerprint here -- appearing as one `resolved` result (the old image
 * reference) plus one `new` result (the new image reference), never as a
 * `persistent` result whose evidence merely changed. This is an approved,
 * documented limitation (see the milestone document and
 * `web/README.md`), not a bug: fixing it would require tracking image
 * references independently of the report contract's `resourceName` field.
 */
function gitlabFingerprint(finding: NormalizedGitLabFinding): Fingerprint {
  return JSON.stringify([
    finding.checkId,
    finding.projectPath,
    finding.resourceKind,
    finding.resourceName,
    finding.jobName,
  ]);
}

export function computeFingerprint(finding: NormalizedFinding): Fingerprint {
  return finding.platform === "kubernetes" ? kubernetesFingerprint(finding) : gitlabFingerprint(finding);
}
