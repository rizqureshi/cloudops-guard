/**
 * Zod schema for `../../data/check-catalogue.json`, this project's own
 * data file (not untrusted user input, unlike `../report-import`'s
 * schemas) -- but still validated strictly, so a malformed entry, a
 * duplicate check ID, an invalid platform/severity, or a missing required
 * text field fails loudly at build/test time rather than silently shipping
 * broken or incomplete catalogue content.
 */

import { z } from "zod";

const checkPlatformSchema = z.enum(["kubernetes", "gitlab"]);
const severitySchema = z.enum(["critical", "high", "medium", "low"]);

/** A check ID matching either family's published pattern, e.g. "K8S-RES-001", "GL-CI-001". */
const checkIdSchema = z.string().regex(/^(K8S|GL)-[A-Z]+-\d{3}$/, "must be a valid check ID (e.g. K8S-RES-001)");

const nonEmptyText = z.string().min(1, "must not be empty");

export const checkCatalogueEntrySchema = z.strictObject({
  checkId: checkIdSchema,
  platform: checkPlatformSchema,
  title: nonEmptyText,
  severity: severitySchema,
  triggerCondition: nonEmptyText,
  evidenceDescription: nonEmptyText,
  impact: nonEmptyText,
  recommendation: nonEmptyText,
  limitations: nonEmptyText.optional(),
});

export const checkCatalogueSchema = z.array(checkCatalogueEntrySchema).superRefine((entries, ctx) => {
  const seen = new Set<string>();
  for (const [index, entry] of entries.entries()) {
    if (seen.has(entry.checkId)) {
      ctx.addIssue({
        code: "custom",
        message: `duplicate check ID: ${entry.checkId}`,
        path: [index, "checkId"],
      });
    }
    seen.add(entry.checkId);
  }
});
