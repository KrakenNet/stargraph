# SPDX-License-Identifier: Apache-2.0
"""SmithSpec - the per-domain plug-in the shared lifecycle nodes run against.

The triage → recall → build → record control flow is identical for every smith
(:mod:`stargraph.skills._smith.nodes` + :class:`stargraph.skills._smith.build.SmithBuild`).
What differs per domain is only the handful of callables bundled here: how a
generation dict becomes artifact files, how those files are gated, which fields
surface as state, where reflexion/trainset rows go, how grounding is recalled,
and how a passing artifact is named + recorded. A smith builds one ``SmithSpec``
and hands it to the shared nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult
    from stargraph.skills._smith.retrieval import Snippet

__all__ = ["SmithSpec"]


@dataclass(frozen=True)
class SmithSpec:
    """The domain-specific seam of one smith — the full plug-in surface.

    Identity / build (used by ``SmithBuild``):
    - ``name``             short tag (``"node"``, ``"tool"``, ...); names scratch dirs + triage.
    - ``artifact_filenames`` the ``(source_file, test_file)`` the gate + landing use.
    - ``artifact_files``   generation dict -> ``{filename: source}`` to gate + land.
    - ``gate``             ``(work_dir, files, gen) -> results``; writes + runs the gate.
    - ``summary_fields``   generation dict -> the domain state fields to surface.

    Recall grounding (used by ``SmithRecall``):
    - ``recall_lessons``   ``(brief, *, limit) -> list[str]`` reflexion lessons.
    - ``retrieve_context`` ``(brief, *, k) -> list[Snippet]`` RAG grounding.

    Record (used by ``SmithRecord``):
    - ``landed_stem``      state -> the raw name the landed files are snake-cased from.
    - ``trainset_fields``  state -> the domain row written to the trainset on success.
    - ``append_lesson``    lesson sink (kwargs ``brief``/``failed_kind``/``finding``/``attempts``).
    - ``append_trainset``  accepted-pair sink (one row dict).

    Landing shape (used by ``SmithRecord._land``):
    - ``bundle_files``     when set, the artifact is a multi-file *bundle*: these
      filenames are written verbatim into ``output_dir/<stem>/`` (a subdir). Empty
      (the default) -> the flat two-file landing (``<stem>.py`` + ``test_<stem>.py``).
    - ``entry_file``       which bundle file's path ``_land`` returns as the landed
      entry point (default: the first ``bundle_files`` entry). Ignored when flat.
    """

    name: str
    artifact_filenames: tuple[str, str]
    artifact_files: Callable[[dict[str, Any]], dict[str, str]]
    gate: Callable[[Path, dict[str, str], dict[str, Any]], list[VerifierResult]]
    summary_fields: Callable[[dict[str, Any]], dict[str, Any]]
    recall_lessons: Callable[..., list[str]]
    retrieve_context: Callable[..., list[Snippet]]
    landed_stem: Callable[[BaseModel], str]
    trainset_fields: Callable[[BaseModel], dict[str, Any]]
    append_lesson: Callable[..., None]
    append_trainset: Callable[[dict[str, Any]], Any]
    bundle_files: tuple[str, ...] = ()
    entry_file: str = ""
