export { compareGitLabReports, compareKubernetesReports } from "./compare";
export { ComparisonError, type ComparisonErrorCode } from "./errors";
export { computeFingerprint, type Fingerprint } from "./fingerprint";
export {
  COMPARISON_STATUS_ORDER,
  type ComparisonFindingResult,
  type ComparisonResult,
  type ComparisonStatus,
  type ComparisonStatusTotals,
  type GitLabComparisonResult,
  type KubernetesComparisonResult,
} from "./types";
export { assertComparable } from "./validation";
