import { useId, useState, type SubmitEvent } from "react";

import "./contact-form.css";
import {
  CONTACT_COMPANY_MAX_LENGTH,
  CONTACT_MESSAGE_MAX_LENGTH,
  CONTACT_NAME_MAX_LENGTH,
  contactFormSchema,
  type ContactFormType,
} from "./contract";
import { submitContactForm } from "./submitContactForm";
import { useTurnstile } from "./useTurnstile";

export interface ContactFormProps {
  readonly formType: ContactFormType;
  /** Read from `PUBLIC_TURNSTILE_SITE_KEY` by the `.astro` page at build time. */
  readonly siteKey: string;
}

interface FieldState {
  readonly name: string;
  readonly workEmail: string;
  readonly company: string;
  readonly message: string;
  readonly consent: boolean;
}

const EMPTY_FIELDS: FieldState = { name: "", workEmail: "", company: "", message: "", consent: false };

/**
 * A fixed, sanitized message per contract field -- a raw Zod issue is never
 * shown to a visitor. Keyed by the first path segment `safeParse` reports.
 */
const FIELD_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  name: "Please enter your name (no line breaks, 100 characters maximum).",
  workEmail: "Please enter a valid work email address.",
  company: "Company name is too long, or contains a line break (200 characters maximum).",
  message: "Please enter a message, 2,000 characters maximum, without unsupported control characters.",
  consent: "Please confirm consent to be contacted before submitting.",
  turnstileToken: "Please complete the verification checkbox.",
};
const GENERIC_VALIDATION_MESSAGE = "Please check the form for errors and try again.";

type SubmitStatus =
  | { readonly kind: "idle" }
  | { readonly kind: "pending" }
  | { readonly kind: "success" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "temporarily_unavailable"; readonly fallbackEmail: string | null };

const FORM_COPY: Readonly<
  Record<ContactFormType, { readonly submitLabel: string; readonly successMessage: string; readonly fixedSubject: string }>
> = {
  pilot_request: {
    submitLabel: "Request a pilot",
    successMessage: "Thank you -- your pilot request has been sent.",
    fixedSubject: "CloudOps Guard pilot request",
  },
  feedback: {
    submitLabel: "Send feedback",
    successMessage: "Thank you -- your feedback has been sent.",
    fixedSubject: "CloudOps Guard feedback",
  },
};

/**
 * The shared contact-form island (Phase 3I), used on both `/request-demo`
 * and `/feedback` with a different `formType`. Holds every field in React
 * memory only -- no `localStorage`/`sessionStorage`/cookie/IndexedDB/
 * service-worker storage, and no report-derived default or suggestion
 * anywhere. See the isolation test for a structural guarantee that this
 * module (and everything it imports) never reaches `report-import`,
 * `report-workspace`, `local-report-explorer`, `comparison`,
 * `executive-summary`, `demo-controller`, or any synthetic/catalogue data.
 */
export function ContactForm({ formType, siteKey }: ContactFormProps) {
  const [fields, setFields] = useState<FieldState>(EMPTY_FIELDS);
  const [status, setStatus] = useState<SubmitStatus>({ kind: "idle" });
  const { containerRef: turnstileContainerRef, token: turnstileToken, status: turnstileStatus, reset: resetTurnstile } = useTurnstile(
    siteKey,
    formType,
  );

  const nameId = useId();
  const emailId = useId();
  const companyId = useId();
  const messageId = useId();
  const consentId = useId();
  const statusId = useId();

  const copy = FORM_COPY[formType];
  const isPending = status.kind === "pending";

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isPending) {
      return;
    }

    // Capture the token for this attempt, then immediately reset the
    // widget -- every attempted submission consumes the current token, so
    // a retry (successful or not) always requires a fresh challenge.
    const capturedToken = turnstileToken;
    resetTurnstile();

    const candidate = {
      formType,
      name: fields.name,
      workEmail: fields.workEmail,
      company: fields.company.length > 0 ? fields.company : undefined,
      message: fields.message,
      consent: fields.consent,
      turnstileToken: capturedToken ?? "",
    };

    const parsed = contactFormSchema.safeParse(candidate);
    if (!parsed.success) {
      const firstIssuePath = parsed.error.issues[0]?.path[0];
      const message =
        typeof firstIssuePath === "string" ? (FIELD_ERROR_MESSAGES[firstIssuePath] ?? GENERIC_VALIDATION_MESSAGE) : GENERIC_VALIDATION_MESSAGE;
      setStatus({ kind: "error", message });
      return;
    }

    setStatus({ kind: "pending" });
    const result = await submitContactForm(parsed.data);

    if (result.kind === "success") {
      setFields(EMPTY_FIELDS);
      setStatus({ kind: "success" });
      return;
    }
    if (result.kind === "temporarily_unavailable") {
      setStatus({ kind: "temporarily_unavailable", fallbackEmail: result.fallbackEmail });
      return;
    }
    if (result.kind === "validation_error") {
      setStatus({ kind: "error", message: result.message });
      return;
    }
    setStatus({ kind: "error", message: "Something went wrong. Please try again." });
  }

  return (
    <form className="contact-form" onSubmit={(event) => void handleSubmit(event)} noValidate>
      <div className="contact-form__field">
        <label htmlFor={nameId}>Name</label>
        <input
          id={nameId}
          type="text"
          required
          maxLength={CONTACT_NAME_MAX_LENGTH}
          value={fields.name}
          onChange={(event) => setFields((previous) => ({ ...previous, name: event.target.value }))}
          disabled={isPending}
          autoComplete="name"
        />
      </div>

      <div className="contact-form__field">
        <label htmlFor={emailId}>Work email</label>
        <input
          id={emailId}
          type="email"
          required
          maxLength={254}
          value={fields.workEmail}
          onChange={(event) => setFields((previous) => ({ ...previous, workEmail: event.target.value }))}
          disabled={isPending}
          autoComplete="email"
        />
      </div>

      <div className="contact-form__field">
        <label htmlFor={companyId}>
          Company <span className="contact-form__optional">(optional)</span>
        </label>
        <input
          id={companyId}
          type="text"
          maxLength={CONTACT_COMPANY_MAX_LENGTH}
          value={fields.company}
          onChange={(event) => setFields((previous) => ({ ...previous, company: event.target.value }))}
          disabled={isPending}
          autoComplete="organization"
        />
      </div>

      <div className="contact-form__field">
        <label htmlFor={messageId}>Message</label>
        <p className="contact-form__warning" role="note">
          Do not include passwords, API tokens, cloud credentials, kubeconfig content, report JSON, or other
          sensitive infrastructure information.
        </p>
        <textarea
          id={messageId}
          required
          maxLength={CONTACT_MESSAGE_MAX_LENGTH}
          rows={6}
          value={fields.message}
          onChange={(event) => setFields((previous) => ({ ...previous, message: event.target.value }))}
          disabled={isPending}
        />
      </div>

      <p className="contact-form__disclosure">
        Text you submit here is transmitted and retained as an email so CloudOps Guard can respond to you. This is
        separate from, and unrelated to, the local report explorer&rsquo;s browser-only report handling.
      </p>

      <div className="contact-form__field contact-form__field--checkbox">
        <input
          id={consentId}
          type="checkbox"
          checked={fields.consent}
          onChange={(event) => setFields((previous) => ({ ...previous, consent: event.target.checked }))}
          disabled={isPending}
        />
        <label htmlFor={consentId}>I consent to CloudOps Guard contacting me about this submission.</label>
      </div>

      <div className="contact-form__field">
        <span className="contact-form__turnstile-label">Verification</span>
        <div ref={turnstileContainerRef} className="contact-form__turnstile" />
        {turnstileStatus === "expired" ? (
          <p className="contact-form__turnstile-note" role="status">
            Verification expired -- a new challenge has been requested.
          </p>
        ) : null}
        {turnstileStatus === "error" ? (
          <p className="contact-form__turnstile-note" role="alert">
            Verification could not be loaded. Please reload the page and try again.
          </p>
        ) : null}
      </div>

      <button type="submit" className="cta" disabled={isPending}>
        {isPending ? "Sending…" : copy.submitLabel}
      </button>

      <div id={statusId} aria-live="polite" className="contact-form__status">
        {status.kind === "success" ? <p role="status">{copy.successMessage}</p> : null}
        {status.kind === "error" ? <p role="alert">{status.message}</p> : null}
        {status.kind === "temporarily_unavailable" ? (
          <div role="alert">
            <p>{FORM_COPY[formType].fixedSubject} delivery is temporarily unavailable.</p>
            {status.fallbackEmail ? (
              <p>
                You can instead email{" "}
                <a href={`mailto:${status.fallbackEmail}?subject=${encodeURIComponent(copy.fixedSubject)}`}>
                  {status.fallbackEmail}
                </a>{" "}
                directly. Selecting this link opens your own email application, and anything you send from there is
                transmitted outside this site.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </form>
  );
}
