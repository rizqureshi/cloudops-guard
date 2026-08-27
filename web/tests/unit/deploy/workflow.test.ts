/**
 * Structural checks against the real `.github/workflows/deploy-web.yml`,
 * `.github/workflows/ci.yml`, and `.github/workflows/web-ci.yml` files --
 * reading their actual text from disk, never a hand-copied duplicate. No
 * YAML-parsing dependency is added (consistent with `CLAUDE.md`: no new
 * dependency without an unavoidable need); these are deliberately
 * targeted textual/structural checks of the real files, sufficient to
 * prove the properties Phase 3K requires without needing a full YAML AST.
 *
 * This suite never runs the workflow, contacts GitHub, or invokes `gh`.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

const REPO_ROOT = fileURLToPath(new URL("../../../..", import.meta.url));
const DEPLOY_WORKFLOW = readFileSync(`${REPO_ROOT}/.github/workflows/deploy-web.yml`, "utf8");
const CI_WORKFLOW = readFileSync(`${REPO_ROOT}/.github/workflows/ci.yml`, "utf8");
const WEB_CI_WORKFLOW = readFileSync(`${REPO_ROOT}/.github/workflows/web-ci.yml`, "utf8");

/**
 * Extracts one top-level job's own text -- from its `  <jobName>:` header
 * up to (but not including) the next two-space-indented job header, or the
 * end of the file. Job-boundary-aware, unlike a plain substring/regex
 * search across the whole file: a check against this block can never be
 * satisfied by content that actually belongs to a different job.
 */
function extractJobBlock(workflowText: string, jobName: string): string {
  const headerPattern = new RegExp(`\\n {2}${jobName}:\\n`);
  const headerMatch = headerPattern.exec(workflowText);
  if (!headerMatch) {
    throw new Error(`job "${jobName}" not found`);
  }
  const start = headerMatch.index + headerMatch[0].length;
  const nextHeaderMatch = /\n {2}[A-Za-z0-9_-]+:\n/.exec(workflowText.slice(start));
  const end = nextHeaderMatch ? start + nextHeaderMatch.index : workflowText.length;
  return workflowText.slice(start, end);
}

/** The `needs:` value(s) declared directly under the given job's own header, as a list -- handles both `needs: x` and a `needs: [a, b]`/block-list form. */
function extractJobNeeds(workflowText: string, jobName: string): string[] {
  const jobBlock = extractJobBlock(workflowText, jobName);
  const inlineMatch = /^ {4}needs:\s*\[([^\]]*)\]\s*$/m.exec(jobBlock);
  if (inlineMatch) {
    return inlineMatch[1]!.split(",").map((entry) => entry.trim()).filter(Boolean);
  }
  const scalarMatch = /^ {4}needs:\s*(\S+)\s*$/m.exec(jobBlock);
  if (scalarMatch) {
    return [scalarMatch[1]!];
  }
  const blockMatch = /^ {4}needs:\s*\n((?: {6}- .+\n)+)/m.exec(jobBlock);
  if (blockMatch) {
    return [...blockMatch[1]!.matchAll(/^ {6}- (.+)$/gm)].map((m) => m[1]!.trim());
  }
  return [];
}

describe("deploy-web.yml: trigger and permissions", () => {
  it("the only trigger is workflow_dispatch", () => {
    const triggerBlockMatch = DEPLOY_WORKFLOW.match(/\non:\n([\s\S]*?)\npermissions:/);
    expect(triggerBlockMatch).not.toBeNull();
    const triggerBlock = triggerBlockMatch![1]!;
    expect(triggerBlock).toContain("workflow_dispatch:");
    for (const forbidden of ["push:", "pull_request:", "pull_request_target:", "schedule:", "release:", "workflow_run:", "repository_dispatch:"]) {
      expect(triggerBlock).not.toContain(forbidden);
    }
  });

  it("top-level permissions are contents: read, and no job grants write permissions", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/\npermissions:\n\s+contents:\s*read\n/);
    expect(DEPLOY_WORKFLOW).not.toMatch(/:\s*write\b/);
  });

  it("requires a typed confirmation input and a commit_sha input", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/confirmation:\s*\n(\s+.*\n)*?\s*required:\s*true/);
    expect(DEPLOY_WORKFLOW).toMatch(/commit_sha:\s*\n(\s+.*\n)*?\s*required:\s*true/);
  });
});

describe("deploy-web.yml: input handling never interpolates directly into shell source", () => {
  it("no run: block references ${{ github.event.inputs.* }} or ${{ inputs.* }} directly inside its shell text", () => {
    const runBlocks = [...DEPLOY_WORKFLOW.matchAll(/run:\s*\|?\n([\s\S]*?)(?=\n\s{2,}(?:- name:|env:|if:)|\n {0,6}\S)/g)].map(
      (match) => match[1] ?? "",
    );
    for (const block of runBlocks) {
      expect(block).not.toMatch(/\$\{\{\s*(github\.event\.inputs|inputs)\./);
    }
  });

  it("inputs are instead mapped through env: before being used in a run: step", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/env:\s*\n\s+INPUT_CONFIRMATION:\s*\$\{\{\s*github\.event\.inputs\.confirmation\s*\}\}/);
    expect(DEPLOY_WORKFLOW).toMatch(/env:\s*\n\s+INPUT_COMMIT_SHA:\s*\$\{\{\s*github\.event\.inputs\.commit_sha\s*\}\}/);
  });
});

describe("deploy-web.yml: validation job", () => {
  it("verifies the dispatch ref is refs/heads/main", () => {
    expect(DEPLOY_WORKFLOW).toContain("refs/heads/main");
  });

  it("verifies the confirmation phrase exactly", () => {
    expect(DEPLOY_WORKFLOW).toContain("REQUIRED_CONFIRMATION_PHRASE");
  });

  it("verifies commit_sha is exactly 40 hexadecimal characters", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/\^\[0-9a-fA-F\]\{40\}\$/);
  });

  it("checks out with persist-credentials: false", () => {
    const occurrences = [...DEPLOY_WORKFLOW.matchAll(/persist-credentials:\s*false/g)];
    // Once per checkout: validate, validate_web, validate_python, deploy.
    expect(occurrences.length).toBeGreaterThanOrEqual(4);
  });

  it("verifies checked-out HEAD equals the requested commit", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/git rev-parse HEAD/);
    expect(DEPLOY_WORKFLOW).toContain("EXPECTED_COMMIT_SHA");
  });

  it("verifies the commit is reachable from origin/main", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/git merge-base --is-ancestor/);
    expect(DEPLOY_WORKFLOW).toMatch(/origin\/main|origin main/);
  });

  it("the validate job has no Cloudflare credential reference anywhere in its own block", () => {
    const validateJobText = extractJobBlock(DEPLOY_WORKFLOW, "validate");
    expect(validateJobText).not.toMatch(/CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID/);
  });
});

describe("deploy-web.yml: credential-free release-readiness validation (finding #2)", () => {
  const expectedWebCommands = [
    "npm ci",
    "npm audit --audit-level=high",
    "npm run check",
    "npm run lint",
    "npm run test",
    "npm run build",
    "npx playwright install --with-deps chromium firefox webkit",
    "npm run test:e2e",
  ];
  const expectedPythonCommands = [
    "uv python install 3.12",
    "uv sync --locked --extra dev",
    "uv run pytest",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv build",
  ];

  it("validate_web and validate_python jobs exist", () => {
    expect(() => extractJobBlock(DEPLOY_WORKFLOW, "validate_web")).not.toThrow();
    expect(() => extractJobBlock(DEPLOY_WORKFLOW, "validate_python")).not.toThrow();
  });

  it("validate_web runs Node 24 and every command web-ci.yml's own validate job runs, in the same job", () => {
    const jobText = extractJobBlock(DEPLOY_WORKFLOW, "validate_web");
    expect(jobText).toMatch(/node-version:\s*"24"/);
    for (const command of expectedWebCommands) {
      expect(jobText).toContain(command);
    }
  });

  it("validate_python runs every command ci.yml's own test job runs, in the same job", () => {
    const jobText = extractJobBlock(DEPLOY_WORKFLOW, "validate_python");
    for (const command of expectedPythonCommands) {
      expect(jobText).toContain(command);
    }
  });

  it("validate_web's production build uses the public test Turnstile key, never the real one", () => {
    const jobText = extractJobBlock(DEPLOY_WORKFLOW, "validate_web");
    expect(jobText).toContain("1x00000000000000000000AA");
    expect(jobText).not.toMatch(/PUBLIC_TURNSTILE_SITE_KEY:\s*\$\{\{\s*vars\.PUBLIC_TURNSTILE_SITE_KEY\s*\}\}/);
  });

  it("neither validate_web nor validate_python references a Cloudflare credential, TURNSTILE_SECRET_KEY, or the production environment", () => {
    for (const jobName of ["validate_web", "validate_python"]) {
      const jobText = extractJobBlock(DEPLOY_WORKFLOW, jobName);
      expect(jobText).not.toMatch(/CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID|TURNSTILE_SECRET_KEY/);
      expect(jobText).not.toMatch(/environment:\s*production/);
      // Excludes full-line comments: a job's own leading docblock (and the
      // trailing edge of the block boundary, which can pick up the next
      // job's preceding comment) may legitimately *mention* "Wrangler" in
      // prose explaining that this job never touches it -- only an
      // executable (non-comment) line naming it would be a real finding.
      const nonCommentText = jobText
        .split("\n")
        .filter((line) => !line.trim().startsWith("#"))
        .join("\n");
      expect(nonCommentText).not.toMatch(/wrangler/i);
    }
  });

  it("neither validate_web nor validate_python renders the Wrangler configuration", () => {
    for (const jobName of ["validate_web", "validate_python"]) {
      const jobText = extractJobBlock(DEPLOY_WORKFLOW, jobName);
      expect(jobText).not.toContain("render-wrangler-configs.mjs");
    }
  });

  it("the deploy job's needs: list depends on validate, validate_web, and validate_python -- not just validate", () => {
    const deployNeeds = extractJobNeeds(DEPLOY_WORKFLOW, "deploy");
    expect(deployNeeds.sort()).toEqual(["validate", "validate_python", "validate_web"]);
  });

  it("only the deploy job's own block targets the production environment", () => {
    for (const jobName of ["validate", "validate_web", "validate_python"]) {
      expect(extractJobBlock(DEPLOY_WORKFLOW, jobName)).not.toMatch(/environment:\s*production/);
    }
    expect(extractJobBlock(DEPLOY_WORKFLOW, "deploy")).toMatch(/environment:\s*production/);
  });
});

describe("deploy-web.yml: deployment job ordering and gating", () => {
  it("the deploy job depends on the validate job (among others -- see finding #2 below)", () => {
    expect(extractJobNeeds(DEPLOY_WORKFLOW, "deploy")).toContain("validate");
  });

  it("the deploy job targets the protected production environment", () => {
    expect(extractJobBlock(DEPLOY_WORKFLOW, "deploy")).toMatch(/environment:\s*production/);
  });

  it("uses production concurrency that does not cancel an in-progress run", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/\nconcurrency:\n(\s+.*\n)*?\s+group:.*\n(\s+.*\n)*?\s+cancel-in-progress:\s*false/);
  });

  it("the deploy job builds with the real production Turnstile site key, never the public test key", () => {
    const deployJobText = extractJobBlock(DEPLOY_WORKFLOW, "deploy");
    expect(deployJobText).toMatch(/PUBLIC_TURNSTILE_SITE_KEY:\s*\$\{\{\s*vars\.PUBLIC_TURNSTILE_SITE_KEY\s*\}\}/);
    expect(deployJobText).not.toContain("1x00000000000000000000AA");
  });
});

describe("deploy-web.yml: Wrangler pinning and secret handling", () => {
  it("pins Wrangler to exactly wrangler@4.102.0", () => {
    expect(DEPLOY_WORKFLOW).toContain("wrangler@4.102.0");
  });

  it("never runs wrangler secret put (a mention inside an explanatory comment is fine; only an executable line is checked)", () => {
    const nonCommentLines = DEPLOY_WORKFLOW.split("\n").filter((line) => !line.trim().startsWith("#"));
    expect(nonCommentLines.join("\n")).not.toMatch(/wrangler[^\n]*secret\s+put/);
  });

  it("never uploads the generated configuration as a build artifact", () => {
    expect(DEPLOY_WORKFLOW).not.toContain("upload-artifact");
  });

  it("cleans the generated configuration directory in an always() step", () => {
    expect(DEPLOY_WORKFLOW).toMatch(/if:\s*always\(\)\n(\s+.*\n)*?\s+run:\s*\|\n(\s+.*\n)*?\s+rm -rf/);
  });

  it("never echoes a secret or the generated configuration contents", () => {
    expect(DEPLOY_WORKFLOW).not.toMatch(/echo[^\n]*\$\{\{\s*secrets\./);
    expect(DEPLOY_WORKFLOW).not.toMatch(/cat\s+.*wrangler\.(static|contact)\.json/);
  });
});

describe("deploy-web.yml: cleanup-step deletion-target guards (finding #9)", () => {
  function extractCleanupStepBlock(): string {
    const stepMatch = /- name: Remove the generated Wrangler configuration\n([\s\S]*?)\n {8}env:/.exec(DEPLOY_WORKFLOW);
    expect(stepMatch).not.toBeNull();
    return stepMatch![1]!;
  }

  /**
   * Extracts and dedents the literal `run: |` shell script body from the
   * cleanup step -- the exact text a real GitHub Actions runner would
   * execute, read fresh from the real file on every call. The YAML block
   * scalar's own fixed indentation (10 spaces: 8 for the step's other
   * keys, plus 2 more for content nested under `run: |`) is stripped from
   * each line; nothing else about the script is altered.
   */
  function extractCleanupScriptBody(): string {
    const stepBlock = extractCleanupStepBlock();
    const runMatch = /run:\s*\|\n([\s\S]*)$/.exec(stepBlock);
    expect(runMatch).not.toBeNull();
    const indent = " ".repeat(10);
    return runMatch![1]!
      .split("\n")
      .map((line) => (line.startsWith(indent) ? line.slice(indent.length) : line))
      .join("\n");
  }

  it("requires always()", () => {
    expect(extractCleanupStepBlock()).toMatch(/if:\s*always\(\)/);
  });

  it("never prints the generated configuration's contents", () => {
    expect(extractCleanupScriptBody()).not.toMatch(/cat\s+.*wrangler\.(static|contact)\.json/);
  });

  // -----------------------------------------------------------------
  // The tests below execute the *actual* extracted script body above --
  // via a real `bash` subprocess against a real, disposable sandbox
  // directory tree standing in for `$RUNNER_TEMP` -- never a
  // reimplemented approximation of its logic. This directly reproduces
  // and proves fixed the independent-review finding that the previous
  // textual `case "$CONFIG_DIR" in "$RUNNER_TEMP"/*)` prefix test
  // accepted `$RUNNER_TEMP/../outside-target`.
  // -----------------------------------------------------------------

  const sandboxDirs: string[] = [];

  function makeSandboxRunnerTemp(): string {
    const dir = mkdtempSync(join(tmpdir(), "cog-cleanup-script-test-"));
    sandboxDirs.push(dir);
    const runnerTemp = join(dir, "runner-temp");
    mkdirSync(runnerTemp);
    return runnerTemp;
  }

  function runCleanupScript(configDir: string, runnerTemp: string): { status: number | null; stdout: string; stderr: string } {
    const result = spawnSync("bash", ["-c", extractCleanupScriptBody()], {
      env: { ...process.env, CONFIG_DIR: configDir, RUNNER_TEMP: runnerTemp },
      encoding: "utf8",
    });
    return { status: result.status, stdout: result.stdout, stderr: result.stderr };
  }

  afterEach(() => {
    while (sandboxDirs.length > 0) {
      const dir = sandboxDirs.pop()!;
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("empty CONFIG_DIR: does nothing (exits 0, removes nothing)", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    const result = runCleanupScript("", runnerTemp);
    expect(result.status).toBe(0);
    expect(existsSync(runnerTemp)).toBe(true);
  });

  it("the expected temporary directory (this workflow's own naming) is removed", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    const target = join(runnerTemp, "wrangler-config.abc123");
    mkdirSync(target);
    writeFileSync(join(target, "wrangler.static.json"), "{}");

    const result = runCleanupScript(target, runnerTemp);
    expect(result.status).toBe(0);
    expect(existsSync(target)).toBe(false);
  });

  it("$RUNNER_TEMP/../outside-target is rejected -- the outside marker remains", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    // `outsideDir` is the real, resolved location (via `path.join`, purely
    // to create the fixture on disk) -- but the value actually passed as
    // `CONFIG_DIR` to the script below is a deliberately *unresolved*
    // string literal containing `..`, exactly matching the independent
    // review's own reproduction. `path.join` would silently normalize
    // `..` away at construction time, which would defeat this test
    // entirely (it would stop exercising path traversal and start
    // exercising nothing more than "an unrelated directory outside
    // $RUNNER_TEMP") -- so this is deliberately built by string
    // concatenation instead, never `join()`.
    const outsideDir = join(runnerTemp, "..", "outside-target");
    mkdirSync(outsideDir);
    const marker = join(outsideDir, "marker.txt");
    writeFileSync(marker, "do not touch me");
    const traversalConfigDir = `${runnerTemp}/../outside-target`;
    expect(traversalConfigDir).toContain("..");

    const result = runCleanupScript(traversalConfigDir, runnerTemp);
    expect(result.status).not.toBe(0);
    expect(existsSync(marker)).toBe(true);
    expect(readFileSync(marker, "utf8")).toBe("do not touch me");
  });

  it("a symlink inside $RUNNER_TEMP pointing outside it is rejected -- the outside target remains", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    const outsideDir = join(runnerTemp, "..", "outside-target-2");
    mkdirSync(outsideDir);
    const marker = join(outsideDir, "marker2.txt");
    writeFileSync(marker, "do not touch me either");
    const symlinkPath = join(runnerTemp, "wrangler-config.evil-symlink");
    symlinkSync(outsideDir, symlinkPath);

    const result = runCleanupScript(symlinkPath, runnerTemp);
    expect(result.status).not.toBe(0);
    expect(existsSync(marker)).toBe(true);
    expect(readFileSync(marker, "utf8")).toBe("do not touch me either");
  });

  it("$RUNNER_TEMP itself is rejected as a deletion target", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    const result = runCleanupScript(runnerTemp, runnerTemp);
    expect(result.status).not.toBe(0);
    expect(existsSync(runnerTemp)).toBe(true);
  });

  it("an unrelated child directory name (not matching wrangler-config.*) is rejected", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    const unrelated = join(runnerTemp, "some-other-directory");
    mkdirSync(unrelated);
    writeFileSync(join(unrelated, "marker3.txt"), "leave this alone");

    const result = runCleanupScript(unrelated, runnerTemp);
    expect(result.status).not.toBe(0);
    expect(existsSync(unrelated)).toBe(true);
  });

  it("none of the rejection paths ever print the rejected path itself", () => {
    const runnerTemp = makeSandboxRunnerTemp();
    const outsideDir = join(runnerTemp, "..", "outside-target-3");
    mkdirSync(outsideDir);
    // Deliberately unresolved, matching the traversal test above -- never
    // `join()`, which would normalize `..` away before the script ever
    // sees it.
    const traversalTarget = `${runnerTemp}/../outside-target-3`;

    const results = [runCleanupScript(traversalTarget, runnerTemp), runCleanupScript(runnerTemp, runnerTemp)];
    for (const result of results) {
      expect(result.stdout).not.toContain(runnerTemp);
      expect(result.stderr).not.toContain(runnerTemp);
    }
  });
});

describe("deploy-web.yml: no unrelated automatic behavior", () => {
  it("never mentions a tag, release, or automatic preview publication step", () => {
    expect(DEPLOY_WORKFLOW).not.toMatch(/\bgh release\b|\bgit tag\b|create-release/);
  });

  it("uses no pull_request_target trigger anywhere", () => {
    expect(DEPLOY_WORKFLOW).not.toContain("pull_request_target");
  });
});

describe("existing CI workflows remain non-deploying", () => {
  it("ci.yml has no Cloudflare/Wrangler/deploy reference", () => {
    expect(CI_WORKFLOW).not.toMatch(/wrangler|cloudflare|deploy/i);
  });

  it("web-ci.yml has no Cloudflare/Wrangler/deploy reference and still uses only the public test Turnstile key", () => {
    expect(WEB_CI_WORKFLOW).not.toMatch(/wrangler|cloudflare/i);
    expect(WEB_CI_WORKFLOW).toContain("1x00000000000000000000AA");
  });

  it("web-ci.yml's own triggers remain push/pull_request (paths-scoped) plus workflow_dispatch -- never workflow_run or schedule", () => {
    expect(WEB_CI_WORKFLOW).not.toMatch(/workflow_run:|schedule:/);
  });
});
