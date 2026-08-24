/**
 * Local, structural binding types for the contact Worker (Phase 3I) --
 * deliberately not `@cloudflare/workers-types`: standard `Request`/
 * `Response`/`fetch`/`URL`/`ReadableStream` already type-check under this
 * project's existing `tsconfig.json`, and the two custom bindings below
 * (`EMAIL`, the six string config values) are simple enough that a local
 * interface is clearer than pulling in a whole ambient-types package for
 * them. See CLAUDE.md: no new dependency without being unavoidable.
 *
 * Phase 3I supplies and tests this source; it does not configure the real
 * Cloudflare bindings, Wrangler, or a deployment (Phase 3K).
 */

/** The structured Email Workers binding shape this Worker calls -- never the legacy raw-MIME API. */
export interface EmailMessage {
  readonly to: string;
  readonly from: string;
  readonly subject: string;
  readonly text: string;
}

export interface EmailBinding {
  readonly send: (message: EmailMessage) => Promise<void>;
}

export interface ContactWorkerEnv {
  readonly TURNSTILE_SECRET_KEY: string;
  readonly TURNSTILE_EXPECTED_HOSTNAME: string;
  readonly EMAIL: EmailBinding;
  readonly CONTACT_TO_EMAIL: string;
  readonly CONTACT_FROM_EMAIL: string;
}
