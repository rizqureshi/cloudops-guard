/**
 * The shared contact-form contract (Phase 3I): a neutral, strictly validated
 * schema used by both the browser form (`ContactForm.tsx`) and the Worker
 * endpoint (`../../../worker/contact.ts`). This module has zero dependency
 * on `report-import`, `report-workspace`, `local-report-explorer`,
 * `comparison`, `executive-summary`, `demo-controller`, or any synthetic/
 * check-catalogue data -- see the isolation test
 * (`tests/unit/contact-form/isolation.test.ts`), which proves this by
 * inspecting real imports, not a hand-maintained list.
 *
 * Every limit below is enforced authoritatively server-side by the Worker,
 * which parses with this exact schema -- the browser form's own `maxLength`
 * attributes are a UX convenience only, never the source of truth.
 */

import { z } from "zod";

export const CONTACT_NAME_MAX_LENGTH = 100;
export const CONTACT_EMAIL_MAX_LENGTH = 254;
export const CONTACT_COMPANY_MAX_LENGTH = 200;
export const CONTACT_MESSAGE_MAX_LENGTH = 2000;
export const CONTACT_TURNSTILE_TOKEN_MAX_LENGTH = 2048;

export const CONTACT_FORM_TYPES = ["pilot_request", "feedback"] as const;
export type ContactFormType = (typeof CONTACT_FORM_TYPES)[number];

/**
 * A single-line field (name, work email, company) must contain no control
 * character at all -- not even a tab or line break. Excludes the full C0
 * range (`\x00`-`\x1F`) and DEL (`\x7F`).
 */
// eslint-disable-next-line no-control-regex -- deliberately matching C0 control characters/DEL to reject them.
const SINGLE_LINE_PATTERN = /^[^\x00-\x1F\x7F]*$/;

/**
 * The message field may retain ordinary line breaks (`\n`, `\r`) and tabs
 * (`\x09`), since those are normal in free-form prose, but every other C0
 * control character and DEL are still rejected.
 */
// eslint-disable-next-line no-control-regex -- deliberately matching C0 control characters/DEL (except tab/CR/LF) to reject them.
const MESSAGE_PATTERN = /^[^\x00-\x08\x0B\x0C\x0E-\x1F\x7F]*$/;

function singleLineField(maxLength: number) {
  return z
    .string()
    .trim()
    .min(1)
    .max(maxLength)
    .refine((value) => SINGLE_LINE_PATTERN.test(value), {
      message: "must not contain a line break, tab, or other control character",
    });
}

const optionalSingleLineField = z
  .string()
  .trim()
  .max(CONTACT_COMPANY_MAX_LENGTH)
  .refine((value) => SINGLE_LINE_PATTERN.test(value), {
    message: "must not contain a line break, tab, or other control character",
  })
  .optional();

export const contactFormSchema = z.strictObject({
  formType: z.enum(CONTACT_FORM_TYPES),
  name: singleLineField(CONTACT_NAME_MAX_LENGTH),
  workEmail: z.string().trim().max(CONTACT_EMAIL_MAX_LENGTH).pipe(z.email()),
  company: optionalSingleLineField,
  // The stored value is never trimmed or rewritten -- a message's
  // original ordinary whitespace and line breaks are preserved exactly as
  // submitted whenever it contains meaningful (non-whitespace) content.
  // `.trim().length > 0` is a validation check only, not a transform: an
  // all-whitespace message (spaces, tabs, line breaks with nothing else)
  // is rejected outright rather than silently accepted as "empty but
  // technically non-empty" content.
  message: z
    .string()
    .max(CONTACT_MESSAGE_MAX_LENGTH)
    .min(1)
    .refine((value) => MESSAGE_PATTERN.test(value), {
      message: "must not contain an unsupported control character",
    })
    .refine((value) => value.trim().length > 0, {
      message: "must contain non-whitespace content",
    }),
  // A literal `true` only -- the string `"true"`, `1`, or any other truthy
  // value is rejected, never coerced.
  consent: z.literal(true),
  turnstileToken: z.string().min(1).max(CONTACT_TURNSTILE_TOKEN_MAX_LENGTH),
});

export type ContactFormInput = z.infer<typeof contactFormSchema>;

/**
 * Parses `value` (already-decoded JSON, of unknown shape) against the
 * contract. Never truncates, coerces, or silently drops a field -- an
 * out-of-range value, an unknown field, or a wrong type is a rejection,
 * not a repair.
 */
export function parseContactFormInput(value: unknown) {
  return contactFormSchema.safeParse(value);
}
