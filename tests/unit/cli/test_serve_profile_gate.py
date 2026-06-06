# SPDX-License-Identifier: Apache-2.0
"""Unit: ``stargraph serve --profile cleared`` rejects ``--allow-*`` flags (task 2.37).

Per FR-32 / FR-68 / AC-4.2 / design §11.1, §15: the cleared profile
forbids the ``--allow-pack-mutation`` and ``--allow-side-effects``
boot-time escape hatches. Setting either flag under
``--profile cleared`` raises :class:`ProfileViolationError` and exits
non-zero before uvicorn starts. The OSS-default profile permits both
flags (developer convenience).

Defense-in-depth: the engine itself REFUSES tools/nodes declaring
``side_effects in {write, external}`` under cleared regardless of
``--allow-side-effects`` (FR-68). The startup gate here is the second
line of defense -- it stops a misconfigured operator from booting a
cleared deployment with the escape hatch enabled at all.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

_runner = CliRunner()


@pytest.mark.unit
def test_cleared_profile_rejects_allow_side_effects() -> None:
    """``--profile cleared --allow-side-effects`` exits non-zero with violation message."""
    result = _runner.invoke(
        app,
        [
            "serve",
            "--profile",
            "cleared",
            "--allow-side-effects",
            "--port",
            "9999",
        ],
    )
    assert result.exit_code != 0, result.stdout
    # typer/click swallows non-Click exceptions into ``result.exception``;
    # the message body lives there. The output channel is also checked
    # for the operator-friendly stderr render that the runtime path
    # produces on real ``stargraph serve`` invocations.
    combined = (result.output or "").lower() + " " + str(result.exception or "").lower()
    assert "violation" in combined or "cleared" in combined or "forbid" in combined, combined


@pytest.mark.unit
def test_cleared_profile_rejects_allow_pack_mutation() -> None:
    """``--profile cleared --allow-pack-mutation`` exits non-zero with violation message."""
    result = _runner.invoke(
        app,
        [
            "serve",
            "--profile",
            "cleared",
            "--allow-pack-mutation",
            "--port",
            "9998",
        ],
    )
    assert result.exit_code != 0, result.stdout
    # typer/click swallows non-Click exceptions into ``result.exception``;
    # the message body lives there. The output channel is also checked
    # for the operator-friendly stderr render that the runtime path
    # produces on real ``stargraph serve`` invocations.
    combined = (result.output or "").lower() + " " + str(result.exception or "").lower()
    assert "violation" in combined or "cleared" in combined or "forbid" in combined, combined
