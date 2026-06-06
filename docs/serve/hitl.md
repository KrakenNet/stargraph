# Human-in-the-Loop (HITL)

Stargraph's HITL flow lets a graph pause at a `WaitForInputNode` and resume
when a human (or upstream system) POSTs to `/v1/runs/{run_id}/respond`.
The pause emits a `WaitingForInputEvent` carrying a prompt + the requested
capability the responder must hold; the resume injects the response as a
`respond` fact into Fathom (with `origin="user", source=<actor>` per
locked Decision #2).

For replay-mode runs, the cf-respond mirror writes `source="cf:<actor>"`
so cf-injected responses are never confused with real-user responses.

## Topics

- TODO: `WaitingForInputEvent` payload shape.
- TODO: `respond` route auth + body validation.
- TODO: body-hash audit (NOT body-content; see sign-off rubric #10).
- TODO: timeout semantics (`InterruptTimeoutEvent` + `on_timeout`).
- TODO: cf-respond + locked Decision #2.
