"""Client knowledge: what this client could lawfully know about a target at the time of a shot.

CurrentInformationSemanticsTest pins, before the knowledge engine exists, exactly which samples each
information check receives today. The engine must reproduce every one of them, except where a test
says it documents behaviour the engine deliberately changes.
"""

from __future__ import annotations

import unittest

from fpsdet.models import Event
from fpsdet.signals import wire_finding
from fpsdet.summarize import summarize
from fpsdet.synthetic import build_demo

PROFILE = build_demo().profile  # hidden_grace_ms 1000


def ev(t: int, pid: str = "x", **values) -> Event:
    fields = dict(game_id="example-loadout", match_id="m1", player_id=pid, t_ms=t, event_type="shot",
                  skill_band="average", weapon_class="rifle", weapon_id="ak")
    fields.update(values)
    return Event(**fields)


def weapon(events: list[Event]):
    (record,) = summarize(events, PROFILE)
    (summary,) = record.weapons
    return summary


class CurrentInformationSemanticsTest(unittest.TestCase):
    """What the hidden-mover, quiet-aim, private-replay and teammate checks read today. Shots are two
    seconds apart, so the time since the previous shot never cuts a track time."""

    def test_hidden_time_counts_on_unknowable_and_unlabelled_shots_outside_grace(self):
        shots = [
            ev(0, hidden_track_ms=300.0, information_state="unknowable"),
            ev(2000, hidden_track_ms=301.0),  # no label: the field itself says neither seen nor heard
            ev(4000, hidden_track_ms=302.0, information_state="audio"),  # heard: never hidden tracking
            ev(6000, hidden_track_ms=303.0, information_state="unknowable", since_perceived_ms=500.0),  # just lost
            ev(8000, hidden_track_ms=304.0, information_state="unknowable", since_perceived_ms=1500.0),
            ev(10000, hidden_track_ms=305.0, information_state="unknowable", since_perceived_ms=999.0),
            ev(12000, hidden_track_ms=306.0, information_state="unknowable", since_perceived_ms=1000.0),  # the grace ends at 1000
        ]
        self.assertEqual(weapon(shots).hidden_track_ms, [300.0, 301.0, 304.0, 306.0])

    def test_a_visible_shot_with_hidden_time_counts_today(self):
        # The shot's enemy is labelled visible, yet the hidden time says neither seen nor heard. Today it
        # counts. The knowledge engine treats this as contradictory telemetry and drops it.
        self.assertEqual(weapon([ev(0, hidden_track_ms=300.0, information_state="visible")]).hidden_track_ms, [300.0])

    def test_quiet_aim_populations(self):
        shots = [
            ev(0, aim_jitter_deg=1.0, information_state="visible"),
            ev(2000, aim_jitter_deg=1.1, information_state="audio"),
            ev(4000, aim_jitter_deg=1.2, information_state="visible", since_perceived_ms=0.0),
            ev(6000, aim_jitter_deg=0.1, information_state="unknowable"),
            ev(8000, aim_jitter_deg=0.2, information_state="unknowable", since_perceived_ms=1500.0),
            ev(10000, aim_jitter_deg=0.3, information_state="unknowable", since_perceived_ms=500.0),  # recently seen: neither
            ev(12000, aim_jitter_deg=0.4),  # no label: neither
        ]
        summary = weapon(shots)
        self.assertEqual(summary.knowable_jitter, [1.0, 1.1, 1.2])
        self.assertEqual(summary.unknowable_jitter, [0.1, 0.2])

    def test_teammate_contacts_are_unknowable_shots_with_an_enemy(self):
        shots = [
            ev(0, information_state="unknowable", enemy_id="e1"),
            ev(2000, information_state="unknowable", enemy_id="e1", since_perceived_ms=500.0),
            ev(4000, information_state="unknowable"),
            ev(6000, information_state="visible", enemy_id="e1"),
            ev(8000, information_state="unknowable", enemy_id="e2", since_perceived_ms=2000.0),
        ]
        self.assertEqual(weapon(shots).hidden_contacts, [("m1", 0, "e1"), ("m1", 8000, "e2")])

    def test_private_time_does_not_read_the_shot_label_or_the_grace(self):
        # The replay body is not the shot's enemy, and this client never perceived it.
        shots = [
            ev(0, private_track_ms=80.0, information_state="visible"),
            ev(2000, private_track_ms=81.0, information_state="audio"),
            ev(4000, private_track_ms=82.0),
            ev(6000, private_track_ms=83.0, information_state="unknowable", since_perceived_ms=10.0),
        ]
        self.assertEqual(weapon(shots).private_track_ms, [80.0, 81.0, 82.0, 83.0])

    def test_wire_needs_a_started_delay_and_both_errors(self):
        led = [ev(i * 2000, wire_error_deg=0.1, picture_error_deg=2.0, interp_delay_ms=200.0) for i in range(8)]
        skipped = [
            ev(20000, wire_error_deg=0.1, picture_error_deg=2.0, interp_delay_ms=0.0),
            ev(22000, wire_error_deg=0.1, picture_error_deg=2.0),
            ev(24000, wire_error_deg=0.1, interp_delay_ms=200.0),
        ]
        text, numbers = wire_finding(weapon(led + skipped), PROFILE)
        self.assertEqual((numbers["led_shots"], numbers["shots_with_both_errors"], numbers["led_delay_ms"]), (8, 8, 1600.0))


if __name__ == "__main__":
    unittest.main()
