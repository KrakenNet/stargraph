# SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible deterministic LLM shim for cve_remediation demo.

Implements just enough of /v1/chat/completions and /v1/models for DSPy's
LM client to exercise the wire shape without needing a real model. The
response is keyed off the system+user prompt so DSPy modules get
deterministic outputs that match their JSON-adapter signatures.

Default-deny: any prompt that doesn't match a known signature returns a
``{"error": "no-canned-response"}`` payload so unmocked DSPy calls fail
loud (matches Harbor's force-loud adapter posture).

Recognized signatures (matched on substring of the user message):

  - "extract a CVE"      -> CveExtract JSON
  - "classify injection" -> {"injection_class": "clean"}
  - "critique"           -> {"verdict": "approved", "feedback_text": ""}
  - "redact"             -> echo + redaction marker
  - "render docx"        -> markdown body
  - "plan"               -> RemediationBundle JSON
  - "code writer"        -> {"runtime": "ansible", "apply_bundle_ref": ...}
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

MODEL = os.environ.get("LLM_MODEL_NAME", "cve-rem-shim")

app = FastAPI(title="cve-rem LLM shim", version="1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL}


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": MODEL, "object": "model", "owned_by": "cve-rem-demo"}],
    }


class _Msg(BaseModel):
    role: str
    content: str


class _ChatReq(BaseModel):
    model: str
    messages: list[_Msg]
    temperature: float = 0.0
    max_tokens: int = 2048
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    stream: bool = False


# --- Canned response logic --------------------------------------------------


def _cve_extract_response() -> dict[str, Any]:
    return {
        "cve_id": "CVE-2026-12345",
        "cwe_class": "CWE-79",
        "vuln_class": "xss",
        "affected_products": ["nginx"],
        "affected_versions": ["1.20.0"],
        "cvss_score_bp": 750,
        "epss_score_bp": 1500,
        "kev_listed": False,
        "references": ["https://nvd.nist.gov/vuln/detail/CVE-2026-12345"],
    }


def _injection_classify_response() -> dict[str, Any]:
    return {"injection_class": "clean"}


def _critic_response() -> dict[str, Any]:
    return {
        "verdict": "approved",
        "feedback_text": "",
        "veto_flags": [],
        "attempt": 1,
    }


def _planner_response() -> dict[str, Any]:
    return {
        "runtime": "ansible",
        "apply_bundle_ref": "memory://demo-apply",
        "rollback_bundle_ref": "memory://demo-rollback",
        "verify_probe_ref": "memory://demo-verify",
        "metadata": {"shim": True},
    }


def _redaction_response() -> dict[str, Any]:
    return {"redacted": True, "marker": "REDACTED-BY-SHIM"}


def _docx_response() -> dict[str, Any]:
    return {
        "title": "CVE Retrospective (shim)",
        "body_md": "# Retro\nDeterministic shim output for demo.",
    }


def _route(user_text: str) -> dict[str, Any]:
    lo = user_text.lower()
    if "extract" in lo and "cve" in lo:
        return _cve_extract_response()
    if "injection" in lo or "classify" in lo:
        return _injection_classify_response()
    if "critic" in lo or "critique" in lo:
        return _critic_response()
    if "redact" in lo:
        return _redaction_response()
    if "docx" in lo or "render" in lo:
        return _docx_response()
    if "plan" in lo or "code writer" in lo or "runtime" in lo:
        return _planner_response()
    return {"error": "no-canned-response", "echo": user_text[:200]}


@app.post("/v1/chat/completions")
def chat_completions(req: _ChatReq) -> dict[str, Any]:
    user_text = next(
        (m.content for m in reversed(req.messages) if m.role == "user"),
        "",
    )
    payload = _route(user_text)
    body = json.dumps(payload, ensure_ascii=False)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": body},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": sum(len(m.content) for m in req.messages) // 4,
            "completion_tokens": len(body) // 4,
            "total_tokens": (sum(len(m.content) for m in req.messages) + len(body)) // 4,
        },
    }
