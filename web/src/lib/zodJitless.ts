/**
 * Side-effect-only module: disables Zod v4's opportunistic `new
 * Function(...)`-based fast validation path globally (`config({ jitless:
 * true })`), by importing it for its side effect alone, before any
 * `.parse()`/`.safeParse()` call.
 *
 * Found during Phase 3J's cross-browser accessibility/product-quality
 * scan: Zod's fast path probes `new Function("")` in a try/catch to
 * detect whether `eval`-like execution is available
 * (`node_modules/zod/v4/core/util.js`, `allowsEval`). The throw itself is
 * caught and swallowed, so nothing breaks functionally -- but under this
 * site's restrictive CSP (`script-src` never includes `'unsafe-eval'`,
 * intentionally, on every route -- see `astro.config.mjs`/
 * `src/lib/reportRouteCsp.ts`/`src/lib/contactRouteCsp.ts`), Firefox
 * still reports the blocked call as a `securitypolicyviolation` console
 * error, even though the exception was caught. Chromium and WebKit did
 * not surface this as a console error in this project's own testing, but
 * relying on that is not safe: it is a real CSP violation regardless of
 * whether a given engine happens to log it.
 *
 * `jitless: true` skips the probe entirely (Zod's own upstream code
 * explicitly special-cases this for "strict CSPs"), at the cost of a
 * slightly slower validation path -- never a change in validation
 * behavior or result. Adding `'unsafe-eval'` to any CSP instead was
 * deliberately rejected: it would weaken this site's CSP, which every
 * route's own tests and this milestone's invariants require to never
 * happen.
 *
 * Imported first (for its side effect) by every module that is the real
 * entry point for Zod validation running *in the browser*:
 * `../features/check-catalogue/catalogue.ts` (the `/checks` island),
 * `../features/contact-form/contract.ts` (`/request-demo` and
 * `/feedback`), and `../features/report-import/schemas.ts` (the
 * `/explorer` island's local file import path). The two demo routes
 * (`/demo/kubernetes`, `/demo/gitlab`) parse their reports at build time,
 * in Node, via `.astro` frontmatter -- never in the browser -- so they
 * were never affected and need no import here.
 */

import { config } from "zod";

config({ jitless: true });
