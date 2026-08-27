/**
 * Phase 3K — renders the two Wrangler configuration files this project's
 * deployment topology needs (see `docs/deployment/web-production.md`):
 * a **static-assets unit** (serves the built `web/dist` output through
 * Workers Static Assets, no Worker code for missing assets) and a
 * **contact-API unit** (routes only the exact `<hostname>/api/contact`
 * path to the existing, unmodified `web/worker/contact.ts`).
 *
 * This script only ever *writes two JSON files to a caller-selected,
 * pre-existing temporary directory*. It never runs Wrangler, never
 * contacts Cloudflare, and never reads or touches any report-related
 * module, fixture, or synthetic data file -- its only inputs are the
 * `DEPLOY_*` environment variables below and two paths already present in
 * the checked-out repository (`web/worker/contact.ts`, `web/dist`).
 *
 * Dependency-free by design (see `CLAUDE.md`: no new dependency without
 * an unavoidable need) -- every import below is a Node built-in, imported
 * explicitly (`node:fs`, `node:path`, `node:process`) rather than relying
 * on ambient globals, so this file lints cleanly under the project's
 * existing flat ESLint config without adding a `.mjs`-specific override
 * or a blanket per-file disable.
 *
 * Required environment variables:
 *   DEPLOY_OUT_DIR             absolute, pre-existing, non-symlink directory
 *                              the two config files are written into
 *   DEPLOY_HOSTNAME            production hostname, e.g. "www.example.com"
 *   DEPLOY_ZONE_NAME           the hostname's DNS zone, e.g. "example.com"
 *                              (hostname must equal the zone, or be a
 *                              subdomain of it)
 *   DEPLOY_STATIC_WORKER_NAME  Cloudflare Worker name for the static unit
 *   DEPLOY_CONTACT_WORKER_NAME Cloudflare Worker name for the contact unit
 *                              (must differ from the static unit's name)
 *   DEPLOY_CONTACT_TO_EMAIL    the Email binding's destination_address
 *   DEPLOY_CONTACT_FROM_EMAIL  the Email binding's allowed_sender_addresses
 *                              entry, and the contact Worker's
 *                              CONTACT_FROM_EMAIL var
 *   DEPLOY_WEB_ROOT            absolute path to the checked-out `web/`
 *                              directory (used only to resolve the two
 *                              absolute paths below -- never read for any
 *                              other purpose); must not itself be a
 *                              symlink, matching `worker/contact.ts` and
 *                              `dist/` below -- this project has no
 *                              deployment scenario that needs any of the
 *                              three to be a symlink, so all three fail
 *                              closed rather than silently following one
 *
 * No `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, or
 * `TURNSTILE_SECRET_KEY` *value* is read, required, or ever written by
 * this script -- the contact config declares only that
 * `TURNSTILE_SECRET_KEY` must be provisioned as a Cloudflare Worker
 * secret out of band (see the deployment doc); this script never sees or
 * needs its value. `secrets.required` below is Wrangler's own enforced
 * configuration field (Wrangler itself refuses to deploy a Worker whose
 * declared required secret has not been provisioned) -- not merely a
 * convention this project's own tooling checks.
 */

import { Buffer } from "node:buffer";
import { chmodSync, closeSync, lstatSync, openSync, statSync, unlinkSync, writeSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import process from "node:process";

// A deliberately selected, fixed Workers compatibility date, reviewed and
// pinned as this project's compatibility baseline -- never computed from
// the current date, and never treated as a placeholder needing review
// before this configuration counts as deployment-ready. Advancing it in
// the future (to adopt newer Workers runtime defaults) is a distinct,
// separately reviewed change to this file, not something a deployment
// dispatch or this script silently decides on its own.
export const COMPATIBILITY_DATE = "2025-01-01";

const STATIC_CONFIG_FILENAME = "wrangler.static.json";
const CONTACT_CONFIG_FILENAME = "wrangler.contact.json";
const CONTACT_PATH = "/api/contact";
const FILE_MODE = 0o600;

const REQUIRED_ENV_VARS = /** @type {const} */ ([
  "DEPLOY_OUT_DIR",
  "DEPLOY_HOSTNAME",
  "DEPLOY_ZONE_NAME",
  "DEPLOY_STATIC_WORKER_NAME",
  "DEPLOY_CONTACT_WORKER_NAME",
  "DEPLOY_CONTACT_TO_EMAIL",
  "DEPLOY_CONTACT_FROM_EMAIL",
  "DEPLOY_WEB_ROOT",
]);

// RFC 2606 / commonly-used placeholder domains this project's own test
// fixtures and documentation use freely -- rejected here, *and rejected
// for every subdomain of each*, specifically so a forgotten placeholder
// can never be rendered into a config that looks deployable.
const RESERVED_EXAMPLE_ZONES = ["example", "example.com", "example.net", "example.org", "test", "invalid", "localhost"];

class ValidationError extends Error {
  /** @param {string} varName @param {string} reason */
  constructor(varName, reason) {
    // Deliberately never includes the invalid value itself -- only the
    // failing variable's *name* and a fixed, generic reason.
    super(`${varName}: ${reason}`);
    this.name = "ValidationError";
    this.varName = varName;
  }
}

/**
 * A sanitized, generic failure raised by this script's own filesystem
 * operations (writing/chmod'ing/verifying a generated file, or cleaning
 * up after a failed attempt) -- its message is always one of the fixed
 * strings below, never a raw OS error (which can embed an absolute path,
 * e.g. `ENOENT: ... open '/actual/path'`).
 */
class RenderError extends Error {
  constructor(message) {
    super(message);
    this.name = "RenderError";
  }
}

/**
 * Raised when the exclusive ('wx') open for one output path fails with
 * `EEXIST` -- a competing path this invocation never owned, whether the
 * earlier preflight check missed it or it appeared in the brief window
 * between preflight and this exact call. A distinct subclass (rather than
 * matching on `RenderError`'s message text) so `renderConfigs` can
 * reliably tell "refused to overwrite a path we don't own" apart from any
 * other write/close/chmod/verification failure, without ever touching the
 * competing path itself.
 */
class ExclusiveCreateConflictError extends RenderError {
  constructor() {
    super("refusing to overwrite an existing output path");
    this.name = "ExclusiveCreateConflictError";
  }
}

/** True if `value` contains any C0 control character or DEL -- checked by character code, never a regex control-character class (keeps this file lint-clean without a `no-control-regex` suppression). */
function containsControlCharacters(value) {
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) {
      return true;
    }
  }
  return false;
}

function requireNonEmptyTrimmed(varName, rawValue) {
  if (rawValue === undefined || rawValue === "") {
    throw new ValidationError(varName, "must be set to a non-empty value");
  }
  if (containsControlCharacters(rawValue)) {
    throw new ValidationError(varName, "must not contain a control character or line break");
  }
  if (rawValue.trim() !== rawValue) {
    throw new ValidationError(varName, "must not have leading or trailing whitespace");
  }
  return rawValue;
}

/**
 * Validates one DNS label: 1-63 ASCII letters/digits/hyphens, never
 * starting or ending with a hyphen.
 */
function isValidDnsLabel(label) {
  if (label.length === 0 || label.length > 63) {
    return false;
  }
  if (label.startsWith("-") || label.endsWith("-")) {
    return false;
  }
  return /^[a-zA-Z0-9-]+$/.test(label);
}

/**
 * True if `normalizedHostname` is exactly one of the reserved/placeholder
 * zones, or a genuine subdomain of one -- an exact, dot-qualified suffix
 * match on whole labels only, the same technique used for the real
 * hostname/zone relationship below. Deliberately never a naive
 * `endsWith(reserved)`: that would also reject a hostname like
 * "notexample.com" purely because its *text* ends with "example.com",
 * even though "notexample.com" is not a subdomain of "example.com" at
 * all (its labels are `["notexample", "com"]`, not `["example", "com"]`
 * with something prepended).
 */
function isReservedExampleDomain(normalizedHostname) {
  for (const reserved of RESERVED_EXAMPLE_ZONES) {
    if (normalizedHostname === reserved || normalizedHostname.endsWith(`.${reserved}`)) {
      return true;
    }
  }
  return normalizedHostname.endsWith(".invalid");
}

/**
 * Validates that `value` is a bare DNS hostname: no scheme, userinfo,
 * port, path, query, fragment, or wildcard -- just ASCII labels joined by
 * `.`, each individually valid, at most 253 characters total. Returns the
 * lowercase-normalized hostname.
 */
function validateHostnameField(varName, rawValue) {
  const value = requireNonEmptyTrimmed(varName, rawValue);

  if (value.includes("://")) {
    throw new ValidationError(varName, "must not include a URL scheme");
  }
  if (value.includes("@")) {
    throw new ValidationError(varName, "must not include user-info");
  }
  if (value.includes("/")) {
    throw new ValidationError(varName, "must not include a path");
  }
  if (value.includes("?")) {
    throw new ValidationError(varName, "must not include a query string");
  }
  if (value.includes("#")) {
    throw new ValidationError(varName, "must not include a fragment");
  }
  if (value.includes("*")) {
    throw new ValidationError(varName, "must not include a wildcard");
  }
  if (value.includes(":")) {
    throw new ValidationError(varName, "must not include a port");
  }
  if (value.length > 253) {
    throw new ValidationError(varName, "exceeds the maximum DNS name length");
  }

  const labels = value.split(".");
  if (labels.some((label) => !isValidDnsLabel(label))) {
    throw new ValidationError(varName, "must consist only of valid ASCII DNS labels");
  }

  const normalized = value.toLowerCase();
  if (isReservedExampleDomain(normalized)) {
    throw new ValidationError(varName, "must not be a reserved/placeholder example domain or a subdomain of one");
  }

  return normalized;
}

function validateWorkerName(varName, rawValue) {
  const value = requireNonEmptyTrimmed(varName, rawValue);
  if (value.length > 63) {
    throw new ValidationError(varName, "exceeds the maximum Worker name length");
  }
  if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/.test(value)) {
    throw new ValidationError(
      varName,
      "must be lowercase alphanumeric-and-hyphen, starting and ending with a letter or digit",
    );
  }
  return value;
}

/** Minimal, deliberately conservative email syntax check -- local-part plus a validly-shaped domain. Never echoes the value. */
function validateEmailField(varName, rawValue) {
  const value = requireNonEmptyTrimmed(varName, rawValue);
  const atIndex = value.indexOf("@");
  if (atIndex <= 0 || atIndex !== value.lastIndexOf("@") || atIndex === value.length - 1) {
    throw new ValidationError(varName, "must be a single local-part@domain address");
  }
  const localPart = value.slice(0, atIndex);
  const domainPart = value.slice(atIndex + 1);
  if (!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(localPart)) {
    throw new ValidationError(varName, "has an invalid local part");
  }
  const domainLabels = domainPart.split(".");
  if (domainLabels.length < 2 || domainLabels.some((label) => !isValidDnsLabel(label))) {
    throw new ValidationError(varName, "has an invalid domain part");
  }
  return value;
}

function validateAbsoluteExistingDirectory(varName, rawValue) {
  const value = requireNonEmptyTrimmed(varName, rawValue);
  if (!isAbsolute(value)) {
    throw new ValidationError(varName, "must be an absolute path");
  }
  let stats;
  try {
    stats = lstatSync(value);
  } catch {
    throw new ValidationError(varName, "must point to an existing directory");
  }
  if (stats.isSymbolicLink()) {
    throw new ValidationError(varName, "must not be a symlink");
  }
  if (!stats.isDirectory()) {
    throw new ValidationError(varName, "must point to a directory");
  }
  return value;
}

/**
 * Symlink-safe verification of one path this project's own checkout is
 * expected to already contain, relative to `DEPLOY_WEB_ROOT`
 * (`worker/contact.ts` or `dist/`). Uses `lstatSync` -- never `statSync`
 * -- so a symlink at this exact path is rejected outright rather than
 * transparently followed to whatever it points at.
 */
function verifyRepoPath(varName, path, description, expectedKind) {
  let stats;
  try {
    stats = lstatSync(path);
  } catch {
    throw new ValidationError(varName, `does not contain ${description}`);
  }
  if (stats.isSymbolicLink()) {
    throw new ValidationError(varName, `${description} must not be a symlink`);
  }
  if (expectedKind === "file" && !stats.isFile()) {
    throw new ValidationError(varName, `${description} is not a regular file`);
  }
  if (expectedKind === "directory" && !stats.isDirectory()) {
    throw new ValidationError(varName, `${description} is not a directory`);
  }
}

/**
 * Validates every `DEPLOY_*` input and returns a fully-normalized,
 * fully-verified configuration object. Throws the first `ValidationError`
 * encountered -- callers that want every failure at once should call the
 * individual `validate*` helpers directly (see the test suite).
 */
export function validateEnvironment(env) {
  for (const varName of REQUIRED_ENV_VARS) {
    if (env[varName] === undefined || env[varName] === "") {
      throw new ValidationError(varName, "must be set to a non-empty value");
    }
  }

  const outDir = validateAbsoluteExistingDirectory("DEPLOY_OUT_DIR", env.DEPLOY_OUT_DIR);
  const hostname = validateHostnameField("DEPLOY_HOSTNAME", env.DEPLOY_HOSTNAME);
  const zoneName = validateHostnameField("DEPLOY_ZONE_NAME", env.DEPLOY_ZONE_NAME);

  if (hostname !== zoneName && !hostname.endsWith(`.${zoneName}`)) {
    // Deliberately not `hostname.endsWith(zoneName)`: that accepts
    // "notexample.com" for zone "example.com". A real subdomain
    // relationship requires the dot-qualified suffix, or exact equality
    // at the zone apex.
    throw new ValidationError("DEPLOY_HOSTNAME", "must equal DEPLOY_ZONE_NAME or be a subdomain of it");
  }

  const staticWorkerName = validateWorkerName("DEPLOY_STATIC_WORKER_NAME", env.DEPLOY_STATIC_WORKER_NAME);
  const contactWorkerName = validateWorkerName("DEPLOY_CONTACT_WORKER_NAME", env.DEPLOY_CONTACT_WORKER_NAME);
  if (staticWorkerName.toLowerCase() === contactWorkerName.toLowerCase()) {
    throw new ValidationError("DEPLOY_CONTACT_WORKER_NAME", "must differ from DEPLOY_STATIC_WORKER_NAME");
  }

  const contactToEmail = validateEmailField("DEPLOY_CONTACT_TO_EMAIL", env.DEPLOY_CONTACT_TO_EMAIL);
  const contactFromEmail = validateEmailField("DEPLOY_CONTACT_FROM_EMAIL", env.DEPLOY_CONTACT_FROM_EMAIL);

  const webRoot = validateAbsoluteExistingDirectory("DEPLOY_WEB_ROOT", env.DEPLOY_WEB_ROOT);
  const contactMainPath = join(webRoot, "worker", "contact.ts");
  verifyRepoPath("DEPLOY_WEB_ROOT", contactMainPath, "the expected worker/contact.ts entry point", "file");

  const distDirPath = join(webRoot, "dist");
  verifyRepoPath("DEPLOY_WEB_ROOT", distDirPath, "a built dist/ directory", "directory");

  return {
    outDir,
    hostname,
    zoneName,
    staticWorkerName,
    contactWorkerName,
    contactToEmail,
    contactFromEmail,
    contactMainPath,
    distDirPath,
  };
}

/** Builds the static-assets unit's Wrangler config object. No `main`, no report/Worker code path for a missing asset. */
export function buildStaticConfig(validated) {
  return {
    name: validated.staticWorkerName,
    compatibility_date: COMPATIBILITY_DATE,
    workers_dev: false,
    preview_urls: false,
    assets: {
      directory: validated.distDirPath,
      not_found_handling: "404-page",
    },
    routes: [{ pattern: validated.hostname, custom_domain: true }],
  };
}

/** Builds the contact-API unit's Wrangler config object. Routes only the exact `<hostname>/api/contact` path to the existing, unmodified Worker source. */
export function buildContactConfig(validated) {
  return {
    name: validated.contactWorkerName,
    main: validated.contactMainPath,
    compatibility_date: COMPATIBILITY_DATE,
    workers_dev: false,
    preview_urls: false,
    routes: [{ pattern: `${validated.hostname}${CONTACT_PATH}`, zone_name: validated.zoneName }],
    vars: {
      TURNSTILE_EXPECTED_HOSTNAME: validated.hostname,
      CONTACT_TO_EMAIL: validated.contactToEmail,
      CONTACT_FROM_EMAIL: validated.contactFromEmail,
    },
    send_email: [
      {
        name: "EMAIL",
        destination_address: validated.contactToEmail,
        allowed_sender_addresses: [validated.contactFromEmail],
      },
    ],
    // Wrangler's own enforced configuration field: Wrangler refuses to
    // deploy a Worker whose declared required secret has not already
    // been provisioned (`wrangler secret put TURNSTILE_SECRET_KEY`, run
    // manually by an operator, before the first deployment-workflow
    // dispatch -- see docs/deployment/web-production.md). This script
    // never reads, requires, or writes the secret's value.
    secrets: {
      required: ["TURNSTILE_SECRET_KEY"],
    },
  };
}

function serialize(configObject) {
  return `${JSON.stringify(configObject, null, 2)}\n`;
}

/**
 * The real filesystem primitives `renderConfigs` writes through, as a
 * plain, replaceable object -- a narrow injectable seam. `main()` (the
 * real CLI) always uses this exact default; the test suite passes a
 * second argument built from this same object with one or two methods
 * wrapped to fail on a specific call, so a test can deterministically
 * exercise "open/write/close/chmod/verify for file N fails" without
 * reimplementing `renderConfigs`'s own transactional algorithm.
 */
export const defaultFileOps = { openSync, writeSync, closeSync, chmodSync, statSync, unlinkSync, lstatSync };

function assertPathAvailable(filePath, fileOps) {
  let existing;
  try {
    existing = fileOps.lstatSync(filePath);
  } catch {
    existing = null;
  }
  if (existing) {
    throw new RenderError("refusing to overwrite an existing output path");
  }
}

/**
 * Sets and re-verifies mode 0600 on an already-created file. Throws a
 * sanitized `RenderError` (never the raw filesystem error, which could
 * embed the absolute path) if either step fails.
 */
function finalizeFilePermissions(filePath, fileOps) {
  try {
    fileOps.chmodSync(filePath, FILE_MODE);
    const finalMode = fileOps.statSync(filePath).mode & 0o777;
    if (finalMode !== FILE_MODE) {
      throw new RenderError("could not set the required file permissions on a generated output file");
    }
  } catch (error) {
    if (error instanceof RenderError) {
      throw error;
    }
    throw new RenderError("could not set the required file permissions on a generated output file");
  }
}

/**
 * Attempts to remove every path in `paths` (each one this same
 * `renderConfigs` call created earlier in the same attempt -- never any
 * other path). Returns the subset that could not be removed, so the
 * caller can report an honest, sanitized "cleanup incomplete" failure
 * rather than silently leaving a partially-written file behind while
 * claiming a clean failure.
 */
function removeCreatedFiles(paths, fileOps) {
  const failures = [];
  for (const path of paths) {
    try {
      fileOps.unlinkSync(path);
    } catch {
      failures.push(path);
    }
  }
  return failures;
}

/** Closes `fd` and swallows any error -- used only when a write has already failed and is about to be reported; the caller's cleanup pass removes the owned path regardless of whether the descriptor itself closed cleanly. */
function closeQuietly(fd, fileOps) {
  try {
    fileOps.closeSync(fd);
  } catch {
    // Best-effort only, deliberately ignored -- see the docstring above.
  }
}

/**
 * Writes every byte of `buffer` to `fd`, looping on a short/partial
 * `writeSync` result rather than assuming one call always writes the
 * whole buffer (rare for a regular file, but not guaranteed by the
 * underlying syscall -- and exactly the shape a fault-injected `fileOps`
 * in the test suite uses to simulate "writing partially succeeds and then
 * throws").
 */
function writeAllSync(fd, buffer, fileOps) {
  let offset = 0;
  while (offset < buffer.length) {
    const bytesWritten = fileOps.writeSync(fd, buffer, offset, buffer.length - offset, offset);
    if (!bytesWritten || bytesWritten <= 0) {
      throw new RenderError("could not write the generated Wrangler configuration file");
    }
    offset += bytesWritten;
  }
}

/**
 * Creates one output file, establishing this invocation's ownership of
 * `filePath` at the earliest possible point -- the instant the exclusive
 * (`wx`) open succeeds, *before* a single byte of content has been
 * written. `ownedPaths` is updated immediately after that open succeeds,
 * so a later failure writing, closing, chmod'ing, or verifying this same
 * file still triggers cleanup of the (now known-to-exist, possibly empty
 * or partial) file on disk -- closing the gap where recording ownership
 * used to happen only after the whole write completed, leaving cleanup
 * unaware that a file had already been created when a later step failed.
 *
 * If the exclusive open itself fails with `EEXIST`, `filePath` is a
 * competing path this invocation never owned -- whether the earlier
 * preflight check missed it, or it appeared in the window between
 * preflight and this exact call. It is never added to `ownedPaths` and is
 * therefore never touched by this invocation's own cleanup.
 */
function createOwnedFile(filePath, contents, fileOps, ownedPaths) {
  let fd;
  try {
    fd = fileOps.openSync(filePath, "wx", FILE_MODE);
  } catch (error) {
    if (error && error.code === "EEXIST") {
      throw new ExclusiveCreateConflictError();
    }
    throw new RenderError("could not create the generated Wrangler configuration file");
  }

  // From this exact point on, this invocation owns `filePath`.
  ownedPaths.push(filePath);

  try {
    writeAllSync(fd, Buffer.from(contents, "utf8"), fileOps);
  } catch (error) {
    closeQuietly(fd, fileOps);
    if (error instanceof RenderError) {
      throw error;
    }
    throw new RenderError("could not write the generated Wrangler configuration file");
  }

  try {
    fileOps.closeSync(fd);
  } catch {
    throw new RenderError("could not finalize the generated Wrangler configuration file");
  }

  finalizeFilePermissions(filePath, fileOps);
}

/**
 * Validates `env`, then writes both Wrangler config files into
 * `validated.outDir` (fixed filenames, mode 0600). Fully transactional
 * with respect to *this invocation's own* output: neither output path is
 * ever overwritten if something already exists there at the moment of its
 * exclusive creation (see `createOwnedFile`); and if opening, writing,
 * closing, chmod'ing, or verifying *either* file fails for any reason,
 * every path this same call has come to own is removed before the failure
 * is reported -- never a partial pair, and never a path this call did not
 * itself create. If that cleanup cannot fully complete, the failure
 * explicitly says so rather than reporting a misleadingly clean error.
 *
 * `fileOps` defaults to the real filesystem (`defaultFileOps`) and is
 * never overridden by `main()`/the real CLI -- it exists so tests can
 * inject a deterministic failure at one precise call, exercising this
 * exact production algorithm, without mocking Node's own module system or
 * reimplementing it.
 */
export function renderConfigs(env, fileOps = defaultFileOps) {
  const validated = validateEnvironment(env);
  const staticConfigPath = join(validated.outDir, STATIC_CONFIG_FILENAME);
  const contactConfigPath = join(validated.outDir, CONTACT_CONFIG_FILENAME);

  const staticContents = serialize(buildStaticConfig(validated));
  const contactContents = serialize(buildContactConfig(validated));

  // An early, best-effort existence check -- gives a clear, specific error
  // in the common case (a leftover file from a previous run). It is NOT
  // what prevents overwriting a path that appears between this check and
  // file creation: that guarantee comes only from the exclusive (`wx`)
  // open inside `createOwnedFile` below, which atomically fails with
  // `EEXIST` if the path exists at the instant of the `open()` syscall
  // itself, regardless of what this preflight check saw.
  assertPathAvailable(staticConfigPath, fileOps);
  assertPathAvailable(contactConfigPath, fileOps);

  const ownedPaths = [];
  try {
    createOwnedFile(staticConfigPath, staticContents, fileOps, ownedPaths);
    createOwnedFile(contactConfigPath, contactContents, fileOps, ownedPaths);
  } catch (error) {
    const cleanupFailures = removeCreatedFiles(ownedPaths, fileOps);
    if (cleanupFailures.length > 0) {
      throw new RenderError(
        "could not write the generated Wrangler configuration, and automatic cleanup did not remove every partially-created output file",
      );
    }
    if (error instanceof ExclusiveCreateConflictError) {
      throw error;
    }
    throw new RenderError("could not write the generated Wrangler configuration");
  }

  return { staticConfigPath, contactConfigPath };
}

function isMainModule() {
  return process.argv[1] !== undefined && import.meta.url === `file://${process.argv[1]}`;
}

function main() {
  try {
    renderConfigs(process.env);
  } catch (error) {
    // Only this script's own deliberately-constructed, already-sanitized
    // error types are ever printed by message -- anything else (a raw OS
    // error, a bug, a non-Error throw) collapses to one fixed generic
    // line, so an absolute path, an environment-variable value, or any
    // other unexpected detail can never reach stdout/stderr.
    const message =
      error instanceof ValidationError || error instanceof RenderError ? error.message : "an unexpected error occurred";
    process.stderr.write(`Wrangler configuration rendering failed: ${message}\n`);
    process.exitCode = 1;
    return;
  }
  process.stdout.write("Wrangler configuration rendered successfully.\n");
}

if (isMainModule()) {
  main();
}
