# Runbook: Identity Anomaly (Impossible Travel / MFA Fatigue)

**Trigger signatures:** `auth.impossible_travel`, `auth.mfa_fatigue` (`T1078`, `T1621`).

**Default disposition:** `needs_human` for staging/prod; `dismiss` for low-severity dev with a benign explanation.

## Triage steps
1. Confirm the two geo points and the time delta — rule out corporate VPN egress hops.
2. Check whether the auth attempts succeeded or were all denied (fatigue vs. compromise).
3. Review concurrent sessions and recent password / MFA-device changes.

## Actions if confirmed malicious
- Revoke active sessions, force re-auth, reset credentials.
- Quarantine the registered MFA device.

**Common false positive:** VPN split-tunnel making one login appear in a second region.
