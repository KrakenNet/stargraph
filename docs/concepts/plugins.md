# Plugin Model

Stargraph uses a two-stage plugin loader built on `importlib.metadata` entry points and `pluggy` hooks. Distributions register under one of four groups: `stargraph.tools`, `stargraph.skills`, `stargraph.stores`, or `stargraph.packs`. Each distribution exposes a single `stargraph_plugin()` callable plus a stdlib-only `PluginManifest` so Stargraph can check `api_version` **before** importing the module.

## Why two stages

- **Discovery without import.** Entry points are enumerated, not loaded — startup stays cheap.
- **Compatibility gating.** Manifests are read from the dist-info; an incompatible plugin never imports.
- **Hook composition.** Once gated, `pluggy` registers the module and composes hooks deterministically.

## Reference

- [PluginManifest schema, entry-point groups, two-stage loader](../reference/plugin-manifest.md)
- [Hookspec catalog (collect-all vs firstresult, full signatures)](../reference/hookspecs.md)
- [Skills (Skill model, ReactSkill, salience, refs)](../reference/skills.md)
