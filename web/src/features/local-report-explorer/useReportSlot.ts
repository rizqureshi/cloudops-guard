/**
 * Race-safe state for one local-import "slot" (earlier/primary or later).
 *
 * File reading is asynchronous, so a slower, earlier read could otherwise
 * resolve *after* a newer selection (or a clear) and silently overwrite
 * it. This hook prevents that with a per-slot generation counter, kept in
 * a `useRef` (mutated synchronously, unlike `useState`, so it is always
 * current the instant a new selection or a clear happens -- no stale
 * closure risk):
 *
 * - `select(file)` increments the generation, *immediately* clears the
 *   slot's displayed report/error (so the UI never keeps showing an old
 *   report while a new file is mid-read), then starts the async import.
 * - When the import resolves or rejects, the callback compares the
 *   generation it captured against the ref's *current* value. If another
 *   `select`/`clear` happened in the meantime, the ref has moved on, the
 *   captured generation is stale, and the result is discarded.
 * - `clear()` also increments the generation, which is what invalidates
 *   any in-flight read: a promise resolving afterward always finds a
 *   mismatched generation.
 *
 * `generation` is exposed on the returned state (not just kept as an
 * internal ref) so a parent component can use it as part of a React `key`
 * to force a clean remount of `ReportWorkspace`/`ExecutiveSummary`
 * whenever this slot's report changes -- see `LocalReportExplorer.tsx`.
 *
 * No global state, cancellation service, or persistence is introduced:
 * everything here is local `useState`/`useRef` for exactly one slot.
 */

import { useCallback, useRef, useState } from "react";

import { ReportValidationError } from "../report-import";
import type { NormalizedWebReport } from "../report-import";
import { importLocalReportFile } from "./importLocalReportFile";
import { LocalImportError } from "./errors";

export interface ReportSlotState {
  readonly generation: number;
  readonly report: NormalizedWebReport | null;
  readonly errorMessage: string | null;
  readonly isLoading: boolean;
}

const INITIAL_STATE: ReportSlotState = {
  generation: 0,
  report: null,
  errorMessage: null,
  isLoading: false,
};

const GENERIC_ERROR_MESSAGE = "This file could not be imported.";

export interface ReportSlot {
  readonly state: ReportSlotState;
  readonly select: (file: File) => void;
  readonly clear: () => void;
}

export function useReportSlot(): ReportSlot {
  const generationRef = useRef(0);
  const [state, setState] = useState<ReportSlotState>(INITIAL_STATE);

  const select = useCallback((file: File) => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    // Invalidate any previously displayed report/error immediately, before
    // the async read even starts -- the UI must never keep showing a
    // report from a prior selection while a new one is mid-import.
    setState({ generation, report: null, errorMessage: null, isLoading: true });

    importLocalReportFile(file)
      .then((report) => {
        if (generationRef.current !== generation) {
          return; // A newer selection or a clear superseded this read.
        }
        setState({ generation, report, errorMessage: null, isLoading: false });
      })
      .catch((error: unknown) => {
        if (generationRef.current !== generation) {
          return;
        }
        // Only the two sanitized error classes have a message safe to
        // display -- both are always constructed from a fixed lookup
        // table (see `errors.ts` here and `../report-import/errors.ts`),
        // never from caller-supplied text. `error instanceof Error` alone
        // is not sufficient: an ordinary `Error`, a DOM exception, or any
        // other rejection could carry a filename, a path, a report value,
        // or another sensitive string, so every other case falls back to
        // the fixed generic message. The original error is never logged.
        const message =
          error instanceof LocalImportError || error instanceof ReportValidationError
            ? error.message
            : GENERIC_ERROR_MESSAGE;
        setState({ generation, report: null, errorMessage: message, isLoading: false });
      });
  }, []);

  const clear = useCallback(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    setState({ generation, report: null, errorMessage: null, isLoading: false });
  }, []);

  return { state, select, clear };
}
