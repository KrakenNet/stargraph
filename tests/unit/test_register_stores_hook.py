# SPDX-License-Identifier: Apache-2.0
"""``register_stores`` hookspec discovery (FR-19, FR-22, NFR-9).

Confirms :mod:`stargraph.plugin.hookspecs` exposes ``register_stores`` with
the contract pluggy expects: zero parameters, ``list[StoreSpec]`` return
annotation, and collect-all (``firstresult=False``) semantics so every
contributing plugin's stores are aggregated.
"""

from __future__ import annotations

import inspect

import pytest

from stargraph.ir._models import StoreSpec
from stargraph.plugin import hookspecs

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_register_stores_hookspec_present() -> None:
    """``register_stores`` is defined and callable."""
    hook = getattr(hookspecs, "register_stores", None)
    assert hook is not None, "register_stores hookspec missing from stargraph.plugin.hookspecs"
    assert callable(hook)


def test_register_stores_hookspec_takes_no_params() -> None:
    """Pluggy collect-all hookspec: zero parameters."""
    hook = hookspecs.register_stores
    sig = inspect.signature(hook)
    assert len(sig.parameters) == 0, (
        f"register_stores must take no params; got {list(sig.parameters)!r}"
    )


def test_register_stores_hookspec_returns_list_of_storespec() -> None:
    """Return annotation is ``list[StoreSpec]`` (resolved to runtime type)."""
    hook = hookspecs.register_stores
    # ``StoreSpec`` is imported under ``TYPE_CHECKING`` in
    # ``stargraph.plugin.hookspecs``; inject into locals so eval_str=True
    # can resolve the string-form annotation.
    hints = inspect.get_annotations(
        hook,
        eval_str=True,
        locals={"StoreSpec": StoreSpec},
    )
    assert hints["return"] == list[StoreSpec]


def test_register_stores_hookspec_is_collect_all() -> None:
    """``register_stores`` aggregates across all plugins (not first-result)."""
    hook = hookspecs.register_stores
    assert getattr(hook, "firstresult", None) is False
