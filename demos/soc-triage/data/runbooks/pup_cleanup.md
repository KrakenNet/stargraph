# Runbook: Potentially-Unwanted Program (PUP) / Adware

**Trigger signatures:** `pup.adware.toolbar`, browser hijackers, bundled adware (`T1059`).

**Default disposition:** `dismiss` — low-grade nuisance, no incident weight on dev assets.

## Actions
1. Let endpoint policy quarantine / remove the PUP automatically.
2. No analyst action required unless it recurs on a prod/exec-owned asset.

**Escalate only if:** the same PUP appears on a prod tier-0 asset or alongside a higher-severity detection.
