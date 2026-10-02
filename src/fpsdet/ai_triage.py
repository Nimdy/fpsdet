"""Optional brief over a finished case. Any OpenAI-compatible chat endpoint.

The model never sees the lake and never changes the decision. One call per case
that a person might open, not one call per shot. That is the speedup: aggregate
first, then ask for language.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

Transport = Callable[[dict], str]

SYSTEM = (
    "You are the review assistant for one FPS baseline case. "
    "Do not recommend a ban and do not invent numbers. "
    "Explain only the numbers in the case, in plain language. "
    "Say whether a blast, ragdoll, vehicle, parachute, ability, or untrained build could explain it. "
    "If the sample is thin, say so. "
    "A report count is a reason to look, not proof."
)


def redact_case(case: dict) -> dict:
    hidden = dict(case)
    hidden["player_id"] = "redacted"
    hidden["party_ids"] = []
    hidden["match_ids"] = []
    hidden["party_note"] = ""
    return hidden


def triage_case(case: dict, transport: Transport, *, redact_ids: bool = True) -> str:
    payload = redact_case(case) if redact_ids else case
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
        ]
    }
    text = transport(body).strip()
    if not text:
        raise RuntimeError("AI transport returned an empty brief")
    return text


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def openai_compatible_transport(base_url: str, api_key: str, model: str) -> Transport:
    url = _endpoint(base_url)

    def _send(body: dict) -> str:
        request_body = dict(body)
        request_body["model"] = model
        request_body["temperature"] = 0
        data = json.dumps(request_body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]

    return _send
