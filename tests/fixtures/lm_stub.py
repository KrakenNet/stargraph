# SPDX-License-Identifier: Apache-2.0
"""Canned-JSON standin LM for skill tests.

Mirrors the inline ``_StandinLM`` pattern from
``tests/integration/test_autoresearch_skill.py`` so e2e skill tests can run
the DSPy seam without a live LM. Returns one JSON object per call carrying
every output field a reference-skill signature asks for; the adapter
extracts the fields the active signature declares and ignores the rest.
"""

from __future__ import annotations

import json

import dspy  # type: ignore[import-untyped]

_CANNED: dict[str, str] = {
    "summary": "STANDIN_SUMMARY",
    "answer": "STANDIN_ANSWER",
}


class StandinLM(dspy.LM):  # pyright: ignore[reportUnknownMemberType]
    """LM stub returning a fixed JSON payload for every request."""

    def __init__(self, payload: dict[str, str] | None = None) -> None:
        super().__init__(model="standin/standin")  # pyright: ignore[reportUnknownMemberType]
        self._payload = dict(_CANNED if payload is None else payload)

    def __call__(self, *_args: object, **_kwargs: object) -> list[str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return [json.dumps(self._payload)]
