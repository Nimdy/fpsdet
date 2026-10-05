"""Active challenges, and the private replay they generalize.

LegacyPrivateReplaySemanticsTest was written before the challenge engine (P4, phase C0) to pin what the
private replay did, from the event field to the case: how ``private_track_ms`` is parsed, cut and agreed
on, the bar, the reason, the observation and the seal.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fpsdet.models import Event, GameProfile
from fpsdet.parse import ParseError, parse_event
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.provenance import verify_packet
from fpsdet.signals import evidence_seal
from fpsdet.summarize import summarize

GAME = GameProfile(game_id="g")  # vision and audio declared; 8 samples and 1200 ms; 40 shots before aim is scored
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def shot(t: int, pid: str = "x", match: str = "m1", weapon: str = "ak", **values) -> Event:
    fields = dict(game_id="g", match_id=match, player_id=pid, t_ms=t, event_type="shot",
                  skill_band="average", weapon_class="rifle", weapon_id=weapon)
    fields.update(values)
    return Event(**fields)


def replay_samples(events: list[Event], profile=GAME) -> dict[str, list[float]]:
    """Each aim key's private replay samples, as the scorer reads them."""
    (record,) = summarize(events, profile)
    return {weapon.weapon_key: weapon.private_track_ms for weapon in record.weapons}


def case_of(events: list[Event], profile=GAME, pid: str = "x") -> dict:
    return next(case_to_dict(case) for case in run_score(events, profile) if case.player_id == pid)


class LegacyPrivateReplaySemanticsTest(unittest.TestCase):
    """The private replay before the challenge engine. Shots are two seconds apart unless a test says otherwise."""

    def test_the_field_is_a_number_and_null_is_absent(self):
        base = {"game_id": "g", "match_id": "m", "player_id": "p", "t_ms": 1, "skill_band": "average"}
        self.assertEqual(parse_event({**base, "private_track_ms": 80}).private_track_ms, 80.0)
        self.assertIsInstance(parse_event({**base, "private_track_ms": 80}).private_track_ms, float)
        self.assertIsNone(parse_event({**base, "private_track_ms": None}).private_track_ms)
        self.assertIsNone(parse_event(base).private_track_ms)
        with self.assertRaises(ParseError):
            parse_event({**base, "private_track_ms": "80"})
        # The schema says at least 0; the parser takes a negative, and it adds nothing.
        self.assertEqual(parse_event({**base, "private_track_ms": -5}).private_track_ms, -5.0)

    def test_zero_and_negative_add_nothing(self):
        shots = [shot(0, private_track_ms=0.0), shot(2000, private_track_ms=-5.0), shot(4000, private_track_ms=80.0)]
        self.assertEqual(replay_samples(shots), {"rifle": [80.0]})

    def test_each_sample_is_cut_to_the_time_since_the_previous_moment_in_the_match(self):
        shots = [
            shot(0, private_track_ms=300.0),  # the first moment of a match is not cut
            shot(50, private_track_ms=300.0),  # 50 ms since the previous shot
            shot(2000, private_track_ms=300.0),
            shot(2100),  # a shot without the field still moves the clock
            shot(2130, private_track_ms=300.0),
        ]
        self.assertEqual(replay_samples(shots), {"rifle": [300.0, 50.0, 300.0, 30.0]})

    def test_a_new_match_starts_uncut(self):
        shots = [shot(0, private_track_ms=300.0), shot(10, match="m2", private_track_ms=300.0)]
        self.assertEqual(replay_samples(shots), {"rifle": [300.0, 300.0]})

    def test_shots_at_one_moment_agree_once_or_disagree_to_nothing(self):
        shots = [
            shot(0, private_track_ms=80.0),
            shot(0, private_track_ms=80.0, weapon="m4"),
            shot(2000, private_track_ms=80.0),
            shot(2000, private_track_ms=90.0),
        ]
        (record,) = summarize(shots, GAME)
        samples = {weapon.weapon_key: weapon.private_track_ms for weapon in record.weapons}
        self.assertEqual(sum(len(values) for values in samples.values()), 1)
        skipped = [weapon.knowledge_skipped.get("private_replay") for weapon in record.weapons]
        self.assertIn({"disagreed": 1}, skipped)

    def test_the_bar_is_eight_samples_and_1200_ms_on_one_aim_key(self):
        self.assertEqual(case_of([shot(i * 2000, private_track_ms=150.0) for i in range(8)])["decision"], "review")
        self.assertNotEqual(case_of([shot(i * 2000, private_track_ms=149.0) for i in range(8)])["decision"], "review")
        self.assertNotEqual(case_of([shot(i * 2000, private_track_ms=400.0) for i in range(7)])["decision"], "review")
        self.assertNotEqual(case_of([shot(0, private_track_ms=5000.0)])["decision"], "review")

    def test_aim_keys_are_not_pooled(self):
        shots = [shot(i * 2000, private_track_ms=200.0, weapon_class="rifle") for i in range(6)]
        shots += [shot(20000 + i * 2000, private_track_ms=200.0, weapon_class="smg") for i in range(6)]
        self.assertEqual(sorted(replay_samples(shots)), ["rifle", "smg"])
        self.assertNotEqual(case_of(shots)["decision"], "review")

    def test_matches_are_pooled_on_one_aim_key(self):
        # Every match in the window adds to one total. Unrelated crossings in many matches can add up.
        shots = [shot(i * 2000, private_track_ms=200.0) for i in range(4)]
        shots += [shot(i * 2000, match="m2", private_track_ms=200.0) for i in range(4)]
        self.assertEqual(case_of(shots)["decision"], "review")

    def test_the_case(self):
        shots = [shot(i * 2000, private_track_ms=200.0) for i in range(10)] + [shot(0, match="m0")]
        case = case_of(shots)
        self.assertEqual(case["decision"], "review")
        self.assertEqual(case["automated_action"], "none")
        self.assertEqual(case["reasons"], ["rifle aim stayed on a private replay for 2000 ms across 10 shots"])
        self.assertEqual(case["checks"], ["private_replay"])
        self.assertEqual(case["seal"], "b9f2139acc3ba01f388dc0cd546d589e0bc0d2dca96b11c58afe6c6f1f07ee90")
        (obs,) = case["evidence"]["observations"]
        self.assertEqual((obs["family"], obs["kind"], obs["role"], obs["key"]), ("information", "private_replay", "review", "rifle"))
        # Every match the aim key was used in, not only the ones with replay time.
        self.assertEqual(obs["match_ids"], ["m0", "m1"])
        self.assertEqual(obs["evidence"], {"shots": 10, "total_ms": 2000.0, "thresholds": {"min_shots": 8, "min_total_ms": 1200}})
        self.assertEqual(obs["observation_id"], "obs-f527f69402a53ff6e8be4f41")
        self.assertEqual(obs["context"]["knowledge"]["status"], "unknowable")
        self.assertEqual(obs["context"]["knowledge"]["channels"], {"audio": "absent", "recent_perception": "not_applicable", "vision": "absent"})
        # Under 40 shots, aim is not scored, and the replay still is.
        self.assertIn("rifle: 11 shots, need 40 before aim is scored", case["observations"])

    def test_the_field_names_no_target(self):
        # Two replays in one match, or a replay and an ordinary hidden enemy, cannot be told apart.
        self.assertNotIn("challenge_id", {name for name in Event.__slots__})
        (record,) = summarize([shot(0, private_track_ms=80.0, enemy_id="e1", information_state="visible")], GAME)
        self.assertEqual(record.weapons[0].private_track_ms, [80.0])

    def test_the_planted_replay_lock(self):
        from fpsdet.synthetic import build_demo

        case = next(case_to_dict(case) for case in build_demo().cases if case.player_id == "replay-lock")
        self.assertEqual(case["reasons"], ["rifle aim stayed on a private replay for 1600 ms across 20 shots"])
        self.assertEqual(case["seal"], "e73f0a73ef538ed1667e4bb7938b487d3de12323b3369441585381cf35b9e3ae")
        self.assertEqual(case["seal"], evidence_seal_of(case))
        (obs,) = case["evidence"]["observations"]
        self.assertEqual((obs["family"], obs["kind"], obs["observation_id"]), ("information", "private_replay", "obs-952d9e164294a9c5fa31861a"))

    def test_a_p3_replay_packet_verifies(self):
        fixture = json.loads((FIXTURES / "historical-packets-p3.json").read_text(encoding="utf-8"))
        (case,) = fixture["cases"]
        self.assertEqual(case["evidence"]["observations"][0]["kind"], "private_replay")
        self.assertEqual(verify_packet(case), [])


def evidence_seal_of(row: dict) -> str:
    from fpsdet.models import Case

    return evidence_seal(Case(player_id=row["player_id"], game_id=row["game_id"], decision=row["decision"],
                              recommended_action="", automated_action="none", skill_band="", reports=0, reasons=row["reasons"]))


if __name__ == "__main__":
    unittest.main()
