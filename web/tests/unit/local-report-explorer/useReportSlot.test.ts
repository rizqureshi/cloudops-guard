// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportValidationError } from "../../../src/features/report-import";
import type { NormalizedWebReport } from "../../../src/features/report-import";

vi.mock("../../../src/features/local-report-explorer/importLocalReportFile", () => ({
  importLocalReportFile: vi.fn(),
}));

import { importLocalReportFile } from "../../../src/features/local-report-explorer/importLocalReportFile";
import { useReportSlot } from "../../../src/features/local-report-explorer/useReportSlot";
import { LocalImportError } from "../../../src/features/local-report-explorer/errors";

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const fakeReport = (marker: string): NormalizedWebReport =>
  ({
    platform: "kubernetes",
    generatedAt: "2026-01-01T00:00:00Z",
    target: { clusterContext: marker, namespaceFilter: null },
    findings: [],
    summary: { critical: 0, high: 0, medium: 0, low: 0, total: 0 },
  }) as NormalizedWebReport;

const fakeFile = {} as File;

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  vi.mocked(importLocalReportFile).mockReset();
});

describe("useReportSlot: initial state", () => {
  it("starts empty, not loading, no report, no error", () => {
    const { result } = renderHook(() => useReportSlot());
    expect(result.current.state).toEqual({ generation: 0, report: null, errorMessage: null, isLoading: false });
  });
});

describe("useReportSlot: selection immediately invalidates the prior state", () => {
  it("select() synchronously clears any previous report and sets isLoading, before the read resolves", async () => {
    const first = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(first.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });

    expect(result.current.state.isLoading).toBe(true);
    expect(result.current.state.report).toBeNull();
    expect(result.current.state.errorMessage).toBeNull();
  });

  it("selecting a new file while a valid report is already shown immediately clears that report", async () => {
    const first = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(first.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });
    first.resolve(fakeReport("a"));
    await flushMicrotasks();
    await waitFor(() => expect(result.current.state.report).not.toBeNull());

    const second = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(second.promise);
    act(() => {
      result.current.select(fakeFile);
    });

    // The prior report must not still be displayed while the new selection
    // is mid-import -- the UI must never show a stale report alongside a
    // freshly chosen file.
    expect(result.current.state.report).toBeNull();
    expect(result.current.state.isLoading).toBe(true);
  });
});

describe("useReportSlot: race safety", () => {
  it("a slower, earlier read cannot overwrite a newer selection, regardless of resolution order", async () => {
    const older = deferred<NormalizedWebReport>();
    const newer = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    const { result } = renderHook(() => useReportSlot());
    act(() => {
      result.current.select(fakeFile); // generation 1 (older)
    });
    act(() => {
      result.current.select(fakeFile); // generation 2 (newer) -- supersedes generation 1
    });

    const newerReport = fakeReport("newer");
    newer.resolve(newerReport);
    await flushMicrotasks();
    await waitFor(() => expect(result.current.state.report).toEqual(newerReport));

    // The stale, slower read resolves *after* the newer one already
    // rendered -- it must be silently discarded, not overwrite the result.
    older.resolve(fakeReport("older"));
    await flushMicrotasks();

    expect(result.current.state.report).toEqual(newerReport);
    expect(result.current.state.generation).toBe(2);
  });

  it("clearing invalidates a pending read: its eventual resolution cannot restore the cleared state", async () => {
    const pending = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });
    expect(result.current.state.isLoading).toBe(true);

    act(() => {
      result.current.clear();
    });
    expect(result.current.state).toMatchObject({ report: null, errorMessage: null, isLoading: false });

    pending.resolve(fakeReport("too-late"));
    await flushMicrotasks();

    expect(result.current.state.report).toBeNull();
    expect(result.current.state.isLoading).toBe(false);
  });

  it("clearing invalidates a pending read: its eventual rejection cannot restore an error onto the cleared state", async () => {
    const pending = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });
    act(() => {
      result.current.clear();
    });

    pending.reject(new Error("stale rejection that must not surface"));
    await flushMicrotasks();

    expect(result.current.state.errorMessage).toBeNull();
    expect(result.current.state.report).toBeNull();
  });

  it("does not retain any report beyond the current selection -- no history", async () => {
    const first = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(first.promise);
    const { result } = renderHook(() => useReportSlot());
    act(() => {
      result.current.select(fakeFile);
    });
    first.resolve(fakeReport("first"));
    await flushMicrotasks();
    await waitFor(() => expect(result.current.state.report).not.toBeNull());

    const second = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(second.promise);
    act(() => {
      result.current.select(fakeFile);
    });
    second.resolve(fakeReport("second"));
    await flushMicrotasks();
    await waitFor(() => expect(result.current.state.report).toEqual(fakeReport("second")));

    // Only ever one report at a time -- the state shape has no array/history field.
    expect(Object.keys(result.current.state).sort()).toEqual(["errorMessage", "generation", "isLoading", "report"]);
  });
});

describe("useReportSlot: error surfacing", () => {
  it("surfaces a LocalImportError's fixed message as errorMessage, with no report set", async () => {
    const first = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(first.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });
    // Reference the real error's own `.message` rather than restating its
    // fixed-table text here, so this test stays correct if that table
    // changes -- it proves the hook passes the message through, not what
    // the message currently says.
    const rejection = new LocalImportError("malformed_json");
    first.reject(rejection);
    await flushMicrotasks();

    await waitFor(() => expect(result.current.state.errorMessage).toBe(rejection.message));
    expect(result.current.state.report).toBeNull();
    expect(result.current.state.isLoading).toBe(false);
  });

  it("surfaces a ReportValidationError's fixed message as errorMessage, with no report set", async () => {
    const first = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(first.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });
    const rejection = new ReportValidationError("summary_mismatch");
    first.reject(rejection);
    await flushMicrotasks();

    await waitFor(() => expect(result.current.state.errorMessage).toBe(rejection.message));
    expect(result.current.state.report).toBeNull();
    expect(result.current.state.isLoading).toBe(false);
  });

  it("never surfaces an unexpected rejection's own message -- only the fixed generic failure text", async () => {
    // Reproduces the disclosure bug found in review: an ordinary `Error`
    // (not one of the two sanitized classes) could previously carry a
    // filename, a path, a report value, or another sensitive string
    // straight through to `errorMessage`, since the hook trusted any
    // `instanceof Error` message. This proves that path is closed.
    const first = deferred<NormalizedWebReport>();
    vi.mocked(importLocalReportFile).mockReturnValueOnce(first.promise);
    const { result } = renderHook(() => useReportSlot());

    act(() => {
      result.current.select(fakeFile);
    });
    first.reject(new Error("SENSITIVE_UNEXPECTED_ERROR_MARKER"));
    await flushMicrotasks();

    await waitFor(() => expect(result.current.state.errorMessage).toBe("This file could not be imported."));
    expect(result.current.state.errorMessage).not.toContain("SENSITIVE_UNEXPECTED_ERROR_MARKER");
    expect(result.current.state.report).toBeNull();
    expect(result.current.state.isLoading).toBe(false);
  });
});
