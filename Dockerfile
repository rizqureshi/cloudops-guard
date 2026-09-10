# Phase 4G-A: production container image for the ingestion API
# (`src/cloudops_guard/ingestion_azure/entrypoint.py`). Building and
# running this image locally is permitted by this phase's own scope;
# pushing it to any registry (ACR, GHCR, Docker Hub, or otherwise) is
# Phase 4G-B, separately authorized, work -- see
# `docs/deployment/azure-ingestion-production.md`.
#
# **Base image pinned by content digest, never a mutable tag** -- see
# that same document's "Updating the pinned base image digest" section
# for the documented, controlled process to update this pin. Re-pin only
# via that documented process, never by replacing the digest with a tag.
ARG BASE_IMAGE_DIGEST=python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

FROM ${BASE_IMAGE_DIGEST} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Pinned uv version -- matches this repository's own development
# environment (see `docs/deployment/azure-ingestion-production.md`).
RUN pip install "uv==0.12.0"

WORKDIR /src

# Only the files a wheel build actually needs -- never the full
# repository (no .git history, no tests/, no docs/, no web/). `.dockerignore`
# is the primary boundary; this explicit COPY list is a second,
# belt-and-suspenders one.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# **Correction-pass item 8**: the image must actually use `uv.lock`'s
# exact pinned versions, never a fresh re-resolution against whatever is
# currently newest-compatible on PyPI at build time. Two steps, in this
# order, are what make that true:
#
# 1. `uv export --locked` fails closed if `uv.lock` is not already
#    up to date with `pyproject.toml` (never silently re-locks), then
#    emits the exact, fully-pinned dependency closure for the `api`/
#    `azure-production` extras as a plain `requirements.txt` --
#    `--no-emit-project` excludes the `-e .` editable-install line `uv
#    export` would otherwise emit for the current source tree itself
#    (this build never wants an editable install). Installing *this*
#    file is what pins every dependency to `uv.lock`'s exact version --
#    `uv pip install -r <file>` never re-resolves a version already
#    pinned in the file it's given.
# 2. The wheel itself is then installed with `--no-deps` -- its own
#    dependency metadata is intentionally never consulted a second time
#    here (step 1 already installed the complete, locked closure); this
#    is what prevents `uv pip install "<wheel>[extras]"`'s own normal
#    resolution behavior (the original bug) from ever running at all.
#
# Verified end to end (a real venv built with this exact `uv` version,
# this exact `uv.lock`) to produce an installed package set matching
# `uv export --locked`'s own output exactly, modulo platform-conditional
# markers (e.g. `tzdata`/`colorama`, `sys_platform == 'win32'` only).
RUN uv export --locked --no-dev --no-emit-project \
        --extra api --extra azure-production --no-hashes \
        -o /tmp/requirements.txt && \
    uv build --wheel --out-dir /tmp/dist && \
    uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt && \
    uv pip install --python /opt/venv/bin/python --no-deps "$(echo /tmp/dist/*.whl)"

# ---------------------------------------------------------------------------

FROM ${BASE_IMAGE_DIGEST} AS runtime

# Deterministic metadata identifying the full commit SHA this image was
# built from (task 8's requirement) -- set by the deployment workflow via
# `--build-arg GIT_COMMIT_SHA=<full 40-hex-character SHA>`, never a
# mutable `latest` label. Required, not defaulted to a placeholder that
# could be silently left in place.
ARG GIT_COMMIT_SHA
LABEL org.opencontainers.image.title="cloudops-guard-ingestion-api" \
      org.opencontainers.image.revision="${GIT_COMMIT_SHA}" \
      org.opencontainers.image.source="https://github.com/rizqureshi/cloudops-guard"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# A fixed, numeric, non-root user/group -- never a named user resolved at
# build time against a base image's own (mutable) /etc/passwd, and never
# root (task 8's explicit requirement). No home directory, no login shell.
RUN groupadd --gid 10001 appgroup && \
    useradd --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

# No repository history, tests, reports, tokens, .env files, cloud
# credentials, or build caches exist anywhere in this stage -- this
# stage's own filesystem consists of the pinned base image plus exactly
# the installed venv above; nothing else was ever COPY'd into it.

USER 10001:10001
WORKDIR /app

# Only the application port. No new public HTTP health endpoint is added
# anywhere in this codebase (task 7) -- Azure Container Apps' own TCP
# startup/liveness/readiness probes (`infra/azure/modules/
# container-app.bicep`) check this exact port directly, never issuing an
# HTTP request.
EXPOSE 8000

# Exec form, never shell form -- signals (SIGTERM on Container Apps scale-
# down/revision replacement) reach the Python process directly, letting
# `entrypoint.py`'s own signal handler perform a graceful shutdown
# (closing every database connection pool) rather than being swallowed by
# an intermediate shell.
ENTRYPOINT ["python", "-m", "cloudops_guard.ingestion_azure.entrypoint"]
