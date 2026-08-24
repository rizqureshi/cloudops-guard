/**
 * Plain-text email delivery through the Cloudflare Email Service's
 * structured `EMAIL.send({ to, from, subject, text })` binding (Phase 3I)
 * -- never the legacy raw-MIME API, never an external email SDK.
 *
 * Anti-open-relay guarantee: `to`, `from`, and `subject` are always taken
 * from trusted Worker configuration (`env.CONTACT_TO_EMAIL`/
 * `env.CONTACT_FROM_EMAIL`) or the fixed `SUBJECT_BY_FORM_TYPE` table
 * below -- never from the submitted request. No visitor-supplied value can
 * reach a header, a recipient, a CC/BCC, or an attachment; the visitor's
 * name/work email/company/message are placed only in the plain-text body.
 * No acknowledgement email is ever sent to the visitor's own address.
 */

import type { ContactFormType } from "../src/features/contact-form/contract";
import type { EmailBinding } from "./env";

const SUBJECT_BY_FORM_TYPE: Readonly<Record<ContactFormType, string>> = {
  pilot_request: "CloudOps Guard pilot request",
  feedback: "CloudOps Guard feedback",
};

export interface SendContactEmailParams {
  readonly email: EmailBinding;
  readonly toEmail: string;
  readonly fromEmail: string;
  readonly formType: ContactFormType;
  readonly name: string;
  readonly workEmail: string;
  readonly company: string | undefined;
  readonly message: string;
}

function buildPlainTextBody(params: SendContactEmailParams): string {
  const lines = [
    `Form: ${params.formType}`,
    `Name: ${params.name}`,
    `Work email: ${params.workEmail}`,
    params.company !== undefined ? `Company: ${params.company}` : null,
    "",
    "Message:",
    params.message,
  ];
  return lines.filter((line): line is string => line !== null).join("\n");
}

/** `true` on success, `false` on any binding/configuration failure -- never throws, never logs. */
export async function sendContactEmail(params: SendContactEmailParams): Promise<boolean> {
  try {
    await params.email.send({
      to: params.toEmail,
      from: params.fromEmail,
      subject: SUBJECT_BY_FORM_TYPE[params.formType],
      text: buildPlainTextBody(params),
    });
    return true;
  } catch {
    return false;
  }
}
