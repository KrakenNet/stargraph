# Contributing to Stargraph

Thanks for contributing! Stargraph is licensed under Apache-2.0 and accepts contributions under the Developer Certificate of Origin (DCO) — no CLA required.

## Finding something to work on

- Issues labelled [`good first issue`](https://github.com/KrakenNet/stargraph/labels/good%20first%20issue) are scoped for newcomers and need no deep codebase knowledge.
- Issues labelled [`help wanted`](https://github.com/KrakenNet/stargraph/labels/help%20wanted) are ones maintainers would welcome a contributor on.
- Please **avoid** issues labelled `needs-decision` or `needs-design` — the approach isn't settled yet, so a PR is likely to be reworked or rejected.
- Comment on an issue to say you're picking it up before you start, so we don't duplicate effort.

## Sign-off (DCO)

Every commit must be signed off:

    git commit -s -m "Add feature X"

This appends `Signed-off-by: Your Name <you@example.com>` to the commit message, certifying you can legally contribute the code (full text: https://developercertificate.org/). The `DCO` check rejects PRs with unsigned commits.

**Forgot to sign off?** You don't need to rewrite history. When the `DCO` check fails it comments the exact one-line command to push a *remediation commit* that signs off your earlier commits — just run it and push. (If you'd rather rewrite history, `git rebase --signoff origin/main` also works.)

## Development Setup

Prerequisites: Python 3.13+, [uv](https://docs.astral.sh/uv/).

    git clone https://github.com/KrakenNet/stargraph
    cd stargraph
    uv sync --group dev
    uv run pre-commit install
    uv run pytest -m unit

## Checks (run before pushing)

    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/
    uv run pyright
    uv run pytest

For a large or cross-cutting change, run the full local CI mirror — every gate plus the complete suite including the slow, container-backed tests (needs a running Docker daemon for the Postgres/Neo4j testcontainers):

    make ci

This is the robust superset of the PR checks. On a pull request, the heavy infra jobs (`engine-test`, `knowledge-test`, `cypher-subset`, `serve-test`) run **only** when their subsystem — or a shared/core path — changed; every job always runs on push to `main`. So `make ci` is how you cover the gates your PR skipped before they surface post-merge.

`pyright` resolves the optional subsystems (ml, stores, skills-rag, …), so type-check against an environment that has the extras installed:

    uv sync --group dev --all-extras
    uv run pyright

With the extras present, `pyright` is clean on `main` (0 errors). A bare install reports hundreds of spurious `reportMissingImports`/`reportUnknown*` errors from the unresolved optional deps — that is an environment gap, not a code problem. New code must keep `pyright --all-extras` at zero errors.

## License

By contributing, you agree your contributions are licensed under Apache-2.0 (see LICENSE). The DCO sign-off serves as the legal record of provenance — Stargraph does not require a separate CLA.
