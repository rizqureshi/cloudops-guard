// Registers @testing-library/jest-dom's Vitest matchers (toBeInTheDocument,
// toHaveTextContent, etc.) globally. Safe to load for every test file,
// including the Node-environment report-import tests: this only extends
// `expect` with additional matcher functions -- it does not require a DOM
// to be present, and those matchers are simply never invoked by tests that
// don't render anything.
import "@testing-library/jest-dom/vitest";

// React Testing Library's `cleanup()` unmounts anything rendered by a
// previous test and removes it from the jsdom `document`. RTL normally
// registers this automatically after each test, but that auto-registration
// only fires when it detects a global `afterEach` -- which this project
// deliberately does not enable (`test.globals` is left off in
// vitest.config.ts, keeping `describe`/`it`/`expect`/etc. as explicit
// per-file imports rather than injected globals). Registering cleanup
// explicitly here keeps that choice while still preventing one component
// test's rendered output (and any state inside it) from leaking into the
// next test in the same file.
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
