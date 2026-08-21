/**
 * Limits enforced on any report a visitor selects (Phase 3G import UI)
 * or that these schemas/parsers accept. See ../../../../docs/milestones/
 * v0.3.0-interactive-web-demo.md, section F, for the product-level rationale.
 */

/** Maximum accepted size, in bytes, of a selected report file. */
export const MAX_REPORT_FILE_BYTES = 10 * 1024 * 1024;

/** Maximum number of findings a single accepted report may contain. */
export const MAX_FINDINGS_PER_REPORT = 10_000;
