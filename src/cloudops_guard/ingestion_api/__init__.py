"""Phase 4D: the local/staging-only `/api/v1` HTTP ingestion service.

**Not deployed anywhere.** This package implements the four endpoints
`docs/milestones/v0.4.0-ingestion-api.md` §E specifies
(`GET /api/v1/capabilities`, `POST /api/v1/reports`,
`GET /api/v1/reports/{ingestion_id}`, `DELETE /api/v1/reports/{ingestion_id}`)
on top of Phase 4B's storage interfaces (`cloudops_guard.ingestion.interfaces`)
and Phase 4C's authentication coordinator
(`cloudops_guard.ingestion.authenticator`). It is a separate service
boundary from `web/`'s Astro site and Cloudflare Worker (§H) -- nothing
here is a route on either of those, and importing this package never
opens a socket, starts a thread, reads a credential, or contacts a
network. A real loopback server exists only when a caller explicitly runs
one (`app.create_app` + an ASGI server, e.g. for a test) -- this package
never binds a public interface itself.

**Dependency justification** (the `api` optional-dependency group in
`pyproject.toml`, kept entirely separate from the base `cloudops-guard`
CLI's dependency set):

- `starlette` -- a thin, well-maintained ASGI toolkit. Used only for its
  `Request`/`Response`/`JSONResponse` convenience wrappers around the raw
  ASGI `scope`/`receive`/`send` protocol -- **never** its `Router`/`Route`
  classes, which default to behavior this contract explicitly forbids
  (automatic trailing-slash redirects, automatic `HEAD`/`OPTIONS`
  handling, framework-default error bodies). All routing and dispatch in
  `app.py` is hand-written.
- `uvicorn` -- a minimal, widely-used ASGI server, needed only to run the
  application on a real loopback socket for genuine concurrent-request
  tests (§13); test code binds it to `127.0.0.1` on an ephemeral port
  and shuts it down deterministically, never a public interface.
- `httpx` -- an async-capable HTTP client, used only by test code to
  drive real concurrent requests against the loopback server above.
- `rfc8785` -- a maintained, standalone implementation of RFC 8785 JSON
  Canonicalization Scheme, required for the deterministic
  `report_fingerprint` algorithm (§E.0) exactly as specified -- writing
  and maintaining a bespoke JCS implementation in-repo was judged riskier
  than depending on a small, focused, spec-conformant library whose only
  job is this one algorithm.
- `anyio` -- already an existing *transitive* dependency of `starlette`
  and `httpx`; made an explicit direct dependency because `app.py`
  (Phase 4D correction pass, item 5) imports `anyio.to_thread` directly,
  to move blocking Argon2id authentication, report validation/
  fingerprinting, and synchronous in-memory store calls onto a bounded
  worker thread (`anyio.to_thread.run_sync`, using its own default,
  bounded thread limiter -- no custom limiter is configured) so the
  ASGI event loop stays free to service other concurrent requests while
  one request's blocking work runs. Still only spawns a worker thread
  lazily, the first time a request actually needs one -- importing this
  package, or calling `create_app`, starts no thread by itself (see
  below).

No database, object store, secret manager, or cloud SDK is imported
anywhere in this package.
"""

from __future__ import annotations
