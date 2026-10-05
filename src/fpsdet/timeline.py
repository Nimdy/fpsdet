"""The canonical player timeline: one order for one set of events, whatever order the file had.

The scorer reads chronology from the server's clock and from fields that carry sequence, never from the
order lines arrived in. Before anything reads a player's events, they are put in this order:

1. ``match_id``. Matches are independent, and ``t_ms`` restarts in each one.
2. ``t_ms``, the server clock.
3. The event kind, by ``KIND_RANK``: movement, then shot. No check reads a shot against a movement
   sample at the same time, so this only makes the order total.
4. ``spray_index``, on shots that carry it: the server's own count within a spray. A sample without one
   sorts before any that has one.
5. The event's canonical text, only for events that 1 to 4 cannot tell apart. Those are simultaneous as
   far as the server's telemetry says. The text gives them one order whatever the file did, and each
   check that reads sequence decides separately what simultaneous events mean to it.

Identical events stay, side by side: a duplicate is kept, never merged.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Iterator

KIND_RANK = {"movement": 0, "shot": 1}

_TEXT = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=True)
_FIELDS: tuple[str, ...] = ()
# Empty containers are how an Event says a field was not sent.
_EMPTY_IS_ABSENT = frozenset({"mod_set", "extras"})


def canonical_text(event) -> str:
    """Every field the event sets, as JSON with sorted keys; extras as one nested object.

    The values are the parsed ones, so the raw file's key order, spacing and number spelling do not
    show, while an integer and a float of the same size stay different, as they are to the scorer.
    NaN and the infinities are written as the tokens NaN, Infinity and -Infinity.
    """
    global _FIELDS
    if not _FIELDS:
        _FIELDS = tuple(spec.name for spec in dataclasses.fields(event))
    row = {}
    for name in _FIELDS:
        value = getattr(event, name)
        if value is None or (name in _EMPTY_IS_ABSENT and not value):
            continue
        row[name] = list(value) if isinstance(value, tuple) else value
    return _TEXT.encode(row)


def _clock(event) -> tuple:
    spray = getattr(event, "spray_index", None)
    return (
        event.match_id,
        event.t_ms,
        KIND_RANK.get(event.event_type, len(KIND_RANK)),
        event.event_type,
        -1 if spray is None else spray,
    )


def timeline(events: Iterable) -> list:
    """One player's events in canonical order. The text is only built where the clock keys tie."""
    ordered = sorted(events, key=_clock)
    start = 0
    while start < len(ordered):
        end = start + 1
        key = _clock(ordered[start])
        while end < len(ordered) and _clock(ordered[end]) == key:
            end += 1
        if end - start > 1:
            ordered[start:end] = sorted(ordered[start:end], key=canonical_text)
        start = end
    return ordered


def player_timelines(events: Iterable) -> dict[str, list]:
    """Every player's canonical timeline, players in id order. One pass to group; no event is copied."""
    grouped: dict[str, list] = {}
    for event in events:
        grouped.setdefault(event.player_id, []).append(event)
    return {player_id: timeline(grouped[player_id]) for player_id in sorted(grouped)}


def same_time_groups(events: list) -> Iterator[list]:
    """Runs of consecutive events at one server moment, the same match and ``t_ms``.

    ``events`` must already be in match and time order, as a timeline is.
    """
    start = 0
    while start < len(events):
        end = start + 1
        moment = (events[start].match_id, events[start].t_ms)
        while end < len(events) and (events[end].match_id, events[end].t_ms) == moment:
            end += 1
        yield events[start:end]
        start = end
