# SPDX-License-Identifier: Apache-2.0
"""End-of-run summary renderer for ``stargraph run`` (FR-8 ergonomics).

Convention-driven:
- Fields ending in ``_files`` with ``dict[str, str]`` value -> dump to disk
- Field named ``verifier_results`` -> render kind/passed/duration table
- Other non-default state fields -> "Final state fields" compact listing
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel
    from rich.console import Console

    from stargraph.checkpoint.protocol import RunSummary
    from stargraph.cli._progress import ProgressStats


__all__ = ["SummaryRenderer"]


def _is_artifact_field(name: str, value: Any) -> bool:
    if not name.endswith("_files"):
        return False
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in value.items()  # type: ignore[union-attr]
    )


def _verifier_to_dict(v: Any) -> dict[str, Any]:
    return {
        "kind": str(getattr(v, "kind", "")),
        "passed": bool(getattr(v, "passed", False)),
        "duration_ms": int(getattr(v, "duration_ms", 0) or 0),
        "findings": list(getattr(v, "findings", []) or []),
    }


def _fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _is_default(value: Any) -> bool:
    """Heuristic: treat empty/zero/false values as default."""
    return (
        value is None or value == "" or value == [] or value == {} or value == 0 or value is False
    )


def _write_artifacts(files: dict[str, str], artifacts_dir: Path) -> None:
    for relpath, content in files.items():
        target = artifacts_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class SummaryRenderer:
    """Renders a final run summary (human or JSON) and writes artifacts."""

    def __init__(
        self,
        console: Console,
        *,
        json_mode: bool = False,
        suppress: bool = False,
    ) -> None:
        self._console = console
        self._json_mode = json_mode
        self._suppress = suppress

    def render(
        self,
        *,
        summary: RunSummary,
        final_state: BaseModel,
        stats: ProgressStats,
        artifacts_dir: Path,
        run_id: str,
        checkpoint: Path,
        duration_ms_override: int | None = None,
    ) -> None:
        """Write artifact files to disk; render summary to console.

        ``duration_ms_override`` (when provided) replaces the
        ``last_step_at - started_at`` derivation. The loop's ``_summary``
        helper currently stamps both fields at run-end, which collapses to
        ``0ms`` on fast graphs; passing ``ResultEvent.run_duration_ms`` here
        is the authoritative wall-clock duration.
        """
        artifact_relpaths: list[str] = []
        verifier_results: list[Any] = []
        state_dump: dict[str, Any] = {}

        dumped = final_state.model_dump()
        for name, value in dumped.items():
            field_value = getattr(final_state, name)
            if _is_artifact_field(name, field_value):
                _write_artifacts(field_value, artifacts_dir)
                artifact_relpaths.extend(sorted(field_value.keys()))
            elif name == "verifier_results" and isinstance(field_value, list):
                verifier_results = list(field_value)  # type: ignore[arg-type]
            elif not _is_default(value):
                state_dump[name] = value

        if self._suppress:
            return

        duration_ms = (
            duration_ms_override
            if duration_ms_override is not None
            else int((summary.last_step_at - summary.started_at).total_seconds() * 1000)
        )

        if self._json_mode:
            payload: dict[str, Any] = {
                "status": summary.status,
                "run_id": run_id,
                "graph_hash": summary.graph_hash,
                "duration_ms": duration_ms,
                "step_count": stats.step_count,
                "llm_call_count": stats.llm_call_count,
                "total_tool_tokens": stats.total_tool_tokens,
                "node_durations_ms": dict(stats.node_durations_ms),
                "artifacts": artifact_relpaths,
                "verifier_results": [_verifier_to_dict(v) for v in verifier_results],
                "state_summary": state_dump,
                "checkpoint": str(checkpoint),
            }
            self._console.print(json.dumps(payload), soft_wrap=True, no_wrap=True)
            return

        # Human summary.
        status_mark = "✓" if summary.status == "done" else "✗"
        status_color = "green" if summary.status == "done" else "red"
        self._console.print(
            f"\n[{status_color}]{status_mark} {summary.status}[/{status_color}] "
            f"in {_fmt_duration(duration_ms)}  "
            f"({stats.step_count} steps, {stats.llm_call_count} llm calls)"
        )

        if artifact_relpaths:
            self._console.print(f"\n  artifacts written to {artifacts_dir}/")
            for relpath in artifact_relpaths:
                size = (artifacts_dir / relpath).stat().st_size
                self._console.print(f"    {relpath:30s} {size} B")

        if verifier_results:
            self._console.print("\n  verifier results:")
            for v in verifier_results:
                vd = _verifier_to_dict(v)
                mark = "[green]✓[/green]" if vd["passed"] else "[red]✗[/red]"
                self._console.print(
                    f"    {vd['kind']:8s} {mark}  {_fmt_duration(vd['duration_ms'])}"
                )

        if state_dump:
            self._console.print(f"\n  final state ({len(state_dump)} fields):")
            for name, value in state_dump.items():
                rendered = repr(value)
                if len(rendered) > 60:
                    rendered = rendered[:57] + "..."
                self._console.print(f"    [dim]{name}[/dim]: {rendered}")

        # Print the inspect hint on its own continuation line so the
        # checkpoint path + UUID don't wrap mid-token in narrow terminals.
        self._console.print("\n  inspect:")
        self._console.print(f"    stargraph inspect {checkpoint} --run-id {run_id}")
