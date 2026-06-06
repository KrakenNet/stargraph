# Nautilus Broker Integration

Stargraph consumes the Nautilus broker (`nautilus_rkm` distribution) for
broker-emit and broker-request graph nodes. The `BrokerResponse` shape
appears in the OpenAPI spec under `components/schemas/BrokerResponse`
so client SDKs can dispatch on broker results.

The broker integration is composition-only: Stargraph imports `nautilus`
lazily; if the package is absent at runtime the broker-emit nodes raise
a clear capability error rather than crashing import. Stripped composition
tests (design §16.10) verify this.

## Topics

- TODO: broker-emit node semantics + capability gate.
- TODO: `BrokerResponse` schema + dispatch patterns.
- TODO: replay isolation for broker-emit (no real emit during cf-runs).
- TODO: error envelope mapping (broker errors → graph events).
- TODO: zero-import-coupling test pattern.
