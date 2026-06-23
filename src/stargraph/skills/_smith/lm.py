# SPDX-License-Identifier: Apache-2.0
"""LM plumbing + clarify — domain-agnostic, shared by every smith.

Builds DSPy LMs against a local Ollama server (native ``ollama_chat`` provider,
so ``num_ctx`` is honored) and runs the best-effort "do I need to ask a
clarifying question?" predictor. None of this depends on what's being generated,
so it lives in the shared core.
"""

from __future__ import annotations

from typing import Any

import dspy  # pyright: ignore[reportMissingTypeStubs]

DEFAULT_OLLAMA_URL = "http://localhost:41001"


def make_lm(
    model: str,
    *,
    url: str = DEFAULT_OLLAMA_URL,
    temperature: float | None = None,
    max_tokens: int | None = None,
    num_ctx: int | None = None,
) -> Any:
    """Build a DSPy LM for a local Ollama server.

    Uses litellm's native ``ollama_chat`` provider (POSTs to ``/api/chat`` with
    an ``options`` block), not the OpenAI-compatible ``/v1`` shim: only the
    native path honors ``num_ctx`` (the context-window size). ``temperature``
    and ``max_tokens`` (→ ``num_predict``) map through the same options block.
    Each is sent only when set, so unset knobs fall back to the model's own
    defaults rather than being pinned to a guess."""
    opts: dict[str, Any] = {}
    if temperature is not None:
        opts["temperature"] = temperature
    if max_tokens is not None:
        opts["max_tokens"] = max_tokens
    if num_ctx is not None:
        opts["num_ctx"] = num_ctx
    return dspy.LM(f"ollama_chat/{model}", api_base=url, **opts)  # pyright: ignore[reportUnknownMemberType]


def configure_lm(
    model: str,
    *,
    url: str = DEFAULT_OLLAMA_URL,
    temperature: float | None = None,
    max_tokens: int | None = None,
    num_ctx: int | None = None,
) -> None:
    """Set the process-global LM (the smith ``make`` CLIs + offline optimizers).

    Uses ``dspy.configure``, which DSPy only permits from the task that first
    configured it. Async workers on another task must scope the LM with
    ``dspy.context(lm=make_lm(...))`` instead — see the TUI Generate worker."""
    dspy.configure(  # pyright: ignore[reportUnknownMemberType]
        lm=make_lm(model, url=url, temperature=temperature, max_tokens=max_tokens, num_ctx=num_ctx)
    )


def clarify(brief: str, prior_findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask the model whether it needs a clarifying question to proceed.

    Used by the Generate flow twice: pre-flight on the brief, and (with the
    failed verifier findings) after the repair loop exhausts attempts. Returns
    ``{"needs": bool, "question": str, "options": list[str]}`` — ``options`` are
    concrete multiple-choice answers when the model can offer them (the UI shows
    them as buttons, with a free-text box as the fallback).

    Best-effort: any model/transport error → ``needs=False`` so a clarify outage
    never blocks generation; the gate still guards correctness. Caller scopes the
    LM with ``dspy.context``."""
    try:
        sig = (
            "brief, prior_findings -> needs_clarification: bool, question: str, options: list[str]"
        )
        predictor = dspy.Predict(sig)  # pyright: ignore[reportUnknownMemberType]
        result = predictor(brief=brief, prior_findings=prior_findings)  # pyright: ignore[reportUnknownVariableType]
        question = str(getattr(result, "question", "") or "").strip()
        needs = bool(getattr(result, "needs_clarification", False)) and bool(question)
        options: list[str] = []
        raw: Any = getattr(result, "options", None)
        try:
            for opt in raw:
                text = str(opt).strip()
                if text:
                    options.append(text)
        except TypeError:
            options = []
        return {"needs": needs, "question": question, "options": options}
    except Exception:
        return {"needs": False, "question": "", "options": []}
