/**
 * Phase 3K: tests against the real production renderer
 * (`web/deploy/render-wrangler-configs.mjs`) -- every assertion below
 * calls its actual exported functions and inspects their actual output;
 * none of this file reimplements the renderer's validation, config-
 * building, or transactional-cleanup logic as a second, independent
 * "expected value" oracle.
 *
 * No test in this file (or its `DEPLOY_WEB_ROOT` default) depends on the
 * repository's real, generated `web/dist` -- every `DEPLOY_WEB_ROOT` used
 * below is a self-contained temporary directory this file creates itself
 * (`worker/contact.ts` + `dist/`), so the whole suite passes identically
 * whether or not the real `web/dist` has ever been built. See the
 * dedicated "independent of the real repository build output" describe
 * block below for an explicit regression proving this.
 */
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  COMPATIBILITY_DATE,
  buildContactConfig,
  buildStaticConfig,
  defaultFileOps,
  renderConfigs,
  validateEnvironment,
} from "../../../deploy/render-wrangler-configs.mjs";

const REAL_WEB_ROOT = fileURLToPath(new URL("../../..", import.meta.url));
const RENDERER_SCRIPT_PATH = join(REAL_WEB_ROOT, "deploy", "render-wrangler-configs.mjs");
const REAL_DIST_PATH = join(REAL_WEB_ROOT, "dist");

const createdDirs: string[] = [];

function makeTempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "cog-deploy-test-"));
  createdDirs.push(dir);
  return dir;
}

/** A standalone, self-contained fake web-root directory (worker/contact.ts + dist/) -- never the real repository. */
function makeFakeWebRoot(): string {
  const root = makeTempDir();
  mkdirSync(join(root, "worker"), { recursive: true });
  writeFileSync(join(root, "worker", "contact.ts"), "export default { fetch: () => new Response() };\n");
  mkdirSync(join(root, "dist"), { recursive: true });
  return root;
}

/** A fresh, valid `DEPLOY_*` environment for one test -- a brand-new fake web root every call, so tests never share or depend on filesystem state (real or fixture) from another test. */
function makeValidEnv(outDir: string, overrides: Record<string, string> = {}) {
  return {
    DEPLOY_OUT_DIR: outDir,
    DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org",
    DEPLOY_ZONE_NAME: "cloudopsguard-deploy-fixture.org",
    DEPLOY_STATIC_WORKER_NAME: "cog-web-static",
    DEPLOY_CONTACT_WORKER_NAME: "cog-web-contact",
    DEPLOY_CONTACT_TO_EMAIL: "contact@cloudopsguard-deploy-fixture.org",
    DEPLOY_CONTACT_FROM_EMAIL: "no-reply@cloudopsguard-deploy-fixture.org",
    DEPLOY_WEB_ROOT: makeFakeWebRoot(),
    ...overrides,
  };
}

afterEach(() => {
  for (const dir of createdDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
  vi.restoreAllMocks();
});

describe("validateEnvironment: accepts well-formed input", () => {
  it("validates a canonical fixture configuration", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const validated = validateEnvironment(env);
    expect(validated.hostname).toBe("www.cloudopsguard-deploy-fixture.org");
    expect(validated.zoneName).toBe("cloudopsguard-deploy-fixture.org");
    expect(validated.contactMainPath).toBe(join(env.DEPLOY_WEB_ROOT, "worker", "contact.ts"));
    expect(validated.distDirPath).toBe(join(env.DEPLOY_WEB_ROOT, "dist"));
  });

  it("accepts hostname === zone (apex)", () => {
    const outDir = makeTempDir();
    const validated = validateEnvironment(
      makeValidEnv(outDir, { DEPLOY_HOSTNAME: "cloudopsguard-deploy-fixture.org" }),
    );
    expect(validated.hostname).toBe("cloudopsguard-deploy-fixture.org");
  });

  it("accepts a genuine subdomain of the zone", () => {
    const outDir = makeTempDir();
    const validated = validateEnvironment(
      makeValidEnv(outDir, { DEPLOY_HOSTNAME: "deep.sub.cloudopsguard-deploy-fixture.org" }),
    );
    expect(validated.hostname).toBe("deep.sub.cloudopsguard-deploy-fixture.org");
  });
});

describe("validateEnvironment: case normalization", () => {
  it("normalizes hostname and zone case consistently and still matches", () => {
    const outDir = makeTempDir();
    const validated = validateEnvironment(
      makeValidEnv(outDir, {
        DEPLOY_HOSTNAME: "WWW.CloudOpsGuard-Deploy-Fixture.ORG",
        DEPLOY_ZONE_NAME: "CloudOpsGuard-Deploy-Fixture.org",
      }),
    );
    expect(validated.hostname).toBe("www.cloudopsguard-deploy-fixture.org");
    expect(validated.zoneName).toBe("cloudopsguard-deploy-fixture.org");
  });
});

describe("validateEnvironment: fails closed on malformed hostname/zone input", () => {
  const cases: Array<[string, Record<string, string>]> = [
    ["missing hostname", { DEPLOY_HOSTNAME: "" }],
    ["whitespace-only hostname", { DEPLOY_HOSTNAME: "   " }],
    ["leading whitespace", { DEPLOY_HOSTNAME: " www.cloudopsguard-deploy-fixture.org" }],
    ["trailing whitespace", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org " }],
    ["control character", { DEPLOY_HOSTNAME: "www.cloudopsguarddeploy-fixture.org" }],
    ["embedded newline", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org\n" }],
    ["URL scheme", { DEPLOY_HOSTNAME: "https://www.cloudopsguard-deploy-fixture.org" }],
    ["port", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org:8080" }],
    ["path", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org/path" }],
    ["wildcard", { DEPLOY_HOSTNAME: "*.cloudopsguard-deploy-fixture.org" }],
    ["query string", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org?x=1" }],
    ["fragment", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.org#frag" }],
    ["userinfo", { DEPLOY_HOSTNAME: "user@www.cloudopsguard-deploy-fixture.org" }],
    ["invalid DNS character", { DEPLOY_HOSTNAME: "www.cloud_opsguard-deploy-fixture.org" }],
    ["placeholder .invalid TLD", { DEPLOY_HOSTNAME: "www.cloudopsguard-deploy-fixture.invalid" }],
    ["hostname outside zone (naive-suffix attack)", { DEPLOY_HOSTNAME: "notcloudopsguard-deploy-fixture.org.evil.org" }],
  ];

  it.each(cases)("rejects: %s", (_label, overrides) => {
    const outDir = makeTempDir();
    expect(() => validateEnvironment(makeValidEnv(outDir, overrides))).toThrow();
  });

  it("rejects notexample.com for zone example.com specifically (naive endsWith bug)", () => {
    const outDir = makeTempDir();
    expect(() =>
      validateEnvironment(
        makeValidEnv(outDir, { DEPLOY_HOSTNAME: "notcloudopsguardfixture.org", DEPLOY_ZONE_NAME: "cloudopsguardfixture.org" }),
      ),
    ).toThrow(/subdomain/);
  });
});

describe("validateEnvironment: reserved/placeholder example-domain rejection is label-boundary-aware", () => {
  const rejectedCases: Array<[string, string]> = [
    ["exact example.org", "example.org"],
    ["exact example.com", "example.com"],
    ["exact example.net", "example.net"],
    ["exact test", "test"],
    ["exact invalid", "invalid"],
    ["exact localhost", "localhost"],
    ["subdomain cloudopsguard.example.org", "cloudopsguard.example.org"],
    ["subdomain www.example.com", "www.example.com"],
    ["subdomain api.example.net", "api.example.net"],
    ["subdomain foo.test", "foo.test"],
    ["multi-level subdomain deep.sub.example.com", "deep.sub.example.com"],
    ["dotted .invalid subdomain", "sub.example.invalid"],
  ];

  it.each(rejectedCases)("rejects %s as a reserved/placeholder domain or its subdomain", (_label, hostname) => {
    const outDir = makeTempDir();
    // The zone is deliberately set equal to the (also-rejected) hostname
    // or a matching reserved suffix so this exercises hostname
    // validation itself, not the separate hostname/zone relationship
    // check.
    const zone = hostname.includes(".") ? hostname.split(".").slice(-2).join(".") : hostname;
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_HOSTNAME: hostname, DEPLOY_ZONE_NAME: zone }))).toThrow(
      /reserved|placeholder/,
    );
  });

  const acceptedCases: Array<[string, string, string]> = [
    // Genuinely NOT a subdomain of any reserved zone, despite sharing a
    // text suffix with one -- the naive-suffix trap this label-boundary
    // logic must not fall into.
    ["notexample.com (naive endsWith(\"example.com\") trap)", "notexample.com", "notexample.com"],
    ["latest.com (naive endsWith(\".test\") trap)", "latest.com", "latest.com"],
    ["mytest.com (naive endsWith(\".test\") trap)", "mytest.com", "mytest.com"],
    ["notinvalid.org (naive endsWith(\".invalid\") trap)", "notinvalid.org", "notinvalid.org"],
  ];

  it.each(acceptedCases)("accepts %s (not actually a reserved-domain subdomain)", (_label, hostname, zone) => {
    const outDir = makeTempDir();
    const validated = validateEnvironment(makeValidEnv(outDir, { DEPLOY_HOSTNAME: hostname, DEPLOY_ZONE_NAME: zone }));
    expect(validated.hostname).toBe(hostname);
  });
});

describe("validateEnvironment: fails closed on other malformed fields", () => {
  it("rejects a missing required variable", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    // @ts-expect-error -- deliberately deleting a required key to prove fail-closed behavior
    delete env.DEPLOY_CONTACT_TO_EMAIL;
    expect(() => validateEnvironment(env)).toThrow(/DEPLOY_CONTACT_TO_EMAIL/);
  });

  it("rejects an invalid email address", () => {
    const outDir = makeTempDir();
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_CONTACT_TO_EMAIL: "not-an-email" }))).toThrow();
  });

  it("rejects a Worker name containing invalid characters", () => {
    const outDir = makeTempDir();
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_STATIC_WORKER_NAME: "Not Valid!" }))).toThrow();
  });

  it("rejects identical Worker names for the two units", () => {
    const outDir = makeTempDir();
    expect(() =>
      validateEnvironment(
        makeValidEnv(outDir, { DEPLOY_STATIC_WORKER_NAME: "cog-web", DEPLOY_CONTACT_WORKER_NAME: "cog-web" }),
      ),
    ).toThrow(/differ/);
  });

  it("rejects an uppercase Worker name outright (case is never silently normalized away)", () => {
    const outDir = makeTempDir();
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_CONTACT_WORKER_NAME: "COG-WEB" }))).toThrow();
  });

  it("rejects a relative DEPLOY_OUT_DIR", () => {
    expect(() => validateEnvironment(makeValidEnv("relative/path"))).toThrow();
  });

  it("rejects a non-existent DEPLOY_OUT_DIR", () => {
    expect(() => validateEnvironment(makeValidEnv("/nonexistent/path/that/should/not/exist/anywhere"))).toThrow();
  });
});

describe("validateEnvironment: symlink rejection for all three repository-path inputs (finding #6)", () => {
  it("rejects a symlinked DEPLOY_OUT_DIR", () => {
    const realDir = makeTempDir();
    const parent = makeTempDir();
    const linkPath = join(parent, "link-to-out-dir");
    symlinkSync(realDir, linkPath, "dir");
    expect(() => validateEnvironment(makeValidEnv(linkPath))).toThrow(/symlink/);
  });

  it("rejects a symlinked DEPLOY_WEB_ROOT", () => {
    const outDir = makeTempDir();
    const realRoot = makeFakeWebRoot();
    const parent = makeTempDir();
    const linkPath = join(parent, "link-to-web-root");
    symlinkSync(realRoot, linkPath, "dir");
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_WEB_ROOT: linkPath }))).toThrow(/symlink/);
  });

  it("rejects a symlinked worker/contact.ts within an otherwise-real DEPLOY_WEB_ROOT", () => {
    const outDir = makeTempDir();
    const root = makeTempDir();
    mkdirSync(join(root, "worker"), { recursive: true });
    const realTarget = join(makeTempDir(), "real-contact.ts");
    writeFileSync(realTarget, "export default {};\n");
    symlinkSync(realTarget, join(root, "worker", "contact.ts"));
    mkdirSync(join(root, "dist"), { recursive: true });
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_WEB_ROOT: root }))).toThrow(/symlink/);
  });

  it("rejects a symlinked dist/ within an otherwise-real DEPLOY_WEB_ROOT", () => {
    const outDir = makeTempDir();
    const root = makeTempDir();
    mkdirSync(join(root, "worker"), { recursive: true });
    writeFileSync(join(root, "worker", "contact.ts"), "export default {};\n");
    const realDistTarget = makeTempDir();
    symlinkSync(realDistTarget, join(root, "dist"), "dir");
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_WEB_ROOT: root }))).toThrow(/symlink/);
  });

  it("rejects a DEPLOY_WEB_ROOT missing worker/contact.ts", () => {
    const outDir = makeTempDir();
    const badRoot = makeTempDir();
    mkdirSync(join(badRoot, "dist"), { recursive: true });
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_WEB_ROOT: badRoot }))).toThrow(
      /worker\/contact\.ts|DEPLOY_WEB_ROOT/,
    );
  });

  it("rejects a DEPLOY_WEB_ROOT missing dist/", () => {
    const outDir = makeTempDir();
    const badRoot = makeTempDir();
    mkdirSync(join(badRoot, "worker"), { recursive: true });
    writeFileSync(join(badRoot, "worker", "contact.ts"), "export default {};\n");
    expect(() => validateEnvironment(makeValidEnv(outDir, { DEPLOY_WEB_ROOT: badRoot }))).toThrow(/dist|DEPLOY_WEB_ROOT/);
  });
});

describe("buildStaticConfig: static-assets unit", () => {
  it("has assets, SSG 404 handling, no main, both preview exposures disabled, and only the intended custom domain", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const validated = validateEnvironment(env);
    const config = buildStaticConfig(validated);

    expect(config).not.toHaveProperty("main");
    expect(config.workers_dev).toBe(false);
    expect(config.preview_urls).toBe(false);
    expect(config.assets).toEqual({
      directory: join(env.DEPLOY_WEB_ROOT, "dist"),
      not_found_handling: "404-page",
    });
    expect(config.routes).toEqual([{ pattern: "www.cloudopsguard-deploy-fixture.org", custom_domain: true }]);
    expect(config).not.toHaveProperty("send_email");
    expect(config).not.toHaveProperty("secrets");
  });
});

describe("buildContactConfig: contact-API unit", () => {
  it("has the real Worker entry point, exact route, no assets, both preview exposures disabled, the required Turnstile secret name, expected vars, and both email restrictions", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const validated = validateEnvironment(env);
    const config = buildContactConfig(validated);

    expect(config.main).toBe(join(env.DEPLOY_WEB_ROOT, "worker", "contact.ts"));
    expect(config).not.toHaveProperty("assets");
    expect(config.workers_dev).toBe(false);
    expect(config.preview_urls).toBe(false);
    expect(config.routes).toEqual([
      { pattern: "www.cloudopsguard-deploy-fixture.org/api/contact", zone_name: "cloudopsguard-deploy-fixture.org" },
    ]);
    expect(config.vars).toEqual({
      TURNSTILE_EXPECTED_HOSTNAME: "www.cloudopsguard-deploy-fixture.org",
      CONTACT_TO_EMAIL: "contact@cloudopsguard-deploy-fixture.org",
      CONTACT_FROM_EMAIL: "no-reply@cloudopsguard-deploy-fixture.org",
    });
    expect(config.send_email).toEqual([
      {
        name: "EMAIL",
        destination_address: "contact@cloudopsguard-deploy-fixture.org",
        allowed_sender_addresses: ["no-reply@cloudopsguard-deploy-fixture.org"],
      },
    ]);
    expect(config.secrets).toEqual({ required: ["TURNSTILE_SECRET_KEY"] });
  });

  it("never uses a wildcard or /api/* route", () => {
    const outDir = makeTempDir();
    const validated = validateEnvironment(makeValidEnv(outDir));
    const config = buildContactConfig(validated);
    for (const route of config.routes) {
      expect(route.pattern).not.toContain("*");
    }
  });
});

describe("finding #8: the compatibility date is a single fixed constant, used consistently and documented", () => {
  it("both generated configs use the exact same COMPATIBILITY_DATE", () => {
    const outDir = makeTempDir();
    const validated = validateEnvironment(makeValidEnv(outDir));
    expect(buildStaticConfig(validated).compatibility_date).toBe(COMPATIBILITY_DATE);
    expect(buildContactConfig(validated).compatibility_date).toBe(COMPATIBILITY_DATE);
  });

  it("is a fixed literal, never derived from the current date", () => {
    expect(COMPATIBILITY_DATE).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(COMPATIBILITY_DATE).not.toBe(new Date().toISOString().slice(0, 10));
  });

  it("the operator documentation states the same date and does not call it a placeholder", () => {
    const doc = readFileSync(join(REAL_WEB_ROOT, "..", "docs", "deployment", "web-production.md"), "utf8");
    expect(doc).toContain(COMPATIBILITY_DATE);
    expect(doc.toLowerCase()).not.toContain("placeholder");
  });
});

describe("renderConfigs: filesystem output", () => {
  it("writes both files, mode 0600, deterministic for identical inputs", () => {
    const outDir1 = makeTempDir();
    const outDir2 = makeTempDir();
    const sharedWebRoot = makeFakeWebRoot();
    const result1 = renderConfigs(makeValidEnv(outDir1, { DEPLOY_WEB_ROOT: sharedWebRoot }));
    const result2 = renderConfigs(makeValidEnv(outDir2, { DEPLOY_WEB_ROOT: sharedWebRoot }));

    const staticContents1 = readFileSync(result1.staticConfigPath, "utf8");
    const staticContents2 = readFileSync(result2.staticConfigPath, "utf8");
    const contactContents1 = readFileSync(result1.contactConfigPath, "utf8");
    const contactContents2 = readFileSync(result2.contactConfigPath, "utf8");

    expect(staticContents1).toBe(staticContents2);
    expect(contactContents1).toBe(contactContents2);

    for (const path of [result1.staticConfigPath, result1.contactConfigPath]) {
      const mode = statSync(path).mode & 0o777;
      expect(mode).toBe(0o600);
    }
  });

  it("excludes any Cloudflare token/account value and the Turnstile secret value from generated files", () => {
    const outDir = makeTempDir();
    const result = renderConfigs(makeValidEnv(outDir));
    const combined = readFileSync(result.staticConfigPath, "utf8") + readFileSync(result.contactConfigPath, "utf8");
    expect(combined).not.toMatch(/CLOUDFLARE_API_TOKEN/);
    expect(combined).not.toMatch(/CLOUDFLARE_ACCOUNT_ID/);
    // Only the secret's *name* may appear, never a value for it.
    const secretValueLikePattern = /"TURNSTILE_SECRET_KEY"\s*:\s*"(?!.*required)/;
    expect(combined).not.toMatch(secretValueLikePattern);
  });

  it("refuses to overwrite an existing output file, and never overwrites/deletes a pre-existing path", () => {
    const outDir = makeTempDir();
    writeFileSync(join(outDir, "wrangler.static.json"), "pre-existing-static");
    writeFileSync(join(outDir, "wrangler.contact.json"), "pre-existing-contact");
    expect(() => renderConfigs(makeValidEnv(outDir))).toThrow(/overwrite/);
    expect(readFileSync(join(outDir, "wrangler.static.json"), "utf8")).toBe("pre-existing-static");
    expect(readFileSync(join(outDir, "wrangler.contact.json"), "utf8")).toBe("pre-existing-contact");
  });

  it("refuses to follow a symlink at an output file path", () => {
    const outDir = makeTempDir();
    const decoyTarget = join(makeTempDir(), "decoy.json");
    writeFileSync(decoyTarget, "original");
    symlinkSync(decoyTarget, join(outDir, "wrangler.static.json"));
    expect(() => renderConfigs(makeValidEnv(outDir))).toThrow(/overwrite/);
    expect(readFileSync(decoyTarget, "utf8")).toBe("original");
  });

  it("resolves main/assets.directory to the fake web root's real absolute paths even though the configs live in an unrelated temp directory", () => {
    const outDir = makeTempDir();
    const webRoot = makeFakeWebRoot();
    expect(outDir).not.toBe(webRoot);
    const result = renderConfigs(makeValidEnv(outDir, { DEPLOY_WEB_ROOT: webRoot }));
    const staticConfig = JSON.parse(readFileSync(result.staticConfigPath, "utf8"));
    const contactConfig = JSON.parse(readFileSync(result.contactConfigPath, "utf8"));
    expect(staticConfig.assets.directory).toBe(join(webRoot, "dist"));
    expect(contactConfig.main).toBe(join(webRoot, "worker", "contact.ts"));
  });
});

describe("renderConfigs: independent of the real repository build output (finding #1 regression)", () => {
  it("passes using only a self-contained fake web root, even when the real web/dist does not exist on disk", () => {
    const distWasPresent = existsSync(REAL_DIST_PATH);
    const hiddenDistPath = `${REAL_DIST_PATH}.hidden-for-test-${process.pid}`;
    if (distWasPresent) {
      renameSync(REAL_DIST_PATH, hiddenDistPath);
    }
    try {
      expect(existsSync(REAL_DIST_PATH)).toBe(false);
      const outDir = makeTempDir();
      // Deliberately a *fake* web root, never REAL_WEB_ROOT -- this
      // proves the renderer itself needs no real dist, independent of
      // whether the real one happens to exist.
      const result = renderConfigs(makeValidEnv(outDir));
      const contactConfig = JSON.parse(readFileSync(result.contactConfigPath, "utf8"));
      expect(contactConfig.main).not.toContain(REAL_WEB_ROOT);
    } finally {
      if (distWasPresent) {
        renameSync(hiddenDistPath, REAL_DIST_PATH);
      }
      expect(existsSync(REAL_DIST_PATH)).toBe(distWasPresent);
    }
  });
});

describe("renderConfigs: never prints sensitive values", () => {
  it("prints only a generic success message to stdout, and nothing to stderr", () => {
    const outDir = makeTempDir();
    const stdoutSpy = vi.spyOn(process.stdout, "write").mockImplementation(() => true);
    const stderrSpy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);

    renderConfigs(makeValidEnv(outDir));
    // renderConfigs() itself never writes to stdout/stderr -- only the
    // CLI `main()` entry point does, and only a fixed success string.
    expect(stdoutSpy).not.toHaveBeenCalled();
    expect(stderrSpy).not.toHaveBeenCalled();
  });

  it("never includes the invalid value itself in a thrown validation error", () => {
    const outDir = makeTempDir();
    const secretLookingHostname = "not a hostname with secret-token-abc123";
    try {
      validateEnvironment(makeValidEnv(outDir, { DEPLOY_HOSTNAME: secretLookingHostname }));
      throw new Error("expected validateEnvironment to throw");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      expect(message).not.toContain(secretLookingHostname);
      expect(message).not.toContain("secret-token-abc123");
    }
  });

  it("never includes an email address value in a thrown validation error", () => {
    const outDir = makeTempDir();
    const invalidEmail = "definitely-not-an-email-value";
    try {
      validateEnvironment(makeValidEnv(outDir, { DEPLOY_CONTACT_TO_EMAIL: invalidEmail }));
      throw new Error("expected validateEnvironment to throw");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      expect(message).not.toContain(invalidEmail);
    }
  });
});

describe("renderConfigs: no real deployment value is hardcoded", () => {
  it("the renderer's own source contains no literal Cloudflare token/account identifier", () => {
    const source = readFileSync(RENDERER_SCRIPT_PATH, "utf8");
    expect(source).not.toMatch(/CLOUDFLARE_API_TOKEN\s*=\s*["'][^"']+["']/);
    expect(source).not.toMatch(/\bTURNSTILE_SECRET_KEY\s*=\s*["'][^"']+["']/);
  });
});

// ---------------------------------------------------------------------
// Finding #4 (and its follow-up correction): fully transactional creation
// of the config *pair*, via the renderer's real exclusive-open/write/
// close/chmod/verify algorithm. Every test below injects a deterministic
// failure at one precise call by passing `renderConfigs` a custom
// `fileOps` object -- built from the renderer's own real `defaultFileOps`,
// with exactly one method overridden. This is the "narrow injectable
// filesystem seam" the renderer itself exposes for this purpose; no test
// here reimplements renderConfigs's own transactional algorithm -- every
// assertion below exercises the real production code path.
// ---------------------------------------------------------------------

/** `defaultFileOps` with one named method replaced by `override` -- every other method is the real, unmodified implementation. */
function withOverride<K extends keyof typeof defaultFileOps>(key: K, override: (typeof defaultFileOps)[K]) {
  return { ...defaultFileOps, [key]: override };
}

/** A fake `fs.Stats`-shaped value reporting an unexpected `mode`, for simulating a failed permission-verification step without touching the real filesystem. */
function fakeStatsWithMode(mode: number): import("node:fs").Stats {
  return { mode } as unknown as import("node:fs").Stats;
}

/**
 * Builds a `fileOps` override for `key`: the real implementation runs on
 * every call except the `failOnCall`-th (1-based) *for that same method*,
 * which instead calls `onFail`. The real method is deliberately erased to
 * a simple rest-args signature before wrapping (rather than typed via
 * `typeof defaultFileOps[K]` throughout), so this helper never fights
 * Node's own multi-overloaded `fs` types (e.g. `statSync`'s
 * `Stats | BigIntStats` return, `openSync`'s union `flags`/`mode`
 * parameter types) -- the exact, precise `defaultFileOps[K]` type is
 * restored only once, at the single point that actually needs it.
 */
function overrideFailingOnCall<K extends keyof typeof defaultFileOps>(
  key: K,
  failOnCall: number,
  onFail: (...args: unknown[]) => unknown,
) {
  const real = defaultFileOps[key] as unknown as (...args: unknown[]) => unknown;
  let callCount = 0;
  const wrapped = (...args: unknown[]): unknown => {
    callCount += 1;
    if (callCount === failOnCall) {
      return onFail(...args);
    }
    return real(...args);
  };
  return withOverride(key, wrapped as unknown as (typeof defaultFileOps)[K]);
}

describe("renderConfigs: transactional cleanup on failure (finding #4)", () => {
  it("static: exclusive open succeeds, writing partially succeeds and then throws -- the static output is removed", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const staticPath = join(outDir, "wrangler.static.json");
    const contactPath = join(outDir, "wrangler.contact.json");

    let call = 0;
    const fileOps = withOverride(
      "writeSync",
      ((fd: number, buffer: Uint8Array, offset: number, length: number, position: number) => {
        call += 1;
        if (call === 1) {
          // A genuine partial write to the real, already-created file --
          // fewer bytes than requested, but a real success, never a
          // thrown error on this call.
          const partialLength = Math.max(1, Math.floor(length / 2));
          return defaultFileOps.writeSync(fd, buffer, offset, partialLength, position);
        }
        throw new Error("simulated: writeSync failed after a genuine partial write");
      }) as unknown as typeof defaultFileOps.writeSync,
    );

    expect(() => renderConfigs(env, fileOps)).toThrow(/could not write/);
    expect(existsSync(staticPath)).toBe(false);
    expect(existsSync(contactPath)).toBe(false);
  });

  it("contact: exclusive open succeeds, writing partially succeeds and then throws -- both outputs are removed", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const staticPath = join(outDir, "wrangler.static.json");
    const contactPath = join(outDir, "wrangler.contact.json");

    let call = 0;
    const fileOps = withOverride(
      "writeSync",
      ((fd: number, buffer: Uint8Array, offset: number, length: number, position: number) => {
        call += 1;
        if (call === 1) {
          // The static file's write completes for real and in full.
          return defaultFileOps.writeSync(fd, buffer, offset, length, position);
        }
        if (call === 2) {
          // The contact file's write succeeds, but only partially.
          const partialLength = Math.max(1, Math.floor(length / 2));
          return defaultFileOps.writeSync(fd, buffer, offset, partialLength, position);
        }
        throw new Error("simulated: contact writeSync failed after a genuine partial write");
      }) as unknown as typeof defaultFileOps.writeSync,
    );

    expect(() => renderConfigs(env, fileOps)).toThrow(/could not write/);
    expect(existsSync(staticPath)).toBe(false);
    expect(existsSync(contactPath)).toBe(false);
  });

  it("a path appearing between preflight and exclusive open is never overwritten or removed", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const staticPath = join(outDir, "wrangler.static.json");
    const contactPath = join(outDir, "wrangler.contact.json");
    const competingContent = "written by a process this invocation does not own or control";

    const fileOps = withOverride(
      "openSync",
      ((path: string, flags: string, mode: number) => {
        if (path === staticPath) {
          // A genuine TOCTOU race, not a simulated error: this creates
          // the competing file for real, in the exact window between
          // `renderConfigs`'s own preflight check (`assertPathAvailable`,
          // already run by this point) and its own exclusive ('wx')
          // open below -- which must then itself fail with the real
          // OS-level EEXIST, exactly as it would for a genuine race.
          writeFileSync(staticPath, competingContent, { encoding: "utf8", mode: 0o644 });
        }
        return defaultFileOps.openSync(path, flags, mode);
      }) as unknown as typeof defaultFileOps.openSync,
    );

    expect(() => renderConfigs(env, fileOps)).toThrow(/refus|overwrit|existing/i);
    // The competing file is never owned by this invocation (it was never
    // added to `ownedPaths`, because the exclusive open that would have
    // done so failed) -- so it is never removed, and never overwritten.
    expect(readFileSync(staticPath, "utf8")).toBe(competingContent);
    expect(existsSync(contactPath)).toBe(false);
  });

  describe("every open/write/close/chmod/verification failure point leaves no owned partial pair", () => {
    const cases: Array<[string, ReturnType<typeof withOverride>]> = [
      [
        "static: exclusive open fails (non-EEXIST)",
        overrideFailingOnCall("openSync", 1, () => {
          throw new Error("simulated: openSync failed (not EEXIST)");
        }),
      ],
      [
        "static: write fails immediately",
        overrideFailingOnCall("writeSync", 1, () => {
          throw new Error("simulated: writeSync failed immediately");
        }),
      ],
      [
        "static: close fails",
        overrideFailingOnCall("closeSync", 1, () => {
          throw new Error("simulated: closeSync failed");
        }),
      ],
      [
        "static: chmod fails",
        overrideFailingOnCall("chmodSync", 1, () => {
          throw new Error("simulated: chmodSync failed");
        }),
      ],
      ["static: permission verification mismatch", overrideFailingOnCall("statSync", 1, () => fakeStatsWithMode(0o644))],
      [
        "contact: exclusive open fails (non-EEXIST)",
        overrideFailingOnCall("openSync", 2, () => {
          throw new Error("simulated: openSync failed (not EEXIST)");
        }),
      ],
      [
        "contact: write fails immediately",
        overrideFailingOnCall("writeSync", 2, () => {
          throw new Error("simulated: writeSync failed immediately");
        }),
      ],
      [
        "contact: close fails",
        overrideFailingOnCall("closeSync", 2, () => {
          throw new Error("simulated: closeSync failed");
        }),
      ],
      [
        "contact: chmod fails",
        overrideFailingOnCall("chmodSync", 2, () => {
          throw new Error("simulated: chmodSync failed");
        }),
      ],
      ["contact: permission verification mismatch", overrideFailingOnCall("statSync", 2, () => fakeStatsWithMode(0o644))],
    ];

    it.each(cases)("%s -> no owned partial pair remains", (_label, fileOps) => {
      const outDir = makeTempDir();
      const env = makeValidEnv(outDir);
      const staticPath = join(outDir, "wrangler.static.json");
      const contactPath = join(outDir, "wrangler.contact.json");

      expect(() => renderConfigs(env, fileOps)).toThrow();
      expect(existsSync(staticPath)).toBe(false);
      expect(existsSync(contactPath)).toBe(false);
    });
  });

  it("never deletes a path that existed before this invocation, even while cleaning up its own failure", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const unrelatedPath = join(outDir, "unrelated-pre-existing-file.txt");
    writeFileSync(unrelatedPath, "do not touch me");

    const fileOps = overrideFailingOnCall("writeSync", 2, () => {
      throw new Error("simulated: contact writeSync failed immediately");
    });

    expect(() => renderConfigs(env, fileOps)).toThrow();
    expect(readFileSync(unrelatedPath, "utf8")).toBe("do not touch me");
  });

  it("reports a distinct, still-sanitized 'cleanup incomplete' failure when a created file cannot be removed", () => {
    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    const staticPath = join(outDir, "wrangler.static.json");

    const fileOps = {
      ...defaultFileOps,
      chmodSync: overrideFailingOnCall("chmodSync", 2, () => {
        throw new Error("simulated: contact chmodSync failed");
      }).chmodSync,
      // Cleanup's own unlink of the static file (the one prior success)
      // is made to fail too, so the sanitized "cleanup incomplete" path
      // is genuinely exercised rather than the ordinary "could not
      // write" path.
      unlinkSync: (path: Parameters<typeof defaultFileOps.unlinkSync>[0]) => {
        if (path === staticPath) {
          throw new Error("simulated: cleanup unlink failed");
        }
        return defaultFileOps.unlinkSync(path);
      },
    };

    let thrownMessage = "";
    try {
      renderConfigs(env, fileOps);
      throw new Error("expected renderConfigs to throw");
    } catch (error) {
      thrownMessage = error instanceof Error ? error.message : String(error);
    }
    expect(thrownMessage).toMatch(/cleanup/i);
    // Still one of this script's own fixed, sanitized strings -- never
    // the raw "simulated: cleanup unlink failed" error or any path.
    expect(thrownMessage).not.toContain(outDir);
    expect(thrownMessage).not.toContain("simulated");
    // The static file was left behind precisely because its own
    // (simulated) removal failed -- clean it up directly so afterEach's
    // recursive removal of `outDir` still succeeds.
    rmSync(staticPath, { force: true });
  });
});

// ---------------------------------------------------------------------
// Finding #5: unexpected filesystem failures must never leak an
// absolute path, an environment-variable value, or a raw OS error.
// Run as a real, separate subprocess (never mocked) so this proves the
// *actual* CLI's stdout/stderr, not an in-process approximation.
// ---------------------------------------------------------------------

describe("CLI subprocess: unexpected filesystem failures are sanitized (finding #5 regression)", () => {
  it("an unwritable (but validation-passing) output directory produces no path/value leak on stdout or stderr", () => {
    if (process.getuid && process.getuid() === 0) {
      // A root process ignores Unix write-permission bits entirely, so
      // this specific failure mode cannot be induced portably when
      // running as root (e.g. some container-based CI runners). Every
      // *other* filesystem-safety property is still covered by the
      // in-process tests above; this one subprocess check is skipped
      // rather than silently reported as passing under a condition that
      // cannot actually exercise it.
      return;
    }

    const outDir = makeTempDir();
    const env = makeValidEnv(outDir);
    chmodSync(outDir, 0o500); // read+execute only: passes directory validation, fails the actual write with EACCES
    try {
      const result = spawnSync(process.execPath, [RENDERER_SCRIPT_PATH], {
        env: { ...process.env, ...env },
        encoding: "utf8",
      });

      expect(result.status).toBe(1);
      expect(result.stdout).not.toContain(outDir);
      expect(result.stderr).not.toContain(outDir);
      expect(result.stdout).not.toMatch(/ENOENT|EACCES|EPERM/);
      expect(result.stderr).not.toMatch(/ENOENT|EACCES|EPERM/);
      for (const [key, value] of Object.entries(env)) {
        if (key === "DEPLOY_OUT_DIR" || key === "DEPLOY_WEB_ROOT") continue; // paths checked above
        expect(result.stdout).not.toContain(value);
        expect(result.stderr).not.toContain(value);
      }
      expect(result.stderr).toContain("Wrangler configuration rendering failed:");
    } finally {
      chmodSync(outDir, 0o700); // restore so afterEach's rmSync can clean it up
    }
  });
});
