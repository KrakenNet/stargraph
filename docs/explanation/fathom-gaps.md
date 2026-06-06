# Fathom Gaps

Stargraph delegates deterministic governance to [Fathom](https://github.com/KrakenNet/fathom). Fathom is good but narrow — this page lists the gaps that Stargraph users need to know about up front (FR-36, AC-20.1 through AC-20.5). The list is intentionally short and concrete; each entry is something Stargraph's design either works around or explicitly accepts as out of scope for 0.x.

## Known gaps

- **Hot reload is rules-only.** Editing a rule file and rerunning is fine. Editing a template or a Python module that backs a rule pack requires restarting the Stargraph process. Fathom's loader cache is invalidated per file, not per template/module.
- **Templates and modules require restart.** As above — there is no live-reload path for `*.fathom.tpl` or imported Python helpers.
- **No backward chaining.** Fathom is a forward-chaining engine. Goal-driven inference is not available; rules must be written to fire from facts forward to decisions. See `fathom/engine.py:812-994` for the chaining loop.

## What this means for Stargraph

- Authoring iteration on rule logic is fast.
- Authoring iteration on rule **shape** (templates, helpers) is restart-bound — IDE workflows should reflect that.
- Anything that wants "ask whether this goal is provable" needs to be expressed as forward-chaining facts plus a sentinel decision.

> TODO: revisit when Fathom 0.4 lands; if backward chaining or template hot-reload arrives, prune this list.
