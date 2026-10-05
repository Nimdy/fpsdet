"""Event order: arrival order is not game state.

Before event normalization (commit d45c0be) each of these fixtures showed the order events arrived in
changing a case. Each now shows it does not, down to the player-events/2 digest and the evidence packet.
Where events are simultaneous on the server's clock and a check reads sequence, the test pins what
simultaneity means to that check.
"""

from __future__ import annotations

import dataclasses
import random
import unittest

from fpsdet.baseline import build_cohorts
from fpsdet.models import Event
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.signals import leftover_signature, mirror_check
from fpsdet.statsutil import exact_sum
from fpsdet.summarize import summarize
from fpsdet.synthetic import _kick_curve, build_demo
from fpsdet.timeline import canonical_text, player_timelines, timeline

DEMO = build_demo()
PROFILE = DEMO.profile
COHORT = build_cohorts(summarize(DEMO.population, PROFILE), PROFILE)
# Eight hidden-track times whose plain left-to-right float sum is exactly 1200.0 one way round and
# 1199.9999999999998 the other. The exact sum is 1199.9999999999998.
AT_THE_LINE = [162.3, 174.2, 179.5, 194.2, 174.0, 192.2, 102.9, 20.699999999999797]


def ev(pid: str = "x", t: int = 0, match: str = "m1", kind: str = "shot", **values) -> Event:
    fields = dict(game_id="example-loadout", match_id=match, player_id=pid, t_ms=t, event_type=kind, skill_band="average")
    if kind == "shot":
        fields.update(weapon_class="rifle", weapon_id="ak")
    fields.update(values)
    return Event(**fields)


def scored(events: list[Event], **kwargs) -> list:
    return run_score(events, PROFILE, kwargs.pop("cohort", COHORT), **kwargs)


def cases(events: list[Event], **kwargs) -> dict[str, dict]:
    return {case.player_id: case_to_dict(case) for case in scored(events, **kwargs)}


def case(events: list[Event], pid: str = "x") -> dict:
    return cases(events)[pid]


def same_case(test: unittest.TestCase, first: list[Event], second: list[Event], pid: str = "x") -> dict:
    """The whole case, input digest and evidence packet included, is the same for both orders."""
    a, b = case(first, pid), case(second, pid)
    test.assertEqual(a, b)
    return a


def hidden_moments(first: float, second: float) -> list[Event]:
    """Eight moments, one second apart, each with two shots at the same time."""
    rows = []
    for moment in range(8):
        rows += [ev(t=moment * 1000, hidden_track_ms=first), ev(t=moment * 1000, hidden_track_ms=second)]
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


class ArrivalOrderIsNotStateTest(unittest.TestCase):
    """Each fixture that showed an arrival-order dependency before normalization."""

    def test_metrics_follow_weapon_keys(self):
        rifle = [ev(t=i * 200, hit=i % 4 == 0) for i in range(50)]
        smg = [ev(t=20000 + i * 200, weapon_class="smg", weapon_id="mp5", hit=i % 3 == 0) for i in range(50)]
        row = same_case(self, rifle + smg, smg + rifle)
        keys = [metric["key"] for metric in row["metrics"]]
        self.assertEqual(keys, sorted(keys))

    def test_recoil_builds_follow_build_keys(self):
        stock = [ev(t=i * 100, weapon_id="m4", recoil_pitch_deg=1.0, spray_index=3 + i) for i in range(12)]
        grip = [ev(t=5000 + i * 100, weapon_id="m4", mod_set=("grip",), recoil_pitch_deg=1.0, spray_index=3 + i) for i in range(12)]
        self.assertEqual(same_case(self, stock + grip, grip + stock)["untrained"], ["m4|", "m4|grip"])

    def test_declared_metric_groups_follow_their_keys(self):
        rifle = [ev(t=i * 100, extras={"ads_ms": 200.0}) for i in range(30)]
        smg = [ev(t=9000 + i * 100, weapon_class="smg", weapon_id="mp5", extras={"ads_ms": 210.0}) for i in range(30)]
        untrained = same_case(self, rifle + smg, smg + rifle)["untrained"]
        self.assertEqual(untrained, sorted(untrained))

    def test_players_follow_their_ids(self):
        one, two = [ev("p-2", t=i * 200) for i in range(5)], [ev("p-1", t=i * 200) for i in range(5)]
        for events in (one + two, two + one):
            self.assertEqual([c.player_id for c in scored(events)], ["p-1", "p-2"])

    def test_fire_rate_evidence_follows_match_keys(self):
        m1 = [ev(t=i * 40, match="m1") for i in range(30)]
        m2 = [ev(t=i * 40, match="m2") for i in range(30)]
        row = same_case(self, m1 + m2, m2 + m1)
        fire = next(obs for obs in row["evidence"]["observations"] if obs["kind"] == "fire_rate")
        self.assertEqual([r["match_id"] for r in fire["evidence"]["counted"]], ["m1", "m2"])

    def test_findings_reasons_and_the_seal_follow_weapon_keys(self):
        rifle, smg = two_weapons_tracking_hidden()
        row = same_case(self, rifle + smg, smg + rifle)
        self.assertEqual([obs["key"] for obs in row["evidence"]["observations"]], ["rifle", "smg"])
        self.assertTrue(row["reasons"][0].startswith("rifle"))

    def test_an_exact_sum_does_not_depend_on_order_or_python(self):
        shots = [ev(t=i * 5000, hidden_track_ms=value) for i, value in enumerate(AT_THE_LINE)]
        row = same_case(self, shots, list(reversed(shots)))
        # math.fsum: 1199.9999999999998 on every version, in every order, so no hidden-mover review.
        self.assertNotEqual(row["decision"], "review")
        self.assertEqual(exact_sum(AT_THE_LINE), 1199.9999999999998)
        self.assertEqual(exact_sum(list(reversed(AT_THE_LINE))), 1199.9999999999998)
        self.assertEqual((exact_sum([80, 80]), type(exact_sum([80, 80]))), (160, int))  # integers stay exact integers

    def test_teammate_reasons_follow_partner_ids(self):
        first, second, mate = voice_party()
        row = same_case(self, first + second + mate, second + first + mate, "m-1")
        self.assertEqual(len(row["reasons"]), 2)
        self.assertIn("after c-1", row["reasons"][0])

    def test_arrival_order_alone_no_longer_moves_the_case_or_its_packet(self):
        shots = [ev(t=i * 300, hit=i % 4 == 0) for i in range(50)]
        row = same_case(self, shots, list(reversed(shots)))
        self.assertEqual(row["evidence"]["provenance"]["inputs"]["recipe"], "fpsdet.player-events/2")
        self.assertEqual(row["evidence"]["packet"]["status"], "complete")


class SimultaneousEventsTest(unittest.TestCase):
    """Events at the same match and time are simultaneous. What that means, check by check."""

    def test_track_time_at_one_moment_counts_once_or_not_at_all(self):
        # Shots at one moment are one aim. Agreeing times count once; disagreeing ones say nothing.
        disagreeing = same_case(self, hidden_moments(200.0, 0.0), hidden_moments(0.0, 200.0))
        self.assertNotEqual(disagreeing["decision"], "review")
        agreeing = same_case(self, hidden_moments(200.0, 200.0), list(reversed(hidden_moments(200.0, 200.0))))
        self.assertEqual(agreeing["decision"], "review")
        hidden = next(obs for obs in agreeing["evidence"]["observations"] if obs["kind"] == "hidden")
        self.assertEqual((hidden["evidence"]["shots"], hidden["evidence"]["total_ms"]), (8, 1600.0))

    def test_the_spray_index_orders_kicks_at_one_time(self):
        sprays = [ev(t=0, recoil_pitch_deg=0.05, spray_index=3 + i) for i in range(12)]
        self.assertEqual(same_case(self, sprays, list(reversed(sprays)))["decision"], "review")

    def test_a_moment_extends_a_low_recoil_run_only_when_every_shot_is_low(self):
        run = [ev(t=i * 100, recoil_pitch_deg=0.05, spray_index=3 + i) for i in range(9)]
        low = ev(t=900, recoil_pitch_deg=0.05, spray_index=12)
        normal = ev(t=900, recoil_pitch_deg=1.8, spray_index=12)
        self.assertNotEqual(same_case(self, run + [low, normal], run + [normal, low])["decision"], "review")
        self.assertEqual(same_case(self, run + [low, low], list(reversed(run + [low, low])))["decision"], "review")

    def test_kicks_the_server_did_not_order_get_no_mirror_or_leftover_test(self):
        kicks = _kick_curve(20)
        shots = [ev(t=0, applied_recoil_pitch_deg=kick, compensation_pitch_deg=-kick + 0.01 * (i % 3)) for i, kick in enumerate(kicks)]
        row = same_case(self, shots, list(reversed(shots)))
        self.assertNotIn("mirror", row["checks"])
        self.assertTrue(any("did not order" in line for line in row["observations"]))
        recoil = summarize(shots, PROFILE)[0].recoils[0]
        self.assertEqual((recoil.unordered_moments, leftover_signature(recoil)), (1, None))
        self.assertIsNone(mirror_check(recoil, PROFILE)[0])
        # Ordered by spray index instead, the same kicks are a sequence again.
        sprayed = [dataclasses.replace(shot, spray_index=i) for i, shot in enumerate(shots)]
        self.assertEqual(summarize(sprayed, PROFILE)[0].recoils[0].unordered_moments, 0)

    def test_identical_kicks_at_one_moment_are_a_duplicate_not_an_ambiguity(self):
        kick = ev(t=0, applied_recoil_pitch_deg=1.2, compensation_pitch_deg=-1.1)
        self.assertEqual(summarize([kick, kick], PROFILE)[0].recoils[0].unordered_moments, 0)

    def test_a_movement_moment_extends_a_run_only_when_every_sample_is_over(self):
        run = [ev(t=i * 100, kind="movement", speed_mps=7.0, loadout_weight_kg=10.0, on_ground=True) for i in range(24)]
        over = ev(t=2400, kind="movement", speed_mps=7.0, loadout_weight_kg=10.0, on_ground=True)
        under = ev(t=2400, kind="movement", speed_mps=5.0, loadout_weight_kg=10.0, on_ground=True)
        mixed = same_case(self, run + [over, under], run + [under, over])
        self.assertNotEqual(mixed["decision"], "review")
        self.assertEqual(mixed["speed"]["longest_run"], 24)
        both = same_case(self, run + [over, over], [over] + run + [over])
        self.assertEqual((both["decision"], both["speed"]["longest_run"]), ("review", 26))

    def test_fire_gaps_at_one_moment_are_zero_whichever_comes_first(self):
        shots = [ev(t=i * 100) for i in range(30)] + [ev(t=1500, hit=True)]
        row = same_case(self, shots, list(reversed(shots)))
        self.assertNotIn("fire_rate", row["checks"])


class TimelineTest(unittest.TestCase):
    def test_canonical_text_reads_parsed_values(self):
        a = ev(t=5, extras={"b": 1.0, "a": 2.0}, mod_set=())
        b = ev(t=5, extras={"a": 2.0, "b": 1.0})
        self.assertEqual(canonical_text(a), canonical_text(b))
        self.assertNotEqual(canonical_text(ev(distance_m=30.0)), canonical_text(ev(distance_m=30)))
        self.assertIn('"extras":{"a":2.0,"b":1.0}', canonical_text(a))

    def test_the_timeline_is_a_total_order_that_keeps_duplicates(self):
        events = [ev(t=10), ev(t=0, kind="movement", speed_mps=1.0), ev(t=0, spray_index=2), ev(t=0, spray_index=1), ev(t=0), ev(t=10)]
        ordered = timeline(events)
        self.assertEqual(len(ordered), len(events))
        self.assertEqual([(e.t_ms, e.event_type, e.spray_index) for e in ordered],
                         [(0, "movement", None), (0, "shot", None), (0, "shot", 1), (0, "shot", 2), (10, "shot", None), (10, "shot", None)])
        for seed in range(5):
            shuffled = events[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual([canonical_text(e) for e in timeline(shuffled)], [canonical_text(e) for e in ordered])
        self.assertEqual(list(player_timelines([ev("b"), ev("a")])), ["a", "b"])


def two_partners(first_swings: int, second_swings: int, first_lag: int = 40, second_lag: int = 60) -> list[Event]:
    """A teammate timed against two wallhackers in one party, with a chosen number of fast swings on each."""
    common = dict(match="w", party_id="stack", information_state="unknowable")
    events = [ev("c-1", i * 200, enemy_id="e1", hidden_track_ms=80.0, **common) for i in range(20)]
    events += [ev("c-2", i * 200 + 100, enemy_id="e2", hidden_track_ms=80.0, **common) for i in range(20)]
    events += [ev("m-1", i * 200 + first_lag, enemy_id="e1", **common) for i in range(first_swings)]
    events += [ev("m-1", i * 200 + 100 + second_lag, enemy_id="e2", **common) for i in range(second_swings)]
    return events


class RelationshipSelectionTest(unittest.TestCase):
    """Which partner's lags the legacy field keeps, when a teammate was timed against several."""

    def kept(self, events: list[Event]) -> list[int]:
        forward, backward = case(events, "m-1"), case(list(reversed(events)), "m-1")
        self.assertEqual(forward["inherit_lags_ms"], backward["inherit_lags_ms"])
        return forward["inherit_lags_ms"]

    def test_the_same_count_keeps_the_faster_partner(self):
        events = two_partners(8, 8)
        self.assertEqual(self.kept(events), [40] * 8)
        partners = sorted(obs["evidence"]["partner"] for obs in case(events, "m-1")["evidence"]["observations"] if obs["kind"] == "voice")
        self.assertEqual(partners, ["c-1", "c-2"])  # both relationships stay, as their own observations

    def test_more_fast_swings_beat_a_faster_median(self):
        self.assertEqual(self.kept(two_partners(8, 10)), [60] * 10)

    def test_reaching_the_bar_beats_everything_else(self):
        # c-2 has 3 swings at 10 ms, under the 4 the watch needs. c-1's 5 at 150 ms reach it.
        self.assertEqual(self.kept(two_partners(5, 3, first_lag=150, second_lag=10)), [150] * 5)
        self.assertEqual(len([o for o in case(two_partners(5, 3, first_lag=150, second_lag=10), "m-1")["evidence"]["observations"] if o["kind"] == "voice"]), 1)


def shuffled_forms(events: list[Event], seed: int = 7) -> dict[str, list[Event]]:
    """Equivalent inputs: the same parsed events, in the orders a file could list them."""
    rng = random.Random(seed)
    by_player: dict[str, list[Event]] = {}
    for event in events:
        by_player.setdefault(event.player_id, []).append(event)
    full = events[:]
    rng.shuffle(full)
    players = list(by_player)
    rng.shuffle(players)
    blocks = [event for player in players for event in by_player[player]]
    own = {player: rng.sample(rows, len(rows)) for player, rows in by_player.items()}
    interleaved = []
    while any(own.values()):
        for player in sorted(own):
            if own[player]:
                interleaved.append(own[player].pop())
    by_weapon: dict[tuple, list[Event]] = {}
    for event in events:
        by_weapon.setdefault((event.player_id, event.weapon_id), []).append(event)
    weapons = []
    while any(by_weapon.values()):
        for key in sorted(by_weapon, key=str):
            if by_weapon[key]:
                weapons.append(by_weapon[key].pop(0))
    reversed_moments = sorted(events, key=lambda e: (e.player_id, e.match_id, e.t_ms), reverse=True)
    extras = [dataclasses.replace(e, extras=dict(reversed(list(e.extras.items())))) for e in events]
    return {
        "original": events,
        "full shuffle": full,
        "player blocks shuffled": blocks,
        "each player shuffled": interleaved,
        "weapons interleaved": weapons,
        "same-time reversed": reversed_moments,
        "extras keys reordered": extras,
        "partner order reversed": [event for player in reversed(sorted(by_player)) for event in by_player[player]],
    }


class PermutationTest(unittest.TestCase):
    """The same parsed events in every order a file could list them give the same cases: every field,
    the observation ids, the legacy seal, the player-events/2 digest and the evidence packet."""

    def check(self, events: list[Event], **kwargs):
        forms = shuffled_forms(events)
        expected = cases(forms["original"], **kwargs)
        for name, form in forms.items():
            got = cases(form, **kwargs)
            self.assertEqual(sorted(got), sorted(expected), name)
            moved = [pid for pid in expected if got[pid] != expected[pid]]
            self.assertEqual(moved, [], f"{name}: {moved[:5]}")

    def test_the_planted_demo(self):
        self.check(DEMO.events)

    def test_simultaneous_and_relationship_fixtures(self):
        first, second, mate = voice_party()
        rifle, smg = two_weapons_tracking_hidden()
        movement = [ev("run", t=i * 100, kind="movement", speed_mps=7.0, loadout_weight_kg=10.0, on_ground=True) for i in range(26)]
        movement += [ev("run", t=2600, kind="movement", speed_mps=5.0, loadout_weight_kg=10.0, on_ground=True)]
        kicks = [ev("kick", t=0, applied_recoil_pitch_deg=k, compensation_pitch_deg=-k) for k in _kick_curve(20)]
        duplicates = [ev("dup", t=i * 300, hit=True) for i in range(45)] + [ev("dup", t=600, hit=True)]
        self.check(first + second + mate + rifle + smg + hidden_moments(200.0, 0.0) + movement + kicks + duplicates)

    def test_the_synthetic_week(self):
        from fpsdet.week import build_week

        week = build_week()
        forms = shuffled_forms(week.events, seed=3)
        expected = {c.player_id: case_to_dict(c) for c in run_score(week.events, week.profile, week.cohort, week.history, week.reports)}
        for name in ("full shuffle", "each player shuffled", "partner order reversed"):
            got = {c.player_id: case_to_dict(c) for c in run_score(forms[name], week.profile, week.cohort, week.history, week.reports)}
            moved = [pid for pid in expected if got[pid] != expected[pid]]
            self.assertEqual(moved, [], f"{name}: {moved[:5]}")

    def test_a_duplicate_is_kept(self):
        shots = [ev(t=i * 300, hit=i % 2 == 0) for i in range(45)]
        once, twice = case(shots), case(shots + shots[:1])
        self.assertNotEqual(once["metrics"], twice["metrics"])  # 46 shots, not 45


if __name__ == "__main__":
    unittest.main()
