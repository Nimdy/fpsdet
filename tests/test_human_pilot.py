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

    def test_amendment_1s_telemetry_stays_in_the_study_rows(self):
        """The body's distance and motion are study telemetry only: written by _study_row, reset by _begin, and
        never part of what fpsdet scores."""
        import re

        study = (GODOT / "scripts" / "study_server.gd").read_text(encoding="utf-8")
        functions = re.split(r"\n(?=func )", study)
        users = [part.split("(")[0] for part in functions[1:] if "motion_window" in part]
        self.assertEqual(users, ["func _begin", "func _study_row"])
        for key in ('"dist"', '"ps"', '"aw"', '"pw"', '"co"'):
            self.assertEqual([part.split("(")[0] for part in functions[1:] if key in part], ["func _study_row"], key)

    def test_the_controls_check_drives_the_unchanged_client(self):
        check = (GODOT / "scripts" / "controls_check_client.gd").read_text(encoding="utf-8")
        self.assertTrue(check.startswith('extends "res://scripts/study_client.gd"'))
        self.assertIn("Input.parse_input_event", check)
        for name in ("_keyboard", "_unhandled_input", "_physics_process", "input_cmd"):
            self.assertNotIn(f"func {name}(", check, "the check must not replace the controls it checks")
        for word in ("get_image", "save_png", "OS.execute", "DirAccess", "OS.get_environment"):
            self.assertNotIn(word, check)
        self.assertNotIn("controls_check", " ".join(harness().CODE), "the check is not part of a study session")


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
        # Amendment 2: a stop-on-first-finding design never prints an interval.
        self.assertFalse(hasattr(study, "interval"))

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

    def test_it_says_it_is_machines_and_keeps_the_code_it_was_made_with(self):
        """A historical record: bound to the study code at the commit that made it (amendment 1), never rewritten
        to match later code."""
        found = dry_run()
        self.assertEqual((found["format"], found["kind"]), ("fpsdet.human-pilot/1", "machine_standin"))
        self.assertIn("Not people", found["statement"])
        self.assertEqual(found["questionnaire"], "not asked: machine stand-ins")
        self.assertEqual(found["design"]["sha256"], harness().pilot.sha256_file(STUDY / "design.json"))
        self.assertEqual(found["digest"], harness().digest(found))
        record = amendment()["dry_run"]
        self.assertEqual(harness().pilot.sha256_file(STUDY / "dry-run" / "result.json"), record["result_sha256"])
        self.assertEqual((found["digest"], found["code"]), (record["digest"], record["made_with"]["code"]))
        now = harness().code_identity()
        self.assertEqual(set(now), set(found["code"]))
        changed = sorted(path for path in now if now[path] != found["code"][path])
        named = set(record["changed_by_this_amendment"]).union(*(json.loads(path.read_text(encoding="utf-8")).get("code_changed", [])
                                                                 for path in STUDY.glob("amendment-*.json")))
        self.assertLessEqual(set(changed), named, "only the files the amendments name may differ")

    def test_its_code_is_the_commit_it_names_when_history_is_available(self):
        import hashlib
        import subprocess

        from fpsdet.provenance import normalized_source

        made = amendment()["dry_run"]["made_with"]
        try:
            subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", made["commit"]], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("a shallow checkout: the commit is not here")
        for path, expected in made["code"].items():
            blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{made['commit']}:{path}"], check=True, capture_output=True).stdout
            self.assertEqual("sha256:" + hashlib.sha256(normalized_source(blob)).hexdigest(), expected, path)

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


def amendment() -> dict:
    return json.loads((STUDY / "amendment-1.json").read_text(encoding="utf-8"))


def amendment_2() -> dict:
    return json.loads((STUDY / "amendment-2.json").read_text(encoding="utf-8"))


class RegressionFixtureTest(unittest.TestCase):
    """dry-run/sessions/hp-8e9f0d72-angle_holding-1: a machine stand-in holding the doorway's frame, still, over
    a probe in the sealed room behind it. Under the current rule it is review-grade. It is kept permanently as
    a false-positive control: a future challenge detector must change this test to show the case is no longer
    review-grade, and why, never delete it."""

    FOLDER = STUDY / "dry-run" / "sessions" / "hp-8e9f0d72-angle_holding-1"

    def test_it_is_the_fixture_amendment_1_names(self):
        self.assertEqual(amendment()["regression_fixture"]["session"], self.FOLDER.relative_to(ROOT).as_posix())
        sample = next(row for row in dry_run()["samples"] if row["folder"] == self.FOLDER.relative_to(ROOT).as_posix())
        self.assertEqual(harness().pilot.sha256_file(self.FOLDER / "events.ndjson"), sample["events"])
        self.assertEqual(harness().pilot.sha256_file(self.FOLDER / "plan.json"), sample["plan"])

    def test_it_replays_to_review_grade_under_the_current_rule(self):
        import tempfile

        with tempfile.TemporaryDirectory() as work:
            cases = harness().score(self.FOLDER / "events.ndjson", self.FOLDER / "plan.json", Path(work))
        case = cases["hp-8e9f0d72"]
        self.assertEqual(case["problems"], [])
        self.assertEqual((case["decision"], case["kinds"]), ("review", ["occluded_motion_replay"]))
        followed = [row for row in case["challenges"] if row["status"] == "followed"]
        self.assertEqual(len(followed), 1)
        row = followed[0]
        self.assertEqual((row["tracked_samples"], round(row["total_ms"])), (56, 5500))
        self.assertEqual((row["verification"], row["verified_samples"]), ("per_sample", row["eligible_samples"]))

    def test_no_visible_bot_explains_it_and_the_aim_never_turned(self):
        worst = dry_run()["worst_case"]
        self.assertEqual((worst["participant"], worst["mode"], worst["room"]), ("hp-8e9f0d72", "angle_holding", "door_edge"))
        self.assertEqual(worst["explained_ms"], 0.0)
        self.assertEqual(worst["turn_rate_dps"], {"median": 0.0, "p95": 0.0})
        self.assertEqual(worst["episodes"], 3)


class AmendmentTest(unittest.TestCase):
    """Amendment 1, declared after the machine dry run and before any human session: it adds instruments and
    collection rules and changes nothing that decides a challenge."""

    def test_it_binds_the_design_and_keeps_every_frozen_value(self):
        found = amendment()
        self.assertEqual(found["format"], "fpsdet.human-pilot-amendment/1")
        self.assertEqual(found["amends"]["sha256"], harness().pilot.sha256_file(STUDY / "design.json"))
        self.assertIn("before any human session", found["declared"])
        self.assertFalse((STUDY / "result.json").exists())
        self.assertEqual(design()["frozen"]["episode_gap_ms"], 250)
        self.assertEqual((design()["frozen"]["hidden_track_min_samples"], design()["frozen"]["hidden_track_min_ms"]), (8, 1200))
        self.assertEqual(found["metrics_added"]["frozen"], {"window_ms": 250, "still_deg_per_s": 2.0, "co_motion_deg": 45.0})
        self.assertEqual(found["metrics_added"]["frozen"]["window_ms"], design()["frozen"]["episode_gap_ms"])
        self.assertIn("never enter scoring", found["metrics_added"]["use"])

    def test_the_questions_are_the_designs_and_the_controls_question(self):
        questions = harness().questions()
        self.assertEqual(questions[:4], design()["questionnaire"])
        self.assertEqual(questions[4:], ["Did the controls behave normally?", "Did you try to find, guess or follow the hidden probes?"])

        replies = iter(["n", "", "n", "a hum near the door", "n", "", "y", "", "y", "", "n", ""])
        with __import__("tempfile").TemporaryDirectory() as folder:
            run = Path(folder)
            (run / "private").mkdir()
            found = harness().questionnaire(design(), "human", lambda prompt: next(replies), run)
            self.assertEqual(found["comments"], 1)
            self.assertIn("a hum", (run / "private" / "comments.json").read_text(encoding="utf-8"))
        self.assertNotIn("a hum", json.dumps(found), "comments stay in the private folder")
        self.assertEqual(len(found["answers"]), 6)

    def test_practice_is_short_unscored_and_kept_apart(self):
        import argparse
        import tempfile

        rules = amendment()["practice"]
        self.assertEqual((rules["length_ms"], rules["mode"], rules["challenges"], rules["min_client_fps"]), (90000, "free", 0, 30))
        with tempfile.TemporaryDirectory() as folder:
            study = Path(folder) / "study"
            (study / "hp-12345678").mkdir(parents=True)
            (study / "hp-12345678" / "participant.json").write_text(json.dumps({"participant": "hp-12345678", "consent": True}))
            for inside in (study, study / "practice"):
                with self.assertRaises(SystemExit):
                    harness().cmd_practice(argparse.Namespace(data=str(inside), study=str(study), participant="hp-12345678", godot="godot",
                                                              port=24800, bind="127.0.0.1", remote=False))

    def test_staging_waits_for_four_completed_and_stops_at_the_maximum(self):
        """Amendment 2: four in play until four have completed every mode with a valid session; a participant who
        withdraws, is discontinued or fails practice frees a place; then at most twelve."""
        import tempfile

        study = harness()
        modes = design()["sessions"]["modes"]
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)

            def enroll(name: str, status: str = "practice_passed", valid_modes: tuple = ()) -> None:
                (data / name).mkdir()
                (data / name / "participant.json").write_text(json.dumps({"participant": name, "kind": "human", "consent": True, "status": status}))
                for mode in valid_modes:
                    play(name, mode, True)

            def play(name: str, mode: str, ok: bool) -> None:
                count = len(list((data / name).glob(f"{mode}-*"))) + 1
                (data / name / f"{mode}-{count}").mkdir()
                (data / name / f"{mode}-{count}" / "session.json").write_text(json.dumps(
                    {"mode": mode, "validity": {"valid": ok, "reason": None if ok else "technical_failure"}}))

            for index in range(4):
                enroll(f"hp-0000000{index}")
            self.assertIn("Staging", study.staging(data))
            # One leaves: a replacement may join, and the audit record stays.
            study.save_participant(data, {"participant": "hp-00000000", "kind": "human", "status": "withdrawn", "sessions_deleted": True,
                                          "reason_class": "participant_request"})
            self.assertIsNone(study.staging(data))
            enroll("hp-00000009", status="practice_failed")
            self.assertIsNone(study.staging(data), "a failed practice frees its place")
            enroll("hp-00000004")
            self.assertIn("Staging", study.staging(data))
            # An invalid session is not a completed mode; a valid one played again is.
            for name in ("hp-00000001", "hp-00000002", "hp-00000003"):
                for mode in modes:
                    play(name, mode, True)
            play("hp-00000004", modes[0], False)
            self.assertEqual(study.state(data, study.load_participant(data, "hp-00000004")), "practice_passed")
            self.assertIn("Staging", study.staging(data))
            for mode in modes:
                play("hp-00000004", mode, True)
            self.assertEqual([study.state(data, row) for row in study.humans(data)].count("completed"), 4)
            self.assertIsNone(study.staging(data))
            for index in range(10, 18):
                enroll(f"hp-000000{index}")
            self.assertIn("maximum", study.staging(data))

    def test_a_review_grade_stop_cannot_be_cleared(self):
        import argparse
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            study.stop(data, [f"hp-12345678 angle_holding-1: {study.FALSIFYING}: stop collection"])
            with self.assertRaises(SystemExit):
                study.cmd_clear_stop(argparse.Namespace(data=str(data), reason="looked at it"))
            self.assertTrue((data / "STOP").exists())
            (data / "STOP").unlink()
            # A crossing on a session already invalid before scoring still stops, but it does not answer the primary question.
            study.stop(data, ["hp-12345678 combat-1: review-grade challenge evidence on a session already invalid (protocol_deviation): investigate"])
            self.assertEqual(study.cmd_clear_stop(argparse.Namespace(data=str(data), reason="the participant said they hunted the probe")), 0)
            study.stop(data, ["hp-12345678 free-1: an address or a machine identity in a kept session file: public/server.log: the hostname"])
            self.assertEqual(study.cmd_clear_stop(argparse.Namespace(data=str(data), reason="a test fixture path; removed")), 0)
            self.assertFalse((data / "STOP").exists())

    def test_motion_metrics_split_overlap_four_ways(self):
        study = harness()
        self.assertEqual(study.motion_metrics([{"t": 0, "in": True, "turn": 0.0}], []), {}, "telemetry from before the amendment has none")

        def rows(aim: float, probe: float, co: float | None) -> list[dict]:
            return [{"t": i * 17, "in": True, "turn": aim, "dist": 6.0, "ps": 1.0, "aw": aim, "pw": probe, "co": co} for i in range(60)]

        cases = {"both_still_ms": rows(0.5, 0.0, None), "stationary_aim_moving_probe_ms": rows(0.5, 6.0, 120.0),
                 "co_moving_ms": rows(6.0, 6.0, 20.0), "aim_moving_otherwise_ms": rows(6.0, 6.0, 120.0)}
        for expected, found in cases.items():
            metrics = study.motion_metrics(found, found)
            self.assertEqual({part: metrics[part] for part in study.MOTION_PARTS}, {part: 1000.0 if part == expected else 0.0 for part in study.MOTION_PARTS}, expected)
        self.assertEqual(study.motion_metrics(rows(6.0, 0.0, None), rows(6.0, 0.0, None))["aim_moving_otherwise_ms"], 1000.0, "sweeping across a still body")



class Amendment2Test(unittest.TestCase):
    """Amendment 2, declared before anyone enrolled: the collection protocol hardened, and nothing that decides a
    challenge changed."""

    def enrolled(self, data: Path) -> str:
        import argparse
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            harness().cmd_enroll(argparse.Namespace(data=str(data), agree=True, publish=False, kind="human"))
        return sorted(path.parent.name for path in data.glob("hp-*/participant.json"))[-1]

    def test_it_binds_both_earlier_documents_and_keeps_the_frozen_values(self):
        found, study = amendment_2(), harness()
        self.assertEqual((found["format"], found["amendment"]), ("fpsdet.human-pilot-amendment/1", 2))
        self.assertEqual(found["amends"]["design"]["sha256"], study.pilot.sha256_file(STUDY / "design.json"))
        self.assertEqual(found["amends"]["amendment_1"]["sha256"], study.pilot.sha256_file(STUDY / "amendment-1.json"))
        self.assertIn("before any participant enrolled", found["declared"])
        self.assertEqual(found["consent"]["enrolled_under_version_1"], 0)
        self.assertFalse((STUDY / "result.json").exists())
        self.assertEqual((design()["frozen"]["hidden_track_min_samples"], design()["frozen"]["hidden_track_min_ms"], design()["frozen"]["episode_gap_ms"]),
                         (8, 1200, 250))
        self.assertEqual(amendment()["metrics_added"]["frozen"], {"window_ms": 250, "still_deg_per_s": 2.0, "co_motion_deg": 45.0})
        self.assertEqual(found["validity"]["reasons"], ["controls_failure", "participant_withdrew", "technical_failure", "visibility_failure",
                                                         "audio_failure", "protocol_deviation", "privacy_failure"])
        self.assertIn("never a reason", found["validity"]["rule"])

    def test_consent_version_2_says_what_participants_now_need_to_know(self):
        text = (STUDY / "CONSENT.md").read_text(encoding="utf-8")
        self.assertIn("*Consent notice, version 2.*", text)
        self.assertIn("Please don't try to find, guess or follow them: the study is about ordinary play.", text)
        self.assertIn("six yes-or-no questions", text)
        self.assertIn("did you try to find the hidden probes", text)
        self.assertIn("totals already published cannot be recalled", text)
        client = (GODOT / "scripts" / "study_client.gd").read_text(encoding="utf-8")
        self.assertIn("Please don't try to find them: just play the round as asked.", client)

    def test_enrollment_binds_what_the_participant_agreed_to(self):
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            participant = self.enrolled(data)
            record = study.load_participant(data, participant)
        self.assertEqual(record["bindings"], study.bindings())
        self.assertEqual(set(record["bindings"]), {"consent", "design", "amendment_1", "amendment_2", "amendment_3"})
        self.assertEqual((record["status"], record["history"][0]["status"]), ("enrolled", "enrolled"))

    def test_a_session_needs_a_passed_practice_the_next_mode_and_the_same_bindings(self):
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            participant = self.enrolled(data)
            order = study.session_order(participant)

            def refused(mode: str) -> str:
                with self.assertRaises(SystemExit) as caught:
                    study.run_session("no-engine-needed", data, participant, mode, 24800, "human")
                return str(caught.exception)

            self.assertIn("needs a passed practice", refused(order[0]))
            record = study.load_participant(data, participant)
            study.set_status(record, "practice_passed")
            study.save_participant(data, record)
            self.assertIn(f"The next session for {participant} is {order[0]}", refused(order[1]))
            record["bindings"]["consent"] = "sha256:" + "0" * 64
            study.save_participant(data, record)
            self.assertIn("a new study version", refused(order[0]))
            self.assertEqual(sorted(path.name for path in (data / participant).iterdir()), ["participant.json"], "nothing ran")

    def test_practice_passes_only_when_the_controls_work(self):
        study = harness()
        confirms = study.practice_confirms()
        self.assertEqual(confirms[1], amendment_2()["practice_gate"]["adds_confirm"])
        everything = {item: True for item in confirms}
        self.assertTrue(study.practice_ready(everything, 60))
        self.assertFalse(study.practice_ready(everything, 29), "under 30 frames a second")
        self.assertFalse(study.practice_ready(everything, None))
        self.assertFalse(study.practice_ready({**everything, confirms[1]: False}, 60), "a captured mouse must let go with Esc")
        self.assertTrue(study.practice_ready({**everything, confirms[0]: False, confirms[1]: False}, 60), "no mouse: the arrow keys")
        for item in confirms[2:]:
            self.assertFalse(study.practice_ready({**everything, item: False}, 60), item)

    def test_withdrawal_deletes_the_data_and_keeps_only_that_someone_withdrew(self):
        import argparse
        import contextlib
        import io
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            data, practice = Path(folder) / "study", Path(folder) / "practice"
            data.mkdir()
            participant = self.enrolled(data)
            for name in ("free-1", "combat-1"):
                (data / participant / name / "private").mkdir(parents=True)
                (data / participant / name / "private" / "comments.json").write_text('{"q": "a comment"}')
            (practice / participant / "practice-1").mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                study.cmd_withdraw(argparse.Namespace(data=str(data), participant=participant, reason="participant_request", practice=str(practice)))
            self.assertEqual(sorted(path.name for path in (data / participant).iterdir()), ["participant.json"])
            self.assertFalse((practice / participant).exists())
            kept = study.load_participant(data, participant)
            self.assertEqual(set(kept), {"participant", "kind", "status", "sessions_deleted", "reason_class", "withdrawn", "bindings"})
            self.assertEqual((kept["status"], kept["sessions_deleted"], kept["reason_class"]), ("withdrawn", True, "participant_request"))
            # Unable to continue: the sessions stay, the place is freed.
            other = self.enrolled(data)
            (data / other / "free-1").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                study.cmd_withdraw(argparse.Namespace(data=str(data), participant=other, reason="unable_to_continue", practice=None))
            self.assertTrue((data / other / "free-1").exists())
            self.assertEqual(study.state(data, study.load_participant(data, other)), "discontinued")

    def test_validity_is_fixed_before_the_score_and_a_crossing_is_never_a_reason(self):
        study = harness()
        question = amendment_2()["validity"]["question_added"]
        asked = {"asked": True, "answers": {question: False}}
        self.assertEqual(study.declare_validity("machine_standin", asked, None), {"valid": True, "reason": None, "by": "machine"})
        self.assertEqual(study.declare_validity("human", {"asked": True, "answers": {question: True}}, None)["reason"], "protocol_deviation")
        self.assertTrue(study.declare_validity("human", asked, lambda prompt: "y")["valid"])
        replies = iter(["n", "9", "3"])
        declared = study.declare_validity("human", asked, lambda prompt: next(replies))
        self.assertEqual(declared, {"valid": False, "reason": "technical_failure", "by": "operator"})
        for reason in study.validity_reasons():
            self.assertNotIn("review", reason)
            self.assertNotIn("cross", reason)
        # declare_validity runs before post_session, which is where the score first exists.
        source = (STUDY / "study.py").read_text(encoding="utf-8")
        self.assertLess(source.index("declared = declare_validity(kind, answers, ask)"), source.index("return post_session(run, plan, secret"))

    def test_the_privacy_sweep_covers_the_whole_session_tree(self):
        import getpass
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "free-1"
            for path, text in (("server.out", f"loading {Path.home()}/project"), ("godot-user/godot/logs/godot.log", f"user {getpass.getuser()}"),
                               ("cases-live/p.json", f"{Path.home()}/cases"), ("public/events.ndjson", '{"t_ms": 100}'),
                               ("private/study.ndjson", '{"dist": 13.32}')):
                (run / path).parent.mkdir(parents=True, exist_ok=True)
                (run / path).write_text(text, encoding="utf-8")
            sweep = study.privacy_sweep(run)
            self.assertEqual(sweep["operational_deleted"], ["cases-live", "godot-user", "server.out"])
            self.assertGreaterEqual(sweep["operational_identifiers"], 3)
            self.assertEqual(sweep["kept_hits"], [])
            self.assertEqual(sorted(path.relative_to(run).as_posix() for path in run.rglob("*") if path.is_file()),
                             ["private/study.ndjson", "public/events.ndjson"])
            (run / "private" / "comments.json").write_text('{"q": "my box is 192.168.1.20"}', encoding="utf-8")
            self.assertEqual(study.privacy_sweep(run)["kept_hits"], ["private/comments.json: an IP address"])

    def test_the_primary_question_allows_only_the_statistics_it_can_carry(self):
        study = harness()
        units = {"participants": 8, "sessions": 56, "challenges": 224}
        finding = study.endpoint([{"participant": "hp-1", "session": "angle_holding-1", "mode": "angle_holding"}], True, units)
        self.assertEqual(finding["answer"], "yes")
        self.assertNotIn("upper_95_if_none", finding)
        self.assertIn("no rate and no interval", finding["statistics"])
        early = study.endpoint([], False, units)
        self.assertTrue(early["answer"].startswith("not yet"))
        self.assertNotIn("upper_95_if_none", early)
        clean = study.endpoint([], True, units)
        self.assertEqual(clean["answer"], "no")
        self.assertEqual(clean["upper_95_if_none"]["participants"], {"of": 8, "bound": study.upper_zero(8)})
        self.assertIn("continuation rule", clean["statistics"])

    def test_the_analysis_refuses_participants_under_different_protocols(self):
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            first = self.enrolled(data)
            self.enrolled(data)
            study.analyze(data, "human")  # the same bindings: fine, with nothing played yet
            record = study.load_participant(data, first)
            record["bindings"]["consent"] = "sha256:" + "1" * 64
            study.save_participant(data, record)
            with self.assertRaises(SystemExit):
                study.analyze(data, "human")

    def test_the_motion_split_at_its_exact_boundaries(self):
        study = harness()

        def part(aw, pw, co):
            row = {"t": 0, "in": True, "turn": aw, "dist": 10.0, "ps": 0.0, "aw": aw, "pw": pw, "co": co}
            metrics = study.motion_metrics([row], [row])
            return next(name for name in study.MOTION_PARTS if metrics[name] > 0)

        self.assertEqual(part(1.9, 1.9, None), "both_still_ms")
        self.assertEqual(part(1.9, 2.0, None), "stationary_aim_moving_probe_ms")
        self.assertEqual(part(2.0, 1.9, None), "aim_moving_otherwise_ms")
        self.assertEqual(part(2.0, 2.0, 45.0), "co_moving_ms")
        self.assertEqual(part(2.0, 2.0, 45.1), "aim_moving_otherwise_ms")
        self.assertEqual(part(2.0, 2.0, None), "aim_moving_otherwise_ms")
        split = amendment_2()["motion_definitions"]["split"]
        self.assertEqual(split["co_moving"], "aw >= 2.0 and pw >= 2.0 and co is not null and co <= 45.0")
        self.assertEqual(amendment()["metrics_added"]["frozen"]["still_deg_per_s"], 2.0)


class Amendment3Test(unittest.TestCase):
    """Amendment 3, declared before anyone enrolled: what a final stop means once its participant asks for deletion."""

    def test_it_binds_the_earlier_documents_and_leaves_consent_alone(self):
        found, study = json.loads((STUDY / "amendment-3.json").read_text(encoding="utf-8")), harness()
        self.assertEqual((found["format"], found["amendment"]), ("fpsdet.human-pilot-amendment/1", 3))
        for name, path in (("design", "design.json"), ("amendment_1", "amendment-1.json"), ("amendment_2", "amendment-2.json")):
            self.assertEqual(found["amends"][name]["sha256"], study.pilot.sha256_file(STUDY / path), name)
        self.assertIn("before any participant enrolled", found["declared"])
        self.assertIn("*Consent notice, version 2.*", (STUDY / "CONSENT.md").read_text(encoding="utf-8"))
        self.assertEqual(study.withdrawn_note(), found["rule"]["note"])

    def test_deleting_a_final_stops_session_keeps_collection_ended_and_unnames_them(self):
        import argparse
        import contextlib
        import io
        import tempfile

        study = harness()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            with contextlib.redirect_stdout(io.StringIO()):
                study.cmd_enroll(argparse.Namespace(data=str(data), agree=True, publish=False, kind="human"))
                study.cmd_enroll(argparse.Namespace(data=str(data), agree=True, publish=False, kind="human"))
            first, second = sorted(path.parent.name for path in data.glob("hp-*/participant.json"))
            (data / first / "angle_holding-1").mkdir()
            (data / first / "angle_holding-1" / "session.json").write_text(json.dumps({"participant": first, "kind": "human", "mode": "angle_holding",
                                                                                     "session": "angle_holding-1", "review_grade": True}))
            study.stop(data, [f"{first} angle_holding-1: {study.FALSIFYING}: stop collection", f"{first} angle_holding-1: a packet or graph does not verify"])
            with open(data / "stops.log", "w", encoding="utf-8") as log:
                log.write(json.dumps({"stopped": f"{second} free-1: an address in a kept file", "cleared": "2026-10-06",
                                      "investigation": f"{second}'s comment held an address; removed"}) + "\n")
            with contextlib.redirect_stdout(io.StringIO()):
                study.cmd_withdraw(argparse.Namespace(data=str(data), participant=first, reason="participant_request", practice=None))
                study.cmd_withdraw(argparse.Namespace(data=str(data), participant=second, reason="participant_request", practice=None))
            stop_text = (data / "STOP").read_text(encoding="utf-8")
            log_text = (data / "stops.log").read_text(encoding="utf-8")
            for name in (first, second):
                self.assertNotIn(name, stop_text)
                self.assertNotIn(name, log_text)
            lines = stop_text.splitlines()
            self.assertIn(study.FALSIFYING, lines[0])
            self.assertTrue(lines[0].endswith(f"[{study.withdrawn_note()}]"))
            self.assertNotIn(study.withdrawn_note(), lines[1], "only a final stop is marked withdrawn evidence")
            # Collection stays ended.
            with self.assertRaises(SystemExit):
                study.cmd_clear_stop(argparse.Namespace(data=str(data), reason="the data is gone"))
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                study.cmd_enroll(argparse.Namespace(data=str(data), agree=True, publish=False, kind="human"))
            # The answer is neither yes nor no, and nothing from the deleted session is reported.
            body = study.analyze(data, "human")
        self.assertEqual(body["primary"]["answer"], json.loads((STUDY / "amendment-3.json").read_text(encoding="utf-8"))["rule"]["primary_answer"])
        self.assertNotIn("upper_95_if_none", body["primary"])
        self.assertNotIn("findings", body["primary"])
        self.assertEqual((body["units"]["sessions"], body["participants"]["withdrawn"]), (0, 2))

    def test_a_retained_finding_answers_yes_before_a_withdrawn_one(self):
        study = harness()
        units = {"participants": 4, "sessions": 28, "challenges": 112}
        retained = [{"participant": "hp-2", "session": "stress-1", "mode": "stress"}]
        self.assertEqual(study.endpoint(retained, False, units, withdrawn=True)["answer"], "yes")
        self.assertTrue(study.endpoint([], True, units, withdrawn=True)["answer"].startswith("indeterminate"))
        self.assertEqual(study.endpoint([], True, units, withdrawn=False)["answer"], "no")

if __name__ == "__main__":
    unittest.main()
