"""Behavior locks for gear rules, human ceilings, glitches, reports, and the AI hook."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from collections import Counter
import math
import random
import tempfile
import unittest
from pathlib import Path

from fpsdet.ai_triage import redact_case, triage_case
from fpsdet.baseline import CohortTable, build_cohorts, screen_matches
from fpsdet.lake import ingest_lines, read_lines
from fpsdet.models import (
    ACTIONS,
    Case,
    Event,
    ExtraMetric,
    ExtraObs,
    GameProfile,
    HistoryWindow,
    MetricView,
    PlayerRecord,
    RecoilSummary,
    WeaponRule,
    WeaponSummary,
)
from fpsdet.casefile import safe_name
from fpsdet.cli import main as cli_main
from fpsdet.ops import _scatter_point, _why, merge_payloads, merge_queue, week_payload
from fpsdet.week import WIRE_SHIPS, build_week
from fpsdet.parse import ParseError, load_events, load_profile, parse_event, profile_from_dict
from fpsdet.persist import case_to_dict, write_json
from fpsdet.pipeline import run_score
from fpsdet.priority import review_order, scan_order
from fpsdet.score import annotate_inheritance, annotate_vendors, assess_player
from fpsdet.signals import (
    command_residual,
    evidence_seal,
    hidden_break,
    metronome_break,
    private_break,
    mirror_break,
    mirror_check,
    pearson,
    poison_alarms,
    smoothness_break,
    wire_break,
)
from fpsdet.statsutil import clustered_lower, design_effect, median_bound, percentile, wilson_lower
from fpsdet.summarize import summarize
from fpsdet.board import render_board
from fpsdet.synthetic import PROFILE_PATH, ROOT, build_demo

WARDOGS = ROOT / "profiles" / "wardogs.json"


def _rifle(pid: str, band: str, shots: int, hits: int, heads: int, distance: float = 30.0) -> WeaponSummary:
    return WeaponSummary(
        weapon_key="rifle",
        weapon_class="rifle",
        skill_band=band,
        shots=shots,
        hits=hits,
        head_hits=heads,
        head_known_hits=hits,
        distances=[distance] * shots,
    )


def _spread(table: CohortTable, band: str, metric: str, start: float, step: float, n: int = 40) -> None:
    for index in range(n):
        table.add(band, "rifle", metric, f"{band}-{index}", start + index * step)


class StatsTest(unittest.TestCase):
    def test_percentile_is_nearest_rank(self):
        self.assertEqual(percentile(list(range(40)), 0.95), 37)

    def test_wilson_lower_stays_under_a_perfect_small_sample(self):
        self.assertGreater(wilson_lower(180, 200), 0.85)
        self.assertLess(wilson_lower(10, 10), 1.0)


class ParseTest(unittest.TestCase):
    def test_skill_prior_and_extra_number(self):
        event = parse_event(
            {
                "game_id": "g",
                "match_id": "m",
                "player_id": "p",
                "t_ms": 10,
                "skill_prior": 0.99,
                "weapon_class": "rifle",
                "ads_ms": 180,
            }
        )
        self.assertEqual(event.skill_band, "elite")
        self.assertEqual(event.extras["ads_ms"], 180)

    def test_bool_is_not_a_timestamp(self):
        with self.assertRaises(ParseError):
            parse_event(
                {
                    "game_id": "g",
                    "match_id": "m",
                    "player_id": "p",
                    "t_ms": True,
                    "skill_band": "average",
                }
            )


class AimCeilingTest(unittest.TestCase):
    def setUp(self):
        self.profile = GameProfile(game_id="t", min_shots=40, min_hits_for_headshot=25, min_cohort_players=30)
        self.table = CohortTable()
        _spread(self.table, "average", "accuracy", 0.10, 0.004)
        _spread(self.table, "elite", "accuracy", 0.30, 0.004)
        _spread(self.table, "average", "headshot_rate", 0.15, 0.004)
        _spread(self.table, "elite", "headshot_rate", 0.35, 0.004)

    def _case(self, pid: str, band: str, shots: int, hits: int, heads: int, reports: int = 0):
        record = PlayerRecord(
            player_id=pid,
            game_id="t",
            skill_band=band,
            weapons=[_rifle(pid, band, shots, hits, heads)],
        )
        return assess_player(record, self.table, self.profile, reports=reports)

    def test_weak_player_stays_clean(self):
        self.assertEqual(self._case("weak", "average", 200, 20, 2).decision, "clean")

    def test_past_best_humans_on_two_rates_is_review(self):
        case = self._case("rage", "average", 200, 180, 150)
        self.assertEqual(case.decision, "review")
        self.assertEqual(case.automated_action, "none")
        self.assertEqual(case.recommended_action, "human_review")

    def test_better_than_rank_but_inside_humans_is_watch(self):
        # average p95 is near 0.25, elite max is 0.456. 0.40 is between them.
        case = self._case("smurf", "average", 400, 160, 40)
        self.assertEqual(case.decision, "watch", case.reasons + case.observations)

    def test_best_human_inside_the_cohort_is_not_a_review(self):
        hits = round(0.456 * 400)
        self.table.add("elite", "rifle", "accuracy", "best", 0.456)
        self.table.add("elite", "rifle", "headshot_rate", "best", 0.50)
        case = self._case("best", "elite", 400, hits, round(0.50 * hits))
        self.assertNotEqual(case.decision, "review", case.reasons)

    def test_reports_do_not_flip_a_clean_player(self):
        clean = self._case("star", "average", 200, 36, 8, reports=0)
        reported = self._case("star", "average", 200, 36, 8, reports=80)
        self.assertEqual(clean.decision, reported.decision)
        self.assertEqual(reported.decision, "clean")
        ordered = scan_order([clean, reported])
        # Distinct objects with the same id still sort by reports.
        reported.player_id = "star-reported"
        ordered = scan_order([clean, reported])
        self.assertEqual(ordered[0].player_id, "star-reported")

    def test_small_sample_is_not_scored(self):
        self.assertEqual(self._case("tiny", "average", 10, 10, 9).decision, "insufficient_data")

    def test_account_jump_is_a_watch_while_still_human(self):
        record = PlayerRecord(
            player_id="changed",
            game_id="t",
            skill_band="average",
            weapons=[_rifle("changed", "average", 200, 52, 8)],
        )
        history = [HistoryWindow("changed", "rifle", "average", 200, 8)]
        case = assess_player(record, self.table, self.profile, history)
        self.assertEqual(case.decision, "watch", case.reasons)


class GearTest(unittest.TestCase):
    def test_sustained_heavy_sprint_at_light_speed_is_review(self):
        profile = load_profile(PROFILE_PATH)
        events = []
        for index in range(30):
            events.append(
                Event(
                    game_id="example-loadout",
                    match_id="m",
                    player_id="runner",
                    t_ms=index * 100,
                    event_type="movement",
                    skill_band="average",
                    speed_mps=7.1,
                    loadout_weight_kg=10,
                    on_ground=True,
                    displacement_cause="none",
                )
            )
        case = run_score(events, profile)[0]
        self.assertEqual(case.decision, "review")
        self.assertTrue(case.speed and case.speed.sustained)

    def test_same_speed_tagged_explosion_is_not_a_cheat(self):
        profile = load_profile(PROFILE_PATH)
        events = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="blasted",
                t_ms=index * 100,
                event_type="movement",
                skill_band="average",
                speed_mps=7.1,
                loadout_weight_kg=10,
                on_ground=True,
                displacement_cause="explosion",
            )
            for index in range(30)
        ]
        case = run_score(events, profile)[0]
        self.assertNotEqual(case.decision, "review")
        self.assertGreaterEqual(case.speed.excluded_innocent, 30)

    def test_two_wild_frames_are_a_glitch(self):
        profile = load_profile(PROFILE_PATH)
        events = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="glitch",
                t_ms=index * 100,
                event_type="movement",
                skill_band="average",
                speed_mps=15 if index < 2 else 5.0,
                loadout_weight_kg=10,
                on_ground=True,
                displacement_cause="none",
            )
            for index in range(30)
        ]
        case = run_score(events, profile)[0]
        self.assertNotEqual(case.decision, "review")
        self.assertGreater(case.speed.spike_samples, 0)
        self.assertTrue(any("glitch" in note for note in case.observations))

    def test_server_expected_speed_overrides_a_stale_curve(self):
        profile = load_profile(PROFILE_PATH)
        events = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="pen",
                t_ms=index * 100,
                event_type="movement",
                skill_band="average",
                speed_mps=7.0,
                loadout_weight_kg=10,
                on_ground=True,
                displacement_cause="none",
                expected_max_ground_speed_mps=7.2,
            )
            for index in range(30)
        ]
        case = run_score(events, profile)[0]
        self.assertFalse(case.speed.sustained)
        self.assertEqual(case.speed.cap_source, "server")

    def test_airborne_and_unknown_cause_are_not_scored(self):
        profile = load_profile(PROFILE_PATH)
        events = []
        for index in range(30):
            events.append(
                Event(
                    game_id="example-loadout",
                    match_id="m",
                    player_id="air",
                    t_ms=index * 100,
                    event_type="movement",
                    skill_band="average",
                    speed_mps=20,
                    loadout_weight_kg=10,
                    on_ground=False,
                    displacement_cause="none",
                )
            )
            events.append(
                Event(
                    game_id="example-loadout",
                    match_id="m",
                    player_id="unknown",
                    t_ms=index * 100,
                    event_type="movement",
                    skill_band="average",
                    speed_mps=20,
                    loadout_weight_kg=10,
                    on_ground=True,
                    displacement_cause="unknown",
                )
            )
        cases = {case.player_id: case for case in run_score(events, profile)}
        self.assertNotEqual(cases["air"].decision, "review")
        self.assertNotEqual(cases["unknown"].decision, "review")
        self.assertGreater(cases["air"].speed.excluded_airborne, 0)
        self.assertGreater(cases["unknown"].speed.excluded_unknown, 0)

    def test_unlisted_cause_is_dropped(self):
        profile = load_profile(PROFILE_PATH)
        events = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="gadget",
                t_ms=index * 100,
                event_type="movement",
                skill_band="average",
                speed_mps=20,
                loadout_weight_kg=10,
                on_ground=True,
                displacement_cause="traversal_gadget",
            )
            for index in range(30)
        ]
        case = run_score(events, profile)[0]
        self.assertNotEqual(case.decision, "review")
        self.assertGreater(case.speed.excluded_unknown, 0)

    def test_fire_rate_flags_before_aim_has_enough_samples(self):
        profile = GameProfile(
            game_id="t",
            min_shots=40,
            weapons={"rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=15)},
        )
        events = [
            Event(
                game_id="t",
                match_id="m",
                player_id="macro",
                t_ms=index * 20,
                event_type="shot",
                skill_band="average",
                weapon_class="rifle",
                hit=index % 5 == 0,
            )
            for index in range(30)
        ]
        case = run_score(events, profile)[0]
        self.assertEqual(case.decision, "review")

    def test_stock_no_recoil_reviews_and_modded_build_does_not(self):
        profile = load_profile(PROFILE_PATH)
        stock = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="script",
                t_ms=index * 100,
                event_type="shot",
                skill_band="average",
                weapon_class="rifle",
                weapon_id="ak",
                recoil_pitch_deg=0.05,
                spray_index=index,
            )
            for index in range(16)
        ]
        modded = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="built",
                t_ms=index * 100,
                event_type="shot",
                skill_band="average",
                weapon_class="rifle",
                weapon_id="ak",
                mod_set=("vertical_grip", "compensator"),
                recoil_pitch_deg=0.8,
                spray_index=index,
            )
            for index in range(16)
        ]
        cases = {case.player_id: case for case in run_score(stock + modded, profile)}
        self.assertEqual(cases["script"].decision, "review")
        self.assertNotEqual(cases["built"].decision, "review")

    def test_unknown_build_is_not_flagged(self):
        profile = load_profile(PROFILE_PATH)
        events = [
            Event(
                game_id="example-loadout",
                match_id="m",
                player_id="new",
                t_ms=index * 100,
                event_type="shot",
                skill_band="average",
                weapon_class="rifle",
                weapon_id="brand-new",
                recoil_pitch_deg=0.01,
                spray_index=index,
            )
            for index in range(16)
        ]
        case = run_score(events, profile)[0]
        self.assertNotEqual(case.decision, "review")
        self.assertTrue(any("brand-new" in item for item in case.untrained + case.observations))


class QueueAndProfileTest(unittest.TestCase):
    def test_wardogs_speed_stays_off_until_a_cap_exists(self):
        profile = load_profile(WARDOGS)
        self.assertIsNone(profile.reference_lightest_speed_mps)
        from fpsdet.models import curve_speed

        self.assertIsNone(curve_speed(profile, 10))
        profile.reference_lightest_speed_mps = 7.0
        self.assertAlmostEqual(curve_speed(profile, 10), 7.0)
        self.assertAlmostEqual(curve_speed(profile, 12), 7.0 * 0.95)
        events = [
            Event(
                game_id="wardogs",
                match_id="m",
                player_id="heavy",
                t_ms=index * 100,
                event_type="movement",
                skill_band="average",
                speed_mps=7.0,
                loadout_weight_kg=18,
                on_ground=True,
                displacement_cause="none",
            )
            for index in range(30)
        ]
        muted = run_score(events, load_profile(WARDOGS))[0]
        self.assertNotEqual(muted.decision, "review")
        self.assertIn("no speed cap", muted.speed.detail)

    def test_review_queue_and_scan_queue_disagree_on_purpose(self):
        profile = GameProfile(game_id="t")
        clean = assess_player(
            PlayerRecord("star", "t", "average", weapons=[_rifle("star", "average", 10, 1, 0)]),
            CohortTable(),
            profile,
            reports=30,
        )
        dirty = assess_player(
            PlayerRecord("rage", "t", "average", weapons=[_rifle("rage", "average", 10, 1, 0)]),
            CohortTable(),
            profile,
            reports=0,
        )
        dirty.decision = "review"
        self.assertEqual(scan_order([dirty, clean])[0].player_id, "star")
        self.assertEqual(review_order([dirty, clean])[0].player_id, "rage")

    def test_extra_metric_is_a_json_addition(self):
        profile = GameProfile(
            game_id="t",
            min_cohort_players=30,
            extra_metrics=[
                ExtraMetric("ads_ms", "ads_ms", "primary", "low", min_samples=30, group_by=("weapon_class",))
            ],
        )
        table = CohortTable()
        for index in range(36):
            table.add("rifle", "ads_ms", "extra", f"p{index}", 200 + index)
        record = PlayerRecord(
            player_id="snap",
            game_id="t",
            skill_band="average",
            extras=[
                ExtraObs(
                    name="ads_ms",
                    group_key="rifle",
                    direction="low",
                    kind="primary",
                    values=[40.0] * 30,
                )
            ],
        )
        case = assess_player(record, table, profile)
        self.assertEqual(case.decision, "watch", case.reasons)

    def test_ai_brief_does_not_receive_the_player_id(self):
        seen = {}

        def transport(body):
            seen["body"] = body
            return "Sustained over the gear cap. A one-frame blast does not fit this run."

        payload = {"player_id": "secret-player", "decision": "review", "reasons": ["speed"], "party_ids": ["stack"], "match_ids": ["m"]}
        text = triage_case(payload, transport)
        blob = json.dumps(seen["body"])
        self.assertNotIn("secret-player", blob)
        self.assertIn("redacted", blob)
        self.assertIn("Sustained", text)
        self.assertEqual(redact_case(payload)["party_ids"], [])


class LakeTest(unittest.TestCase):
    def test_roundtrip_partitions_by_game_and_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            line = json.dumps(
                {
                    "game_id": "wardogs",
                    "match_id": "m",
                    "player_id": "p",
                    "t_ms": 1,
                    "utc": "2026-10-02T03:00:00Z",
                    "skill_band": "average",
                    "event_type": "movement",
                }
            )
            ingest_lines([line, "not-json"], tmp)
            stored = read_lines(tmp, game_id="wardogs")
            self.assertEqual(stored, [line])
            self.assertTrue((Path(tmp) / "game=wardogs" / "dt=2026-10-02" / "events.ndjson").exists())

    def test_event_text_cannot_put_a_partition_outside_the_lake(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            row = {"match_id": "m", "player_id": "p", "t_ms": 1}
            lines = [
                json.dumps({**row, "game_id": "x/../../escaped"}),
                json.dumps({**row, "game_id": "x\\..\\escaped"}),
                json.dumps({**row, "game_id": "wardogs", "utc": "../../../escaped"}),
                json.dumps({**row, "game_id": "wardogs", "utc": "2026-10-02T03:00:00Z"}),
                json.dumps({**row, "game_id": "wardogs", "utc": "late"}),
            ]
            result = ingest_lines(lines, lake, default_dt="2026-10-01")
            self.assertEqual(result, {"written": 2, "skipped": 3})
            written = sorted(str(path.relative_to(tmp)) for path in Path(tmp).rglob("events.ndjson"))
            self.assertEqual(written, ["lake/game=wardogs/dt=2026-10-01/events.ndjson", "lake/game=wardogs/dt=2026-10-02/events.ndjson"])
            with self.assertRaises(ValueError):
                ingest_lines(lines, lake, default_dt="../x")
            with self.assertRaises(ValueError):
                read_lines(lake, game_id="../lake")


class DemoTest(unittest.TestCase):
    def test_planted_matches(self):
        demo = build_demo()
        self.assertEqual(demo.failures, [], "\n".join(demo.failures))

    def test_review_desk_opens_on_the_weight_tape(self):
        demo = build_demo()
        self.assertEqual(demo.failures, [])
        html = render_board(demo)
        self.assertIn("10 kg on the ground", html)
        self.assertIn("The answer key.", html)
        self.assertIn("Automated action", html)
        self.assertIn("tape-speed", html)
        marker = '<script id="payload" type="application/json">'
        start = html.index(marker) + len(marker)
        payload = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual([row["id"] for row in payload["players"][:2]], ["weight-cheat", "blasted"])
        weight, blasted = payload["players"][:2]
        self.assertEqual(weight["decision"], "review")
        self.assertGreaterEqual(len(weight["speed"]), 25)
        self.assertTrue(all(row["over"] and row["cause"] == "none" for row in weight["speed"]))
        self.assertEqual(round(weight["speed"][0]["speed"], 1), 7.1)
        self.assertEqual(blasted["decision"], "clean")
        self.assertTrue(all(row["cause"] == "explosion" and not row["over"] for row in blasted["speed"]))
        self.assertEqual(payload["counts"], {"review": 11, "watch": 4, "clean": 16, "insufficient_data": 1})
        by_id = {row["id"]: row for row in payload["players"]}
        self.assertEqual(by_id["mirror-script"]["decision"], "review")
        self.assertLess(by_id["mirror-script"]["mirror_r"], -0.9)
        self.assertGreater(
            by_id["mirror-script"]["mirror_lag_r"], by_id["mirror-script"]["mirror_r"] + 0.25
        )
        self.assertEqual(by_id["late-compensate"]["decision"], "clean")
        self.assertGreater(by_id["late-compensate"]["mirror_r"], -0.9)
        self.assertEqual(by_id["metronome"]["decision"], "review")
        self.assertEqual(set(by_id["metronome"]["gaps"]), {140})
        self.assertEqual(by_id["wall-eye"]["decision"], "review")
        self.assertGreaterEqual(sum(by_id["wall-eye"]["hidden"]), 1200)
        self.assertEqual(by_id["angle-holder"]["decision"], "clean")
        self.assertEqual(sum(by_id["angle-holder"]["hidden"]), 0)
        self.assertEqual(len(by_id["weight-cheat"]["seal"]), 64)
        self.assertIn("tape-mirror", html)
        self.assertIn("tape-hidden", html)
        self.assertIn("tape-quiet", html)
        self.assertIn("tape-vendor", html)
        self.assertIn("tape-party", html)
        self.assertIn("tape-replay", html)
        self.assertIn("tape-wire", html)
        self.assertIn("thirty-two", html)
        self.assertEqual(by_id["quiet-radar"]["decision"], "review")
        self.assertTrue(any(row["state"] == "unknowable" for row in by_id["quiet-radar"]["jitter"]))
        self.assertEqual(by_id["steady-hands"]["decision"], "clean")
        self.assertEqual(by_id["listened"]["decision"], "clean")
        self.assertEqual(by_id["clone-source"]["decision"], "review")
        self.assertEqual(by_id["clone-buyer"]["decision"], "watch")
        self.assertEqual(by_id["clone-buyer"]["vendor_twin"], "clone-source")
        self.assertGreaterEqual(by_id["clone-buyer"]["vendor_r"], 0.85)
        self.assertGreater(len(by_id["clone-buyer"]["residual"]), 0)
        self.assertEqual(by_id["radar-friend"]["decision"], "watch")
        self.assertTrue(by_id["radar-friend"]["lags"])
        self.assertTrue(all(lag < payload["voice_ms"] for lag in by_id["radar-friend"]["lags"]))
        self.assertEqual(by_id["callout-friend"]["decision"], "clean")
        self.assertTrue(by_id["callout-friend"]["lags"])
        self.assertTrue(all(lag >= payload["voice_ms"] for lag in by_id["callout-friend"]["lags"]))
        self.assertEqual(payload["voice_ms"], 350)
        self.assertEqual(by_id["replay-lock"]["decision"], "review")
        self.assertGreaterEqual(sum(ms for ms in by_id["replay-lock"]["private"] if ms > 0), 1200)
        self.assertEqual(by_id["real-fight"]["decision"], "clean")
        self.assertLess(sum(ms for ms in by_id["real-fight"]["private"] if ms > 0), 1200)
        self.assertLess(sum(1 for ms in by_id["real-fight"]["private"] if ms > 0), 8)
        self.assertEqual(by_id["wire-lock"]["decision"], "review")
        self.assertEqual(by_id["picture-track"]["decision"], "clean")
        self.assertEqual(by_id["wire-lock"]["chart"], "wire")
        self.assertEqual(len(by_id["wire-lock"]["wire"]), 48)
        self.assertGreaterEqual(sum(row["delay"] for row in by_id["wire-lock"]["wire"]), 1200)
        self.assertTrue(all(row["wire"] < row["picture"] for row in by_id["wire-lock"]["wire"]))
        self.assertTrue(any("wire snapshot" in reason for reason in by_id["wire-lock"]["reasons"]))
        self.assertFalse(any("hidden mover" in reason for reason in by_id["wire-lock"]["reasons"]))
        self.assertEqual(len(by_id["picture-track"]["wire"]), 48)
        self.assertTrue(all(row["picture"] < row["wire"] for row in by_id["picture-track"]["wire"]))
        self.assertFalse(any("wire snapshot" in reason for reason in by_id["picture-track"]["reasons"]))
        self.assertEqual(by_id["wire-lock"]["aim"]["accuracy"], by_id["picture-track"]["aim"]["accuracy"])
        fire = by_id["fire-rate"]
        self.assertTrue(all(gap < 75 for gap in fire["gaps"]))
        self.assertGreater(len(set(fire["gaps"])), 1)
        adrenaline = next(row for row in payload["players"] if row["id"] == "adrenaline")
        self.assertTrue(all(not row["over"] for row in adrenaline["speed"]))
        self.assertGreater(adrenaline["speed"][0]["cap"], payload["loaded_cap"])
        streamer = next(row for row in payload["players"] if row["id"] == "reported-streamer")
        self.assertEqual(streamer["reports"], 25)
        self.assertEqual(streamer["decision"], "clean")
        self.assertEqual(payload["gap_line"], 75)

    def test_desk_puts_the_worked_examples_on_the_glass(self):
        demo = build_demo()
        self.assertEqual(demo.failures, [])
        html = render_board(demo)
        self.assertIn('id="face"', html)
        self.assertLess(html.index("tape-speed"), html.index("tape-replay"))
        marker = '<script id="payload" type="application/json">'
        start = html.index(marker) + len(marker)
        payload = json.loads(html[start:html.index("</script>", start)])
        face = payload["face"]
        self.assertEqual(len(face), 5)
        self.assertEqual(face[0]["tape"], "tape-speed")
        self.assertIn("10 kg", face[0]["title"])
        self.assertIn("7.1", face[0]["title"])
        self.assertIn("5.6", face[0]["detail"])
        self.assertEqual(
            [chip["tape"] for chip in face],
            ["tape-speed", "tape-mirror", "tape-quiet", "tape-replay", "tape-wire"],
        )
        by_id = {row["id"]: row for row in payload["players"]}
        self.assertIn("137", face[3]["title"])
        self.assertIn("1600", face[3]["title"])
        self.assertIn("160 ms", face[3]["detail"])
        self.assertIn(str(by_id["mirror-script"]["mirror_r"]), face[1]["title"])
        scene = payload["replay"]
        self.assertEqual(scene["scored_by"], "private_track_ms")
        self.assertEqual(scene["source_id"], "live-route")
        self.assertNotIn(scene["source_id"], by_id)
        self.assertEqual(scene["enemy_id"], "seen-1")
        self.assertEqual(scene["heading_deg"], 137)
        self.assertEqual(scene["delay"], 4)
        self.assertEqual(len(scene["source"]), 48)
        self.assertEqual(len(scene["replay"]), 48)
        theta = math.radians(scene["heading_deg"])
        cosine, sine = math.cos(theta), math.sin(theta)
        origin = scene["origin"]
        for index in range(scene["delay"], 48):
            dx = scene["replay"][index][0] - origin[0]
            dy = scene["replay"][index][1] - origin[1]
            back_x = cosine * dx + sine * dy
            back_y = -sine * dx + cosine * dy
            source = scene["source"][index - scene["delay"]]
            self.assertAlmostEqual(back_x, source[0], places=2)
            self.assertAlmostEqual(back_y, source[1], places=2)
        source_step = (
            scene["source"][-1][0] - scene["source"][0][0],
            scene["source"][-1][1] - scene["source"][0][1],
        )
        replay_step = (
            scene["replay"][-1][0] - scene["replay"][0][0],
            scene["replay"][-1][1] - scene["replay"][0][1],
        )
        dot = source_step[0] * replay_step[0] + source_step[1] * replay_step[1]
        cosine_step = dot / (math.hypot(*source_step) * math.hypot(*replay_step))
        self.assertLess(cosine_step, 0.5)
        wall = scene["wall"]

        def inside(point):
            return wall["x"] <= point[0] <= wall["x"] + wall["w"] and wall["y"] <= point[1] <= wall["y"] + wall["h"]

        for point in scene["source"] + scene["enemy"]:
            self.assertFalse(inside(point))
        lock = scene["aims"]["replay-lock"]
        fight = scene["aims"]["real-fight"]
        self.assertEqual(len(lock), 48)
        self.assertEqual(len(fight), 48)

        def apart(point, other):
            return math.hypot(point[0] - other[0], point[1] - other[1])

        for index, ms in enumerate(by_id["replay-lock"]["private"]):
            gap = apart(lock[index], scene["replay"][index])
            if ms > 0:
                self.assertLess(gap, 0.3)
            else:
                self.assertGreater(gap, 2)
        fight_near = []
        for index, point in enumerate(fight):
            gap = apart(point, scene["replay"][index])
            if gap < 1.2:
                fight_near.append(index)
                self.assertAlmostEqual(gap, 0.85, delta=0.05)
                self.assertGreater(by_id["real-fight"]["private"][index], 0)
            else:
                self.assertEqual(by_id["real-fight"]["private"][index], 0)
                self.assertGreater(min(apart(point, other) for other in scene["replay"]), 5)
        self.assertEqual(fight_near, [22, 23, 24, 25])
        self.assertIn("turned 137°", by_id["replay-lock"]["lede"])
        self.assertIn("not this picture", by_id["replay-lock"]["lede"])
        self.assertIn("4 red marks", by_id["real-fight"]["lede"])
        self.assertLess(html.index("tape-replay"), html.index("tape-wire"))
        self.assertIn("0.18", face[4]["title"])
        self.assertIn("2.40", face[4]["title"])
        self.assertIn("100", face[4]["detail"])
        self.assertIn("The hits match", face[4]["detail"])
        self.assertIn("wire_error_deg", by_id["wire-lock"]["lede"])
        self.assertIn("wrong timeline", by_id["picture-track"]["lede"])
        self.assertIn("The picture is late", html)
        self.assertIn('href="#tape-wire"', html)

    def test_public_answer_key_matches_the_planted_decisions(self):
        import re

        demo = build_demo()
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        drawn = {}
        for decision, body in re.findall(r'data-decision="([a-z_]+)">(.*?)</ul>', index, re.S):
            for player in re.findall(r'#card-([a-z0-9-]+)"', body):
                drawn[player] = decision
        self.assertEqual(drawn, {case.player_id: case.decision for case in demo.cases})
        # Every planted pair on the page opens a player the desk actually has.
        for player in re.findall(r'board\.html#card-([a-z0-9-]+)"', index):
            self.assertIn(player, drawn)

    def test_public_page_keeps_the_argument_honest(self):
        from fpsdet.pages import write_pages

        demo = build_demo()
        self.assertEqual(demo.failures, [])
        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(demo, tmp)
            index = (dest / "index.html").read_text(encoding="utf-8")
            board = (dest / "board.html").read_text(encoding="utf-8")
            pages = {
                name: (dest / name).read_text(encoding="utf-8")
                for name in ("scoring.html", "wire.html", "games.html", "source.html")
            }
            css = (dest / "fpsdet.css").read_text(encoding="utf-8")
            self.assertIn("Geist Mono", css)
            self.assertIn(".grid", css)
            self.assertNotIn("<script", css)
            # The channel split is switched off for anyone who asked the OS for more contrast.
            self.assertIn("prefers-contrast", css)
            self.assertTrue((dest / "fonts" / "geist-variable.woff2").is_file())
            self.assertTrue((dest / "fonts" / "geist-mono-variable.woff2").is_file())
            self.assertTrue((dest / "fonts" / "OFL-geist.txt").is_file())
        self.assertIn('href="board.html"', index)
        self.assertNotIn("../demo/board.html", index)
        self.assertIn("What we are building", index)
        self.assertIn("python3 -m fpsdet demo", index)
        self.assertIn("weight-cheat", index)
        self.assertIn("blasted", index)
        self.assertIn("examples/shot.jsonl", index)
        self.assertIn("insufficient_data", index)
        self.assertIn("The answer key.", board)
        for name in ("scoring.html", "wire.html", "games.html", "source.html"):
            self.assertIn(f'href="{name}"', index)
            page = pages[name]
            self.assertIn('href="board.html"', page)
            self.assertNotIn("../demo/board.html", page)
            self.assertIn("Automated action: none", page)
            self.assertIn("This does not end cheating", page)
            self.assertNotIn("<script src=", page)
            self.assertNotIn("buy the rights", page.lower())
            self.assertNotIn("patent", page.lower())
        self.assertIn("Walk through", board)
        self.assertIn('href="index.html"', board)
        self.assertIn('href="scoring.html"', board)
        self.assertIn('href="wire.html"', board)
        self.assertIn('href="games.html"', board)
        self.assertIn('href="source.html"', board)
        self.assertNotIn("../site/", board)
        self.assertIn("This does not end cheating", index)
        self.assertIn("This does not end cheating", board)
        self.assertIn("Automated action: none", index)
        self.assertIn("10 kg", index)
        self.assertIn("7.1", index)
        self.assertIn("dedicated server", index.lower())
        self.assertIn("What you have to lose", index)
        self.assertIn("What still gets through", index)
        self.assertIn("displacement_cause", pages["scoring.html"])
        self.assertIn("expected_max_ground_speed_mps", pages["wire.html"])
        self.assertIn("WARDOGS", pages["games.html"])
        self.assertIn("Tarkov", pages["games.html"])
        self.assertIn("wire-lock", index)
        self.assertIn("picture-track", index)
        self.assertIn("The picture is late", index)
        self.assertIn("wire_error_deg", pages["scoring.html"])
        self.assertIn("interp_delay_ms", pages["wire.html"])
        self.assertIn("picture_error_deg", pages["games.html"])
        self.assertIn("tape-wire", board)
        self.assertLess(board.index("tape-speed"), board.index("tape-wire"))
        lowered = index.lower()
        self.assertNotIn("buy the rights", lowered)
        self.assertNotIn("patent", lowered)
        self.assertNotIn("<script src=", index)
        self.assertIn("max-w-[1800px]", index)
        self.assertIn("max-md:grid-cols-1", index)
        self.assertIn('id="face"', board)
        self.assertLess(board.index("tape-speed"), board.index("tape-replay"))
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("python3 -m fpsdet pages", workflow)
        self.assertIn("upload-pages-artifact@v4", workflow)
        self.assertIn("deploy-pages@v4", workflow)
        self.assertNotIn("git push", workflow)

    def test_population_cohort_still_catches_a_member_cheater(self):
        profile = GameProfile(
            game_id="t",
            min_shots=40,
            min_hits_for_headshot=25,
            min_cohort_players=30,
            weapons={"rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=15)},
        )
        events = []
        for index in range(36):
            for shot in range(80):
                hit = shot < 16
                events.append(
                    Event(
                        game_id="t",
                        match_id="pop",
                        player_id=f"human-{index}",
                        t_ms=shot * 140 + (shot % 5) * 9,
                        event_type="shot",
                        skill_band="average",
                        weapon_class="rifle",
                        hit=hit,
                        hitbox="upper_torso" if hit else None,
                        distance_m=25,
                    )
                )
        for shot in range(80):
            hit = shot < 74
            events.append(
                Event(
                    game_id="t",
                    match_id="pop",
                    player_id="inside",
                    t_ms=shot * 140,
                    event_type="shot",
                    skill_band="average",
                    weapon_class="rifle",
                    hit=hit,
                    hitbox="head" if hit else None,
                    distance_m=70,
                )
            )
        cases = {case.player_id: case for case in run_score(events, profile)}
        self.assertEqual(cases["inside"].decision, "review", cases["inside"].reasons)
        self.assertNotEqual(cases["human-0"].decision, "review")


class SummarizeCohortTest(unittest.TestCase):
    def test_build_cohorts_uses_one_point_per_player(self):
        profile = GameProfile(game_id="t", min_shots=40, min_hits_for_headshot=25)
        events = []
        for index in range(5):
            for shot in range(40):
                events.append(
                    Event(
                        game_id="t",
                        match_id="m",
                        player_id=f"p{index}",
                        t_ms=shot * 100,
                        event_type="shot",
                        skill_band="average",
                        weapon_class="rifle",
                        hit=True,
                        hitbox="head",
                        distance_m=10,
                    )
                )
        table = build_cohorts(summarize(events, profile), profile)
        self.assertEqual(len(table.series("average", "rifle", "accuracy", None)), 5)


def _kicks(count: int) -> list[float]:
    state = 7
    kicks = []
    for _ in range(count):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        kicks.append(0.8 + (state % 1000) / 1000 * 1.6)
    return kicks


def _noise(count: int, salt: int = 99) -> list[float]:
    state = salt
    values = []
    for _ in range(count):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        values.append((state % 2000) / 1000 - 1.0)
    return values


def _bare_case(player_id: str, decision: str, reasons: list[str] | None = None) -> Case:
    case = Case(
        player_id=player_id,
        game_id="t",
        decision=decision,
        recommended_action=ACTIONS[decision],
        automated_action="none",
        skill_band="average",
        reports=0,
        reasons=list(reasons or []),
        party_ids=["stack"],
    )
    case.seal = evidence_seal(case)
    return case


class SignalTest(unittest.TestCase):
    def setUp(self):
        self.profile = GameProfile(
            game_id="t",
            weapons={"rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=15)},
        )

    def test_mirror_flags_a_same_tick_cancel_and_leaves_a_late_pull(self):
        kicks = _kicks(48)
        script = [-kick + 1.35 for kick in kicks]
        late = [-0.45 * (kicks[0] if index == 0 else kicks[index - 1]) for index, _kick in enumerate(kicks)]
        flagged = mirror_break(kicks, script, self.profile)
        self.assertIsNotNone(flagged)
        self.assertIn("same tick", flagged)
        self.assertIsNone(mirror_break(kicks, late, self.profile))
        self.assertIsNone(mirror_break([1.6] * 20, [-1.6] * 20, self.profile))
        self.assertIsNone(mirror_break(kicks[:10], script[:10], self.profile))

    def test_metronome_is_a_legal_flat_cycle_only(self):
        flat = WeaponSummary(
            weapon_key="rifle", weapon_class="rifle", skill_band="average", fire_gaps=[140] * 50
        )
        self.assertIn("std 0.00", metronome_break(flat, self.profile))
        jittered = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            fire_gaps=[140 + ((index * 13) % 37) - 18 for index in range(1, 51)],
        )
        self.assertIsNone(metronome_break(jittered, self.profile))
        paced = GameProfile(
            game_id="t",
            weapons={
                "rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=15, server_paced=True)
            },
        )
        self.assertIsNone(metronome_break(flat, paced))
        quantized = GameProfile(
            game_id="t",
            tick_ms=140,
            weapons={"rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=15)},
        )
        self.assertIsNone(metronome_break(flat, quantized))
        too_fast = WeaponSummary(
            weapon_key="rifle", weapon_class="rifle", skill_band="average", fire_gaps=[40] * 50
        )
        self.assertIsNone(metronome_break(too_fast, self.profile))

    def test_hidden_track_needs_a_sustained_sum(self):
        held = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            hidden_track_ms=[80.0] * 20,
        )
        self.assertIn("1600", hidden_break(held, self.profile))
        zeros = WeaponSummary(
            weapon_key="rifle", weapon_class="rifle", skill_band="average", hidden_track_ms=[0.0] * 30
        )
        self.assertIsNone(hidden_break(zeros, self.profile))
        one_long = WeaponSummary(
            weapon_key="rifle", weapon_class="rifle", skill_band="average", hidden_track_ms=[5000.0]
        )
        self.assertIsNone(hidden_break(one_long, self.profile))

    def test_private_replay_needs_the_same_sustained_bar(self):
        held = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            private_track_ms=[80.0] * 20,
        )
        flagged = private_break(held, self.profile)
        self.assertIn("private replay", flagged)
        self.assertNotIn("hidden mover", flagged)
        crossing = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            private_track_ms=[40.0] * 4,
        )
        self.assertIsNone(private_break(crossing, self.profile))
        one_long = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            private_track_ms=[5000.0],
        )
        self.assertIsNone(private_break(one_long, self.profile))
        parsed = parse_event(
            {
                "game_id": "g",
                "match_id": "m",
                "player_id": "p",
                "t_ms": 1,
                "skill_band": "average",
                "private_track_ms": 80,
            }
        )
        self.assertEqual(parsed.private_track_ms, 80)

    def test_wire_picture_needs_the_same_sustained_bar(self):
        def weapon(**rows):
            count = len(rows["wire"])
            return WeaponSummary(
                weapon_key="rifle",
                weapon_class="rifle",
                skill_band="average",
                wire_error_deg=rows["wire"],
                picture_error_deg=rows["picture"],
                interp_delay_ms=rows["delay"],
                shots=count,
            )

        held = weapon(wire=[0.18] * 12, picture=[2.40] * 12, delay=[100.0] * 12)
        flagged = wire_break(held, self.profile)
        self.assertIn("wire snapshot", flagged)
        self.assertIn("1200", flagged)
        self.assertNotIn("hidden mover", flagged)
        short_time = weapon(wire=[0.18] * 8, picture=[2.40] * 8, delay=[100.0] * 8)
        self.assertIsNone(wire_break(short_time, self.profile))
        one_long = weapon(wire=[0.18], picture=[2.40], delay=[5000.0])
        self.assertIsNone(wire_break(one_long, self.profile))
        standing = weapon(wire=[0.05] * 20, picture=[0.06] * 20, delay=[100.0] * 20)
        self.assertIsNone(wire_break(standing, self.profile))
        close = weapon(wire=[1.0] * 20, picture=[1.1] * 20, delay=[100.0] * 20)
        self.assertIsNone(wire_break(close, self.profile))
        swapped = weapon(wire=[2.40] * 48, picture=[0.18] * 48, delay=[100.0] * 48)
        self.assertIsNone(wire_break(swapped, self.profile))
        ratio = weapon(wire=[1.0] * 20, picture=[2.0] * 20, delay=[100.0] * 20)
        self.assertIsNone(wire_break(ratio, self.profile))
        negative = weapon(wire=[-0.2] * 20, picture=[2.4] * 20, delay=[100.0] * 20)
        self.assertIsNone(wire_break(negative, self.profile))
        parsed = parse_event(
            {
                "game_id": "g",
                "match_id": "m",
                "player_id": "p",
                "t_ms": 1,
                "skill_band": "average",
                "wire_error_deg": 0.18,
                "picture_error_deg": 2.4,
                "interp_delay_ms": 100,
                "stray_deg": 9,
            }
        )
        self.assertEqual(parsed.wire_error_deg, 0.18)
        self.assertEqual(parsed.picture_error_deg, 2.4)
        self.assertEqual(parsed.interp_delay_ms, 100)
        self.assertEqual(parsed.extras, {"stray_deg": 9.0})

    def test_seal_is_the_decision_and_ignores_reports(self):
        demo = build_demo()
        case = next(item for item in demo.cases if item.player_id == "mirror-script")
        self.assertEqual(case.seal, evidence_seal(case))
        stored = case.seal
        case.observations.append("not in the seal")
        self.assertEqual(evidence_seal(case), stored)
        case.reasons.append("tamper")
        self.assertNotEqual(evidence_seal(case), stored)

    def test_smoothness_needs_a_drop_that_only_happens_while_unknowable(self):
        drop = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            knowable_jitter=[0.90] * 12,
            unknowable_jitter=[0.08] * 12,
        )
        flagged = smoothness_break(drop, self.profile)
        self.assertIsNotNone(flagged)
        self.assertIn("could not have known", flagged)
        flat = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            knowable_jitter=[0.40] * 12,
            unknowable_jitter=[0.40] * 12,
        )
        self.assertIsNone(smoothness_break(flat, self.profile))
        only_hidden = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            unknowable_jitter=[0.08] * 24,
        )
        self.assertIsNone(smoothness_break(only_hidden, self.profile))
        short = WeaponSummary(
            weapon_key="rifle",
            weapon_class="rifle",
            skill_band="average",
            knowable_jitter=[0.90] * 11,
            unknowable_jitter=[0.08] * 12,
        )
        self.assertIsNone(smoothness_break(short, self.profile))

    def test_audio_is_knowable_and_a_missing_label_is_skipped(self):
        events = []
        labeled = [("visible", 0.90)] * 12 + [("audio", 0.08)] * 12 + [(None, 0.01)] * 12
        for index, (state, jitter) in enumerate(labeled):
            events.append(
                Event(
                    game_id="t",
                    match_id="m",
                    player_id="listener",
                    t_ms=index * 140,
                    event_type="shot",
                    skill_band="average",
                    weapon_class="rifle",
                    information_state=state,
                    aim_jitter_deg=jitter,
                )
            )
        weapon = summarize(events, self.profile)[0].weapons[0]
        self.assertEqual(len(weapon.knowable_jitter), 24)
        self.assertEqual(weapon.unknowable_jitter, [])
        self.assertIsNone(smoothness_break(weapon, self.profile))

    def test_bad_information_state_is_rejected(self):
        with self.assertRaises(ParseError):
            parse_event(
                {
                    "game_id": "g",
                    "match_id": "m",
                    "player_id": "p",
                    "t_ms": 1,
                    "skill_band": "average",
                    "information_state": "radar",
                }
            )
        parsed = parse_event(
            {
                "game_id": "g",
                "match_id": "m",
                "player_id": "p",
                "t_ms": 1,
                "skill_band": "average",
                "information_state": "unknowable",
                "aim_jitter_deg": 0.08,
                "enemy_id": "mover-1",
            }
        )
        self.assertEqual(parsed.information_state, "unknowable")
        self.assertEqual(parsed.aim_jitter_deg, 0.08)
        self.assertEqual(parsed.enemy_id, "mover-1")

    def test_shared_leftover_correlates_and_independent_noise_does_not(self):
        kicks = _kicks(48)
        shared = _noise(48)
        other = _noise(48, salt=5)
        source = [-kick + 0.2 * sample for kick, sample in zip(kicks, shared)]
        buyer = [-0.2 * kick + sample for kick, sample in zip(kicks, shared)]
        stranger = [-0.2 * kick + sample for kick, sample in zip(kicks, other)]
        source_left = command_residual(kicks, source)
        buyer_left = command_residual(kicks, buyer)
        stranger_left = command_residual(kicks, stranger)
        self.assertIsNotNone(source_left)
        self.assertIsNotNone(buyer_left)
        self.assertGreaterEqual(pearson(source_left, buyer_left), 0.85)
        self.assertLess(pearson(source_left, stranger_left), 0.85)
        self.assertIsNone(command_residual(kicks[:6], source[:6]))

    def test_leftover_match_is_a_watch_unless_someone_is_already_a_review(self):
        kicks = _kicks(48)
        shared = _noise(48)
        source_cmd = [-kick + 0.2 * sample for kick, sample in zip(kicks, shared)]
        buyer_cmd = [-0.2 * kick + sample for kick, sample in zip(kicks, shared)]

        def record(player_id: str, command: list[float]) -> PlayerRecord:
            return PlayerRecord(
                player_id=player_id,
                game_id="t",
                skill_band="average",
                recoils=[
                    RecoilSummary(
                        build_key="ak|",
                        weapon_key="rifle",
                        skill_band="average",
                        applied=kicks,
                        compensation=command,
                    )
                ],
            )

        confirmed = _bare_case("source", "review", ["player command matched the server kick on the same tick"])
        buyer = _bare_case("buyer", "clean")
        stored = confirmed.seal
        annotate_vendors(
            [confirmed, buyer],
            [record("source", source_cmd), record("buyer", buyer_cmd)],
            self.profile,
        )
        self.assertEqual(confirmed.decision, "review")
        self.assertEqual(confirmed.seal, stored)
        self.assertTrue(any("leftover command matches buyer" in note for note in confirmed.observations))
        self.assertEqual(buyer.decision, "watch")
        self.assertEqual(buyer.recommended_action, "monitor")
        self.assertEqual(buyer.queue_rank, 2)
        self.assertTrue(any("already a review" in reason for reason in buyer.reasons))

        left = _bare_case("left", "clean")
        right = _bare_case("right", "clean")
        annotate_vendors(
            [left, right],
            [record("left", source_cmd), record("right", buyer_cmd)],
            self.profile,
        )
        self.assertEqual(left.decision, "watch")
        self.assertEqual(right.decision, "watch")
        self.assertTrue(any("Nobody in the pair is a review yet" in reason for reason in left.reasons))
        self.assertNotEqual(left.decision, "review")

    def test_fast_teammate_is_a_watch_and_a_voice_lag_is_not(self):
        cheater = _bare_case("wall", "review", ["rifle aim stayed on a hidden mover for 1600 ms"])
        fast = _bare_case("friend", "insufficient_data")
        voiced = _bare_case("callout", "clean")
        alone = _bare_case("solo", "clean")
        alone.party_ids = ["other"]
        elsewhere = _bare_case("elsewhere", "clean")
        stored = cheater.seal

        def record(player_id: str, party: str, contacts: list[tuple[str, int, str]]) -> PlayerRecord:
            return PlayerRecord(
                player_id=player_id,
                game_id="t",
                skill_band="average",
                weapons=[
                    WeaponSummary(
                        weapon_key="rifle",
                        weapon_class="rifle",
                        skill_band="average",
                        hidden_contacts=contacts,
                    )
                ],
                party_ids={party},
            )

        stamps = [1000, 2000, 3000, 4000]
        annotate_inheritance(
            [cheater, fast, voiced, alone, elsewhere],
            [
                record("wall", "stack", [("m", stamp, "mover-1") for stamp in stamps]),
                record("friend", "stack", [("m", stamp + 40, "mover-1") for stamp in stamps]),
                record("callout", "stack", [("m", stamp + 800, "mover-1") for stamp in stamps]),
                record("solo", "other", [("m", stamp + 40, "mover-1") for stamp in stamps]),
                # Same party, same enemy, same clock reading, a different match. t_ms restarted.
                record("elsewhere", "stack", [("m2", stamp + 40, "mover-1") for stamp in stamps]),
            ],
            self.profile,
        )
        self.assertEqual(cheater.decision, "review")
        self.assertEqual(cheater.seal, stored)
        self.assertEqual(cheater.reasons, ["rifle aim stayed on a hidden mover for 1600 ms"])
        self.assertEqual(fast.decision, "watch")
        self.assertEqual(fast.recommended_action, "monitor")
        self.assertEqual(fast.queue_rank, 1)
        self.assertEqual(fast.inherit_lags_ms, [40, 40, 40, 40])
        self.assertTrue(any("a voice needs" in reason for reason in fast.reasons))
        self.assertEqual(voiced.decision, "clean")
        self.assertEqual(voiced.inherit_lags_ms, [800, 800, 800, 800])
        self.assertEqual(voiced.reasons, [])
        self.assertEqual(alone.decision, "clean")
        self.assertEqual(alone.inherit_lags_ms, [])
        self.assertEqual(elsewhere.decision, "clean")
        self.assertEqual(elsewhere.inherit_lags_ms, [])

    def test_poison_alarm_fires_only_when_the_ceiling_jumps(self):
        previous = CohortTable()
        current = CohortTable()
        for index in range(30):
            value = 0.34 + (index % 5) * 0.01
            previous.add("elite", "rifle", "accuracy", f"p{index}", value)
            current.add("elite", "rifle", "accuracy", f"p{index}", value)
        current.add("elite", "rifle", "accuracy", "cheat", 0.95)
        alarms = poison_alarms(previous, current, jump=0.08, min_players=30)
        self.assertTrue(any("accuracy" in alarm and "38%" in alarm and "95%" in alarm for alarm in alarms))
        self.assertEqual(poison_alarms(previous, previous, jump=0.08, min_players=30), [])



# A fixed 25-round pattern: it climbs, then swings. Players of CS-style games learn this by heart.
_PATTERN = [0.0, 1.6, 1.9, 2.1, 1.8, 1.2, 0.4, 1.5, 0.2, 1.1, 0.3, 1.4, 0.1,
            0.9, 1.6, 0.2, 1.0, 0.3, 1.3, 0.2, 0.8, 1.5, 0.1, 1.2, 0.4]


def _sprays(lengths: list[int], kick, command) -> RecoilSummary:
    """kick(k, rng) and command(k, kick_now, kick_before, rng) per shot, k = spray_index."""
    rng = random.Random(11)
    applied, commands, spray = [], [], []
    for length in lengths:
        before = 0.0
        for k in range(length):
            now = kick(k, rng)
            applied.append(now)
            commands.append(command(k, now, before, rng))
            spray.append(k)
            before = now
    return RecoilSummary(
        build_key="ak|", weapon_key="rifle", skill_band="average",
        applied=applied, compensation=commands, spray=spray,
    )


class InnocentTwinTest(unittest.TestCase):
    """Each check here once framed an honest player. The twin pins the fix."""

    def setUp(self):
        self.learnable = GameProfile(game_id="t")
        self.random_kick = GameProfile(game_id="t", recoil_pattern="random")

    def test_a_memorised_spray_is_not_a_mirror_script(self):
        def kick(k, rng):
            return _PATTERN[k] * rng.uniform(0.97, 1.03)

        def memorised(k, now, before, rng):
            # Pulls on time because they know shot k, not because they read the kick.
            prev = _PATTERN[k - 1] if k else 0.0
            return -(0.9 * _PATTERN[k] + 0.1 * prev) * rng.uniform(0.85, 1.05) + rng.gauss(0, 0.12)

        human = _sprays([25, 22, 25, 18, 25], kick, memorised)
        self.assertEqual(mirror_check(human, self.learnable), (None, None))
        # The old raw test, which assumes nobody can anticipate the kick, frames them.
        raw = RecoilSummary("ak|", "rifle", "average", applied=human.applied, compensation=human.compensation)
        found, _ = mirror_check(raw, self.random_kick)
        self.assertIn("same tick", found)

    def test_a_script_in_a_pattern_game_is_still_a_mirror(self):
        def kick(k, rng):
            return _PATTERN[k] * rng.uniform(0.8, 1.2)

        def script(k, now, before, rng):
            return -now + rng.gauss(0, 0.01)

        found, note = mirror_check(_sprays([25, 22, 25, 18, 25], kick, script), self.learnable)
        self.assertIsNone(note)
        self.assertIn("same tick", found)
        self.assertIn("spray pattern was removed", found)

    def test_too_few_sprays_waits_unless_the_kick_is_random(self):
        kicks = _kicks(48)
        script = [-kick for kick in kicks]
        one_spray = RecoilSummary("ak|", "rifle", "average", applied=kicks, compensation=script, spray=list(range(48)))
        found, note = mirror_check(one_spray, self.learnable)
        self.assertIsNone(found)
        self.assertIn("recoil_pattern", note)
        found, note = mirror_check(one_spray, self.random_kick)
        self.assertIn("same tick", found)
        self.assertIsNone(note)

    def test_recoil_pattern_must_be_known(self):
        self.assertEqual(profile_from_dict({"game_id": "g"}).recoil_pattern, "learnable")
        self.assertEqual(profile_from_dict({"game_id": "g", "recoil_pattern": "random"}).recoil_pattern, "random")
        with self.assertRaises(ParseError):
            profile_from_dict({"game_id": "g", "recoil_pattern": "fixed"})

    def _elite(self) -> tuple[CohortTable, GameProfile]:
        profile = GameProfile(game_id="t", min_shots=40, min_hits_for_headshot=25, min_cohort_players=30)
        table = CohortTable()
        for key in ("rifle", "smg"):
            for index in range(40):
                table.add("elite", key, "accuracy", f"e{index}", 0.30 + index * 0.004)
                table.add("elite", key, "headshot_rate", f"e{index}", 0.35 + index * 0.004)
                table.add("elite", key, "median_distance", f"e{index}", 30.0 + index * 0.5)
        return table, profile

    def _weapon(self, key: str, shots: int, hits: int, heads: int, distance: float) -> WeaponSummary:
        weapon = _rifle("x", "elite", shots, hits, heads, distance)
        weapon.weapon_key = key
        weapon.weapon_class = key
        return weapon

    def test_long_range_in_the_top_five_percent_is_not_past_every_human(self):
        table, profile = self._elite()
        # 49 m is past the elite p95 (48.5) and inside the farthest elite (49.5).
        weapon = self._weapon("rifle", 400, 240, 96, 49.0)
        record = PlayerRecord("long", "t", "elite", weapons=[weapon])
        case = assess_player(record, table, profile)
        view = next(row for row in case.metrics if row.name == "median_distance")
        self.assertFalse(view.beyond_human)
        self.assertEqual(view.ceiling_extreme, 49.5)
        # Accuracy past every elite is one kind of number. One kind is a watch.
        self.assertEqual(case.decision, "watch", case.reasons)
        far = PlayerRecord("far", "t", "elite", weapons=[self._weapon("rifle", 400, 240, 96, 60.0)])
        self.assertEqual(assess_player(far, table, profile).decision, "review")

    def test_the_same_number_on_two_guns_is_one_finding(self):
        table, profile = self._elite()
        record = PlayerRecord(
            "best", "t", "elite",
            weapons=[self._weapon("rifle", 400, 240, 96, 35.0), self._weapon("smg", 400, 240, 96, 35.0)],
        )
        case = assess_player(record, table, profile)
        self.assertEqual(sum(1 for row in case.metrics if row.beyond_human), 2)
        self.assertEqual(case.decision, "watch", case.reasons)

    def test_one_hot_match_does_not_carry_the_bound(self):
        table, profile = self._elite()
        weapon = self._weapon("rifle", 400, 202, 80, 35.0)
        # Nine ordinary matches and one where everything hit. Same total as a steady 50%.
        weapon.per_match = {f"m{i}": [40, 18, 0, 0] for i in range(9)}
        weapon.per_match["hot"] = [40, 40, 0, 0]
        case = assess_player(PlayerRecord("streak", "t", "elite", weapons=[weapon]), table, profile)
        view = next(row for row in case.metrics if row.name == "accuracy")
        self.assertFalse(view.beyond_human)
        self.assertTrue(any("varies between matches" in note for note in case.observations))
        steady = self._weapon("rifle", 400, 202, 80, 35.0)
        steady.per_match = {f"m{i}": [40, 20 + (i % 2), 0, 0] for i in range(10)}
        steady_view = next(
            row for row in assess_player(PlayerRecord("steady", "t", "elite", weapons=[steady]), table, profile).metrics
            if row.name == "accuracy"
        )
        self.assertTrue(steady_view.beyond_human)

    def test_design_effect(self):
        self.assertEqual(design_effect([(40, 12)] * 10), 1.0)
        self.assertEqual(design_effect([(40, 40)] * 3 + [(40, 0)]), 1.0)  # too few matches to tell
        streaky = [(40, 8)] * 9 + [(40, 40)]
        self.assertGreater(design_effect(streaky), 3.0)
        self.assertLess(clustered_lower(112, 400, design_effect(streaky)), wilson_lower(112, 400))

    def _shots(self, **fields) -> list[Event]:
        rows = []
        count = fields.pop("count", 20)
        gap = fields.pop("gap", 140)
        values = fields.pop("hidden", [80.0] * count)
        for index in range(count):
            rows.append(
                Event(
                    game_id="t", match_id="m", player_id="p", t_ms=index * gap, event_type="shot",
                    skill_band="average", weapon_class="rifle", hidden_track_ms=values[index], **fields,
                )
            )
        return rows

    def _hidden(self, events: list[Event]) -> list[float]:
        return summarize(events, GameProfile(game_id="t"))[0].weapons[0].hidden_track_ms

    def test_following_footsteps_through_a_wall_is_not_a_wallhack(self):
        self.assertEqual(len(self._hidden(self._shots(information_state="unknowable"))), 20)
        self.assertEqual(self._hidden(self._shots(information_state="audio")), [])

    def test_tracking_a_body_that_just_broke_line_of_sight_is_human(self):
        self.assertEqual(self._hidden(self._shots(since_perceived_ms=300.0)), [])
        self.assertEqual(len(self._hidden(self._shots(since_perceived_ms=4000.0))), 20)

    def test_a_running_total_is_not_counted_once_per_shot(self):
        # 800 ms on a hidden body, reported as a running total on eight shots 100 ms apart.
        events = self._shots(count=8, gap=100, hidden=[100.0 * (i + 1) for i in range(8)])
        windowed = self._hidden(events)
        self.assertEqual(sum(windowed), 800.0)
        weapon = summarize(events, GameProfile(game_id="t"))[0].weapons[0]
        self.assertIsNone(hidden_break(weapon, GameProfile(game_id="t")))

    def test_a_server_with_no_ranks_still_has_a_ceiling(self):
        profile = GameProfile(game_id="t", min_shots=40, min_hits_for_headshot=25, min_cohort_players=30)
        table = CohortTable()
        for index in range(40):
            table.add("unrated", "rifle", "accuracy", f"u{index}", 0.15 + index * 0.004)
            table.add("unrated", "rifle", "headshot_rate", f"u{index}", 0.20 + index * 0.004)

        def case(hits: int, heads: int):
            weapon = _rifle("x", "unrated", 200, hits, heads)
            return assess_player(PlayerRecord("x", "t", "unrated", weapons=[weapon]), table, profile)

        self.assertEqual(case(185, 160).decision, "review")
        honest = case(50, 12)
        self.assertEqual(honest.decision, "clean")
        self.assertTrue(any("No skill_band" in note for note in honest.observations))


class VendorAlignmentTest(unittest.TestCase):
    def test_a_humanizer_table_lines_up_by_spray_index(self):
        profile = GameProfile(game_id="t")
        rng = random.Random(5)
        table = [rng.uniform(-0.4, 0.4) for _ in range(30)]

        def customer(seed: int, lengths: list[int], build: str = "ak|", weapon: str = "rifle") -> RecoilSummary:
            local = random.Random(seed)
            applied, command, spray = [], [], []
            for length in lengths:
                for k in range(length):
                    kick = local.uniform(0.8, 2.4)
                    applied.append(kick)
                    command.append(-kick + table[k] + local.gauss(0, 0.02))
                    spray.append(k)
            return RecoilSummary(build, weapon, "average", applied=applied, compensation=command, spray=spray)

        def honest(seed: int, lengths: list[int]) -> RecoilSummary:
            local = random.Random(seed)
            applied, command, spray = [], [], []
            before = 1.6
            for length in lengths:
                for k in range(length):
                    kick = local.uniform(0.8, 2.4)
                    applied.append(kick)
                    command.append(-0.45 * before + local.gauss(0, 0.3))
                    spray.append(k)
                    before = kick
            return RecoilSummary("ak|", "rifle", "average", applied=applied, compensation=command, spray=spray)

        def record(player_id: str, recoil: RecoilSummary) -> PlayerRecord:
            return PlayerRecord(player_id, "t", "average", recoils=[recoil])

        # Different spray lengths and a different first spray: the log positions never line up.
        first = customer(1, [30, 30, 30, 30])
        second = customer(2, [12, 30, 25, 30, 28])
        # The same tool on a modded rifle still lines up. A different weapon is never compared.
        modded = customer(5, [30, 30, 30], build="ak|compensator")
        other_gun = customer(3, [30, 30, 30], build="vector|", weapon="smg")
        stranger = honest(4, [30, 26, 30, 30])
        cases = {pid: _bare_case(pid, "clean") for pid in ("first", "second", "modded", "other-gun", "stranger")}
        annotate_vendors(
            list(cases.values()),
            [record("first", first), record("second", second), record("modded", modded),
             record("other-gun", other_gun), record("stranger", stranger)],
            profile,
        )
        self.assertIn(cases["first"].vendor_twin, ("second", "modded"))
        self.assertEqual(cases["second"].decision, "watch")
        self.assertEqual(cases["modded"].decision, "watch")
        self.assertEqual(cases["other-gun"].decision, "clean")
        self.assertEqual(cases["stranger"].decision, "clean")

        # Without spray_index the same pair is only lined up by log position, and is missed.
        blind = {pid: _bare_case(pid, "clean") for pid in ("first", "second")}
        annotate_vendors(
            list(blind.values()),
            [
                record("first", RecoilSummary("ak|", "rifle", "average", applied=first.applied, compensation=first.compensation)),
                record("second", RecoilSummary("ak|", "rifle", "average", applied=second.applied, compensation=second.compensation)),
            ],
            profile,
        )
        self.assertEqual(blind["second"].decision, "clean")


class PlumbingTest(unittest.TestCase):
    def test_ai_brief_strips_every_account_the_case_names(self):
        payload = {
            "player_id": "average-joe",
            "decision": "watch",
            "reasons": ["leftover command matches rage (r 0.97), who is already a review in this batch"],
            "observations": ["average accuracy, rage-adjacent wording stays"],
            "vendor_twin": "rage",
            "seal": "abc",
            "party_ids": ["stack"],
            "match_ids": ["m"],
        }
        hidden = redact_case(payload, ["average-joe", "rage", "someone-else"])
        blob = json.dumps(hidden)
        self.assertNotIn("average-joe", blob)
        self.assertNotIn("matches rage", blob)
        self.assertEqual(hidden["vendor_twin"], "player-A")
        self.assertIn("leftover command matches player-A", hidden["reasons"][0])
        # An id inside another word is not an id.
        self.assertIn("average accuracy", hidden["observations"][0])
        self.assertIn("rage-adjacent", hidden["observations"][0])
        self.assertEqual(hidden["seal"], "")
        self.assertEqual(payload["vendor_twin"], "rage")  # the case itself is untouched

    def test_case_files_never_share_a_name(self):
        ids = ["p 1", "p_1", "Bob", "bob", "x" * 90, "x" * 89 + "y", "émile", "emile"]
        names = [safe_name(pid) for pid in ids]
        self.assertEqual(len({name.lower() for name in names}), len(ids))
        self.assertEqual(safe_name("p_1"), "p_1")
        self.assertTrue(all(len(name) <= 80 for name in names))

    def test_baseline_output_folder_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "baselines" / "week.json"
            write_json(target, {"ok": True})
            self.assertTrue(target.is_file())

    def test_sample_week_round_trips_through_the_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "week.ndjson"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(["sample", "--out", str(target)]), 0)
            events, errors = load_events(target)
            self.assertEqual(errors, [])
            players = {event.player_id for event in events}
            self.assertIn("weight-cheat", players)
            self.assertTrue(any(player.startswith("pop-elite") for player in players))
            moved = [event for event in events if event.player_id == "blasted" and event.event_type == "movement"]
            self.assertTrue(moved and all(event.displacement_cause == "explosion" for event in moved))


class PopulationTest(unittest.TestCase):
    """The synthetic week: four hundred players, seventeen planted cheats, scored nightly and weekly.

    A rule that is fine on one planted player and noisy on a population shows up here.
    """

    @classmethod
    def setUpClass(cls):
        cls.week = build_week()
        cls.queue = merge_queue(cls.week.cases, cls.week.nightly)
        cls.payload = week_payload(cls.week)

    def _decisions(self, label: str) -> list[str]:
        return [self.queue[pid].decision for pid, truth in self.week.truth.items() if truth == label]

    def test_no_honest_player_reaches_review(self):
        framed = [pid for pid, truth in self.week.truth.items() if truth == "honest" and self.queue[pid].decision == "review"]
        self.assertEqual(framed, [])

    def test_honest_watch_rate_is_small(self):
        honest = self._decisions("honest")
        self.assertLessEqual(honest.count("watch"), len(honest) * 0.02)

    def test_blatant_cheats_reach_review(self):
        for label in ("speed", "rage", "no-recoil", "mirror", "fire-rate", "metronome", "wallhack", "quiet-radar", "wire", "esp"):
            self.assertEqual(set(self._decisions(label)), {"review"}, label)

    def test_every_watch_says_why(self):
        # The queue line is the reason for the watch, never a note about too few shots on another gun.
        for row in self.payload["rows"]:
            if row["decision"] == "watch":
                self.assertTrue(row["why"], row["id"])
                self.assertNotIn("shots, need", row["why"], row["id"])

    def test_teammate_on_a_wallhacker_call_is_a_watch(self):
        friend = next(pid for pid, truth in self.week.truth.items() if truth == "radar-friend")
        self.assertEqual(self.queue[friend].decision, "watch")
        self.assertIn("voice", self.queue[friend].checks)

    def test_payload_reports_what_the_server_sent(self):
        self.assertTrue(self.payload["synthetic"])
        self.assertEqual(len(self.payload["rows"]), self.payload["totals"]["players"])
        wire = next(row for row in self.payload["coverage"] if row["field"] == "wire_error_deg")
        self.assertEqual(wire["daily"][:WIRE_SHIPS], [0.0] * WIRE_SHIPS)
        self.assertTrue(all(share > 0.99 for share in wire["daily"][WIRE_SHIPS:]))
        counts = Counter(row["decision"] for row in self.payload["rows"])
        self.assertEqual(counts, Counter(case.decision for case in self.queue.values()))
        self.assertTrue(all(len(row["nights"]) == len(self.week.days) for row in self.payload["rows"]))


class OpsTest(unittest.TestCase):
    def _row(self, pid: str, decision: str) -> dict:
        return {"id": pid, "decision": decision, "reports": 0, "checks": [], "why": "", "nights": [], "first": None, "metrics": []}

    def _payload(self, rows: list[dict]) -> dict:
        return {"rows": rows, "totals": {"events": 10, "shots": 8, "movement": 2, "matches": 1, "players": len(rows)}}

    def test_a_nightly_review_stays_open(self):
        first = self._payload([self._row("a", "review"), self._row("b", "watch")])
        second = self._payload([self._row("a", "clean"), self._row("b", "clean"), self._row("c", "watch")])
        merged = merge_payloads([first, second], ["2026-09-26", "2026-09-27"])
        rows = {row["id"]: row for row in merged["rows"]}
        self.assertEqual(rows["a"]["decision"], "review")
        self.assertEqual(rows["a"]["nights"], ["R", "C"])
        self.assertEqual(rows["a"]["first"], 0)
        # A watch is a monitor flag. The latest run decides it.
        self.assertEqual(rows["b"]["decision"], "clean")
        self.assertEqual(rows["c"]["nights"], ["", "W"])
        self.assertEqual(merged["days"][0]["label"], "Sat 26")

    def test_score_writes_a_dashboard_and_dashboard_merges_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            nights = [Path(tmp) / "2026-10-01", Path(tmp) / "2026-10-02"]
            with contextlib.redirect_stdout(io.StringIO()):
                for night in nights:
                    self.assertEqual(cli_main(["score", str(ROOT / "examples" / "shot.jsonl"), "--profile", str(PROFILE_PATH), "--out", str(night)]), 0)
                self.assertEqual(cli_main(["dashboard", *map(str, nights), "--out", str(Path(tmp) / "week.html")]), 0)
            self.assertTrue((nights[0] / "dashboard.html").is_file())
            html = (Path(tmp) / "week.html").read_text(encoding="utf-8")
            marker = '<script id="ops-payload" type="application/json">'
            start = html.index(marker) + len(marker)
            payload = json.loads(html[start:html.index("</script>", start)])
            self.assertEqual([day["label"] for day in payload["days"]], ["Thu 01", "Fri 02"])
            self.assertEqual(payload["rows"][0]["id"], "p-1044")
            self.assertEqual(len(payload["rows"][0]["nights"]), 2)
            with self.assertRaises(SystemExit):
                cli_main(["dashboard", str(Path(tmp) / "missing")])

    def test_the_scatter_point_takes_both_numbers_from_one_weapon(self):
        def metric(name: str, key: str, value: float) -> MetricView:
            return MetricView(name, value, value, None, None, None, None, None, key=key)

        case = Case("p", "t", "clean", "none", "none", "average", 0, metrics=[
            metric("accuracy", "smg", 0.31),
            metric("accuracy", "rifle", 0.22),
            metric("headshot_rate", "rifle", 0.27),
        ])
        self.assertEqual(_scatter_point(case, "rifle"), {"acc": 0.22, "hs": 0.27})
        # The smg has no headshot rate yet. It does not borrow the rifle's.
        self.assertIsNone(_scatter_point(case, "smg"))

    def test_a_review_shows_the_latest_night_that_opened_it(self):
        def case(pid: str, decision: str, reason: str) -> Case:
            return Case(pid, "t", decision, "human_review" if decision == "review" else "none", "none", "average", 0, reasons=[reason])

        queue = merge_queue([case("a", "clean", "")], [[case("a", "review", "night 1")], [case("a", "review", "night 2")]])
        self.assertEqual(queue["a"].reasons, ["night 2"])

    def test_merged_coverage_counts_each_night_by_the_events_that_could_carry_the_field(self):
        def night(share: float, n: int, events: int) -> dict:
            payload = self._payload([self._row("a", "clean")])
            payload["totals"]["events"] = events
            payload["coverage"] = [{"field": "hitbox", "needs": "headshot rate", "kind": "shot", "share": share, "n": n}]
            return payload

        # A night with few hits but many movement samples must not outweigh a night with many hits.
        merged = merge_payloads([night(1.0, 10, 10_000), night(0.0, 90, 100)], ["2026-09-26", "2026-09-27"])
        self.assertEqual(merged["coverage"][0]["share"], 0.1)

    def test_the_queue_line_is_the_one_behind_the_decision(self):
        glitch = "4 over-cap ground samples, longest run 1 (need 25). Treated as a glitch or a blast the server did not tag, not as a cheat."
        tail = "pistol accuracy is above this rank's range and inside the best humans measured"
        short = "sniper: 4 shots, need 40 before aim is scored"
        case = Case("p", "t", "watch", "monitor", "none", "average", 0, observations=[glitch, tail, short])
        self.assertEqual(_why(case), tail)
        case.decision = "insufficient_data"
        self.assertEqual(_why(case), short)

    def test_the_desk_links_the_real_cs2_matches(self):
        from fpsdet.pages import write_pages

        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(build_demo(), tmp)
            board = (dest / "board.html").read_text(encoding="utf-8")
            page = (dest / "cs2.html").read_text(encoding="utf-8")
        self.assertIn('href="cs2.html"', board)
        self.assertIn('<a class="ops-home" href="board.html">', page)
        self.assertIn("<title>Real CS2 matches · fpsdet</title>", page)
        marker = '<script id="ops-payload" type="application/json">'
        start = page.index(marker) + len(marker)
        payload = json.loads(page[start:page.index("</script>", start)])
        # Real matches with the dataset's labels: never called synthetic, and the labels are not fpsdet's.
        self.assertFalse(payload["synthetic"])
        self.assertEqual(payload["truth_kind"], "labelled")
        self.assertEqual(payload["tape_base"], "board.html")
        self.assertEqual({row["truth"] for row in payload["rows"]}, {"cheater", *payload["honest_labels"]})
        self.assertFalse([row["id"] for row in payload["rows"] if row["decision"] == "review" and row["truth"] != "cheater"])
        self.assertTrue(any("result" in link["text"] for link in payload["links"]))

    def test_the_decoys_page_is_linked_honest_and_drawn_from_the_tf2_run(self):
        from fpsdet.pages import write_pages

        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(build_demo(), tmp)
            page = (dest / "decoys.html").read_text(encoding="utf-8")
            board = (dest / "board.html").read_text(encoding="utf-8")
            index = (dest / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="decoys.html"', index)
        self.assertIn('href="decoys.html"', board)
        self.assertIn('href="board.html#tape-replay"', page)
        self.assertIn("This does not end cheating", page)
        self.assertIn("Automated action: none", page)
        self.assertIn("private_track_ms", page)
        self.assertIn("Can it be countered?", page)
        self.assertNotIn("<script", page)
        rows = json.loads((ROOT / "examples" / "tf2" / "desk.json").read_text(encoding="utf-8"))["rows"]
        def sniper(label):
            return [m for r in rows if r["truth"] == label for m in r["metrics"] if m["key"] == "sniperrifle" and m["name"] == "accuracy"]
        honest, cheats = sniper("never banned"), sniper("banned for cheating")
        best = max(m["human"] for m in honest + cheats)
        past = sum(1 for m in cheats if m["value"] > best)
        # The chart's caption is the run's, not a number typed in.
        self.assertIn(f"sniper rifle accuracy for {len(honest)} never-banned players and {len(cheats)} players banned for cheating", page)
        self.assertIn(f"Only {past} of the {len(cheats)} banned cheaters are past that line", page)
        self.assertIn(f"best human measured {best:.0%}", page)

    def test_the_home_page_numbers_come_from_the_benchmark(self):
        """The home page's real-match numbers are FPSDET Benchmark v1's: TF2 over all nine predeclared baseline
        draws, and CS2. The first TF2 run (one draw, chosen by a private key) is linked only as a historical record."""
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        tf2 = json.loads((ROOT / "benchmark" / "sensitivity" / "tf2-rgl-v2.json").read_text(encoding="utf-8"))
        counts = json.loads((ROOT / "benchmark" / "datasets" / "tf2-rgl-v2.json").read_text(encoding="utf-8"))["expected_counts"]
        across, draws = tf2["across"], tf2["draws"]
        self.assertEqual(len(draws), 9)
        self.assertIn(f"Tested on {counts['selected_logs']:,} real TF2 league matches, nine ways.", index)
        self.assertIn(f"{counts['labelled_accounts']} accounts banned for cheating by RGL", index)
        self.assertIn(f"{across['ratio']['median']:.1f}×</p>", index)
        self.assertIn(f"A median {across['positive_rate']['median']:.1%} of the banned accounts it could compare were flagged for review or watch, "
                      f"against {across['comparison_rate']['median']:.1%} of never-banned players. "
                      f"{across['ratio']['min']:.1f}× to {across['ratio']['max']:.1f}× across the nine draws.", index)
        # "0 never-banned players sent to review, in any draw", beside how rarely anyone reaches review on match totals.
        self.assertEqual({draw["numbers"]["comparison"]["review"] for draw in draws}, {0})
        self.assertIn("never-banned players sent to review, in any draw", index)
        reviews = sorted(draw["numbers"]["positive"]["review"] for draw in draws)
        self.assertIn(f"Few checks can reach review on match totals: {reviews[0]} to {reviews[-1]} banned accounts did per draw, median {reviews[len(reviews) // 2]}.", index)
        self.assertIn("Most of it is the rank-tail check, a watch-level signal, and league bans may partly follow suspicious performance.", index)
        missed = sorted(draw["numbers"]["positive"]["eligible"] - draw["numbers"]["positive"]["flagged"] for draw in draws)
        (eligible,) = {draw["numbers"]["positive"]["eligible"] for draw in draws}
        self.assertIn(f"{missed[len(missed) // 2]} of {eligible}</p>", index)
        self.assertIn(f"Between {missed[0]} and {missed[-1]} in each draw.", index)
        cs2 = json.loads((ROOT / "benchmark" / "results" / "cs2cd-v2.json").read_text(encoding="utf-8"))["published"]["decisions"]
        watched = sum(row.get("watch", 0) for row in cs2.values())
        self.assertEqual(sum(row.get("review", 0) for row in cs2.values()), 0)  # "nobody went to review"
        self.assertIn(f"{cs2['cheater']['watch']} of {watched}</p>", index)
        self.assertIn(f"{sum(sum(row.values()) for row in cs2.values()):,} players in public CS2 matchmaking matches", index)
        # The historical run's own numbers never come back as the headline.
        for historical in ("10,218", "better than picking at random", "It picked 97 players"):
            self.assertNotIn(historical, index)
        self.assertIn("one baseline draw, kept as a historical record", index)

    def test_the_evidence_page_is_linked_and_says_only_what_the_artifacts_hold(self):
        import re
        from fpsdet.pages import write_pages
        from fpsdet.provenance import PACKET_RECIPE

        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(build_demo(), tmp)
            page = (dest / "evidence.html").read_text(encoding="utf-8")
            board = (dest / "board.html").read_text(encoding="utf-8")
            others = {name: (dest / name).read_text(encoding="utf-8")
                      for name in ("index.html", "scoring.html", "wire.html", "games.html", "source.html", "decoys.html")}
        self.assertIn('href="evidence.html"', board)
        for name, text in others.items():
            self.assertIn('href="evidence.html"', text, name)
        self.assertNotIn("../demo/board.html", page)
        self.assertIn('href="board.html"', page)
        for words in ("Automated action: none", "This does not end cheating", "No person has played yet"):
            self.assertIn(words, page)
        self.assertNotIn("<script", page)
        self.assertNotRegex(page, r"sha256:[0-9a-f]{8}", "digests change with the code; the page elides them")
        self.assertIn(f'"recipe": "{PACKET_RECIPE}"', page)
        claims = json.loads((ROOT / "benchmark" / "claims.json").read_text(encoding="utf-8"))["claims"]
        fixtures = next(claim["text"] for claim in claims if claim["class"] == "controlled_fixture" and "native detectors" in claim["text"])
        detectors = int(re.search(r"All (\d+) of \1 native detectors", fixtures).group(1))
        self.assertIn(f"Every one of the {detectors} checks", page)
        self.assertIn(f"All {detectors} checks trip", others["index.html"])
        # The benchmark's TF2 and CS2 numbers, as on the home page.
        across = json.loads((ROOT / "benchmark" / "sensitivity" / "tf2-rgl-v2.json").read_text(encoding="utf-8"))["across"]
        self.assertIn(f"a median {across['positive_rate']['median']:.1%} of the banned accounts it could compare, against {across['comparison_rate']['median']:.1%} of the never-banned. "
                      f"{across['ratio']['median']:.1f}× at the median, {across['ratio']['min']:.1f}× to {across['ratio']['max']:.1f}× across the draws.", page)
        historical = json.loads((ROOT / "benchmark" / "sensitivity" / "tf2-rgl-v1.json").read_text(encoding="utf-8"))
        curator = next(row for row in historical["draws"] if row["draw"] == "curator")["summary"]["ratio"]
        self.assertIn(f"{curator:.1f}×: the most favourable of its {len(historical['draws'])} draws", page)
        # The live pilot: every declared scenario, every capture replayed identically.
        pilot = json.loads((ROOT / "examples" / "pilot" / "result.json").read_text(encoding="utf-8"))["scenarios"]
        self.assertTrue(all(row["as_expected"] for row in pilot))
        self.assertEqual({row["live_vs_offline"] for row in pilot}, {"identical"})
        self.assertIn(f"All {len(pilot)} declared scenarios came out as declared", page)
        self.assertIn(f"All {len(pilot)} declared scenarios came out as declared", others["index.html"])
        # The human pilot: no person yet, and the dry run's machine numbers as recorded.
        self.assertFalse((ROOT / "examples" / "human-pilot" / "result.json").exists(), "a human result exists: this page must report it")
        dry = json.loads((ROOT / "examples" / "human-pilot" / "dry-run" / "result.json").read_text(encoding="utf-8"))
        worst, bar = dry["worst_case"], dry["review_grade"]["bar"]
        self.assertIn(f"counted {worst['counted_ms']:,.0f} ms against a bar of {bar['min_total_ms']:,.0f} ms", page)
        self.assertIn(f"The challenge bar counts time on the probe: {bar['min_samples']} moments and {bar['min_total_ms']:,.0f} ms.", page)
        spells = ", ".join(f"{ms / 1000:.1f}" for ms in worst["episode_ms"][:-1]) + f" and {worst['episode_ms'][-1] / 1000:.1f} s"
        self.assertIn(f"in three spells of {spells}, and fpsdet counted {worst['counted_ms']:,.0f} ms.", page)
        self.assertEqual(worst["episodes"], 3)
        self.assertIn(f"the probe's median distance from the crosshair was {worst['aim_error_deg']['median']:.1f}°", page)
        self.assertEqual(worst["turn_rate_dps"]["median"], 0.0)  # "The aim never turned."
        self.assertEqual(worst["explained_ms"], 0.0)  # "No visible bot explained it."
        self.assertIn(f"counted a median {dry['controlled_follower']['counted_ms']['median']:,.0f} ms per challenge", page)
        share = round(dry["explained"]["explained_by_visible_bot_ms"] / dry["explained"]["overlap_ms"] * 100)
        self.assertIn(f"Visible bots explained {share}% of all honest-style overlap", page)
        # The README's status table says the same.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"All {detectors} checks against a planted behaviour", readme)
        self.assertIn(f"All {len(pilot)} declared scenarios came out as declared", readme)
        self.assertIn("**No person has played yet.**", readme)

    def test_the_desk_links_the_real_tf2_matches(self):
        import re
        from fpsdet.pages import write_pages

        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(build_demo(), tmp)
            board = (dest / "board.html").read_text(encoding="utf-8")
            page = (dest / "tf2.html").read_text(encoding="utf-8")
        self.assertIn('href="tf2.html"', board)
        self.assertLess(board.index('href="cs2.html"'), board.index('href="tf2.html"'))
        marker = '<script id="ops-payload" type="application/json">'
        start = page.index(marker) + len(marker)
        payload = json.loads(page[start:page.index("</script>", start)])
        self.assertEqual((payload["truth_kind"], payload["cheat_labels"], payload["honest_labels"]),
                         ("labelled", ["banned for cheating"], ["never banned"]))
        # Every review is a labelled cheater, and no raw SteamID reaches the page.
        self.assertEqual({row["truth"] for row in payload["rows"] if row["decision"] == "review"}, {"banned for cheating"})
        self.assertIsNone(re.search(r"7656\d{13}|\[U:1:\d+\]", page))
        # The page says what fpsdet made and where the data and the labels came from.
        self.assertIn('id="card-found"', page)
        self.assertTrue(payload["label_source"] and payload["data_source"])

    def test_board_opens_on_operations_and_keeps_the_answer_key(self):
        html = render_board(build_demo())
        self.assertIn('role="tablist"', html)
        self.assertLess(html.index('id="ops"'), html.index('id="key"'))
        self.assertIn("The answer key.", html)
        marker = '<script id="ops-payload" type="application/json">'
        start = html.index(marker) + len(marker)
        payload = json.loads(html[start:html.index("</script>", start)])
        self.assertTrue(payload["synthetic"])
        self.assertEqual(payload["tape_base"], "")
        self.assertEqual(payload["tapes"]["mirror"], "tape-mirror")


class CadenceTest(unittest.TestCase):
    def setUp(self):
        self.profile = GameProfile(game_id="t", weapons={"rifle": WeaponRule(min_shot_interval_ms=90, interval_slack_ms=15)})

    def _weapon(self, matches: dict[str, list[int]]) -> WeaponSummary:
        gaps = [gap for rows in matches.values() for gap in rows]
        return WeaponSummary("rifle", "rifle", "average", fire_gaps=gaps, fire_matches=matches)

    def test_a_macro_switched_on_midweek_is_judged_on_its_own_matches(self):
        honest = {f"h{i}": [100 + (k * 17) % 60 for k in range(20)] for i in range(4)}
        macro = {"m1": [140] * 9 + [4200] + [140] * 12, "m2": [140] * 14 + [2500] + [140] * 8}
        weapon = self._weapon({**honest, **macro})
        pooled = WeaponSummary("rifle", "rifle", "average", fire_gaps=weapon.fire_gaps)
        self.assertIsNone(metronome_break(pooled, self.profile))
        found = metronome_break(weapon, self.profile)
        self.assertIn("std 0.00", found)
        self.assertIn("in 2 matches", found)
        self.assertIsNone(metronome_break(self._weapon(honest), self.profile))

    def test_fire_rate_is_judged_per_match(self):
        honest = {f"h{i}": [110 + k % 40 for k in range(30)] for i in range(3)}
        rapid = {"r1": [50] * 25}
        record = PlayerRecord("p", "t", "average", weapons=[self._weapon({**honest, **rapid})])
        case = assess_player(record, CohortTable(), self.profile)
        self.assertEqual(case.decision, "review")
        self.assertIn("fire_rate", case.checks)
        # One jittery honest gap per match is not a habit.
        jitter = {f"j{i}": [110] * 28 + [60] for i in range(8)}
        clean = assess_player(PlayerRecord("q", "t", "average", weapons=[self._weapon(jitter)]), CohortTable(), self.profile)
        self.assertNotIn("fire_rate", clean.checks)

    def test_a_glitch_in_a_two_shot_match_is_not_a_habit(self):
        # Ten matches where the gun fired three times, one stamp 40 ms after the last: 50% of two gaps each time.
        honest = {f"h{i}": [110 + k % 40 for k in range(30)] for i in range(30)}
        short = {f"s{i}": [40, 160] for i in range(10)}
        case = assess_player(PlayerRecord("p", "t", "average", weapons=[self._weapon({**honest, **short})]), CohortTable(), self.profile)
        self.assertNotIn("fire_rate", case.checks)

    def test_a_held_trigger_on_a_full_auto_is_not_a_macro(self):
        # Sprays of eight on a 64-tick server: the 90 ms cycle lands on 6 ticks, 93 or 94 ms. Pauses between sprays.
        spray = [94, 94, 93, 94, 94, 94, 93]
        held = {f"m{i}": spray + [3100] + spray + [2400] + spray for i in range(10)}
        self.assertIsNone(metronome_break(self._weapon(held), self.profile))
        # A 20-tick server puts the same cycle on 100 ms. With tick_ms set, that is still the server's pace.
        coarse = GameProfile(game_id="t", tick_ms=50, weapons={"rifle": WeaponRule(min_shot_interval_ms=90)})
        stamped = {f"m{i}": [100] * 7 + [3100] + [100] * 7 for i in range(10)}
        self.assertIsNone(metronome_break(self._weapon(stamped), coarse))
        # A tap macro well above the cycle is still a macro.
        tapped = {f"m{i}": [140] * 7 + [3100] + [140] * 7 for i in range(10)}
        self.assertIsNotNone(metronome_break(self._weapon(tapped), self.profile))


class BoundTest(unittest.TestCase):
    def test_the_named_band_and_its_p95_come_from_the_same_band(self):
        from fpsdet.score import _rate_flags

        cohorts = CohortTable()
        for index in range(30):
            cohorts.add("elite", "rifle", "accuracy", f"e{index}", 0.20 + index * 0.003)
            cohorts.add("average", "rifle", "accuracy", f"a{index}", 0.10 + index * 0.012)
        profile = GameProfile(game_id="t", min_cohort_players=30)
        weapon = WeaponSummary("rifle", "rifle", "average", shots=200, hits=150)
        view, _band, _human, _did = _rate_flags("accuracy", 150, 200, 0.75, PlayerRecord("p", "t", "average"), weapon, cohorts, profile)
        # The best human measured is in the average band, not the top one, so the case names that band and its p95.
        self.assertEqual(view.ceiling_band, "average")
        self.assertAlmostEqual(view.ceiling_p95, cohorts.dist("average", "rifle", "accuracy", "p").p95)

    def test_a_zero_vendor_z_is_kept(self):
        self.assertEqual(profile_from_dict({"game_id": "t", "vendor_min_z": 0}).vendor_min_z, 0.0)

    def test_the_match_spread_does_not_depend_on_match_order_or_python_version(self):
        # Plain float summation gives these 40 matches a different last digit forwards and backwards,
        # and Python 3.12 changed how sum() adds floats. The bound on a case must not move with either.
        rng = random.Random(3)
        groups = [(n, rng.randint(0, n)) for n in (rng.randint(5, 60) for _ in range(40))]
        self.assertEqual(design_effect(groups), design_effect(groups[::-1]))

    def test_a_short_sample_gets_a_wide_median_bound(self):
        short = [30.0, 31.0, 29.0, 35.0, 28.0, 33.0, 30.5, 34.0, 32.0]
        self.assertLess(median_bound(short, upper=False), 29.5)
        long = [30.0 + (i % 7) * 0.5 for i in range(400)]
        self.assertGreaterEqual(median_bound(long, upper=False), 31.0)
        self.assertGreaterEqual(median_bound(short, upper=True), 33.0)

    def test_past_every_human_means_every_band(self):
        profile = GameProfile(game_id="t", min_shots=40, min_cohort_players=30)
        table = CohortTable()
        for index in range(40):
            table.add("developing", "rifle", "median_distance", f"d{index}", 30.0 + index * 0.25)
            table.add("elite", "rifle", "median_distance", f"e{index}", 25.0 + index * 0.15)
        # 35 m is past the farthest elite (30.9) and inside the farthest developing player (39.75).
        record = PlayerRecord("long", "t", "elite", weapons=[_rifle("long", "elite", 80, 20, 5, 35.0)])
        view = next(row for row in assess_player(record, table, profile).metrics if row.name == "median_distance")
        self.assertFalse(view.beyond_human)
        self.assertEqual(view.ceiling_band, "developing")


class HonestLeftoverTest(unittest.TestCase):
    def test_honest_players_with_ordinary_sprays_do_not_match_each_other(self):
        profile = GameProfile(game_id="t")
        records, cases = [], []
        for index in range(60):
            rng = random.Random(index)
            gain = rng.uniform(0.3, 0.6)
            applied, command, spray = [], [], []
            for _ in range(rng.randint(25, 45)):
                before = 0.0
                for k in range(rng.randint(1, 14)):
                    kick = rng.uniform(0.8, 2.4)
                    applied.append(kick)
                    command.append(-gain * before + rng.gauss(0, 0.08))
                    spray.append(k)
                    before = kick
            pid = f"h{index}"
            records.append(PlayerRecord(pid, "t", "average", recoils=[RecoilSummary("ak|", "rifle", "average", applied=applied, compensation=command, spray=spray)]))
            cases.append(_bare_case(pid, "clean"))
        annotate_vendors(cases, records, profile)
        self.assertEqual([case.player_id for case in cases if case.decision != "clean"], [])


class MatchScreenTest(unittest.TestCase):
    """A lobby where cheaters played each other is found before it becomes the ceiling."""

    def setUp(self):
        self.profile = GameProfile(game_id="t", min_shots=40)

    def _match(self, match: str, rate: float, shots: int = 300, through: float | None = None) -> list[Event]:
        events = []
        hits = round(rate * shots)
        for k in range(shots):
            hit = k < hits
            events.append(
                Event(
                    game_id="t",
                    match_id=match,
                    player_id=f"{match}-p{k % 10}",
                    t_ms=k * 500,
                    event_type="shot",
                    skill_band="average",
                    weapon_class="rifle",
                    hit=hit,
                    through_geometry=(k < round(through * hits)) if (through is not None and hit) else None,
                )
            )
        return events

    def _window(self) -> list[Event]:
        events = []
        for index in range(20):
            events += self._match(f"m{index:02d}", 0.17 + 0.003 * index, through=0.06)
        return events

    def test_a_hack_lobby_is_flagged_and_a_strong_honest_lobby_is_not(self):
        events = self._window() + self._match("strong", 0.27, through=0.10) + self._match("hvh", 0.62, through=0.7)
        screen = screen_matches(events, self.profile)
        self.assertTrue(screen.judged)
        self.assertEqual(screen.matches, ["hvh"])
        self.assertIn("through geometry", screen.lines[0])

    def test_a_tiny_match_and_a_short_window_are_not_judged(self):
        tiny = self._window() + self._match("tiny", 1.0, shots=5)
        self.assertEqual(screen_matches(tiny, self.profile).matches, [])
        short = [event for event in self._window() if event.match_id < "m05"] + self._match("hvh", 0.62)
        self.assertFalse(screen_matches(short, self.profile).judged)

    def test_baseline_flags_by_default_and_leaves_out_on_request(self):
        events = self._window() + self._match("hvh", 0.62, through=0.7)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "week.ndjson"
            source.write_text(
                "".join(
                    json.dumps({"game_id": e.game_id, "match_id": e.match_id, "player_id": e.player_id, "t_ms": e.t_ms,
                                "event_type": "shot", "skill_band": "average", "weapon_class": "rifle", "hit": e.hit,
                                **({"through_geometry": e.through_geometry} if e.through_geometry is not None else {})}) + "\n"
                    for e in events
                ),
                encoding="utf-8",
            )
            profile = Path(tmp) / "profile.json"
            profile.write_text(json.dumps({"game_id": "t", "min_shots": 40}), encoding="utf-8")
            flagged, screened = Path(tmp) / "flagged.json", Path(tmp) / "screened.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                cli_main(["baseline", str(source), "--profile", str(profile), "--out", str(flagged)])
                cli_main(["baseline", str(source), "--profile", str(profile), "--screen-matches", "--out", str(screened)])
            kept = json.loads(flagged.read_text(encoding="utf-8"))
            self.assertEqual(kept["integrity"]["status"], "poison_risk")
            self.assertTrue(kept["integrity"]["alarms"][0].startswith("match hvh:"))
            left = json.loads(screened.read_text(encoding="utf-8"))
            self.assertEqual(left["integrity"]["status"], "ok")
            self.assertEqual(len(left["integrity"]["left_out"]), 1)
            players = {p["player_id"] for metric in left["metrics"] for p in metric["players"]}
            self.assertFalse(any(pid.startswith("hvh") for pid in players))


def _load_cs2_example():
    spec = importlib.util.spec_from_file_location("cs2cd", ROOT / "examples" / "cs2" / "cs2cd.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Cs2ExampleTest(unittest.TestCase):
    """The CS2CD converter in examples/cs2. Converting needs pandas and pyarrow; the rest is plain Python."""

    def setUp(self):
        self.cs2 = _load_cs2_example()

    def test_both_rank_scales_cut_on_the_game_tiers(self):
        band = self.cs2.skill_band
        self.assertEqual([band(12, rank) for rank in (5, 9, 13, 18)], ["developing", "average", "advanced", "elite"])
        self.assertEqual([band(11, rating) for rating in (4000, 7000, 15000, 25000)], ["developing", "average", "advanced", "elite"])
        self.assertEqual([band(11, 0), band(None, 5), band(12, 0)], ["unrated"] * 3)

    def test_the_desk_page_carries_labels_and_drops_what_it_does_not_show(self):
        ops = {
            "rows": [
                {"id": "wc001-p1", "decision": "watch", "metrics": [{"name": "accuracy"}, {"name": "view_p95"}],
                 "case": {"decision": "watch", "reasons": [], "observations": ["x"], "speed": {"samples": 9}, "limits": "..."}},
                {"id": "nc150-p2", "decision": "clean", "metrics": []},
            ],
            "integrity": {"status": "ok", "alarms": [], "left_out": ["match a", "match b"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ops.json").write_text(json.dumps(ops), encoding="utf-8")
            labels = Path(tmp) / "labels.json"
            labels.write_text(json.dumps({"wc001-p1": "cheater", "nc150-p2": "clean, unreviewed match"}), encoding="utf-8")
            out = Path(tmp) / "desk.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.cs2.main(["desk", tmp, "--labels", str(labels), "--out", str(out)])
            desk = json.loads(out.read_text(encoding="utf-8"))
        first, second = desk["rows"]
        self.assertEqual((first["truth"], second["truth"]), ("cheater", "clean, unreviewed match"))
        self.assertEqual([m["name"] for m in first["metrics"]], ["accuracy"])
        self.assertEqual(sorted(first["case"]), ["decision", "observations", "reasons"])
        self.assertEqual((desk["truth_kind"], desk["synthetic"]), ("labelled", False))
        self.assertIn("2 hack-vs-hack lobbies", desk["notes"]["baseline"])

    def test_the_profile_loads(self):
        profile = load_profile(ROOT / "examples" / "cs2" / "cs2.json")
        self.assertEqual(profile.game_id, "cs2")
        self.assertEqual(profile.weapons, {})

    @unittest.skipUnless(importlib.util.find_spec("pandas") and importlib.util.find_spec("pyarrow"), "needs pandas and pyarrow")
    def test_a_match_converts_to_events_fpsdet_reads(self):
        import pandas as pd

        rows = []
        for tick in range(100, 141):
            for steamid, team, x in (("Player_1", 2, 0.0), ("Player_2", 3, 1000.0), ("Player_3", 2, 50.0)):
                rows.append({
                    "tick": tick, "steamid": steamid, "team_num": team, "X": x, "Y": 0.0, "Z": 0.0,
                    "pitch": 0.0, "yaw": 0.0 if tick < 109 else 30.0, "velocity_X": 200.0, "velocity_Y": 0.0,
                    "is_airborne": False, "is_alive": True, "move_type": 2.0, "shots_fired": 1.0,
                    "rank": 9, "comp_rank_type": 12, "is_warmup_period": False, "is_freeze_period": False,
                })
        info = {
            "weapon_fire": [
                {"tick": 110, "user_steamid": "Player_1", "weapon": "weapon_ak47"},
                {"tick": 120, "user_steamid": "Player_1", "weapon": "weapon_ak47"},
                {"tick": 125, "user_steamid": "Player_1", "weapon": "weapon_knife"},
                {"tick": 130, "user_steamid": "Player_1", "weapon": "weapon_ak47"},
            ],
            "player_hurt": [
                {"tick": 110, "attacker_steamid": "Player_1", "user_steamid": "Player_2", "hitgroup": "head", "weapon": "ak47"},
                {"tick": 120, "attacker_steamid": "Player_1", "user_steamid": "Player_3", "hitgroup": "chest", "weapon": "ak47"},
            ],
            "bullet_damage": [
                {"tick": 110, "attacker_steamid": "Player_1", "victim_steamid": "Player_2", "distance": 1000.0, "num_penetrations": 1},
            ],
            "cheaters": [{"steamid": "Player_1"}],
            "CSstats_info": [{"map": "de_inferno"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "with_cheater_present"
            folder.mkdir()
            (folder / "7.json").write_text(json.dumps(info), encoding="utf-8")
            pd.DataFrame(rows).to_parquet(folder / "7.parquet")
            lines = self.cs2.convert_match(folder / "7.json", folder / "7.parquet", movement_hz=16)
        events = [parse_event(line) for line in lines]
        shots = [event for event in events if event.event_type == "shot"]
        # The knife is not a gun. Hurting a teammate is not a hit on an enemy.
        self.assertEqual([(event.t_ms, event.hit) for event in shots], [(1719, True), (1875, False), (2031, False)])
        first = shots[0]
        self.assertEqual((first.player_id, first.match_id, first.skill_band), ("wc007-p1", "wc007", "average"))
        self.assertEqual((first.hitbox, first.distance_m, first.through_geometry), ("head", 25.4, True))
        self.assertAlmostEqual(first.view_delta_deg, 30.0, places=3)
        moves = [event for event in events if event.event_type == "movement"]
        self.assertTrue(moves and all(event.expected_max_ground_speed_mps == 6.35 for event in moves))
        self.assertAlmostEqual(moves[0].speed_mps, 5.08, places=2)


class Tf2ExampleTest(unittest.TestCase):
    """The logs.tf converter in examples/tf2. Standard library only, so it runs in CI."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("tf2logs", ROOT / "examples" / "tf2" / "tf2logs.py")
        self.tf2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tf2)

    def test_ban_reasons_become_labels(self):
        label = self.tf2.ban_label
        self.assertEqual(label("Cheating - Multiple Offenses"), "cheater")
        self.assertEqual(label("ETF2L Mirror Cheating Ban - First Offense"), "cheater")
        self.assertEqual(label("Permanently banned due to a VAC ban."), "vac")
        # Helping a cheater, or selling cheats, is not cheating in a match.
        self.assertIsNone(label("Assisting Cheating - First Offense"))
        self.assertIsNone(label("Distribution of Cheats"))
        self.assertEqual(self.tf2.steam3("76561198890776257"), "[U:1:930510529]")

    def test_a_match_becomes_one_event_per_aimed_shot(self):
        log = {
            "info": {"map": "cp_process_f12", "total_length": 1800, "hasHS_hit": True, "date": 1743465295},
            "players": {
                "[U:1:1]": {"headshots_hit": 12, "class_stats": [{"type": "sniper", "weapon": {
                    "sniperrifle": {"shots": 50, "hits": 30}, "smg": {"shots": 20, "hits": 5}}}]},
                "[U:1:2]": {"class_stats": [{"type": "soldier", "weapon": {
                    "tf_projectile_rocket": {"shots": 300, "hits": 120}, "shotgun_soldier": {"shots": 10, "hits": 6}}}]},
                "[U:1:3]": {"class_stats": [{"type": "scout", "weapon": {"scattergun": {"shots": 9, "hits": 9}}}]},
            },
        }
        out = self.tf2.match_events(77, log, {"[U:1:1]": "sniper", "[U:1:2]": "soldier"})
        events = {pid: [parse_event(e) for e in rows] for pid, rows in out.items()}
        self.assertEqual(set(events), {"sniper", "soldier"})  # an unlisted player is left out
        rifle = [e for e in events["sniper"] if e.weapon_id == "sniperrifle"]
        self.assertEqual((len(rifle), sum(e.hit for e in rifle)), (50, 30))
        self.assertEqual(Counter(e.hitbox for e in rifle if e.hit), Counter({"upper_torso": 18, "head": 12}))
        smg = [e for e in events["sniper"] if e.weapon_id == "smg"]
        self.assertTrue(all(e.hitbox is None for e in smg))
        # Rockets hit by splash and are not aimed one shot at a time.
        self.assertEqual({e.weapon_id for e in events["soldier"]}, {"shotgun_soldier"})
        self.assertEqual({e.match_id for rows in events.values() for e in rows}, {"tf77"})

    def test_convert_keeps_a_ban_honest_and_never_judges_a_player_against_themselves(self):
        cheater, steady, rare = "76561197960265729", "76561197960265730", "76561197960265731"
        def log(day: int, players: list[str]) -> dict:
            return {"info": {"map": "cp_x", "total_length": 600, "date": day * 86400},
                    "players": {self.tf2.steam3(sid): {"class_stats": [{"type": "scout", "weapon": {"scattergun": {"shots": 10, "hits": 5}}}]} for sid in players}}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            # Day d is 1970-01-(d+1). The ban lands on day 11, so days 11 and 12 must not count.
            (root / "bans.json").write_text(json.dumps({cheater: {"label": "cheater", "banned": "1970-01-12"}}), encoding="utf-8")
            for day in range(1, 13):
                players = [cheater, steady] + ([rare] if day == 1 else [])
                (root / "logs" / f"{day}.json").write_text(json.dumps(log(day, players)), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.tf2.main(["convert", tmp, "--min-matches", "8"])
            labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))
            scored = [json.loads(line) for line in (root / "scored.ndjson").read_text(encoding="utf-8").splitlines()]
            baseline = [json.loads(line) for line in (root / "baseline.ndjson").read_text(encoding="utf-8").splitlines()]
            key = bytes.fromhex((root / "pseudonym.key").read_text(encoding="utf-8"))
        cheater_id = self.tf2._pseudonym(key, self.tf2.steam3(cheater))
        self.assertEqual(labels[cheater_id], "cheater")
        self.assertEqual(len({e["match_id"] for e in scored if e["player_id"] == cheater_id}), 10)
        # Seen once: too few matches to judge, and left out entirely.
        everyone = {e["player_id"] for e in scored + baseline}
        self.assertNotIn(self.tf2._pseudonym(key, self.tf2.steam3(rare)), everyone)
        self.assertFalse({e["player_id"] for e in scored} & {e["player_id"] for e in baseline})
        self.assertFalse(any(sid in json.dumps(scored + baseline) for sid in (cheater, steady, "[U:1:")))

    def test_headshots_that_do_not_add_up_are_not_sent(self):
        log = {"info": {"map": "cp_x", "total_length": 600, "hasHS_hit": True, "date": 86400},
               "players": {"[U:1:1]": {"headshots_hit": 9, "class_stats": [{"type": "sniper", "weapon": {"sniperrifle": {"shots": 20, "hits": 6}}}]}}}
        events = self.tf2.match_events(1, log, {"[U:1:1]": "p"})["p"]
        # 9 headshots on 6 hits cannot be right. Capping would read as every hit a head, so no hitbox is sent.
        self.assertEqual(sum(e["hit"] for e in events), 6)
        self.assertFalse(any("hitbox" in e for e in events))

    def test_per_minute_rates_ride_on_each_long_stints_first_shot(self):
        log = {
            "info": {"map": "cp_x", "total_length": 1800, "hasHS": True, "hasHS_hit": True, "date": 86400},
            "players": {"[U:1:1]": {"headshots": 3, "headshots_hit": 4, "class_stats": [
                {"type": "sniper", "kills": 6, "total_time": 600, "weapon": {"sniperrifle": {"shots": 20, "hits": 10}}},
                {"type": "scout", "kills": 2, "total_time": 120, "weapon": {"scattergun": {"shots": 30, "hits": 20}}},
            ]}},
        }
        events = self.tf2.match_events(1, log, {"[U:1:1]": "p"})["p"]
        with_rates = [e for e in events if "kills_per_min" in e]
        # One sniper event carries 6 kills and 3 headshot kills over 10 minutes. Two minutes of scout is too short.
        self.assertEqual(len(with_rates), 1)
        self.assertEqual((with_rates[0]["weapon_class"], with_rates[0]["kills_per_min"], with_rates[0]["headshot_kills_per_min"]), ("sniper", 0.6, 0.3))
        self.assertEqual(parse_event(with_rates[0]).extras, {"kills_per_min": 0.6, "headshot_kills_per_min": 0.3})

    def test_every_player_keeps_at_most_their_latest_matches(self):
        cheater, steady = "76561197960265729", "76561197960265730"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            # Banned on 1970-02-01 (day 31). 30 matches before it, 5 after.
            (root / "bans.json").write_text(json.dumps({cheater: {"label": "cheater", "banned": "1970-02-01"}}), encoding="utf-8")
            for day in range(1, 36):
                log = {"info": {"map": "cp_x", "total_length": 600, "date": day * 86400},
                       "players": {self.tf2.steam3(sid): {"class_stats": [{"type": "scout", "weapon": {"scattergun": {"shots": 10, "hits": 5}}}]}
                                   for sid in (cheater, steady)}}
                (root / "logs" / f"{day}.json").write_text(json.dumps(log), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.tf2.main(["convert", tmp, "--max-matches", "20"])
            events = [json.loads(line) for name in ("scored.ndjson", "baseline.ndjson")
                      for line in (root / name).read_text(encoding="utf-8").splitlines()]
            key = bytes.fromhex((root / "pseudonym.key").read_text(encoding="utf-8"))
        matches = lambda sid: sorted(int(e["match_id"][2:]) for e in events if e["player_id"] == self.tf2._pseudonym(key, self.tf2.steam3(sid)))
        # The cheater's 20 latest before the ban, the honest player's 20 latest overall.
        self.assertEqual(sorted(set(matches(cheater))), list(range(11, 31)))
        self.assertEqual(sorted(set(matches(steady))), list(range(16, 36)))

    def test_the_desk_page_names_the_labels_and_keeps_neither_kind_apart(self):
        ops = {"rows": [
            {"id": "tf-a", "decision": "review", "metrics": [{"name": "accuracy"}, {"name": "view_p95"}],
             "case": {"decision": "review", "reasons": ["r"], "observations": [], "limits": "..."}},
            {"id": "tf-b", "decision": "clean", "metrics": []},
            {"id": "tf-c", "decision": "watch", "metrics": []},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ops.json").write_text(json.dumps(ops), encoding="utf-8")
            labels = Path(tmp) / "labels.json"
            labels.write_text(json.dumps({"tf-a": "cheater", "tf-b": "not banned", "tf-c": "other ban"}), encoding="utf-8")
            out = Path(tmp) / "desk.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.tf2.main(["desk", tmp, "--labels", str(labels), "--out", str(out)])
            desk = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([row["truth"] for row in desk["rows"]], ["banned for cheating", "never banned", "banned for something else"])
        # A ban for something else is neither a cheater nor a known-clean player in the answer check.
        self.assertNotIn("banned for something else", desk["cheat_labels"] + desk["honest_labels"])
        self.assertEqual([m["name"] for m in desk["rows"][0]["metrics"]], ["accuracy"])
        self.assertNotIn("limits", desk["rows"][0]["case"])

    def test_the_profile_loads(self):
        profile = load_profile(ROOT / "examples" / "tf2" / "tf2.json")
        self.assertEqual((profile.game_id, profile.aim_group), ("tf2", "weapon_id"))



class TryItTest(unittest.TestCase):
    """examples/historic and the Try it page: an operator's own logs, converted with pseudonyms and scored
    offline. The page's numbers are what the bundled sample actually gives."""

    HERE = ROOT / "examples" / "historic"

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("historic_convert", cls.HERE / "convert.py")
        cls.convert = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.convert)

    def run_convert(self, folder: Path, *extra: str) -> tuple[str, list[dict]]:
        out = folder / "events.ndjson"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            code = self.convert.main([str(self.HERE / "sample.csv"), "--map", str(self.HERE / "map.json"), "--game", "my-game",
                                      "--salt-file", str(folder / "salt.hex"), "--out", str(out), *extra])
        self.assertEqual(code, 0)
        return printed.getvalue(), [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    def test_the_converter_keeps_only_fpsdet_fields_under_pseudonyms(self):
        import csv
        import os
        import re
        import stat

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            printed, events = self.run_convert(folder)
            sample = list(csv.DictReader((self.HERE / "sample.csv").open(encoding="utf-8")))
            self.assertEqual(len(events), len(sample))
            self.assertIn(f"{len(sample)} of {len(sample)} rows written", printed)
            self.assertIn("columns not copied (fpsdet has no field for them): ip, name", printed)
            properties = json.loads((ROOT / "schema" / "combat_event.schema.json").read_text(encoding="utf-8"))["properties"]
            text = (folder / "events.ndjson").read_text(encoding="utf-8")
            for row in sample:  # no account id, name or address survives
                for column in ("account", "name", "ip"):
                    self.assertNotIn(row[column], text)
            for event in events:
                self.assertLessEqual(set(event), set(properties))
                self.assertRegex(event["player_id"], r"^p-[0-9a-f]{16}$")
                self.assertIsInstance(event["t_ms"], int)
                parse_event(event)
            accounts = {row["account"] for row in sample}
            self.assertEqual(len({event["player_id"] for event in events}), len(accounts))
            self.assertEqual(stat.S_IMODE(os.stat(folder / "salt.hex").st_mode), 0o600)
            # The same salt gives the same pseudonyms, and --who finds an account's.
            _, again = self.run_convert(folder)
            self.assertEqual(events, again)
            who = io.StringIO()
            with contextlib.redirect_stdout(who):
                self.convert.main(["--who", "acct-1001", "--salt-file", str(folder / "salt.hex")])
            self.assertIn(who.getvalue().strip(), {event["player_id"] for event in events})
            # Another salt, other pseudonyms.
            other = folder / "other"
            other.mkdir()
            _, elsewhere = self.run_convert(other)
            self.assertNotEqual({e["player_id"] for e in events}, {e["player_id"] for e in elsewhere})
            self.assertIsNone(re.search(r"acct-\d+", json.dumps(elsewhere)))

    def test_the_sample_scores_as_the_page_says(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            printed, _ = self.run_convert(folder)
            events, errors = load_events(folder / "events.ndjson")
            self.assertEqual(errors, [])
            cases = run_score(events, load_profile(self.HERE / "profile.json"))
            who = io.StringIO()
            with contextlib.redirect_stdout(who):
                self.convert.main(["--who", "acct-1001", "--salt-file", str(folder / "salt.hex")])
            sprinter = who.getvalue().strip()
        decisions = {case.player_id: case.decision for case in cases}
        self.assertEqual([player for player, decision in decisions.items() if decision == "review"], [sprinter])
        self.assertEqual({case.automated_action for case in cases}, {"none"})
        review = next(case for case in cases if case.decision == "review")
        self.assertEqual(review.checks, ["speed"])
        page = (ROOT / "site" / "try.html").read_text(encoding="utf-8")
        readme = (self.HERE / "README.md").read_text(encoding="utf-8")
        first = printed.splitlines()[0].replace(str(folder / "events.ndjson"), "events.ndjson")
        self.assertIn(first, page)
        self.assertIn(first, readme)
        self.assertIn("columns not copied (fpsdet has no field for them): ip, name", page)
        for text in (page, readme):
            self.assertIn("7.0 m/s against", text)
            self.assertIn("for 30 samples", text)

    def test_every_page_asks_to_try_it(self):
        import re
        from fpsdet.pages import write_pages

        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(build_demo(), tmp)
            pages = {path.name: path.read_text(encoding="utf-8") for path in dest.glob("*.html")}
        self.assertIn("try.html", pages)
        for name in ("index.html", "scoring.html", "wire.html", "games.html", "source.html", "decoys.html", "evidence.html"):
            self.assertIn('id="try-cta"', pages[name], name)
            self.assertIn('<a class="nav-link nav-cta" href="try.html"><span>09</span>Try it</a>', pages[name], name)
        self.assertIn('id="try-top"', pages["index.html"])
        self.assertLess(pages["index.html"].index('id="try-top"'), pages["index.html"].index('id="real"'))
        self.assertIn('href="try.html"', pages["board.html"])
        page = pages["try.html"]
        for words in ("Automated action: none", "This does not end cheating", "Production never knows it ran"):
            self.assertIn(words, page)
        self.assertNotIn("<script", page)
        self.assertIn('href="board.html"', page)
        # Every path and form the page names exists.
        for path in set(re.findall(r"examples/historic/[\w.]+", page)):
            self.assertTrue((ROOT / path).exists(), path)
        for template in set(re.findall(r"template=([\w.]+)", page)):
            self.assertTrue((ROOT / ".github" / "ISSUE_TEMPLATE" / template).exists(), template)
        # Scoring opens no connection unless asked for a brief: only the AI module touches the network.
        network = [path.name for path in (ROOT / "src" / "fpsdet").glob("*.py")
                   if re.search(r"^\s*(import|from)\s+(urllib|socket|http\.client|requests)\b", path.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(network, ["ai_triage.py"])

    def test_the_setup_quest_is_drawn_by_its_script_and_jokes_only_about_facts(self):
        import re
        from fpsdet.pages import write_pages

        spec = importlib.util.spec_from_file_location("build_quest", ROOT / "site" / "build_quest.py")
        quest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(quest)
        images = {"setup-quest.svg": quest.wide(), "setup-quest-tall.svg": quest.tall()}
        for name, drawn in images.items():
            committed = (ROOT / "site" / "img" / name).read_text(encoding="utf-8")
            self.assertEqual(committed, drawn, f"{name}: run python site/build_quest.py")
            # Plain SVG: nothing runs, nothing loads.
            self.assertNotIn("<script", committed)
            self.assertNotIn("<image", committed)
            self.assertNotIn("url(", committed)
            self.assertEqual(re.findall(r"https?://[^\s\"']+", committed), ["http://www.w3.org/2000/svg"])
        # Every command and file a level names is real.
        self.assertTrue((self.HERE / "convert.py").is_file())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cases"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main(["score", str(ROOT / "examples" / "shot.jsonl"), "--profile", str(PROFILE_PATH), "--out", str(out)]), 0)
            self.assertTrue((out / "dashboard.html").is_file())
            cases = [json.loads(path.read_text(encoding="utf-8")) for path in out.glob("*.json") if path.name not in ("ops.json", "scan-index.json", "review-index.json")]
            self.assertTrue(cases)
            self.assertEqual({case["automated_action"] for case in cases}, {"none"})  # "Players banned: 0"
            dest = write_pages(build_demo(), Path(tmp) / "site")
            for name in images:
                self.assertTrue((dest / "img" / name).is_file(), name)
            try_page = (dest / "try.html").read_text(encoding="utf-8")
            index = (dest / "index.html").read_text(encoding="utf-8")
        tall_height = re.search(r'viewBox="0 0 600 (\d+)"', images["setup-quest-tall.svg"]).group(1)
        for page in (try_page, index):
            self.assertIn(f'srcset="img/setup-quest-tall.svg" width="600" height="{tall_height}"', page)
            self.assertIn('src="img/setup-quest.svg" width="1200" height="700" alt="The setup as a game quest.', page)
        self.assertIn("template=live_test.yml", try_page)
        self.assertNotIn("template=question.yml", try_page)


class ChallengeStatusTest(unittest.TestCase):
    """Until the consented human pilot and the research after it report, every public description of a challenge
    review says it is experimental. The scoring itself stays frozen, and case text is untouched: it is sealed."""

    def test_every_public_description_of_a_challenge_review_says_it_is_experimental(self):
        from fpsdet.pages import write_pages

        with tempfile.TemporaryDirectory() as tmp:
            dest = write_pages(build_demo(), tmp)
            pages = {name: (dest / name).read_text(encoding="utf-8") for name in ("index.html", "evidence.html", "scoring.html", "wire.html", "decoys.html")}
        texts = {**pages, "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
                 "docs/challenges.md": (ROOT / "docs" / "challenges.md").read_text(encoding="utf-8")}
        for name, text in texts.items():
            self.assertIn("Challenge reviews are experimental and not production-qualified.", text, name)
        self.assertIn("Label-selection confounding.", (ROOT / "docs" / "calibration.md").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
