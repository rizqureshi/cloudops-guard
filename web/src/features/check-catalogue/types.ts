/**
 * The check catalogue's own entry shape.
 *
 * Deliberately not `NormalizedFinding` from `../report-import`: a catalogue
 * entry describes a *check* in the abstract (what condition it looks for,
 * what it means, how to fix it), not one finding instance from one report
 * (a specific resource, evidence string, or timestamp). `platform` and
 * `severity` reuse the same value sets as the report-import types so the
 * two stay in lockstep, but this type is otherwise independent.
 */

import type { Severity } from "../report-import";

export type CheckPlatform = "kubernetes" | "gitlab";

export interface CheckCatalogueEntry {
  readonly checkId: string;
  readonly platform: CheckPlatform;
  readonly title: string;
  readonly severity: Severity;
  readonly triggerCondition: string;
  readonly evidenceDescription: string;
  readonly impact: string;
  readonly recommendation: string;
  /** Present only for a check with a documented, important qualification. */
  readonly limitations?: string | undefined;
}
