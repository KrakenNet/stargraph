# How to Write a Markdown Skill (SKILL.md)

## Goal

Drop a **Claude Code-compatible** `SKILL.md` file into a skills
directory and have Stargraph discover, compile, and register it — no
Python packaging, no entry-points.

## The minimum valid skill

The minimum is exactly the Claude Code format: `name` + `description`
frontmatter and a markdown body. A stock Claude Code skill loads
**unmodified** — Claude-only keys such as `argument-hint`,
`allowed-tools`, `model`, `user-invocable`, and
`disable-model-invocation` are accepted silently.

```markdown
---
name: release-notes
description: Draft release notes from the merged PR list.
---
Summarize the merged PRs into user-facing release notes.
Group by feature/fix/docs. Keep each bullet under 20 words.
```

The body becomes the skill's `system_prompt`. With no `subgraph` and
the default `kind: agent`, the skill mounts the built-in ReAct loop
over its declared tools (an empty tool list is fine — pure-prompt
skill).

## Where to put it

Discovery walks, in order: `$STARGRAPH_SKILLS_DIR`, `./skills/`,
`~/.stargraph/skills/` — one directory per skill, containing
`SKILL.md` (the Claude Code layout). `stargraph serve` seeds every
discovered skill at startup; broken files are skipped with a warning
(one bad drop-in must not take down the server). Two skills resolving
to the same `namespace/name` fail loudly.

## Stargraph superset keys (all optional)

| Key | Default | Meaning |
|---|---|---|
| `version` | `0.1.0` | SemVer for the registry id `namespace/name@version` |
| `namespace` | `local` | Registry namespace |
| `kind` | `agent` | `agent` / `workflow` / `utility` |
| `tools` | `[]` | Tool ids the skill may call, e.g. `std.web_search@1` |
| `requires` | `[]` | Capability strings, e.g. `tools:std:net` |
| `subgraph` | — | Path to an IR YAML, relative to the SKILL.md; must exist |
| `state_schema` | — | Field map compiled to a Pydantic model (below) |
| `examples` | `[]` | `[{inputs: {...}, expected_output: {...}}]` |

`state_schema` declares the skill's output channels
(the `SubGraphNode` write whitelist — see
[How to Build a Skill](build-skill.md)):

```yaml
state_schema:
  query: str                        # shorthand: just the type
  attempts: {type: int, default: 0} # long form with default
```

Types: `str` / `int` / `float` / `bool` / `list` / `dict`.

## Compile and inspect

```bash
stargraph skills compile ./skills/release-notes   # strict; JSON envelope
stargraph skills list                             # all discovery roots
```

`compile` exits 1 with structured errors (`path` / `expected` /
`actual` / `hint`) on anything malformed; unknown frontmatter keys are
reported as warnings, never errors. Registered skills surface via
`GET /v1/registry/skills`.
