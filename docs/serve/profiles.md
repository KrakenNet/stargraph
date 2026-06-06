# Deployment Profiles

Stargraph ships two profiles: `oss-default` (permissive, JWT-auth, anonymous
reads) and `cleared` (mTLS, default-deny capabilities, audit-sink mandatory).
The profile selects the auth provider, the capability-gate semantics, the
TLS posture, and several startup-time refusal gates.

Profile is selected by precedence: env (`STARGRAPH_PROFILE=cleared`) > CLI
flag (`--profile cleared`) > `stargraph.toml` `[serve].profile` > default
(`oss-default`).

## Topics

- TODO: `OssDefaultProfile` field reference.
- TODO: `ClearedProfile` field reference.
- TODO: capability-gate matrix (oss vs cleared on the 9 routes).
- TODO: auth-provider factory wiring.
- TODO: `stargraph.toml` schema + per-profile overrides.
- TODO: profile selection precedence.
