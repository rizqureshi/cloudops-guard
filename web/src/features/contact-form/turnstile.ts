/**
 * Explicit-rendering client integration for Cloudflare's official Turnstile
 * widget (Phase 3I) -- the only third-party script permitted anywhere on
 * this site, and only on `/request-demo` and `/feedback`.
 *
 * Uses `render=explicit` (never the default auto-render mode) so the widget
 * only appears inside `ContactForm`'s own container, at a time this module
 * controls, with an `onload` query-string callback -- Cloudflare's own
 * documented pattern for reliably detecting that `window.turnstile` is
 * ready, rather than polling or guessing a fixed delay.
 */

export interface TurnstileRenderOptions {
  readonly sitekey: string;
  readonly action: string;
  readonly callback: (token: string) => void;
  readonly "expired-callback": () => void;
  readonly "error-callback": () => void;
}

export interface TurnstileApi {
  readonly render: (container: HTMLElement, options: TurnstileRenderOptions) => string;
  readonly reset: (widgetId: string) => void;
  readonly remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const TURNSTILE_SCRIPT_SRC_PREFIX = "https://challenges.cloudflare.com/turnstile/v0/api.js";
const ONLOAD_CALLBACK_NAME = "__cloudopsGuardTurnstileOnload";
const SCRIPT_MARKER_ATTRIBUTE = "data-cloudops-guard-turnstile";

let scriptLoadPromise: Promise<TurnstileApi> | null = null;

/**
 * Loads the official Turnstile script at most once per page, regardless of
 * how many times a `ContactForm` island mounts -- a second call while the
 * first is still loading (or after it has already resolved) reuses the
 * same promise instead of inserting a duplicate `<script>` tag.
 */
export function loadTurnstileScript(): Promise<TurnstileApi> {
  if (scriptLoadPromise) {
    return scriptLoadPromise;
  }

  scriptLoadPromise = new Promise<TurnstileApi>((resolve, reject) => {
    if (window.turnstile) {
      resolve(window.turnstile);
      return;
    }

    const existing = document.querySelector<HTMLScriptElement>(`script[${SCRIPT_MARKER_ATTRIBUTE}]`);

    (window as unknown as Record<string, () => void>)[ONLOAD_CALLBACK_NAME] = () => {
      if (window.turnstile) {
        resolve(window.turnstile);
      } else {
        reject(new Error("turnstile_unavailable"));
      }
    };

    if (existing) {
      // A script tag is already present (e.g. a fast remount) but has not
      // finished loading yet -- wait for its own onload rather than
      // inserting a second script element.
      return;
    }

    const script = document.createElement("script");
    script.src = `${TURNSTILE_SCRIPT_SRC_PREFIX}?onload=${ONLOAD_CALLBACK_NAME}&render=explicit`;
    script.async = true;
    script.defer = true;
    script.setAttribute(SCRIPT_MARKER_ATTRIBUTE, "true");
    script.addEventListener("error", () => reject(new Error("turnstile_script_failed")));
    document.head.appendChild(script);
  });

  return scriptLoadPromise;
}

/** Test-only: clears the cached script-load promise between test cases. */
export function resetTurnstileScriptCacheForTests(): void {
  scriptLoadPromise = null;
}
