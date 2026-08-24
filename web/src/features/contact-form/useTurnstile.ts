import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { loadTurnstileScript, type TurnstileApi } from "./turnstile";

export type TurnstileStatus = "loading" | "ready" | "success" | "expired" | "error";

export interface UseTurnstileResult {
  /** Attach to the `<div>` the widget should render into. */
  readonly containerRef: RefObject<HTMLDivElement | null>;
  readonly token: string | null;
  readonly status: TurnstileStatus;
  /** Clears the current token and requests a fresh challenge from the widget. */
  readonly reset: () => void;
}

/**
 * Renders exactly one explicit Turnstile widget into `containerRef`'s
 * element, for the given `siteKey`/`action`, and exposes its current token
 * as React state. The widget is removed on unmount; no interval, poll, or
 * global mutable singleton state survives past that.
 */
export function useTurnstile(siteKey: string, action: string): UseTurnstileResult {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  // The resolved Turnstile API instance, captured once from
  // `loadTurnstileScript()` and reused for `reset()`/cleanup -- never
  // re-read from the `window.turnstile` global, so this hook works
  // identically whether or not the real script happens to have set that
  // global (e.g. under a mocked `loadTurnstileScript` in tests).
  const apiRef = useRef<TurnstileApi | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState<TurnstileStatus>("loading");

  useEffect(() => {
    let cancelled = false;

    loadTurnstileScript()
      .then((turnstile) => {
        if (cancelled || !containerRef.current) {
          return;
        }
        apiRef.current = turnstile;
        const widgetId = turnstile.render(containerRef.current, {
          sitekey: siteKey,
          action,
          callback: (nextToken) => {
            if (cancelled) return;
            setToken(nextToken);
            setStatus("success");
          },
          "expired-callback": () => {
            if (cancelled) return;
            setToken(null);
            setStatus("expired");
            // Request a fresh challenge immediately, using the existing
            // widget/API instance -- never a new script load, a poll, or a
            // second widget. `status` deliberately stays "expired" here
            // (not "ready"): the token remains unavailable, and the
            // expiry notice stays accurate, until Turnstile's own
            // `callback` above fires again with a genuinely new token.
            if (widgetIdRef.current && apiRef.current) {
              apiRef.current.reset(widgetIdRef.current);
            }
          },
          "error-callback": () => {
            if (cancelled) return;
            setToken(null);
            setStatus("error");
          },
        });
        widgetIdRef.current = widgetId;
        if (!cancelled) {
          setStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setStatus("error");
        }
      });

    return () => {
      cancelled = true;
      if (widgetIdRef.current && apiRef.current) {
        apiRef.current.remove(widgetIdRef.current);
      }
      widgetIdRef.current = null;
      apiRef.current = null;
    };
  }, [siteKey, action]);

  const reset = useCallback(() => {
    setToken(null);
    if (widgetIdRef.current && apiRef.current) {
      apiRef.current.reset(widgetIdRef.current);
      setStatus("ready");
    }
  }, []);

  return { containerRef, token, status, reset };
}
