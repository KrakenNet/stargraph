# Runbook: Reconnaissance / Port Scan

**Trigger signatures:** `ET.SCAN.nmap_syn_scan`, port sweeps, service enumeration (`T1046`).

**Default disposition:** `dismiss` when the source is an authorised internal scanner; otherwise `needs_human`.

## Triage steps
1. Check the source against the authorised-scanner allowlist (subnet / host).
2. If authorised → dismiss, no action.
3. If unknown source → assess target exposure and escalate per asset tier.

**Common false positive:** scheduled vulnerability scans from the internal scanning subnet.
