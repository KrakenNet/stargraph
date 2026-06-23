# Runbook: Endpoint Process Injection

**Trigger signatures:** `T1055.process_injection`, reflective DLL load, hollowing into a trusted process.

**Default disposition:** `needs_human` — EDR injection signals are frequently ambiguous (legit packers, EDR self-injection).

## Triage steps
1. Identify the injecting and target processes; check parent/child lineage.
2. Confirm whether the injected module is signed / known-good.
3. Correlate with any preceding execution or download events.

## Actions if confirmed malicious
- Isolate the host, capture memory, collect the injected module for analysis.
- Sweep for the same module hash fleet-wide.

**Note:** Tune out known-good injectors (some AV/EDR agents inject by design).
