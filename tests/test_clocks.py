"""Profiles that declare their clocks: speed runs in milliseconds, and shots stamped on ticks.

Every field here is opt-in. A profile without them scores as before and keeps its digest."""

from __future__ import annotations

import dataclasses
import json
import unittest

from fpsdet.models import Event, GameProfile, WeaponRule, WeaponSummary
from fpsdet.parse import ParseError, load_profile, profile_from_dict
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.provenance import canonical_profile, profile_digest
from fpsdet.signals import metronome_break, metronome_eligibility, metronome_finding
from fpsdet.synthetic import PROFILE_PATH


def _run(count: int, step_ms: int) -> list[Event]:
    """A heavy loadout at light speed: every sample over its cap, ``step_ms`` apart."""
    return [
        Event(
            game_id="example-loadout",
            match_id="m",
            player_id="runner",
            t_ms=index * step_ms,
            event_type="movement",
            skill_band="average",
            speed_mps=7.1,
            loadout_weight_kg=10,
            on_ground=True,
            displacement_cause="none",
        )
        for index in range(count)
    ]


class SpeedRunInMillisecondsTest(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(PROFILE_PATH)
        self.timed = dataclasses.replace(self.profile, speed_min_run_ms=2000, movement_clock="server")

    def test_absent_is_todays_sample_count(self):
        # 30 samples at 64 Hz: 0.45 s of running, and 30 samples is over the default 25.
        case = run_score(_run(30, 15), self.profile)[0]
        self.assertTrue(case.speed.sustained)
        self.assertNotIn("min_run_ms", json.dumps(case_to_dict(case)))

    def test_a_fast_emitter_needs_the_same_time_as_a_slow_one(self):
        self.assertFalse(run_score(_run(30, 15), self.timed)[0].speed.sustained)
        slow = run_score(_run(21, 100), self.timed)[0]  # 2.0 s at 10 Hz, under 25 samples
        self.assertTrue(slow.speed.sustained)
        self.assertEqual(slow.decision, "review")
        self.assertEqual(slow.speed.longest_run_ms, 2000)
        self.assertIn("2000 ms of server time", slow.speed.detail)

    def test_no_declared_movement_clock_abstains(self):
        undeclared = dataclasses.replace(self.profile, speed_min_run_ms=2000)
        case = run_score(_run(40, 100), undeclared)[0]
        self.assertFalse(case.speed.sustained)
        self.assertIn("movement_clock", case.speed.detail)
        self.assertNotIn("speed", case.checks)

    def test_eligible_needs_the_samples_to_span_the_bar(self):
        def speed(case) -> dict:
            return case_to_dict(case)["evidence"]["detector_eligibility"]["detectors"]["speed"]

        self.assertIn("insufficient_samples", speed(run_score(_run(30, 15), self.timed)[0]))  # 0.44 s
        self.assertIn("eligible", speed(run_score(_run(21, 100), self.timed)[0]))  # 2.0 s

    def test_the_finding_names_the_clock(self):
        case = run_score(_run(21, 100), self.timed)[0]
        text = json.dumps(case_to_dict(case))
        self.assertIn('"min_run_ms": 2000', text)
        self.assertIn('"movement_clock": "server"', text)


class ShotClockTest(unittest.TestCase):
    def setUp(self):
        rules = {"rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=5)}
        self.today = GameProfile(game_id="t", weapons=rules)
        self.ticked = GameProfile(game_id="t", shot_clock="server_tick", weapons=rules)
        self.declared = GameProfile(game_id="t", shot_clock="server_tick", tick_ms=16, weapons=rules)
        # A held trigger on a 64-tick server: a 90 ms cycle lands on 6.25 ticks, stamped 100 ms every time.
        held = {f"m{i}": [100] * 7 + [3100] + [100] * 7 for i in range(10)}
        self.held = WeaponSummary(
            "rifle", "rifle", "average", fire_gaps=[gap for rows in held.values() for gap in rows], fire_matches=held
        )

    def test_absent_is_todays_behaviour(self):
        self.assertIsNotNone(metronome_break(self.held, self.today))
        self.assertNotIn("shot_clock", metronome_finding(self.held, self.today)[1]["thresholds"])

    def test_ticks_of_no_declared_length_abstain(self):
        self.assertIsNone(metronome_break(self.held, self.ticked))
        self.assertEqual(metronome_eligibility(self.held, self.ticked), "disabled")

    def test_a_declared_tick_is_the_servers_pace(self):
        self.assertIsNone(metronome_break(self.held, self.declared))

    def test_the_finding_names_the_clock(self):
        tapped = {f"m{i}": [140] * 7 + [3100] + [140] * 7 for i in range(10)}
        weapon = WeaponSummary(
            "rifle", "rifle", "average", fire_gaps=[gap for rows in tapped.values() for gap in rows], fire_matches=tapped
        )
        thresholds = metronome_finding(weapon, self.declared)[1]["thresholds"]
        self.assertEqual((thresholds["shot_clock"], thresholds["tick_ms"]), ("server_tick", 16))


class ProfileTest(unittest.TestCase):
    def test_older_profiles_keep_their_digest(self):
        profile = load_profile(PROFILE_PATH)
        for name in ("speed_min_run_ms", "movement_clock", "shot_clock"):
            self.assertNotIn(name, canonical_profile(profile))

    def test_a_declared_clock_changes_the_digest(self):
        profile = load_profile(PROFILE_PATH)
        for change in ({"speed_min_run_ms": 2000}, {"movement_clock": "server"}, {"shot_clock": "server_ms"}):
            self.assertNotEqual(profile_digest(dataclasses.replace(profile, **change)), profile_digest(profile))

    def test_parse(self):
        profile = profile_from_dict({"game_id": "t", "speed_min_run_ms": 1500, "movement_clock": "server", "shot_clock": "server_tick", "tick_ms": 16})
        self.assertEqual((profile.speed_min_run_ms, profile.movement_clock, profile.shot_clock), (1500, "server", "server_tick"))
        with self.assertRaises(ParseError):
            profile_from_dict({"game_id": "t", "movement_clock": "client"})
        with self.assertRaises(ParseError):
            profile_from_dict({"game_id": "t", "shot_clock": "ticks"})


if __name__ == "__main__":
    unittest.main()
