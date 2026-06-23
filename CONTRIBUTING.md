# Contributing to Stargraph

Thanks for contributing! Stargraph is licensed under Apache-2.0 and accepts contributions under the Developer Certificate of Origin (DCO) — no CLA required.

## Sign-off (DCO)

Every commit must be signed off:

    git commit -s -m "Add feature X"

This appends `Signed-off-by: Your Name <you@example.com>` to the commit message, certifying you can legally contribute the code (full text: https://developercertificate.org/). The CI rejects PRs with unsigned commits. To fix existing commits:

    git rebase --signoff origin/main

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

`pyright` resolves the optional subsystems (ml, stores, skills-rag, …), so type-check against an environment that has the extras installed:

    uv sync --group dev --all-extras
    uv run pyright

With the extras present, `pyright` is clean on `main` (0 errors). A bare install reports hundreds of spurious `reportMissingImports`/`reportUnknown*` errors from the unresolved optional deps — that is an environment gap, not a code problem. New code must keep `pyright --all-extras` at zero errors.

## License

By contributing, you agree your contributions are licensed under Apache-2.0 (see LICENSE). The DCO sign-off serves as the legal record of provenance — Stargraph does not require a separate CLA.
