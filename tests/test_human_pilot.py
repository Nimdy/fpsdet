"""The consented honest-human pilot (examples/human-pilot): its design, fixed before any human played."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "examples" / "human-pilot"


def design() -> dict:
    return json.loads((STUDY / "design.json").read_text(encoding="utf-8"))


class DesignTest(unittest.TestCase):
    """What the study measures and how, frozen before collection: a later change is a new study version."""

    def test_the_design_is_complete(self):
        found = design()
        self.assertEqual((found["format"], found["version"]), ("fpsdet.human-pilot-design/1", 1))
        self.assertIn("before any human session", found["declared"])
        self.assertEqual(found["sessions"]["modes"], ["free", "combat", "angle_holding", "sweep_search", "tracking", "high_motion", "stress"])
        self.assertEqual(set(found["sessions"]["instructions"]), set(found["sessions"]["modes"]))
        self.assertGreaterEqual(found["participants"]["target"], found["participants"]["minimum_to_report"])
        for key in ("primary_outcome", "analysis", "exclusions", "stop_conditions", "questionnaire", "data", "topology"):
            self.assertTrue(found[key], key)
        self.assertIn("not a false-positive rate", " ".join(found["analysis"]))

    def test_the_schedule_is_the_real_planners_and_the_thresholds_are_p12s(self):
        from fpsdet.challenge import Budget
        from fpsdet.parse import load_profile

        found = design()
        schedule = found["challenges"]["schedule"]
        self.assertEqual(Budget(**schedule).problems(), [])
        self.assertLessEqual(schedule["to_ms"], found["sessions"]["length_ms"])
        self.assertEqual((found["challenges"]["type"], found["challenges"]["version"]), ("occluded_motion_replay", 2))
        study = load_profile(ROOT / found["frozen"]["profile"])
        pilot = load_profile(ROOT / "examples" / "pilot" / "pilot.json")
        for name in ("hidden_track_min_ms", "hidden_track_min_samples", "hidden_grace_ms", "knowledge_channels"):
            self.assertEqual(getattr(study, name), getattr(pilot, name), name)
        self.assertEqual((found["frozen"]["hidden_track_min_ms"], found["frozen"]["hidden_track_min_samples"]), (study.hidden_track_min_ms, study.hidden_track_min_samples))
        self.assertEqual(found["frozen"]["episode_gap_ms"], 250)
        self.assertEqual(set(found["challenges"]["rooms"]), {"door_edge", "corner", "chokepoint", "long_wall"})

    def test_the_consent_notice_says_what_participants_must_know(self):
        text = (STUDY / "CONSENT.md").read_text(encoding="utf-8")
        for words in ("Hidden probes exist", "Gameplay is recorded by the game server", "Nothing else is recorded", "No accounts",
                      "You are a random code", "It is voluntary", "only if you tick the separate box", "deleted"):
            self.assertIn(words, text)
        never = design()["data"]["never_recorded"]
        for item in ("names", "emails", "IP addresses", "memory"):
            self.assertIn(item, never)


def harness():
    import importlib.util
    import sys

    if "study_harness" not in sys.modules:
        spec = importlib.util.spec_from_file_location("study_harness", STUDY / "study.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["study_harness"] = module
        spec.loader.exec_module(module)
    return sys.modules["study_harness"]


GODOT = ROOT / "examples" / "pilot" / "godot"


class StudyCodeTest(unittest.TestCase):
    """The study server is P12's in every way that decides a challenge, and the client records nothing it should not."""

    def test_the_study_server_keeps_p12s_challenge_and_knowledge_rules(self):
        import re

        p12 = (GODOT / "scripts" / "server.gd").read_text(encoding="utf-8")
        study = (GODOT / "scripts" / "study_server.gd").read_text(encoding="utf-8")
        for name in ("CONE_DEG", "HEARING_RADIUS", "SOUND_MEMORY_TICKS", "STEP_EVERY_TICKS", "FIRE_COOLDOWN_TICKS", "RESPONDER_LAG_TICKS", "VISION_MARGIN", "DAMAGE"):
            pattern = re.compile(rf"^const {name} := ([^#\n]+)", re.M)
            self.assertEqual(pattern.search(study).group(1).strip(), pattern.search(p12).group(1).strip(), name)

        def body(source: str, name: str) -> str:
            start = source.index(f"func {name}(")
            end = source.index("\n\n\n", start)
            return source[start:end]

        for name in ("_vision", "_body_points", "_audio", "_in_cone", "_worst", "_challenge_fields"):
            self.assertEqual(body(study, name), body(p12, name), name)
        self.assertIn('accumulator["vision"] = _worst(', study)
        self.assertIn('_end("perceivable")', study)

    def test_the_server_never_binds_a_public_address_or_writes_one(self):
        study = (GODOT / "scripts" / "study_server.gd").read_text(encoding="utf-8")
        self.assertIn('options.get("bind", "127.0.0.1")', study)
        self.assertIn("func _private(address: String)", study)
        self.assertNotIn("get_peer_address", study)
        self.assertNotIn("get_remote_address", study)

    def test_the_client_captures_no_screen_and_reads_nothing_else(self):
        client = (GODOT / "scripts" / "study_client.gd").read_text(encoding="utf-8")
        for word in ("get_image", "save_png", "OS.execute", "DirAccess", "OS.get_environment", "get_unique_id", "OS.get_name"):
            self.assertNotIn(word, client)
        self.assertIn("Press Y to agree and start", client)
        for word in ("Hidden test objects exist", "records your movement, aim and shots in this game, and nothing else"):
            self.assertIn(word, client)

    def test_the_p12_pilot_files_are_untouched(self):
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location("pilot_for_study_test", ROOT / "examples" / "pilot" / "pilot.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["pilot_for_study_test"] = module
        spec.loader.exec_module(module)
        result = json.loads((ROOT / "examples" / "pilot" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(module.code_identity(), result["code"])


class MetricsTest(unittest.TestCase):
    """The study's own numbers, defined before collection."""

    def test_episodes_join_across_short_gaps_only(self):
        study = harness()
        tick = 1000 / 60
        rows = [{"t": round(i * tick), "in": i in (0, 1, 2, 10, 11, 40)} for i in range(50)]
        found = study.episodes(rows, 250)  # 117 ms out of the cone between ticks 2 and 10 joins them; 467 ms before tick 40 does not
        self.assertEqual([(first, last) for first, last in found], [(0, 183), (667, 667)])
        rows = [{"t": round(i * tick), "in": i in (0, 14)} for i in range(20)]  # 216 ms out of the cone: one episode
        self.assertEqual(len(study.episodes(rows, 250)), 1)
        rows = [{"t": round(i * tick), "in": i in (0, 17)} for i in range(20)]  # 266 ms out: two
        self.assertEqual(len(study.episodes(rows, 250)), 2)

    def test_bounds_are_exact_and_say_what_they_are(self):
        study = harness()
        self.assertAlmostEqual(study.upper_zero(8), 1 - 0.05 ** (1 / 8), places=4)
        self.assertIsNone(study.upper_zero(0))
        low, high = study.interval(1, 8)
        self.assertLess(low, 1 / 8)
        self.assertGreater(high, 1 / 8)
        self.assertEqual(study.interval(0, 10)[0], 0.0)

    def test_session_order_is_fixed_by_the_participant(self):
        study = harness()
        order = study.session_order("hp-12345678")
        self.assertEqual(order, study.session_order("hp-12345678"))
        self.assertEqual((order[0], order[-1]), ("free", "stress"))
        self.assertEqual(sorted(order), sorted(design()["sessions"]["modes"]))

    def test_personal_data_is_found_where_it_must_never_be(self):
        import socket
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            clean = Path(folder) / "clean.ndjson"
            clean.write_text('{"player_id":"hp-12345678","t_ms":100}\n', encoding="utf-8")
            dirty = Path(folder) / "dirty.log"
            dirty.write_text(f'{{"peer":"192.168.1.20","host":"{socket.gethostname()}"}}\n', encoding="utf-8")
            self.assertEqual(study.personal_data([clean]), [])
            self.assertEqual(len(study.personal_data([dirty])), 2)


def dry_run() -> dict:
    return json.loads((STUDY / "dry-run" / "result.json").read_text(encoding="utf-8"))


class DryRunTest(unittest.TestCase):
    """examples/human-pilot/dry-run: machine stand-ins that qualify the instruments before any person plays.
    Never people, never counted as human, and what they found is recorded, not hidden."""

    def test_it_says_it_is_machines_and_was_made_by_this_code(self):
        found = dry_run()
        self.assertEqual((found["format"], found["kind"]), ("fpsdet.human-pilot/1", "machine_standin"))
        self.assertIn("Not people", found["statement"])
        self.assertEqual(found["questionnaire"], "not asked: machine stand-ins")
        self.assertEqual(found["code"], harness().code_identity())
        self.assertEqual(found["design"]["sha256"], harness().pilot.sha256_file(STUDY / "design.json"))
        self.assertEqual(found["digest"], harness().digest(found))

    def test_every_runtime_check_held(self):
        runtime = dry_run()["runtime"]
        self.assertEqual(runtime["verdicts"]["known"], 0)
        self.assertEqual(runtime["verdicts"]["unchecked"], 0)
        self.assertEqual(runtime["live_vs_offline"], ["identical"])
        self.assertTrue(runtime["packets_and_graphs_verify"])
        self.assertEqual(runtime["realization"], ["reproduced"])
        self.assertEqual((runtime["secret_leaks"], runtime["personal_data"]), (0, 0))

    def test_the_strongest_honest_style_case_is_recorded_against_the_bar(self):
        found = dry_run()
        worst = found["worst_case"]
        self.assertEqual(worst["counted_ms"], found["distributions"]["counted_ms"]["max"])
        self.assertEqual(worst["against_the_bar"]["crossed"], worst["status"] == "followed")
        crossed = found["review_grade"]
        self.assertEqual(crossed["sessions"], sum(1 for row in found["sessions"] if row["review_grade"] and row["kind"] == "machine_standin"))
        self.assertGreaterEqual(crossed["challenges"], crossed["sessions"])
        follower = found["controlled_follower"]
        self.assertEqual(follower["followed"], follower["challenges"])
        self.assertGreater(follower["counted_ms"]["median"], worst["counted_ms"])

    def test_the_samples_replay_offline(self):
        self.assertEqual(harness().verify_samples(STUDY / "dry-run" / "result.json"), [])
        self.assertEqual(len(dry_run()["samples"]), 2)

    def test_the_documented_numbers_are_the_artifacts(self):
        found = dry_run()
        doc = (ROOT / "docs" / "human-pilot.md").read_text(encoding="utf-8")
        counted = found["distributions"]["counted_ms"]
        worst = found["worst_case"]
        self.assertIn(f"median {counted['median']:.0f}, 95th percentile {counted['p95']:.0f}, max {counted['max']:,.0f}", doc)
        follower = found["controlled_follower"]
        self.assertIn(f"median {follower['counted_ms']['median']:,.0f}, max {follower['counted_ms']['max']:,.0f}", doc)
        self.assertIn(f"median {follower['longest_episode_ms']['median']:,.0f} ms", doc)
        self.assertIn(f"median {found['distributions']['longest_episode_ms']['median']:.0f} ms, max {found['distributions']['longest_episode_ms']['max']:,.0f} ms", doc)
        self.assertIn(f"{found['review_grade']['challenges']} of {found['units']['challenges']}", doc)
        self.assertIn(f"{worst['counted_moments']} counted moments and {worst['counted_ms']:,.0f} ms", doc)
        share = round(found["explained"]["explained_by_visible_bot_ms"] / found["explained"]["overlap_ms"] * 100)
        self.assertIn(f"Visible bots explained {share}% of all overlap", doc)
        self.assertIn("No person has played yet, and there are no human results", doc)

    def test_machine_results_never_become_the_human_addendum(self):
        from fpsdet import benchmark as bm

        with self.assertRaises(bm.BenchmarkError):
            bm.human_addendum_from(dry_run(), "sha256:" + "0" * 64)
        self.assertFalse((ROOT / "benchmark" / "addenda" / "p13-consented-human-pilot.json").exists())
        self.assertFalse((STUDY / "result.json").exists(), "no human result exists until people have played")


if __name__ == "__main__":
    unittest.main()
