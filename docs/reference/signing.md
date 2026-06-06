# Release Signing

Stargraph releases are signed with Ed25519 (AC-4.5). Each release artifact (sdist, wheel, source tarball) ships with a detached `.sig` file produced by the maintainer release key.

## Verifying a release

```bash
# Fetch the artifact and its signature
curl -LO https://github.com/KrakenNet/stargraph/releases/download/v0.1.0/stargraph-0.1.0-py3-none-any.whl
curl -LO https://github.com/KrakenNet/stargraph/releases/download/v0.1.0/stargraph-0.1.0-py3-none-any.whl.sig

# Verify with the published Ed25519 public key
# (signing-key.pub is published in the repo and on krakn.ai)
# TODO: replace with the actual `minisign` / `signify` invocation once the
# release pipeline lands.
```

## Key rotation

The Stargraph signing key is rotated on a published schedule; the active fingerprint is recorded in `SECURITY.md` and on the release page.

> TODO: fill in once the release pipeline (task in Phase 4/5) selects between `minisign`, `sigstore`, or `signify` and publishes the public key.
