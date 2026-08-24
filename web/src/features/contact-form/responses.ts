/**
 * The `/api/contact` response contract, shared by the Worker
 * (`../../../worker/contact.ts`) and the browser form. Contains no server
 * binding or secret -- just fixed, sanitized shapes -- so it is safe to
 * import from client code without pulling any Worker-only dependency into
 * the browser bundle.
 */

export type ContactApiErrorCode =
  | "invalid_request"
  | "origin_rejected"
  | "payload_too_large"
  | "unsupported_content_type"
  | "unsupported_content_encoding"
  | "verification_failed"
  | "method_not_allowed"
  | "not_found"
  | "temporarily_unavailable";

export interface ContactApiSuccessResponse {
  readonly ok: true;
}

export interface ContactApiErrorResponse {
  readonly ok: false;
  readonly error: ContactApiErrorCode;
  /**
   * Present only for `temporarily_unavailable`: a fallback destination
   * address the client may offer as a `mailto:` link. Never trust this
   * value as already-validated -- the client re-validates it as a plain
   * email address before using it (see `submitContactForm.ts`).
   */
  readonly fallbackEmail?: string;
}

export type ContactApiResponse = ContactApiSuccessResponse | ContactApiErrorResponse;
