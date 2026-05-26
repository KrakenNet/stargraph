"""LLM shim mock server — OpenAI-compatible endpoint with maritime canned responses."""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="LLM Shim Mock")

# ---------------------------------------------------------------------------
# Request / response models (OpenAI chat-completion shape)
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "mock-maritime"
    messages: list[ChatMessage] = []
    temperature: float = 0.7
    max_tokens: int = 512

class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage = Usage()

# ---------------------------------------------------------------------------
# Canned response templates
# ---------------------------------------------------------------------------

GEO_CONTEXT_RESPONSE = (
    "Vessel detected 14nm inside Iranian EEZ, 52nm from Bandar Abbas port. "
    "Located outside established IOTC fishing zones in the Strait of Hormuz "
    "traffic separation scheme. No AIS correlation detected — assessed as "
    "potential dark vessel operating in a high-sensitivity maritime zone. "
    "Recommend priority ISR tasking."
)

INTEL_REPORT_RESPONSE = (
    "## Detection Summary\n"
    "SAR-based vessel detection in Strait of Hormuz AOI.\n\n"
    "## Imagery Reference\n"
    "Source tile processed via YOLO-OBB inference pipeline.\n\n"
    "## AIS Correlation\n"
    "No matching AIS track within 5nm radius. Vessel classified as dark.\n\n"
    "## Geo-Context\n"
    "Located within Iranian EEZ, 14nm from territorial boundary. "
    "52nm from nearest major port (Bandar Abbas).\n\n"
    "## Risk Assessment\n"
    "CRITICAL — dark vessel in sensitive EEZ with no AIS transponder. "
    "Pattern consistent with sanctions evasion or IUU fishing.\n\n"
    "## Recommended Actions\n"
    "1. Task follow-up ISR collection\n"
    "2. Cross-reference with known vessel patterns\n"
    "3. Issue maritime awareness bulletin"
)

GENERIC_ACK = "Acknowledged. Processing complete — no additional context required."

# ---------------------------------------------------------------------------
# Keyword routing
# ---------------------------------------------------------------------------

def _route_response(messages: list[ChatMessage]) -> str:
    """Pick a canned response based on keywords in the message content."""
    combined = " ".join(m.content.lower() for m in messages)
    if "geo_context" in combined or "situational summary" in combined:
        return GEO_CONTEXT_RESPONSE
    if "intel report" in combined or "reporting" in combined:
        return INTEL_REPORT_RESPONSE
    return GENERIC_ACK

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(req: ChatCompletionRequest):
    text = _route_response(req.messages)
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=req.model,
        choices=[
            Choice(
                message=ChatMessage(role="assistant", content=text),
            )
        ],
        usage=Usage(
            prompt_tokens=len(text) // 4,
            completion_tokens=len(text) // 4,
            total_tokens=len(text) // 2,
        ),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=41001)
