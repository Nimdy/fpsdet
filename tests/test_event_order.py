"""Event order: what the scorer reads from the order events arrive in.

Characterization, not endorsement. Each test feeds the scorer the same parsed events in two orders and
records what the arrival order changes today, before event normalization. Every one of these is a
dependency on arrival order that a game server never meant to send.
"""

from __future__ import annotations

import sys
import unittest

from fpsdet.baseline import build_cohorts
from fpsdet.models import Event
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.signals import leftover_signature
from fpsdet.summarize import summarize
from fpsdet.synthetic import _kick_curve, build_demo

DEMO = build_demo()
PROFILE = DEMO.profile
COHORT = build_cohorts(summarize(DEMO.population, PROFILE), PROFILE)
# Eight hidden-track times whose plain left-to-right float sum is exactly 1200.0 one way round and
# 1199.9999999999998 the other. Python 3.12's sum() compensates, Python 3.11's does not.
AT_THE_LINE = [162.3, 174.2, 179.5, 194.2, 174.0, 192.2, 102.9, 20.699999999999797]


def ev(pid: str = "x", t: int = 0, match: str = "m1", kind: str = "shot", **values) -> Event:
    fields = dict(game_id="example-loadout", match_id=match, player_id=pid, t_ms=t, event_type=kind, skill_band="average")
    if kind == "shot":
        fields.update(weapon_class="rifle", weapon_id="ak")
    fields.update(values)
    return Event(**fields)


def cases(events: list[Event]) -> dict[str, dict]:
    return {case.player_id: case_to_dict(case) for case in run_score(events, PROFILE, COHORT)}


def case(events: list[Event], pid: str = "x") -> dict:
    return cases(events)[pid]


def without_provenance(row: dict) -> dict:
    row = dict(row)
    row["evidence"] = {key: value for key, value in row["evidence"].items() if key not in ("provenance", "packet")}
    return row


def hidden_ticks(first: float, second: float) -> list[Event]:
    """Eight ticks, one second apart. At each, two shots at the same time report different hidden-track times."""
    rows = []
    for tick in range(8):
        rows += [ev(t=tick * 1000, hidden_track_ms=first), ev(t=tick * 1000, hidden_track_ms=second)]
    return rows


def two_weapons_tracking_hidden() -> tuple[list[Event], list[Event]]:
    rifle = [ev(t=i * 300, hidden_track_ms=200.0) for i in range(10)]
    smg = [ev(t=5000 + i * 300, weapon_class="smg", weapon_id="mp5", hidden_track_ms=200.0) for i in range(10)]
    return rifle, smg


def voice_party() -> tuple[list[Event], list[Event], list[Event]]:
    """Two wallhackers in one party, and a teammate who swings on each one's hidden enemy soon after."""
    common = dict(match="w", party_id="stack", information_state="unknowable")
    first = [ev("c-1", i * 200, enemy_id="e1", hidden_track_ms=80.0, **common) for i in range(20)]
    second = [ev("c-2", i * 200 + 100, enemy_id="e2", hidden_track_ms=80.0, **common) for i in range(20)]
    mate = [ev("m-1", i * 200 + 40, enemy_id="e1", **common) for i in range(8)]
    mate += [ev("m-1", i * 200 + 160, enemy_id="e2", **common) for i in range(8)]
    return first, second, mate


class CurrentOrderDependenceTest(unittest.TestCase):
    """What arrival order decides today. These document the old behaviour; they do not endorse it."""

    def test_first_seen_weapon_sets_the_metric_order(self):
        rifle = [ev(t=i * 200, hit=i % 4 == 0) for i in range(50)]
        smg = [ev(t=20000 + i * 200, weapon_class="smg", weapon_id="mp5", hit=i % 3 == 0) for i in range(50)]
        keys = lambda row: [metric["key"] for metric in row["metrics"]]
        self.assertNotEqual(keys(case(rifle + smg)), keys(case(smg + rifle)))

    def test_first_seen_recoil_build_sets_the_untrained_order(self):
        stock = [ev(t=i * 100, weapon_id="m4", recoil_pitch_deg=1.0, spray_index=3 + i) for i in range(12)]
        grip = [ev(t=5000 + i * 100, weapon_id="m4", mod_set=("grip",), recoil_pitch_deg=1.0, spray_index=3 + i) for i in range(12)]
        self.assertEqual(sorted(case(stock + grip)["untrained"]), ["m4|", "m4|grip"])
        self.assertNotEqual(case(stock + grip)["untrained"], case(grip + stock)["untrained"])

    def test_first_seen_declared_metric_group_sets_its_order(self):
        rifle = [ev(t=i * 100, extras={"ads_ms": 200.0}) for i in range(30)]
        smg = [ev(t=9000 + i * 100, weapon_class="smg", weapon_id="mp5", extras={"ads_ms": 210.0}) for i in range(30)]
        self.assertEqual(len(case(rifle + smg)["untrained"]), 2)
        self.assertNotEqual(case(rifle + smg)["untrained"], case(smg + rifle)["untrained"])

    def test_first_seen_player_sets_the_case_order(self):
        one, two = [ev("p-1", t=i * 200) for i in range(5)], [ev("p-2", t=i * 200) for i in range(5)]
        order = lambda events: [c.player_id for c in run_score(events, PROFILE, COHORT)]
        self.assertNotEqual(order(one + two), order(two + one))

    def test_same_time_shots_decide_hidden_tracking(self):
        # The first shot at a tick gets the time since the last tick, the second gets zero, by arrival.
        self.assertEqual(case(hidden_ticks(200.0, 0.0))["decision"], "review")
        self.assertNotEqual(case(hidden_ticks(0.0, 200.0))["decision"], "review")

    def test_first_seen_match_orders_the_fire_rate_evidence(self):
        m1 = [ev(t=i * 40, match="m1") for i in range(30)]
        m2 = [ev(t=i * 40, match="m2") for i in range(30)]
        forward, backward = case(m1 + m2), case(m2 + m1)
        fire = lambda row: next(obs for obs in row["evidence"]["observations"] if obs["kind"] == "fire_rate")
        self.assertEqual((forward["decision"], forward["reasons"]), (backward["decision"], backward["reasons"]))
        self.assertNotEqual([row["match_id"] for row in fire(forward)["evidence"]["counted"]], [row["match_id"] for row in fire(backward)["evidence"]["counted"]])
        self.assertNotEqual(fire(forward)["observation_id"], fire(backward)["observation_id"])

    def test_same_time_recoil_order_decides_the_floor_run(self):
        sprays = [ev(t=0, recoil_pitch_deg=0.05, spray_index=3 + i) for i in range(12)]
        self.assertEqual(case(sprays)["decision"], "review")
        self.assertNotEqual(case(list(reversed(sprays)))["decision"], "review")

    def test_same_time_kicks_change_the_mirror_lag(self):
        kicks = _kick_curve(20)
        shots = [ev(t=0, applied_recoil_pitch_deg=kick, compensation_pitch_deg=-kick + 0.01 * (i % 3)) for i, kick in enumerate(kicks)]
        mirror = lambda row: next(obs for obs in row["evidence"]["observations"] if obs["kind"] == "mirror")["evidence"]["lagged_r"]
        self.assertNotEqual(mirror(case(shots)), mirror(case(list(reversed(shots)))))

    def test_same_time_movement_decides_a_sustained_run(self):
        run = [ev(t=i * 100, kind="movement", speed_mps=7.0, loadout_weight_kg=10.0, on_ground=True) for i in range(24)]
        over = ev(t=2400, kind="movement", speed_mps=7.0, loadout_weight_kg=10.0, on_ground=True)
        under = ev(t=2400, kind="movement", speed_mps=5.0, loadout_weight_kg=10.0, on_ground=True)
        self.assertEqual(case(run + [over, under])["decision"], "review")
        self.assertNotEqual(case(run + [under, over])["decision"], "review")

    def test_same_time_commands_change_the_leftover_signature(self):
        kicks = _kick_curve(40)
        shots = [ev(t=0, applied_recoil_pitch_deg=kick, compensation_pitch_deg=-0.5 * kick + 0.1 * (i % 7)) for i, kick in enumerate(kicks)]
        signature = lambda events: leftover_signature(summarize(events, PROFILE)[0].recoils[0])
        self.assertNotEqual(signature(shots), signature(list(reversed(shots))))

    @unittest.skipUnless(sys.version_info < (3, 12), "Python 3.12's sum() compensates, so this order does not cross the line there")
    def test_float_sums_follow_arrival_order(self):
        shots = [ev(t=i * 5000, hidden_track_ms=value) for i, value in enumerate(AT_THE_LINE)]
        self.assertEqual(sum(AT_THE_LINE), 1200.0)
        self.assertEqual(case(shots)["decision"], "review")
        self.assertNotEqual(case(list(reversed(shots)))["decision"], "review")

    def test_first_seen_cheater_orders_the_teammate_reasons(self):
        first, second, mate = voice_party()
        forward, backward = case(first + second + mate, "m-1"), case(second + first + mate, "m-1")
        self.assertEqual(len(forward["reasons"]), 2)
        self.assertEqual(forward["reasons"], list(reversed(backward["reasons"])))

    def test_the_last_cheater_processed_keeps_its_lags(self):
        first, second, mate = voice_party()
        self.assertEqual(case(first + second + mate, "m-1")["inherit_lags_ms"], [60] * 8)
        self.assertEqual(case(second + first + mate, "m-1")["inherit_lags_ms"], [40] * 8)

    def test_first_seen_weapon_orders_reasons_observations_and_the_seal(self):
        rifle, smg = two_weapons_tracking_hidden()
        forward, backward = case(rifle + smg), case(smg + rifle)
        self.assertEqual(forward["reasons"], list(reversed(backward["reasons"])))
        kinds = lambda row: [obs["key"] for obs in row["evidence"]["observations"]]
        self.assertEqual(kinds(forward), list(reversed(kinds(backward))))
        self.assertNotEqual(forward["seal"], backward["seal"])

    def test_arrival_order_alone_moves_the_input_digest_and_the_packet(self):
        shots = [ev(t=i * 300, hit=i % 4 == 0) for i in range(50)]
        forward, backward = case(shots), case(list(reversed(shots)))
        self.assertEqual(without_provenance(forward), without_provenance(backward))
        self.assertNotEqual(forward["evidence"]["provenance"]["inputs"]["digest"], backward["evidence"]["provenance"]["inputs"]["digest"])
        self.assertNotEqual(forward["evidence"]["packet"]["digest"], backward["evidence"]["packet"]["digest"])


if __name__ == "__main__":
    unittest.main()
