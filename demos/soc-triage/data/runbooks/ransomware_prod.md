# Runbook: Ransomware on a Production Asset

**Trigger signatures:** `T1486.ransomware.mass_file_encrypt`, mass file rewrite, shadow-copy deletion (`vssadmin`), known ransomware extensions.

**Default disposition (prod tier):** `auto_remediate` → **requires analyst gate (HITL)** before isolation executes on a tier-0 asset.

## Immediate actions
1. Network-isolate the host (EDR contain) — pause, do not power off (preserve volatile memory).
2. Snapshot the volume for forensics before any restore.
3. Block the observed C2 indicators (IP / domain / hash) at the perimeter.
4. Identify shared-drive blast radius; isolate mounts before lateral encryption spreads.

## Restore
- Restore from the most recent clean snapshot predating the first encryption event.
- Validate backup integrity before reattach.

## Escalate when
- The encrypted asset stores regulated data (restricted classification) → notify CISO + legal.
- Exec-owned asset → notify the business owner per the exec-owner notification rule.

**MTTR target:** < 15 min to containment.
