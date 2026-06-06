# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.skills.shipwright._pack import fresh_engine, load_pack

if TYPE_CHECKING:
    from fathom import Engine


def _fact_targets(eng: Engine) -> set[str]:
    return {
        str(dict(raw)["node"])
        for raw in eng._env.find_template("fix.target").facts()  # pyright: ignore[reportPrivateUsage]
    }


@pytest.fixture
def engine() -> Engine:
    eng = fresh_engine()
    load_pack(eng, "edits")
    return eng


@pytest.mark.integration
def test_static_failure_routes_to_synthesize(engine: Engine) -> None:
    engine._env.assert_string('(verify.failed (kind "static"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string("(fix.attempts (value 0))")  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    assert _fact_targets(engine) == {"synthesize_graph"}


@pytest.mark.integration
def test_test_failure_routes_to_synthesize(engine: Engine) -> None:
    engine._env.assert_string('(verify.failed (kind "tests"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string("(fix.attempts (value 0))")  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    assert _fact_targets(engine) == {"synthesize_graph"}


@pytest.mark.integration
def test_smoke_failure_routes_to_synthesize(engine: Engine) -> None:
    engine._env.assert_string('(verify.failed (kind "smoke"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string("(fix.attempts (value 0))")  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    assert _fact_targets(engine) == {"synthesize_graph"}


@pytest.mark.integration
def test_third_attempt_escalates_to_human_input(engine: Engine) -> None:
    engine._env.assert_string('(verify.failed (kind "static"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string("(fix.attempts (value 3))")  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    assert _fact_targets(engine) == {"human_input"}
