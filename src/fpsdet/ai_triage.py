"""Optional brief over a finished case. Any OpenAI-compatible chat endpoint.

The model never sees the lake and never changes the decision. One call per case
that a person might open, not one call per shot. That is the speedup: aggregate
first, then ask for language.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.request
from collections.abc import Callable, Iterable

Transport = Callable[[dict], str]

SYSTEM = (
    "You are the review assistant for one FPS baseline case. "
    "Do not recommend a ban and do not invent numbers. "
    "Explain only the numbers in the case, in plain language. "
    "Say whether a blast, ragdoll, vehicle, parachute, ability, or untrained build could explain it. "
    "If the sample is thin, say so. "
    "A report count is a reason to look, not proof."
)


# Characters that can sit inside a player id. A match must not be glued to more of them,
# so the id "rage" is not found inside the word "average".
_ID_EDGE = r"A-Za-z0-9_.\-"
_TEXT_FIELDS = ("reasons", "observations", "untrained")


def redact_case(case: dict, known_ids: Iterable[str] = ()) -> dict:
    """Strip every account the case names, not only its own.

    Batch checks write other players into the case: the account whose
    leftover matched, the teammate who called the enemy. ``known_ids`` is every
    player id in the batch. Each one the case mentions becomes ``player-A``,
    ``player-B`` and so on, so the brief can still say "the same account". The
    seal is dropped because it hashes the player id.
    """
    hidden = copy.deepcopy(case)
    own = str(case.get("player_id") or "")
    others = {str(pid) for pid in known_ids if pid and str(pid) != own}
    if case.get("vendor_twin"):
        others.add(str(case["vendor_twin"]))
    aliases: dict[str, str] = {}

    def alias(player_id: str) -> str:
        if player_id not in aliases:
            aliases[player_id] = f"player-{_letters(len(aliases))}"
        return aliases[player_id]

    names = sorted(others, key=len, reverse=True)
    pattern = None
    if names or own:
        parts = [re.escape(name) for name in ([own] if own else []) + names]
        pattern = re.compile(rf"(?<![{_ID_EDGE}])(?:{'|'.join(parts)})(?![{_ID_EDGE}])")

    def scrub(text: str) -> str:
        if pattern is None:
            return text
        return pattern.sub(lambda m: "this player" if m.group(0) == own else alias(m.group(0)), text)

    for name in _TEXT_FIELDS:
        if isinstance(hidden.get(name), list):
            hidden[name] = [scrub(item) if isinstance(item, str) else item for item in hidden[name]]
    if isinstance(hidden.get("speed"), dict) and isinstance(hidden["speed"].get("detail"), str):
        hidden["speed"]["detail"] = scrub(hidden["speed"]["detail"])
    if hidden.get("vendor_twin"):
        hidden["vendor_twin"] = alias(str(hidden["vendor_twin"]))
    hidden["player_id"] = "redacted"
    hidden["party_ids"] = []
    hidden["match_ids"] = []
    hidden["party_note"] = ""
    hidden["seal"] = ""
    return hidden


def _letters(index: int) -> str:
    text = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        text = chr(65 + rest) + text
    return text


def triage_case(
    case: dict,
    transport: Transport,
    *,
    redact_ids: bool = True,
    known_ids: Iterable[str] = (),
) -> str:
    payload = redact_case(case, known_ids) if redact_ids else case
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
