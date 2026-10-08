"""occluded_motion_replay/3: secret turns, not proximity.

The human pilot's dry run showed a player holding a doorway, with the probe resting behind it, reaching the
version 2 bar without knowing anything. Version 3 turns the body at secret times and asks whether the aim
turned with it, against the same aim at shuffled times. These are its acceptance tests:

- the doorway holder is not a review;
- a follower of server state is still caught;
- the shuffles do not catch the holder, or an honest player sweeping their aim;
- versions 1 and 2 are untouched (the dry run's own tests in test_human_pilot still reproduce it).
"""

from __future__ import annotations

import math
import random
import unittest
from pathlib import Path

from fpsdet.challenge import (
    ABSTAINED,
    FOLLOWED,
    NOT_FOLLOWED,
    OCCLUDED_MOTION_REPLAY_V3,
    Budget,
    ChallengeError,
    ChallengeRegistry,
    evaluate_challenges,
    plan_file_from_dict,
)
from fpsdet.challenge_plan import ServerSecret, plan_match, realize, turn_schedule
from fpsdet.models import Event, GameProfile
from fpsdet.parse import ParseError, parse_event
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.provenance import INPUTS_RECIPE_V4, timeline_recipe

GAME = GameProfile(game_id="g")
RULE = OCCLUDED_MOTION_REPLAY_V3.turns
BUDGET = Budget(to_ms=200_000, count=1, min_duration_ms=12_000, max_duration_ms=16_000)
TICK_MS = 50


def planned(seed: int):
    secret = ServerSecret(random.Random(seed).randbytes(32))
    plans = plan_match(secret, GAME, "m1", ["p"], BUDGET, nonce=f"{seed:032x}", spec=OCCLUDED_MOTION_REPLAY_V3)
    plan = plans.plans[0]
    return plans, plan, turn_schedule(plan, realize(secret, plan, BUDGET))


def body_bearing(plan, turns, rate_step: float = 40.0):
    """The body's bearing from the player, in degrees, by match time: drifting, and at each secret turn its
    rate changes by ``rate_step`` degrees a second in the turn's direction."""
    changes = sorted(turns)

    def at(t_ms: float) -> float:
        bearing, rate, clock = 0.0, 5.0, plan.start_ms
        for turn_ms, sign in changes:
            if turn_ms >= t_ms:
                break
            bearing += rate * (turn_ms - clock) / 1000
            rate, clock = rate + sign * rate_step, turn_ms
        return bearing + rate * (t_ms - clock) / 1000

    return at


def events_for(plan, turns, yaw_at, *, verdict: str = "absent", turn_verdict: str = "absent") -> list[Event]:
    common = dict(game_id="g", match_id=plan.match_id, player_id=plan.subject_id, event_type="movement", skill_band="average",
                  challenge_id=plan.challenge_id, challenge_audio_state="absent")
    out = [
        Event(t_ms=t, view_yaw_deg=round(yaw_at(t) % 360.0, 4), challenge_vision_state=verdict, **common)
        for t in range(plan.start_ms, plan.end_ms + 1, TICK_MS)
    ]
    out += [
        Event(t_ms=t, challenge_turn_index=index, challenge_turn_sign=sign, challenge_vision_state=turn_verdict, **common)
        for index, (t, sign) in enumerate(turns)
    ]
    return out


def judged(plans, events):
    registry = ChallengeRegistry.from_files([plans])
    case = run_score(events, GAME, challenges=registry)[0]
    return case, case.challenges[0]


def holder(seed: int):
    """Holding the doorway: the aim stays on its edge, with a person's small tremor."""
    rng = random.Random(seed)
    return lambda t: 0.3 * math.sin(t / 700) + rng.gauss(0, 0.15)


def sweeper(seed: int):
    """An honest player checking angles: the aim wanders, turns and settles, on its own schedule."""
    rng = random.Random(seed)
    knots = [(0, 0.0)]
    while knots[-1][0] < 400_000:
        knots.append((knots[-1][0] + rng.randint(300, 1500), knots[-1][1] + rng.gauss(0, 25)))

    def at(t: float) -> float:
        for (t0, y0), (t1, y1) in zip(knots, knots[1:]):
            if t0 <= t <= t1:
                return y0 + (y1 - y0) * (t - t0) / (t1 - t0) + rng.gauss(0, 0.2)
        return knots[-1][1]

    return at


def follower(plan, turns, latency_ms: int = 100, seed: int = 0):
    """Software reading the body from server state and aiming at it, a little late, with some noise."""
    bearing = body_bearing(plan, turns)
    rng = random.Random(seed)
    return lambda t: bearing(t - latency_ms) + rng.gauss(0, 0.3)


class TurnScheduleTest(unittest.TestCase):
    def test_turns_are_secret_spaced_and_inside_the_window(self):
        for seed in range(20):
            _plans, plan, turns = planned(seed)
            times = [t for t, _sign in turns]
            self.assertEqual(len(turns), RULE.count)
            self.assertEqual(times, sorted(times))
            self.assertTrue(all(b - a >= RULE.min_gap_ms for a, b in zip(times, times[1:])))
            self.assertGreaterEqual(times[0], plan.start_ms + RULE.lead_ms)
            self.assertLessEqual(times[-1], plan.end_ms - RULE.react_to_ms)
            self.assertTrue({sign for _t, sign in turns} <= {1, -1})

    def test_a_window_too_short_for_six_turns_is_not_planned(self):
        secret = ServerSecret(bytes(32))
        short = Budget(to_ms=200_000, count=1, min_duration_ms=8_000, max_duration_ms=16_000)
        with self.assertRaises(ChallengeError):
            plan_match(secret, GAME, "m1", ["p"], short, spec=OCCLUDED_MOTION_REPLAY_V3)

    def test_a_plan_file_with_a_short_window_does_not_load(self):
        plans, _plan, _turns = planned(1)
        data = plans.to_dict()
        data["schedule"]["min_duration_ms"] = 8_000
        row = data["challenges"][0]
        row["end_ms"] = row["start_ms"] + 9_000
        with self.assertRaises(ChallengeError):
            plan_file_from_dict(data)


class SecretTurnsTest(unittest.TestCase):
    def test_the_doorway_holder_is_not_a_review(self):
        for seed in range(10):
            plans, plan, turns = planned(seed)
            case, result = judged(plans, events_for(plan, turns, holder(seed)))
            self.assertEqual(result.status, NOT_FOLLOWED, (seed, result.to_dict()))
            self.assertNotEqual(case.decision, "review")
            # Tremor beats the per-turn bar about 1 time in 20 by chance; never 4 turns of 6.
            self.assertLess(result.followed_turns, RULE.min_followed)
            self.assertGreater(result.p_value, RULE.alpha)

    def test_a_follower_of_server_state_is_caught(self):
        for seed in range(10):
            plans, plan, turns = planned(seed)
            case, result = judged(plans, events_for(plan, turns, follower(plan, turns, seed=seed)))
            self.assertEqual(result.status, FOLLOWED, (seed, result.to_dict()))
            self.assertEqual(case.decision, "review")
            self.assertLessEqual(result.p_value, RULE.alpha)
            self.assertGreaterEqual(result.followed_turns, RULE.min_followed)

    def test_the_shuffles_do_not_catch_an_honest_sweep(self):
        caught = []
        for seed in range(40):
            plans, plan, turns = planned(seed)
            _case, result = judged(plans, events_for(plan, turns, sweeper(seed)))
            if result.status == FOLLOWED:
                caught.append(seed)
        self.assertEqual(caught, [])

    def test_the_finding_carries_its_turns_and_its_bar(self):
        plans, plan, turns = planned(3)
        case, _result = judged(plans, events_for(plan, turns, follower(plan, turns)))
        (obs,) = [obs for obs in case_to_dict(case)["evidence"]["observations"] if obs.get("family") == "challenge"]
        evidence = obs["evidence"]
        self.assertEqual(evidence["challenge"]["version"], 3)
        self.assertEqual([turn["t_ms"] for turn in evidence["turns"]], [t for t, _sign in turns])
        self.assertEqual(evidence["thresholds"], RULE.to_dict())
        self.assertIn("secret turns", obs["text"] if "text" in obs else " ".join(case.reasons))

    def test_the_same_case_scores_the_same_twice(self):
        plans, plan, turns = planned(4)
        events = events_for(plan, turns, sweeper(4))
        self.assertEqual(judged(plans, events)[1].to_dict(), judged(plans, list(reversed(events)))[1].to_dict())


class VerdictTest(unittest.TestCase):
    def test_a_body_seen_at_any_moment_voids_the_challenge(self):
        plans, plan, turns = planned(5)
        events = events_for(plan, turns, follower(plan, turns))
        events[40].challenge_vision_state = "known"
        _case, result = judged(plans, events)
        self.assertEqual((result.status, result.cause), (ABSTAINED, "seen"))

    def test_turns_the_server_did_not_check_do_not_count(self):
        plans, plan, turns = planned(6)
        _case, result = judged(plans, events_for(plan, turns, follower(plan, turns), verdict="unchecked", turn_verdict="unchecked"))
        self.assertEqual((result.status, result.cause), (ABSTAINED, "unchecked"))

    def test_too_few_turns_abstain(self):
        plans, plan, turns = planned(7)
        events = [event for event in events_for(plan, turns, follower(plan, turns)) if event.challenge_turn_index is None or event.challenge_turn_index < 3]
        case, result = judged(plans, events)
        self.assertEqual((result.status, result.cause), (ABSTAINED, "too_few_turns"))
        self.assertNotEqual(case.decision, "review")

    def test_a_trace_with_gaps_around_a_turn_does_not_count_that_turn(self):
        plans, plan, turns = planned(8)
        t0 = turns[0][0]
        events = [event for event in events_for(plan, turns, follower(plan, turns))
                  if not (event.view_yaw_deg is not None and t0 - 300 < event.t_ms < t0 + 300)]
        _case, result = judged(plans, events)
        self.assertEqual(result.not_counted.get("no_trace"), 1)
        self.assertEqual(len(result.turns), RULE.count - 1)


class TelemetryTest(unittest.TestCase):
    def test_parse(self):
        base = {"game_id": "g", "match_id": "m", "player_id": "p", "t_ms": 1, "event_type": "movement"}
        event = parse_event({**base, "view_yaw_deg": 271.5, "challenge_turn_index": 2, "challenge_turn_sign": -1})
        self.assertEqual((event.view_yaw_deg, event.challenge_turn_index, event.challenge_turn_sign), (271.5, 2, -1))
        self.assertEqual(event.extras, {})
        with self.assertRaises(ParseError):
            parse_event({**base, "challenge_turn_sign": 0})
        with self.assertRaises(ParseError):
            parse_event({**base, "challenge_turn_index": -1})

    def test_a_timeline_with_turn_telemetry_has_its_own_recipe(self):
        _plans, plan, turns = planned(9)
        self.assertEqual(timeline_recipe(events_for(plan, turns, holder(9))), INPUTS_RECIPE_V4)

    def test_yaw_wraps_at_360(self):
        # A follower whose aim crosses north: 359 to 1 degree is a 2 degree turn, not 358.
        plans, plan, turns = planned(10)
        bearing = follower(plan, turns)
        _case, result = judged(plans, events_for(plan, turns, lambda t: bearing(t) + 359.0))
        self.assertEqual(result.status, FOLLOWED)


class GodotTurnsTest(unittest.TestCase):
    """The Godot study server runs version 3 from new files only, with fpsdet's own rule."""

    GODOT = Path(__file__).resolve().parents[1] / "examples" / "pilot" / "godot"

    def test_the_gdscript_rule_is_fpsdets(self):
        import re

        source = (self.GODOT / "scripts" / "turns_recipe.gd").read_text(encoding="utf-8")
        found = dict(re.findall(r"const (COUNT|MIN_GAP_MS|REACT_FROM_MS|REACT_TO_MS|VERSION) := (\d+)", source))
        self.assertEqual({key: int(value) for key, value in found.items()},
                         {"VERSION": 3, "COUNT": RULE.count, "MIN_GAP_MS": RULE.min_gap_ms, "REACT_FROM_MS": RULE.react_from_ms, "REACT_TO_MS": RULE.react_to_ms})
        self.assertIn('"turn_%d_pick" % index, 0, 4294967295', source)
        self.assertIn('"turn_signs", 0, (1 << COUNT) - 1', source)
        names = [name for name, _low, _high in OCCLUDED_MOTION_REPLAY_V3.parameters[4:]]
        self.assertEqual(names, [f"turn_{index}_pick" for index in range(RULE.count)] + ["turn_signs"])

    def test_the_turns_server_extends_the_study_server_and_keeps_the_secret(self):
        server = (self.GODOT / "scripts" / "turns_server.gd").read_text(encoding="utf-8")
        self.assertTrue(server.startswith('extends "res://scripts/study_server.gd"'))
        self.assertIn("secret = PackedByteArray()", server)
        for line in server.splitlines():
            if "_log(" in line or "store_string" in line:
                self.assertNotIn("secret", line.replace("secret-file", "").replace("no challenge secret", "").replace("loaded[1]", ""), line)
        self.assertIn('preload("res://scripts/turns_server.gd")', (self.GODOT / "turns.gd").read_text(encoding="utf-8"))


class PublishedTurnsPilotTest(unittest.TestCase):
    """examples/turns-pilot/result.json: what the live run found, checked offline with no engine."""

    HERE = Path(__file__).resolve().parents[1] / "examples" / "turns-pilot"

    def setUp(self):
        import importlib.util
        import json
        import sys

        path = self.HERE / "result.json"
        if not path.exists():
            self.skipTest("no published turns pilot")
        self.result = json.loads(path.read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location("turns_pilot_for_test", self.HERE / "turns.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules["turns_pilot_for_test"] = self.module
        spec.loader.exec_module(self.module)

    def test_it_ran_this_code_under_this_rule(self):
        self.assertEqual(self.result["code"], self.module.code_identity())
        self.assertEqual(self.result["rule"], RULE.to_dict())

    def test_every_check_held(self):
        for row in self.result["sessions"]:
            self.assertEqual((row["turns_check"], row["leaks"], row["case_problems"]), ([], [], []), row["session"])
            self.assertEqual(row["inputs"], INPUTS_RECIPE_V4)

    def test_the_captures_score_as_published(self):
        import json

        from fpsdet.parse import load_events, load_profile

        profile = load_profile(self.module.PROFILE)
        sessions = {row["session"]: row for row in self.result["sessions"]}
        for name in self.module.PUBLISHED:
            folder = self.HERE / "captured" / name
            row = sessions[name]
            self.assertEqual("sha256:" + __import__("hashlib").sha256((folder / "events.ndjson").read_bytes()).hexdigest(), row["events_sha256"])
            plans = plan_file_from_dict(json.loads((folder / "plan.json").read_text(encoding="utf-8")))
            events, _errors = load_events(folder / "events.ndjson")
            subject = plans.plans[0].subject_id
            case = next(case for case in run_score(events, profile, challenges=ChallengeRegistry.from_files([plans])) if case.player_id == subject)
            self.assertEqual(case.decision, row["decision"], name)
            self.assertEqual([(result.status, result.followed_turns, None if result.p_value is None else round(result.p_value, 12)) for result in case.challenges],
                             [(c["status"], c["followed_turns"], None if c["p_value"] is None else round(c["p_value"], 12)) for c in row["challenges"]], name)


if __name__ == "__main__":
    unittest.main()
