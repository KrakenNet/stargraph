# Runbook: Command-and-Control Beacon

**Trigger signatures:** `beacon.cobaltstrike.jitter`, periodic jittered egress, known C2 framework fingerprints (`T1071`).

**Default disposition (prod tier):** `auto_remediate` → **analyst gate (HITL)** before host isolation.

## Immediate actions
1. Isolate the beaconing host (EDR contain).
2. Capture the beacon destination, jitter interval, and payload hash.
3. Pivot: search for the same C2 indicator across all hosts (lateral movement / staging).
4. Reset credentials used on the affected host (the beacon implies prior access).

## Investigate
- Identify the initial access vector (phish, exposed service, supply chain).
- Reimage rather than clean for confirmed framework implants.

**MTTR target:** < 20 min to isolation.
