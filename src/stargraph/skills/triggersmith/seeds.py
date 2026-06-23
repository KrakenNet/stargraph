# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed triggers — the trainset cold start.

Each entry is a verified ``(brief → trigger + test)`` pair for the MANUAL trigger
variant, distilled from ``stargraph.triggers.manual.ManualTrigger``: a zero-arg
class that captures ``deps['scheduler']`` in ``init`` (raising on a missing
scheduler), delegates ``enqueue`` to the scheduler, and returns the scheduler's
``run_id``. They give RAG retrieval and few-shot compile something to stand on
before the generator has produced anything. ``id`` is a fixed literal so
``seed_trainset`` is idempotent across runs.

``tests/integration/triggersmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any


def _pair(
    seed_id: str,
    brief: str,
    class_name: str,
    fixture: dict[str, Any],
    trigger_source: str,
    test_source: str,
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "variant": "manual",
        "class_name": class_name,
        "fixture": fixture,
        "trigger_source": trigger_source,
        "test_source": test_source,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "70030000001",
        "a manual trigger that enqueues a graph run by delegating to the scheduler",
        "ManualEnqueueTrigger",
        {"graph_id": "graph:demo", "params": {"alpha": 1}},
        """\
from typing import Any

from stargraph.errors import StargraphRuntimeError


class ManualEnqueueTrigger:
    def __init__(self) -> None:
        self._scheduler: Any = None

    def init(self, deps: dict[str, Any]) -> None:
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise StargraphRuntimeError(
                "ManualEnqueueTrigger.init(deps) requires deps['scheduler']"
            )
        self._scheduler = scheduler

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def routes(self) -> list[Any]:
        return []

    def enqueue(
        self,
        graph_id: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        if self._scheduler is None:
            raise StargraphRuntimeError(
                "ManualEnqueueTrigger.enqueue() requires init(deps) to have been called"
            )
        handle = self._scheduler.enqueue(
            graph_id=graph_id,
            params=params,
            idempotency_key=idempotency_key,
        )
        return handle.run_id
""",
        """\
from trigger import ManualEnqueueTrigger


class _Handle:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


class _RecScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, graph_id, params, idempotency_key=None):
        self.calls.append(
            {"graph_id": graph_id, "params": params, "idempotency_key": idempotency_key}
        )
        return _Handle("run-abc")


def test_init_requires_scheduler():
    try:
        ManualEnqueueTrigger().init({})
    except Exception as e:
        assert type(e).__name__ == "StargraphRuntimeError"
    else:
        raise AssertionError("expected StargraphRuntimeError")


def test_enqueue_delegates_and_returns_run_id():
    rec = _RecScheduler()
    t = ManualEnqueueTrigger()
    t.init({"scheduler": rec})
    run_id = t.enqueue("graph:demo", {"alpha": 1})
    assert run_id == "run-abc"
    assert len(rec.calls) == 1
    assert rec.calls[0]["graph_id"] == "graph:demo"
    assert rec.calls[0]["params"] == {"alpha": 1}


def test_lifecycle_noops():
    t = ManualEnqueueTrigger()
    t.init({"scheduler": _RecScheduler()})
    t.start()
    t.stop()
    t.start()
    assert t.routes() == []
""",
    ),
    _pair(
        "70030000002",
        "a manual trigger whose enqueue threads a caller-supplied idempotency key to the scheduler",
        "IdempotentManualTrigger",
        {"graph_id": "graph:idem", "params": {"x": 7}},
        """\
from typing import Any

from stargraph.errors import StargraphRuntimeError


class IdempotentManualTrigger:
    def __init__(self) -> None:
        self._scheduler: Any = None

    def init(self, deps: dict[str, Any]) -> None:
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise StargraphRuntimeError(
                "IdempotentManualTrigger.init(deps) requires deps['scheduler']"
            )
        self._scheduler = scheduler

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def routes(self) -> list[Any]:
        return []

    def enqueue(
        self,
        graph_id: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        if self._scheduler is None:
            raise StargraphRuntimeError(
                "IdempotentManualTrigger.enqueue() requires init(deps) to have been called"
            )
        handle = self._scheduler.enqueue(
            graph_id=graph_id,
            params=params,
            idempotency_key=idempotency_key,
        )
        return handle.run_id
""",
        """\
from trigger import IdempotentManualTrigger


class _Handle:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


class _RecScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, graph_id, params, idempotency_key=None):
        self.calls.append(
            {"graph_id": graph_id, "params": params, "idempotency_key": idempotency_key}
        )
        return _Handle("run-xyz")


def test_idempotency_key_threads_through():
    rec = _RecScheduler()
    t = IdempotentManualTrigger()
    t.init({"scheduler": rec})
    run_id = t.enqueue("graph:idem", {"x": 7}, idempotency_key="key-1")
    assert run_id == "run-xyz"
    assert len(rec.calls) == 1
    assert rec.calls[0]["idempotency_key"] == "key-1"
    assert rec.calls[0]["graph_id"] == "graph:idem"
    assert rec.calls[0]["params"] == {"x": 7}


def test_default_idempotency_key_is_none():
    rec = _RecScheduler()
    t = IdempotentManualTrigger()
    t.init({"scheduler": rec})
    t.enqueue("graph:idem", {"x": 7})
    assert rec.calls[0]["idempotency_key"] is None
""",
    ),
]
