# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for the Stargraph test suite.

Provides ``engine`` and ``adapter`` fixtures wired against Fathom v0.3.x. The
``adapter`` fixture also seeds the engine with the ``stargraph_action`` deftemplate
(via :class:`stargraph.fathom.FathomAdapter.register_stargraph_action_template`) and
mirrors a matching :class:`fathom.models.TemplateDefinition` into the engine's
``_template_registry`` so :py:meth:`fathom.Engine.query` can read back asserted
``stargraph_action`` facts -- raw ``load_clips_function`` builds the CLIPS template
but does not populate the registry by itself.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fathom import Engine
from fathom.models import ModuleDefinition, SlotDefinition, SlotType, TemplateDefinition

from stargraph.fathom import FathomAdapter

FIXTURES_DIR: Path = Path(__file__).parent / "fixtures"


# Skip COLLECTION of test modules that import optional providers at module
# top-level when the matching extra is not installed. Engine-only test jobs
# (`pytest -m engine`, no `--extra stores` etc.) use this to collect cleanly
# without forcing the optional-extra wheels into every CI matrix slot.
# Jobs that install the matching extra pick up the full set normally.
_STORES_EXTRA_TESTS: tuple[str, ...] = (
    # Tests that import ryugraph / lancedb / pyarrow at module top-level
    # (the `stores` extra: `ryugraph`, `lancedb`, `pyarrow`).
    "integration/test_cypher_subset.py",
    "integration/test_embed_hash_drift_gate.py",
    "integration/test_health_warns_on_nfs.py",
    "integration/test_hybrid_search_rrf.py",
    "integration/test_kg_fact_promotion_rule.py",
    "integration/test_kg_promotion_counterfactual.py",
    "integration/test_knowledge_phase3_ve.py",
    "integration/test_knowledge_phase5_final.py",
    "integration/test_knowledge_poc_e2e.py",
    "integration/test_lancedb_provider.py",
    "integration/test_lancedb_versioning_checkpoint.py",
    "integration/test_promotion_one_way.py",
    "integration/test_rag_reference_skill.py",
    "integration/test_retrieval_node_parallel_fanout.py",
    "integration/test_ryugraph_bulk_copy_extension_api.py",
    "integration/test_ryugraph_provider.py",
    "integration/test_single_writer_serialization.py",
    "integration/test_walk_vs_trail_documented.py",
    "perf/test_knowledge_perf.py",
    "unit/test_graphstore_expand_bounds.py",
    "unit/test_ryugraph_singleton_per_path.py",
    "unit/test_store_protocols_isinstance.py",
)

_ML_EXTRA_TESTS: tuple[str, ...] = (
    # Tests that import sklearn / joblib at module top-level
    # (the `ml` extra: `scikit-learn`, `xgboost`, `onnxruntime`, `skops`,
    # `joblib`). xgboost / onnx tests use ``pytest.importorskip`` already.
    "integration/test_mlnode_sklearn.py",
    "integration/test_training_subgraph_example.py",
)

collect_ignore_glob: list[str] = []
if (
    importlib.util.find_spec("ryugraph") is None
    or importlib.util.find_spec("lancedb") is None
    or importlib.util.find_spec("pyarrow") is None
):
    collect_ignore_glob.extend(_STORES_EXTRA_TESTS)
if importlib.util.find_spec("sklearn") is None or importlib.util.find_spec("joblib") is None:
    collect_ignore_glob.extend(_ML_EXTRA_TESTS)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--runslow`` so ``@pytest.mark.slow`` tests opt in.

    Slow tests (e.g. perf calibration suites under ``tests/perf/``) are
    skipped by default to keep ``pytest -q`` snappy. Pass ``--runslow`` to
    include them: ``pytest -q tests/perf --runslow``.
    """
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="run tests marked @pytest.mark.slow (perf calibration, etc.)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``@pytest.mark.slow`` items unless ``--runslow`` was passed."""
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


def _stargraph_action_template_definition() -> TemplateDefinition:
    """Mirror of ``STARGRAPH_ACTION_DEFTEMPLATE`` for the engine's registry.

    Required so :py:meth:`fathom.Engine.query` can read ``stargraph_action`` facts
    after rules fire; ``load_clips_function`` only registers in CLIPS itself,
    not in the Python-side ``_template_registry``.
    """
    return TemplateDefinition(
        name="stargraph_action",
        slots=[
            SlotDefinition(
                name="kind",
                type=SlotType.SYMBOL,
                allowed_values=["goto", "parallel", "halt", "retry", "assert", "retract"],
            ),
            SlotDefinition(name="target", type=SlotType.STRING, default=""),
            SlotDefinition(name="reason", type=SlotType.STRING, default=""),
            SlotDefinition(name="rule_id", type=SlotType.STRING, default=""),
            SlotDefinition(name="step", type=SlotType.INTEGER, default=0),
            SlotDefinition(name="targets", type=SlotType.STRING),
            SlotDefinition(name="join", type=SlotType.STRING, default=""),
            SlotDefinition(
                name="strategy",
                type=SlotType.SYMBOL,
                allowed_values=["all", "any", "race", "quorum"],
                default="all",
            ),
            SlotDefinition(name="backoff_ms", type=SlotType.INTEGER, default=0),
            SlotDefinition(name="fact", type=SlotType.STRING, default=""),
            SlotDefinition(name="slots", type=SlotType.STRING, default=""),
            SlotDefinition(name="pattern", type=SlotType.STRING, default=""),
        ],
    )


def _evidence_template_definition() -> TemplateDefinition:
    """User template carrying provenance slots plus a payload (field, value).

    Slot types are chosen to match what
    :func:`stargraph.fathom._provenance._sanitize_provenance_slot` returns for the
    standard provenance bundle: ``_step`` is an int; everything else is a string.
    """
    return TemplateDefinition(
        name="evidence",
        slots=[
            SlotDefinition(name="_origin", type=SlotType.STRING),
            SlotDefinition(name="_source", type=SlotType.STRING),
            SlotDefinition(name="_run_id", type=SlotType.STRING),
            SlotDefinition(name="_step", type=SlotType.INTEGER),
            SlotDefinition(name="_confidence", type=SlotType.STRING),
            SlotDefinition(name="_timestamp", type=SlotType.STRING),
            SlotDefinition(name="field", type=SlotType.STRING),
            SlotDefinition(name="value", type=SlotType.STRING),
        ],
    )


def _build_evidence_clips(defn: TemplateDefinition) -> str:
    """Build a CLIPS deftemplate for the ``evidence`` template.

    Uses the same compile path Fathom would use for a YAML template, but
    inline (avoids an extra fixture file for a single template).
    """
    from fathom.compiler import Compiler

    return Compiler().compile_template(defn)


@pytest.fixture
def engine() -> Engine:
    """Fresh Fathom engine for every test (deny by default, fail-closed)."""
    return Engine(default_decision="deny")


def _register_poc_module(engine: Engine) -> None:
    """Register a ``poc`` module so the POC ruleset can compile against it.

    ``Engine.load_modules`` does this when reading a YAML modules file; here we
    short-circuit to a single inline call so the only fixture file on disk is
    the rules YAML the task specifies. Mirrors ``load_modules``: ensures
    ``MAIN`` is built (with ``?ALL`` export) before any non-MAIN module, then
    builds the ``poc`` module and records it in ``_module_registry``.
    """
    if not engine._module_registry:  # pyright: ignore[reportPrivateUsage]
        engine._safe_build(  # pyright: ignore[reportPrivateUsage]
            "(defmodule MAIN (export ?ALL))",
            context="module:MAIN",
        )
    poc_defn = ModuleDefinition(name="poc", description="Stargraph POC smoke ruleset")
    engine._safe_build(  # pyright: ignore[reportPrivateUsage]
        "(defmodule poc (import MAIN ?ALL))",
        context="module:poc",
    )
    engine._module_registry["poc"] = poc_defn  # pyright: ignore[reportPrivateUsage]
    engine.set_focus(["poc"])


@pytest.fixture
def adapter(engine: Engine) -> FathomAdapter:
    """``FathomAdapter`` wired to a fresh engine with templates and module seeded.

    Seeds four things needed for the POC smoke:

    1. ``stargraph_action`` deftemplate in CLIPS (via the adapter API).
    2. ``stargraph_action`` :class:`TemplateDefinition` in the engine's registry
       so ``engine.query("stargraph_action", None)`` returns rows.
    3. ``evidence`` template (CLIPS deftemplate + registry entry) so user
       fixtures can call ``engine.assert_fact("evidence", ...)``.
    4. ``poc`` module (CLIPS defmodule + registry entry) so the rules YAML
       loaded by individual tests can compile against it.
    """
    adapter_ = FathomAdapter(engine)
    adapter_.register_stargraph_action_template()
    engine._template_registry[  # pyright: ignore[reportPrivateUsage]
        "stargraph_action"
    ] = _stargraph_action_template_definition()

    evidence_defn = _evidence_template_definition()
    engine.load_clips_function(_build_evidence_clips(evidence_defn))
    engine._template_registry["evidence"] = evidence_defn  # pyright: ignore[reportPrivateUsage]

    _register_poc_module(engine)

    return adapter_
