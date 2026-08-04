# syntax=docker/dockerfile:1
# Stargraph serve container image (multi-stage, air-gap friendly).
#
# Default (network-attached) build resolves dependencies from the
# committed uv.lock:
#
#   docker build -t stargraph .
#
# Air-gapped build: stage a wheelhouse first (see
# docs/guides/air-gap-deployment.md §1 for the wheelhouse recipe), drop
# it at ./wheelhouse/ in the build context, and build with no network:
#
#   docker build --network=none -t stargraph .
#
# When wheelhouse/*.whl is present the builder installs with
# `pip --no-index --find-links wheelhouse` and never reaches out;
# otherwise it falls back to `uv sync --frozen`.

FROM python:3.13-slim AS builder

ENV UV_PROJECT_ENVIRONMENT=/venv \
    UV_PYTHON_DOWNLOADS=never \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY . .

RUN set -eux; \
    python -m venv /venv; \
    if ls wheelhouse/*.whl >/dev/null 2>&1; then \
        # Air-gap path: offline install from the staged wheelhouse
        # (docs/guides/air-gap-deployment.md §1). --no-index blocks any
        # PyPI reach-out; a missing transitive fails loudly.
        /venv/bin/pip install --no-index --find-links wheelhouse stargraph; \
    else \
        # Network path: locked, non-editable install via uv.
        pip install uv; \
        uv sync --frozen --no-dev --no-editable; \
    fi

FROM python:3.13-slim

# UTC per the air-gap guide's timezone recommendation: audit/provenance
# timestamps are UTC-always; keeping process logs in UTC too avoids
# skew during forensics.
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

RUN useradd --create-home --uid 1000 stargraph \
    && mkdir -p /data \
    && chown stargraph:stargraph /data

COPY --from=builder /venv /venv

USER stargraph
WORKDIR /home/stargraph

# /data holds the SQLite checkpointer DB + JSONL audit log. Mount a
# volume here for durable state (POSIX-local filesystem required -- the
# checkpointer refuses NFS/SMB; see docs/guides/air-gap-deployment.md §5).
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"]

ENTRYPOINT ["stargraph"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", \
     "--db", "/data/checkpoint.sqlite", "--audit-log", "/data/audit.jsonl"]
