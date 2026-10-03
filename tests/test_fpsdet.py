"""Behavior locks for gear rules, human ceilings, glitches, reports, and the AI hook."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from fpsdet.ai_triage import redact_case, triage_case
from fpsdet.baseline import CohortTable, build_cohorts
from fpsdet.lake import ingest_lines, read_lines
from fpsdet.models import (
    ACTIONS,
    Case,
    Event,
    ExtraMetric,
    ExtraObs,
    GameProfile,
    HistoryWindow,
    PlayerRecord,
    RecoilSummary,
    WeaponRule,
    WeaponSummary,
)
from fpsdet.parse import ParseError, load_profile, parse_event
from fpsdet.persist import case_to_dict
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
    pearson,
    poison_alarms,
    smoothness_break,
    wire_break,
)
from fpsdet.statsutil import percentile, wilson_lower
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
        stored = cheater.seal

        def record(player_id: str, party: str, contacts: list[tuple[int, str]]) -> PlayerRecord:
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
            [cheater, fast, voiced, alone],
            [
                record("wall", "stack", [(stamp, "mover-1") for stamp in stamps]),
                record("friend", "stack", [(stamp + 40, "mover-1") for stamp in stamps]),
                record("callout", "stack", [(stamp + 800, "mover-1") for stamp in stamps]),
                record("solo", "other", [(stamp + 40, "mover-1") for stamp in stamps]),
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


if __name__ == "__main__":
    unittest.main()
