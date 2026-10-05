"""Client knowledge: what this client could lawfully know about a target at the time of a shot.

CurrentInformationSemanticsTest pins, before the knowledge engine exists, exactly which samples each
information check receives today. The engine must reproduce every one of them, except where a test
says it documents behaviour the engine deliberately changes.
"""

from __future__ import annotations

import unittest

import dataclasses
import itertools

from fpsdet.knowledge import (
    LEGACY_STATES,
    presentation,
    private_knowledge,
    resolve,
    shot_channels,
    shot_knowledge,
    tracked_knowledge,
)
from fpsdet.models import Event, GameProfile
from fpsdet.parse import ParseError, profile_from_dict
from fpsdet.provenance import profile_digest
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


GAME = GameProfile(game_id="g")  # vision and audio declared, grace 1000 ms
WITH_TEAM = dataclasses.replace(GAME, knowledge_channels=("vision", "audio", "team_share"))


class AbstentionMatrixTest(unittest.TestCase):
    """Any known channel: known. Every declared channel checked and absent: unknowable. Otherwise unknown."""

    def test_every_combination_of_vision_audio_and_recent_perception(self):
        states = ("known", "absent", "unchecked")
        for vision, audio, recent in itertools.product(states, states, states):
            state = resolve({"vision": vision, "audio": audio, "recent_perception": recent}, ("vision", "audio"))
            if "known" in (vision, audio, recent):
                expected = "known"
            elif vision == "absent" and audio == "absent":
                expected = "unknowable"
            else:
                expected = "unknown"
            self.assertEqual(state.status, expected, (vision, audio, recent))

    def test_unchecked_is_never_absent(self):
        for required in (("vision",), ("vision", "audio"), ("vision", "audio", "team_share"), ("vision", "audio", "recent_perception")):
            for missing in required:
                channels = {name: "absent" for name in required if name != missing}
                self.assertEqual(resolve(channels, required).status, "unknown", (required, missing))
                self.assertEqual(resolve({**channels, missing: "unchecked"}, required).status, "unknown")
            self.assertEqual(resolve({name: "absent" for name in required}, required).status, "unknowable")

    def test_a_conflict_is_unknown_and_no_declared_channel_is_never_unknowable(self):
        self.assertEqual(resolve({"vision": "absent", "audio": "absent"}, ("vision", "audio"), "telemetry disagrees").status, "unknown")
        self.assertEqual(resolve({"vision": "absent"}, ()).status, "unknown")
        with self.assertRaises(ValueError):
            resolve({"vision": "maybe"}, ("vision",))


class LegacyAdapterTest(unittest.TestCase):
    def test_information_state_maps_to_what_the_docs_promise(self):
        self.assertEqual(LEGACY_STATES, {
            "visible": {"vision": "known"},
            "audio": {"audio": "known"},
            "unknowable": {"vision": "absent", "audio": "absent"},
        })
        self.assertEqual(shot_channels(ev(0)), ({}, ""))
        for label, status in (("visible", "known"), ("audio", "known"), ("unknowable", "unknowable"), (None, "unknown")):
            self.assertEqual(shot_knowledge(ev(0, information_state=label), GAME).status, status, label)

    def test_per_channel_fields_and_their_conflicts(self):
        cases = [
            (dict(vision_state="absent"), "unknown"),  # audio was not reported
            (dict(audio_state="absent"), "unknown"),
            (dict(vision_state="absent", audio_state="absent"), "unknowable"),
            (dict(vision_state="known", audio_state="absent"), "known"),
            (dict(vision_state="absent", audio_state="known"), "known"),
            (dict(information_state="visible", audio_state="absent"), "known"),
            (dict(information_state="unknowable", vision_state="absent"), "unknowable"),
        ]
        for fields, status in cases:
            self.assertEqual(shot_knowledge(ev(0, **fields), GAME).status, status, fields)
        torn = shot_knowledge(ev(0, information_state="unknowable", vision_state="known"), GAME)
        self.assertEqual((torn.status, torn.cause), ("unknown", "conflict"))
        self.assertIn("information_state unknowable has vision absent", torn.conflict)
        self.assertEqual(shot_knowledge(ev(0, information_state="unknowable", audio_state="unchecked"), GAME).status, "unknown")


class RecentPerceptionTest(unittest.TestCase):
    def status(self, **fields) -> str:
        return shot_knowledge(ev(0, **fields), GAME).status

    def test_the_grace_window(self):
        self.assertEqual(self.status(information_state="visible"), "known")
        self.assertEqual(self.status(information_state="audio"), "known")
        self.assertEqual(self.status(information_state="unknowable", since_perceived_ms=100.0), "known")
        self.assertEqual(self.status(information_state="unknowable", since_perceived_ms=999.0), "known")
        self.assertEqual(self.status(information_state="unknowable", since_perceived_ms=1000.0), "unknowable")
        self.assertEqual(self.status(information_state="unknowable"), "unknowable")  # not sent: decides nothing by default
        self.assertEqual(self.status(since_perceived_ms=5000.0), "unknown")  # no vision or audio verdict
        recent = shot_knowledge(ev(0, information_state="unknowable", since_perceived_ms=100.0), GAME)
        self.assertEqual((recent.cause, recent.perceived_now), ("recent", False))

    def test_a_game_can_require_recent_perception(self):
        strict = dataclasses.replace(GAME, knowledge_channels=("vision", "audio", "recent_perception"))
        self.assertEqual(shot_knowledge(ev(0, information_state="unknowable"), strict).status, "unknown")
        self.assertEqual(shot_knowledge(ev(0, information_state="unknowable", since_perceived_ms=2000.0), strict).status, "unknowable")

    def test_a_declared_channel_without_telemetry_means_unknown(self):
        state = shot_knowledge(ev(0, information_state="unknowable"), WITH_TEAM)
        self.assertEqual((state.status, state.channel("team_share")), ("unknown", "unchecked"))


class TargetsTest(unittest.TestCase):
    """The hidden-track enemy, the private replay body and the wire each have their own knowledge."""

    def test_the_tracked_enemy(self):
        self.assertEqual(tracked_knowledge(ev(0, hidden_track_ms=200.0), GAME).status, "unknowable")
        self.assertEqual(tracked_knowledge(ev(0, hidden_track_ms=200.0, information_state="audio"), GAME).status, "known")
        self.assertEqual(tracked_knowledge(ev(0, hidden_track_ms=200.0, information_state="visible"), GAME).status, "known")
        self.assertEqual(tracked_knowledge(ev(0, hidden_track_ms=200.0, since_perceived_ms=10.0), GAME).status, "known")
        self.assertEqual(tracked_knowledge(ev(0, hidden_track_ms=200.0), WITH_TEAM).status, "unknown")
        unchecked = tracked_knowledge(ev(0, hidden_track_ms=200.0, vision_state="unchecked"), GAME)
        self.assertEqual((unchecked.status, unchecked.cause), ("unknown", "conflict"))

    def test_the_private_replay_body(self):
        state = private_knowledge(GAME)
        self.assertEqual((state.status, state.channel("recent_perception")), ("unknowable", "not_applicable"))
        self.assertEqual(private_knowledge(WITH_TEAM).status, "unknown")

    def test_the_wire_is_data_and_the_picture_is_perception(self):
        self.assertIsNone(presentation(ev(0, wire_error_deg=0.1, picture_error_deg=2.0)))
        shown = presentation(ev(0, wire_error_deg=0.1, picture_error_deg=2.0, interp_delay_ms=100.0))
        self.assertEqual((shown.wire_error_deg, shown.picture_error_deg, shown.interp_delay_ms), (0.1, 2.0, 100.0))
        self.assertIn("client_data", shown.to_dict())


class ProfileChannelsTest(unittest.TestCase):
    def test_the_default_is_what_unknowable_has_always_meant(self):
        self.assertEqual(profile_from_dict({"game_id": "g"}).knowledge_channels, ("vision", "audio"))
        self.assertEqual(profile_from_dict({"game_id": "g", "knowledge_channels": ["vision", "audio", "team_share"]}).knowledge_channels,
                         ("vision", "audio", "team_share"))
        for bad in ([], ["sonar"], "vision"):
            with self.assertRaises(ParseError):
                profile_from_dict({"game_id": "g", "knowledge_channels": bad})

    def test_the_declared_channels_are_part_of_the_profile_digest(self):
        self.assertNotEqual(profile_digest(GAME), profile_digest(WITH_TEAM))


if __name__ == "__main__":
    unittest.main()
