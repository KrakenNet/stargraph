# WebSocket Stream

`/v1/runs/{run_id}/stream` is the read-only WebSocket endpoint that pushes
typed `Event` shapes to subscribers as a run progresses. Frames are
JSON-serialized via the canonical Pydantic discriminated union (see
`stargraph.runtime.events:Event`).

The WS surface is read-only on the server side: clients receive event
frames but cannot send anything except an optional `ping`. Slow
consumers are disconnected with WS code 1011 + a 5s emit timeout
(`stargraph.serve.broadcast`); this prevents one stalled subscriber from
back-pressuring the entire broadcast hub.

## Topics

- TODO: connection upgrade + auth header chain.
- TODO: `?last_event_id=` resume semantics (Phase-2 task).
- TODO: backpressure + slow-consumer disconnect (1011).
- TODO: subscriber cap per run.
- TODO: client examples (Python websockets + JS).
