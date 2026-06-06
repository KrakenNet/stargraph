# Stargraph — Directory-Based Plugin Discovery (`stargraph-dir-plugins`)

**Status:** Proposal v0.1
**Audience:** Plugin authors, end-users running `stargraph serve`, anyone who has used Claude Code's `~/.claude/plugins/` workflow
**Stability:** Extension over the stable Plugin API; no core changes required

---

## 1. Motivation

Today, Stargraph plugins are pip-installable Python distributions that register
themselves through the `stargraph` entry-point group. That's the right model
for production deployments — versioning, dependency resolution, audit, and
namespace-conflict detection all benefit from packaging metadata.

But onboarding suffers. To try a community skill or share a tweak, an author
has to:

1. Write a `pyproject.toml` with the right entry point.
2. Build and install the package (`pip install -e .` or via private index).
3. Restart `stargraph serve`.

Claude Code, Cursor, and adjacent ecosystems demonstrated that a much
lighter path works for prototyping and personal-config use:

```
~/.claude/plugins/
  my-plugin/
    plugin.json
    skills/
    tools/
    hooks.json
```

Drop the directory in, restart (or `/reload-plugins`), done.

`stargraph-dir-plugins` provides that experience for Stargraph — without
abandoning the typed, signed plugin contract that production deployments
rely on.

---

## 2. Layered model

```
~/.stargraph/plugins/<plugin-dir>/   (or $STARGRAPH_PLUGINS_DIR)
   │
   ▼
stargraph-dir-plugins discovery scanner    (runs at stargraph serve startup)
   │
   ├── reads plugin.toml manifest         -> PluginManifest
   ├── compiles skills/*.md               -> via stargraph-md-skills (sibling)
   ├── imports tools/*.py                 -> @tool callables -> ToolSpec
   ├── reads packs/*/                     -> signed Bosun PackSpec
   └── reads stores/*.toml (optional)     -> StoreSpec
   │
   ▼
Synthesizes a transient PluginManifest + hookimpls in-memory
   │
   ▼
Registers with the same pluggy PluginManager as pip-installed plugins
   │
   ▼
Indistinguishable from a "real" plugin from the runtime's perspective
```

The discovery scanner is the only new code. Every downstream guarantee
(api_version match, namespace conflicts, capability gates, replay,
audit chain) is inherited from the existing two-stage loader.

---

## 3. Directory layout

```
~/.stargraph/plugins/
  refund-toolkit/                       -- the plugin dir name
    plugin.toml                         -- manifest (required)
    skills/                             -- compiled by stargraph-md-skills
      refund-handler/
        SKILL.md
      cancel-order/
        SKILL.md
    tools/                              -- imported as Python modules
      billing.py
      __init__.py
    packs/                              -- Bosun rule packs (signed)
      refund-policy/
        pack.yaml
        rules/
          *.yaml
        pack.sig                        -- detached Ed25519
    stores/                             -- (optional) declarative store specs
      refund_history.toml
    capabilities.toml                   -- (optional) declared capabilities
    fixtures/                           -- (optional) examples / test data
```

### 3.1 `plugin.toml` manifest

```toml
name = "refund-toolkit"
version = "0.3.1"
api_version = "1.x"
order = 100                              # registration order; lower = earlier
namespaces = ["refund_toolkit"]

[author]
name = "Sean"
email = "sean@example.com"
url = "https://github.com/sean/refund-toolkit"

[trust]
# Bosun pack verification; absent means the plugin ships no signed packs.
keys = [
  "ed25519:abcdef1234..."                # author's pubkey
]

[runtime]
# Python imports happen lazily during stage-2 (NFR-7 invariant).
python_path = ["tools"]                  # paths added to sys.path for imports
```

Maps 1:1 to the existing `PluginManifest` Pydantic model. No new fields in
the runtime contract.

### 3.2 `skills/` directory

One subdirectory per skill, each containing a `SKILL.md`. Compiled by the
`stargraph-md-skills` plugin (sibling design doc) at discovery time. Result is
a `list[Skill]` returned through the normal `register_skills` hookspec.

### 3.3 `tools/` directory

Ordinary Python module(s). Each `@tool`-decorated callable is collected and
returned through `register_tools`. Module imports happen during stage-2 only
(import-cold stage-1 is preserved per NFR-7).

### 3.4 `packs/` directory

Subdirs are Bosun rule packs in the existing on-disk format. Each pack is
verified against `[trust].keys` from `plugin.toml` plus the global
`FilesystemTrustStore` (TOFU). Unsigned packs are loaded **read-only** into
a sandbox and emit a warning; this is recorded in the audit chain so
production deployments can refuse them via policy.

### 3.5 `stores/` directory (optional)

Declarative TOML for stores that don't need custom Python code (e.g.,
"this plugin needs a sqlite_doc store at this path"). Translated into
`StoreSpec` instances.

### 3.6 `capabilities.toml` (optional)

Declares any new capability strings the plugin's tools or skills require:

```toml
[capabilities."billing.refund"]
description = "Issue a refund against the billing system"
sensitivity = "high"
```

Capabilities surfaced here become known to `stargraph.security.capabilities`
so the gate can enforce them.

---

## 4. Discovery and load lifecycle

```
stargraph serve startup
   │
   ▼
1. Standard pip-based plugin loader runs (today's path, unchanged)
   │
   ▼
2. stargraph-dir-plugins scanner runs
   ├── for each dir in $STARGRAPH_PLUGINS_DIR (or default ~/.stargraph/plugins):
   │     ├── parse plugin.toml -> PluginManifest
   │     ├── enforce api_version compatibility (stage-1, import-cold)
   │     ├── detect namespace conflicts vs already-registered set
   │     └── synthesize a hookimpl shim that knows where to find the dir
   ▼
3. PluginManager.register(shim) for each accepted dir-plugin
   ▼
4. Stage-2 hookspecs fire across all plugins (pip + dir):
   register_tools / register_skills / register_stores / register_packs
   │
   ▼
5. Audit chain emits a startup record:
   - count of pip plugins, dir plugins
   - per-plugin: name, version, signing status (for packs)
   - any rejected dir-plugins with reason
```

Reload command (no restart needed):

```
$ stargraph plugins reload
```

Re-runs steps 2-4 against the same `PluginManager`. Existing in-flight runs
are unaffected; new requests pick up new plugins. Hot-reload semantics
mirror the Fathom rule-reload pattern.

---

## 5. Trust model

| Source | Default trust | Production policy |
|---|---|---|
| pip-installed plugin from declared index | trusted | trusted |
| dir-plugin with all packs signed by known key | trusted | trusted |
| dir-plugin with unsigned packs | sandbox warn | **reject** (config flag) |
| dir-plugin from system path (`/etc/stargraph/plugins`) | trusted | trusted |
| dir-plugin from user path (`~/.stargraph/plugins`) | sandbox warn | **reject** (config flag) |

The default trust matrix favors "easy to try" for development. Production
deployments set `stargraph.plugins.allow_unsigned = false` and
`stargraph.plugins.dir_paths = ["/etc/stargraph/plugins"]` to lock the system
path only.

This mirrors how Claude Code distinguishes `~/.claude/plugins/` (user) from
the marketplace, except Stargraph adds cryptographic verification of the
signed-pack subset.

---

## 6. CLI surface

`stargraph-dir-plugins` contributes these subcommands:

```
stargraph plugins list                     -- show loaded plugins (pip + dir)
stargraph plugins reload                   -- re-scan dir-plugin paths
stargraph plugins inspect <name>           -- show manifest + signing status
stargraph plugins new <name>               -- scaffold a new dir-plugin layout
stargraph plugins verify <path>            -- offline lint of plugin.toml + packs
stargraph plugins enable <name>            -- mark trusted in TOFU store
stargraph plugins disable <name>           -- mark blocked in TOFU store
```

`stargraph plugins new` scaffolds:

```
~/.stargraph/plugins/<name>/
  plugin.toml               -- pre-filled from prompts
  skills/example/SKILL.md   -- template skill
  tools/example.py          -- @tool example
  README.md                 -- "how to extend this plugin"
```

---

## 7. Comparison to Claude Code plugin model

| | Claude Code | `stargraph-dir-plugins` |
|---|---|---|
| Drop-in dir | `~/.claude/plugins/` | `~/.stargraph/plugins/` |
| Manifest | `plugin.json` | `plugin.toml` (typed via `PluginManifest`) |
| Skill format | `SKILL.md` | `SKILL.md` (via `stargraph-md-skills`) |
| Tools | Per-plugin definitions | Typed `@tool` callables (replay-aware) |
| Hooks | `hooks.json` | Existing `pluggy` hookspecs (richer) |
| Reload | `/reload-plugins` | `stargraph plugins reload` |
| Versioning | Implicit | Required SemVer + api_version major-match |
| Signing | None | Bosun packs Ed25519-verified |
| Hot-swap policy | Restart per plugin | Bosun rule packs hot-reload mid-run |

Same DX where it makes the difference (drop-in, single-folder authoring),
plus the typed contract and signing where production needs them.

---

## 8. Out of scope (v0.1)

- Marketplace / registry. Plugins live on disk or in pip index. A future
  marketplace would layer on top of either, not replace them.
- Auto-update of dir-plugins from a remote source. Stays manual.
- Cross-plugin dependency resolution beyond namespace-conflict detection.
- Sandboxed execution of unsigned dir-plugin Python code. v0.1 either
  trusts the dir or refuses to load it; sandboxing is a v0.2 concern.

---

## 9. Open questions

- Should `plugin.toml` support a `[depends]` table that lists other
  dir-plugins by `name@version`? Today the entry-point loader doesn't
  do cross-plugin deps; would dir-plugins be the right place to add
  light-weight ordering hints beyond `order`?
- Do we want a `stargraph plugins doctor` command that explains why a
  plugin failed to load (signing, namespace, api_version, capability)?
- Where should the `stargraph-dir-plugins` plugin itself live — in the core
  Stargraph distribution (always-on), or as an optional `pip install
  stargraph[dir-plugins]` extra?
