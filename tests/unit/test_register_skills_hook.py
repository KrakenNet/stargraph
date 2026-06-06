# SPDX-License-Identifier: Apache-2.0
"""``register_skills`` hookspec discovery (FR-22, AC-3.2).

Confirms that :mod:`stargraph.plugin.hookspecs` exposes a ``register_skills``
hookspec with the contract pluggy expects: zero parameters and a
``list[SkillSpec]`` return annotation. The collect-all semantic
(``firstresult=False``) is asserted via the stable boolean attribute the
loader pins on each hookspec.
"""

from __future__ import annotations

import inspect

import pytest

from stargraph.ir._models import SkillSpec
from stargraph.plugin import hookspecs

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_register_skills_hookspec_present() -> None:
    """``register_skills`` hookspec is defined with the expected signature."""
    hook = getattr(hookspecs, "register_skills", None)
    assert hook is not None, "register_skills hookspec missing from stargraph.plugin.hookspecs"
    assert callable(hook)

    sig = inspect.signature(hook)
    assert len(sig.parameters) == 0, (
        f"register_skills must take no params; got {list(sig.parameters)!r}"
    )

    # ``SkillSpec`` is imported under ``TYPE_CHECKING`` in
    # ``stargraph.plugin.hookspecs``, so it is not present in the module
    # globals at runtime. Inject it into ``locals`` so
    # ``inspect.get_annotations(eval_str=True)`` can resolve the
    # string-form ``list[SkillSpec]`` annotation.
    hints = inspect.get_annotations(
        hook,
        eval_str=True,
        locals={"SkillSpec": SkillSpec},
    )
    assert hints["return"] == list[SkillSpec]

    # Collect-all (not first-result) -- aggregation across all plugins.
    assert getattr(hook, "firstresult", None) is False
