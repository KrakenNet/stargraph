# Versioning Policy

Stargraph follows a strict semantic-versioning contract for the runtime, the IR schema, and the plugin ABI (FR-37). The table below tells contributors and downstream plugin authors what kind of change requires which kind of bump.

## Bump matrix

| Bump      | When to use it                                                                                       | Examples                                                                                          |
|-----------|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| **Major** | Backwards-incompatible change to the IR schema, the plugin ABI, the CLI contract, or the wire trace. | Removing or renaming a hookspec; changing the IR root shape; deleting a CLI flag.                 |
| **Minor** | Backwards-compatible additions or behavior changes that plugins/IR may opt into.                     | New optional hookspec; new IR field with a default; new CLI subcommand; relaxed validation rule. |
| **Patch** | Bug fixes and internal changes with no API/IR/CLI surface impact.                                    | Fixing a determinism bug; perf improvement; doc fix; tightening an internal invariant.            |

## Rules

- The IR schema, the plugin ABI (`api_version`), and the CLI surface each carry their own SemVer line in addition to the package version. A major bump in any one forces a major bump on the package.
- Deprecations land in a minor release with a runtime warning and ship at least one full minor cycle before removal.
- Security fixes ship as patch releases on every supported minor.

> TODO: link to the deprecation log and the supported-version table once they exist.
