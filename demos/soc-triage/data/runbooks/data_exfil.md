# Runbook: Anomalous Data Egress / Exfiltration

**Trigger signatures:** `exfil.s3.anomalous_egress`, large/unusual outbound transfer, anomalous destination (`T1567`).

**Default disposition (prod tier):** `escalate` to IR on-call (data loss has regulatory weight).

## Immediate actions
1. Block egress to the destination endpoint.
2. Rotate the IAM role / credentials that initiated the transfer.
3. Quantify what left: object listing, byte volume, data classification.
4. Preserve flow logs and access logs for the transfer window.

## Escalate when
- Restricted-classification data left the boundary → CISO + legal + privacy.
- Exec-owned data store → business-owner notification (exec-owner rule).

**MTTR target:** < 45 min to egress block.
