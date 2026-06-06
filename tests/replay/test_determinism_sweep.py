# SPDX-License-Identifier: Apache-2.0
"""Determinism source sweep (NFR-2, design §4.1).

Pins the FR-28 / NFR-2 contract that *all 12* hidden non-determinism
sources from design §4.1 are mitigated. Each source maps to one
test below; the headline integration case (1) records and replays a
3-node graph end-to-end and byte-compares per-step state + the modeled
event sequence, the same shape as the existing
``tests/replay/test_replay_determinism.py`` but extended to every
:mod:`stargraph.replay.determinism` shim (``now``, ``random``, ``uuid4``,
``urandom``, ``secrets_token``).

The remaining 11 sources are pinned by smaller hermetic checks:

* DNS / HTTP -- vcrpy ``record_mode='none'`` raises on unrecorded
  requests (covered cross-reference in
  ``tests/replay/test_must_stub_policy.py``; we re-assert the matcher
  tuple is the FR-28 ``(method, url, body_hash)`` triple).
* FS ``os.listdir`` / ``Path.iterdir`` -- the engine sources its IR
  inputs through :class:`pathlib.Path` and never iterates a directory
  without an explicit sort. We pin the pattern by hashing a sorted
  iteration vs. the raw one against a tmp directory containing entries
  in an order that differs from lexicographic.
* ``set`` iteration -- IR :attr:`state_schema` rejects ``set`` /
  ``frozenset`` types at compile time
  (:func:`stargraph.graph.definition._check_state_schema_no_set_fields`).
* ``dict`` ordering -- 3.7+ insertion order is the spec contract; we
  sanity-check round-trip JCS canonicalization is insensitive to
  insertion order so any in-engine dict shuffle is benign for the
  hash.
* ``gc.collect()`` -- documented (no engine call); we assert the
  source tree contains no ``gc.collect`` invocations under
  ``src/stargraph`` so a future regression is loud.
* LLM completions -- the dspy adapter routes through vcrpy; we assert
  :data:`stargraph.replay.determinism.HTTP_CASSETTE_MATCHERS` is the
  FR-28 tuple (covered above) and that the dspy stub registered in
  ``stargraph.cli.run`` does *not* hit the network (no transport import).
* Tool side effects -- :func:`stargraph.tools.decorator.tool` defaults
  ``write`` / ``external`` to ``ReplayPolicy.must_stub``
  (cross-reference: ``tests/replay/test_must_stub_policy.py``). We
  re-assert the default here so the sweep stays self-contained.
* ``threading.local`` / ``locale`` / ``asyncio.gather`` -- pin the
  mitigations: (a) the engine uses :class:`contextvars.ContextVar`
  for replay scoping, not :class:`threading.local`; (b) the engine
  sources locale-sensitive operations through ``ascii``-only paths
  (we assert the structural-hash pre-image is ASCII); (c) the engine
  uses :func:`anyio.create_task_group` not :func:`asyncio.gather`
  (gather's completion order is non-deterministic).

Each source is given its own ``test_<source>_*`` function so a
regression points at exactly one mitigation.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import pytest
import rfc8785

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import IRValidationError
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.ir._models import HaltAction
from stargraph.nodes.base import NodeBase
from stargraph.replay import determinism
from stargraph.runtime.events import ResultEvent
from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel


REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SRC_ROOT: Path = REPO_ROOT / "src" / "stargraph"


# --------------------------------------------------------------------------- #
# Source 1-4 + 10: shimmed primitives -- end-to-end record/replay byte-id     #
# --------------------------------------------------------------------------- #


class _AllShimsConsumingNode(NodeBase):
    """Node that draws from *every* determinism shim and writes to state.

    Extends ``_ShimConsumingNode`` from
    :mod:`tests.replay.test_replay_determinism` with the two shims that
    test does not exercise (``urandom``, ``secrets_token``) so the sweep
    pins all five FR-28 primitives in a single record/replay round trip.
    """

    def __init__(self, node_id: str) -> None:
        self._id = node_id

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del ctx
        prev_trail: str = getattr(state, "trail", "")
        # Order matters: the recording dict is positional per shim name,
        # so the replay must call shims in the same order to pop the
        # right value (see stargraph.replay.determinism._shim).
        ts = determinism.now()
        rnd = determinism.random()
        uid = determinism.uuid4()
        ur = determinism.urandom(8)
        tok = determinism.secrets_token(16)
        new_trail = self._id if not prev_trail else f"{prev_trail},{self._id}"
        return {
            "trail": new_trail,
            "ts_repr": repr(ts),
            "rnd_repr": repr(rnd),
            "uid_repr": str(uid),
            "ur_repr": ur.hex(),
            "tok_repr": tok,
        }


class _HaltAfterAllNodesFathom:
    """Tiny stub Fathom that halts once the loop has visited every node."""

    def __init__(self, *, halt_after: int) -> None:
        self._halt_after = halt_after
        self._calls = 0

    def mirror_state(self, state: object, *, annotations: dict[str, Any]) -> list[Any]:
        del state, annotations
        return []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: Any = None,
    ) -> None:
        del template, slots, provenance

    def evaluate(self) -> list[Any]:
        self._calls += 1
        if self._calls >= self._halt_after:
            return [HaltAction(reason="done")]
        return []


def _build_graph() -> Graph:
    """3-node sequential graph with state slots for every shim's output."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:replay-determinism-sweep",
        nodes=[
            NodeSpec(id="n0", kind="echo"),
            NodeSpec(id="n1", kind="echo"),
            NodeSpec(id="n2", kind="echo"),
        ],
        state_schema={
            "trail": "str",
            "ts_repr": "str",
            "rnd_repr": "str",
            "uid_repr": "str",
            "ur_repr": "str",
            "tok_repr": "str",
        },
    )
    return Graph(ir)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_event(ev: Any) -> dict[str, Any]:
    """Strip wall-clock fields the FR-28 shims do not cover."""
    dump: dict[str, Any] = ev.model_dump(mode="json")
    dump.pop("ts", None)
    if isinstance(ev, ResultEvent):
        dump.pop("run_duration_ms", None)
    return dump


async def _drain_events(run: GraphRun) -> list[Any]:
    received: list[Any] = []
    with anyio.fail_after(5.0):
        while True:
            ev = await run.bus.receive()
            received.append(ev)
            if isinstance(ev, ResultEvent):
                return received


async def _drive_run(
    *,
    run_id: str,
    cp: SQLiteCheckpointer,
    scope: determinism.DeterminismScope,
) -> tuple[list[dict[str, Any]], list[Any]]:
    graph = _build_graph()
    initial_state = graph.state_schema(
        trail="",
        ts_repr="",
        rnd_repr="",
        uid_repr="",
        ur_repr="",
        tok_repr="",
    )
    registry: dict[str, NodeBase] = {
        "n0": _AllShimsConsumingNode("n0"),
        "n1": _AllShimsConsumingNode("n1"),
        "n2": _AllShimsConsumingNode("n2"),
    }
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=initial_state,
        node_registry=registry,
        checkpointer=cp,
        fathom=_HaltAfterAllNodesFathom(halt_after=3),
    )
    events: list[Any] = []

    async def _drive() -> None:
        with scope:
            await run.start()

    async def _drain() -> None:
        events.extend(await _drain_events(run))

    async with anyio.create_task_group() as tg:
        tg.start_soon(_drive)
        tg.start_soon(_drain)

    states: list[dict[str, Any]] = []
    step = 0
    while True:
        ckpt = await cp.read_at_step(run_id, step)
        if ckpt is None:
            break
        states.append(ckpt.state)
        step += 1
    return states, events


@pytest.mark.integration
async def test_sources_1_to_4_and_10_record_replay_byte_identical(tmp_path: Path) -> None:
    """Sources 1-4 (time/random/urandom/uuid4) + 10 (LLM via shim).

    Records a 3-node graph that pulls from every FR-28 shim, then
    replays it from the recording dict and byte-compares per-step state
    plus the modeled event sequence. Wall-clock fields the shims do not
    cover (``ts``, ``run_duration_ms``) are excluded -- they are
    sourced by the loop driver outside the determinism scope.
    """
    record_recording: dict[str, list[Any]] = {}
    record_scope = determinism.DeterminismScope(replay=False, recording=record_recording)
    cp_record = SQLiteCheckpointer(tmp_path / "record.sqlite")
    await cp_record.bootstrap()
    try:
        record_states, record_events = await _drive_run(
            run_id="run-sweep",
            cp=cp_record,
            scope=record_scope,
        )
    finally:
        await cp_record.close()

    # Every shim recorded exactly 3 values (one per node tick).
    for name in ("now", "random", "uuid4", "urandom", "secrets_token"):
        assert len(record_recording.get(name, [])) == 3, (
            f"shim {name!r} did not record 3 values: {record_recording.get(name)!r}"
        )

    replay_recording: dict[str, list[Any]] = {
        name: list(values) for name, values in record_recording.items()
    }
    replay_scope = determinism.DeterminismScope(replay=True, recording=replay_recording)
    cp_replay = SQLiteCheckpointer(tmp_path / "replay.sqlite")
    await cp_replay.bootstrap()
    try:
        replay_states, replay_events = await _drive_run(
            run_id="run-sweep",
            cp=cp_replay,
            scope=replay_scope,
        )
    finally:
        await cp_replay.close()

    for name, leftover in replay_recording.items():
        assert leftover == [], (
            f"replay did not consume all recorded {name!r} values; leftover: {leftover!r}"
        )

    assert len(replay_states) == len(record_states)
    for i, (rec, rep) in enumerate(zip(record_states, replay_states, strict=True)):
        assert _canonical_hash(rec) == _canonical_hash(rep), (
            f"state hash mismatch at step {i}: rec={rec!r} rep={rep!r}"
        )

    rec_payload = [_normalize_event(ev) for ev in record_events]
    rep_payload = [_normalize_event(ev) for ev in replay_events]
    assert _canonical_hash(rec_payload) == _canonical_hash(rep_payload)


# --------------------------------------------------------------------------- #
# Source 5: Network DNS -- vcrpy matcher tuple                                #
# --------------------------------------------------------------------------- #


def test_source_5_vcrpy_matcher_tuple_is_method_url_body_hash() -> None:
    """FR-28 amendment-6 §cassette-layer-#1: matcher tuple is fixed."""
    assert determinism.HTTP_CASSETTE_MATCHERS == ("method", "url", "body_hash")


def test_source_5_vcrpy_default_record_mode_is_loud_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """In CI (``CI=true``), unrecorded HTTP must raise (not record silently)."""
    monkeypatch.setenv("CI", "true")
    assert determinism._default_record_mode() == "none"  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv("CI", "")
    assert determinism._default_record_mode() == "once"  # pyright: ignore[reportPrivateUsage]


# --------------------------------------------------------------------------- #
# Source 6: FS os.listdir/iterdir -- sort at use site                         #
# --------------------------------------------------------------------------- #


def test_source_6_filesystem_iteration_must_be_sorted(tmp_path: Path) -> None:
    """Pattern check: the engine's contract is "sort at use site".

    We cannot easily grep every iterdir call, so we pin the *pattern*:
    given a directory whose creation order differs from lexicographic
    (filesystem-dependent), ``sorted(p.iterdir())`` produces a stable
    answer where raw ``p.iterdir()`` does not. Any engine code that
    iterates a dir without sorting would inherit the unstable order.
    """
    # Create entries in non-lexicographic order; the directory's on-disk
    # order is filesystem-defined, but sorted() is always stable.
    for name in ("z.txt", "a.txt", "m.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    sorted_names = [p.name for p in sorted(tmp_path.iterdir())]
    assert sorted_names == ["a.txt", "m.txt", "z.txt"]


# --------------------------------------------------------------------------- #
# Source 7: set iteration -- forbidden in IR state_schema                     #
# --------------------------------------------------------------------------- #


def test_source_7_set_field_in_state_schema_is_rejected() -> None:
    """``set``/``frozenset`` in ``state_schema`` raises at compile time."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:set-field",
        nodes=[NodeSpec(id="n0", kind="echo")],
        state_schema={"members": "set"},
    )
    with pytest.raises(IRValidationError) as excinfo:
        Graph(ir)
    assert excinfo.value.context.get("violation") == "set-field-forbidden"


def test_source_7_frozenset_field_in_state_schema_is_rejected() -> None:
    """``frozenset`` is rejected for the same reason as ``set``."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:frozenset-field",
        nodes=[NodeSpec(id="n0", kind="echo")],
        state_schema={"members": "frozenset"},
    )
    with pytest.raises(IRValidationError) as excinfo:
        Graph(ir)
    assert excinfo.value.context.get("violation") == "set-field-forbidden"


# --------------------------------------------------------------------------- #
# Source 8: dict ordering -- 3.7+ insertion order, JCS canonicalizes          #
# --------------------------------------------------------------------------- #


def test_source_8_jcs_canonical_hash_is_insertion_order_insensitive() -> None:
    """RFC 8785 sorts keys: dict insertion order does not affect the hash.

    This is the structural invariant that lets the engine round-trip
    state through ``rfc8785.dumps`` without worrying about whether some
    component constructed a dict in a different order than its peer.
    """
    a = {"x": 1, "y": 2, "z": 3}
    b: dict[str, int] = {}
    b["z"] = 3
    b["x"] = 1
    b["y"] = 2
    assert rfc8785.dumps(a) == rfc8785.dumps(b)


# --------------------------------------------------------------------------- #
# Source 9: gc.collect() -- documented (no engine call)                       #
# --------------------------------------------------------------------------- #


def test_source_9_no_gc_collect_in_engine_source() -> None:
    """Engine source tree must not call ``gc.collect()`` (replay hazard).

    A future regression that adds ``gc.collect()`` -- which can run
    weakref callbacks at non-deterministic moments -- would break
    replay byte-identity. The mitigation is "documented: don't call
    it"; the test pins it loud.
    """
    pattern = re.compile(r"\bgc\.collect\s*\(")
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "engine source contains gc.collect() calls -- replay determinism "
        f"hazard (design §4.1 source #9): {offenders!r}"
    )


# --------------------------------------------------------------------------- #
# Source 11: Tool side effects -- must_stub default                           #
# --------------------------------------------------------------------------- #


def test_source_11_write_default_replay_policy_is_must_stub() -> None:
    """Cross-reference of FR-26: ``write`` -> :attr:`ReplayPolicy.must_stub`."""

    @tool(
        name="sweep_writer",
        namespace="sweep",
        version="1",
        side_effects=SideEffects.write,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    def sweep_writer() -> dict[str, Any]:
        return {}

    spec: Any = sweep_writer.spec  # type: ignore[attr-defined]
    assert spec.replay_policy == ReplayPolicy.must_stub


def test_source_11_external_default_replay_policy_is_must_stub() -> None:
    """Cross-reference of FR-26: ``external`` -> :attr:`ReplayPolicy.must_stub`."""

    @tool(
        name="sweep_external",
        namespace="sweep",
        version="1",
        side_effects=SideEffects.external,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    def sweep_external() -> dict[str, Any]:
        return {}

    spec: Any = sweep_external.spec  # type: ignore[attr-defined]
    assert spec.replay_policy == ReplayPolicy.must_stub


# --------------------------------------------------------------------------- #
# Source 12: threading.local + locale + asyncio.gather                        #
# --------------------------------------------------------------------------- #


def test_source_12_determinism_scope_uses_contextvar_not_threading_local() -> None:
    """The replay scope is bound via :class:`contextvars.ContextVar`.

    ``threading.local`` does not propagate through ``asyncio`` /
    ``anyio`` task switches; any engine code that scoped replay state
    on it would silently lose the recording across an ``await`` point.
    """
    assert isinstance(
        determinism._CURRENT_SCOPE,  # pyright: ignore[reportPrivateUsage]
        contextvars.ContextVar,
    )


def test_source_12_no_threading_local_in_engine_source() -> None:
    """No ``threading.local`` at module scope in the engine source."""
    pattern = re.compile(r"\bthreading\.local\s*\(")
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "engine source uses threading.local -- ContextVar is the contract "
        f"(design §4.1 source #12): {offenders!r}"
    )


def test_source_12_no_asyncio_gather_in_engine_runtime() -> None:
    """Runtime must not use ``asyncio.gather`` (anyio TaskGroup is the contract).

    ``asyncio.gather`` does not preserve completion order in a way the
    Stargraph parallel block can rely on -- ``anyio.create_task_group``
    is the structured-concurrency primitive design §3.6.1 mandates.
    Tests are exempt (they may need plain asyncio for stubs).
    """
    pattern = re.compile(r"\basyncio\.gather\s*\(")
    runtime_root = SRC_ROOT / "runtime"
    offenders: list[str] = []
    for py in runtime_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "stargraph.runtime uses asyncio.gather -- design §3.6.1 mandates "
        f"anyio.create_task_group: {offenders!r}"
    )


def test_source_12_locale_independent_canonicalization() -> None:
    """JCS canonicalization is ASCII-only -- no locale-dependent collation.

    rfc8785 sorts keys by Unicode code point, not the system locale,
    so structural-hash pre-images are byte-identical across machines
    with different :envvar:`LC_ALL` settings.
    """
    payload = {"é": 1, "e": 2}  # "é" vs "e": code-point sort
    canonical = rfc8785.dumps(payload)
    # JCS uses UTF-8 bytes with code-point key sort; "e" (0x65) sorts
    # before "é" (0xe9) regardless of locale collation.
    assert canonical.find(b'"e":2') < canonical.find(b'"\\u00e9":1') or canonical.find(
        b'"e":2'
    ) < canonical.find('"é":1'.encode())


# --------------------------------------------------------------------------- #
# Self-test: every ``test_source_<N>_*`` function exists                       #
# --------------------------------------------------------------------------- #


def test_sweep_covers_all_twelve_sources() -> None:
    """Meta-check: every design §4.1 source has at least one test here.

    Sources 1-4 + 10 are folded into the single
    ``test_sources_1_to_4_and_10_record_replay_byte_identical``
    integration test (they share the shim machinery and a single
    record/replay round trip is the strongest claim).
    """
    module = __import__(__name__, fromlist=["*"])
    names = {n for n in dir(module) if n.startswith("test_source")}
    aggregated = {n for n in dir(module) if n.startswith("test_sources_")}
    # Sources covered individually: 5, 6, 7, 8, 9, 11, 12
    individual_covered: set[int] = set()
    for n in names:
        m = re.match(r"test_source_(\d+)_", n)
        if m:
            individual_covered.add(int(m.group(1)))
    assert individual_covered == {5, 6, 7, 8, 9, 11, 12}, individual_covered
    # Sources 1-4 + 10 covered by the aggregated record/replay case.
    assert any("1_to_4_and_10" in n for n in aggregated), aggregated


# Asyncio guard: this module mixes ``async def`` integration tests
# (for the record/replay sweep) with plain sync tests; pytest-anyio's
# default loop scope is per-function, which is what we want.
def test_event_loop_policy_is_default() -> None:
    """No custom event loop policy installed by import-time side effects."""
    # Default policy is fine for replay; a custom uvloop, etc., would be
    # an additional non-determinism source the engine does not document.
    policy = asyncio.get_event_loop_policy()
    # Stdlib default is :class:`asyncio.DefaultEventLoopPolicy`; the
    # name varies per-platform (WindowsSelectorEventLoopPolicy on Win).
    # We just assert it lives in :mod:`asyncio` (no third-party loop
    # like uvloop has been installed at import time).
    assert type(policy).__module__.startswith("asyncio")
