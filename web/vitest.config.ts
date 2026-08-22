import { defineConfig } from "vitest/config";

// Vitest configuration for the web report-import (Phase 3C) and
// report-workspace (Phase 3D) test suites.
//
// The global environment stays "node": most tests (report-import's schema/
// parser tests, report-workspace's pure category/sorting/filtering
// utilities) exercise plain TypeScript logic against plain data, with no
// DOM involved, so they do not need -- and should not pay the cost of --
// jsdom. React component tests are the one exception: those files opt into
// jsdom individually via a `// @vitest-environment jsdom` docblock at the
// top of the file, rather than switching every test in the project to
// jsdom.
//
// `setupFiles` registers @testing-library/jest-dom's matchers and React
// Testing Library's post-test cleanup globally (see
// tests/setup/react-testing-library.ts) -- safe for non-DOM tests too,
// since neither does anything unless a test actually renders something.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/unit/**/*.test.ts", "tests/component/**/*.test.tsx"],
    setupFiles: ["tests/setup/react-testing-library.ts"],
    passWithNoTests: false,
  },
});
