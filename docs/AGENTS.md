# AGENTS.md — docs

Local contract for documentation. Parent: [`../AGENTS.md`](../AGENTS.md).

## Purpose

User-facing docs built with MkDocs (`mkdocs.yml` at repo root). Conceptual depth
lives in `design-docs/` (ADRs, specs); `docs/` is the how-to/tutorial/reference
surface.

## Local Contracts

- **`make docs-build` runs `mkdocs build --strict`** — broken links, missing nav
  entries, and bad references fail the build. Add new pages to the `nav:` in
  `mkdocs.yml`.
- Structure: `getting-started.md`, `architecture-map.md`, `tutorials/` (guided),
  `how-to/` (task recipes), `reference/` (API/schema), `concepts/` +
  `explanation/` (the why), `serve/`, `knowledge/`, `security/`, `guides/`.
- Commands and file paths in docs must be real and current — examples referenced
  from docs must exist under `examples/` and stay golden-tested.
- Don't leave `TODO` placeholders in shipped pages; either finish or omit.

## Work Guidance

- Recipe/task → put it in `how-to/`. Guided narrative → `tutorials/`. Stable
  contracts → `reference/`. Rationale/trade-offs → `explanation/`.
- Keep `architecture-map.md` in sync when packages or key symbols move.

## Verification

`make docs-build`.
