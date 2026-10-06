"""The live challenge pilot (examples/pilot): what qualifies it, declared before any live run, and the
captured telemetry checked offline against it."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "examples" / "pilot"


def qualification() -> dict:
    return json.loads((PILOT / "qualification.json").read_text(encoding="utf-8"))


class QualificationTest(unittest.TestCase):
    """The scenarios and their expected outcomes are fixed in the repository, so a live run is judged
    against what was declared, never against what it happened to show."""

    def test_every_required_scenario_is_declared_with_an_outcome(self):
        found = qualification()
        self.assertEqual(found["format"], "fpsdet.pilot-qualification/1")
        scenarios = {row["id"]: row["expected"] for row in found["scenarios"]}
        self.assertEqual(set(scenarios), {"honest", "accidental_crossing", "exposed_vision", "exposed_audio", "missing_audio_channel",
                                          "contradictory_channels", "visible_enemy_cover", "illicit_follower"})
        for name, expected in scenarios.items():
            self.assertIn(expected["status"], ("followed", "not_followed", "abstained"), name)
            self.assertEqual(expected["evidence"], expected["status"] == "followed", name)
        self.assertEqual({name for name, expected in scenarios.items() if expected["evidence"]}, {"illicit_follower"})
        causes = {scenarios[name].get("cause") for name in ("exposed_vision", "exposed_audio", "missing_audio_channel", "contradictory_channels")}
        self.assertEqual(causes, {"seen", "heard", "unchecked", "conflict"})

    def test_every_failure_is_declared_safe(self):
        failures = {row["id"] for row in qualification()["failures"]}
        self.assertEqual(failures, {"missing_plan", "unknown_challenge_id", "wrong_player", "wrong_match", "stale_window", "duplicate_events",
                                    "malformed_channel_state", "visible_unexpectedly", "audible_unexpectedly", "secret_unavailable"})

    def test_the_engine_is_pinned_and_the_challenge_is_the_verified_version(self):
        found = qualification()
        self.assertRegex(found["engine"]["sha512"], r"^[0-9a-f]{128}$")
        self.assertTrue(found["engine"]["download"].startswith("https://github.com/godotengine/godot/releases/download/"))
        self.assertEqual(found["challenge"], {"type": "occluded_motion_replay", "version": 2})
        profile = json.loads((ROOT / found["profile"]).read_text(encoding="utf-8"))
        self.assertEqual(profile["knowledge_channels"], ["vision", "audio"])


def _harness():
    import importlib.util
    import sys

    if "pilot_harness" not in sys.modules:
        spec = importlib.util.spec_from_file_location("pilot_harness", PILOT / "pilot.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["pilot_harness"] = module
        spec.loader.exec_module(module)
    return sys.modules["pilot_harness"]


def result() -> dict:
    return json.loads((PILOT / "result.json").read_text(encoding="utf-8"))


def scenario(name: str) -> dict:
    return next(row for row in result()["scenarios"] if row["id"] == name)


class ServerCodeTest(unittest.TestCase):
    """The Godot server derives realizations with fpsdet's recipe, listens on this machine only, and keeps
    the secret to itself."""

    def test_the_gdscript_recipe_is_fpsdets(self):
        import re

        from fpsdet.challenge import COMMITMENT_RECIPE, DERIVATION_RECIPE, OCCLUDED_MOTION_REPLAY_V2, PLAN_FILE_FORMAT

        source = (PILOT / "godot" / "scripts" / "recipe.gd").read_text(encoding="utf-8")
        self.assertIn(f'DERIVATION_RECIPE := "{DERIVATION_RECIPE}"', source)
        self.assertIn(f'COMMITMENT_RECIPE := "{COMMITMENT_RECIPE}"', source)
        self.assertIn(f'PLAN_FORMAT := "{PLAN_FILE_FORMAT}"', source)
        ranges = re.findall(r'\["(\w+)", (\d+), (\d+)\]', source)
        self.assertEqual([(name, int(low), int(high)) for name, low, high in ranges], list(OCCLUDED_MOTION_REPLAY_V2.parameters))
        committed = re.search(r"const COMMITTED := \[(.*?)\]", source).group(1)
        from fpsdet.challenge import ChallengePlan

        fields = list(ChallengePlan("i", "t", 1, "g", "m", "s", 0, "n", 0, 1, "c").committed_fields())
        self.assertEqual(re.findall(r'"(\w+)"', committed), fields)

    def test_the_server_listens_on_loopback_and_never_writes_the_secret(self):
        server = (PILOT / "godot" / "scripts" / "server.gd").read_text(encoding="utf-8")
        self.assertIn('peer.set_bind_ip("127.0.0.1")', server)
        self.assertIn("secret = PackedByteArray()", server)  # the key is dropped once the realizations are derived
        for public in ("_log(", "_write(", "store_string"):
            for line in server.splitlines():
                if public in line:
                    self.assertNotIn("secret", line.replace("secret-file", "").replace("no challenge secret", "").replace("loaded[1]", ""), line)
        client = (PILOT / "godot" / "scripts" / "client.gd").read_text(encoding="utf-8")
        self.assertNotIn("challenge", client.replace("challenge_id", "").lower().replace("challenges", ""))  # the client knows nothing of challenges


class LiveResultTest(unittest.TestCase):
    """examples/pilot/result.json (fpsdet.pilot/1): what the live qualification found, scenario by scenario."""

    def test_every_scenario_came_out_as_declared(self):
        found = result()
        self.assertEqual(found["format"], "fpsdet.pilot/1")
        self.assertEqual(found["class"], "live_controlled_pilot")
        declared = {row["id"]: row["expected"] for row in qualification()["scenarios"]}
        self.assertEqual({row["id"]: row["expected"] for row in found["scenarios"]}, declared)
        for row in found["scenarios"]:
            with self.subTest(row["id"]):
                self.assertTrue(row["as_expected"], row["problems"])
                self.assertEqual(row["live_vs_offline"], "identical")
                self.assertEqual(row["realization"], {"status": "reproduced", "problems": []})
                self.assertEqual(row["without_secret"], "secret_unavailable")
                self.assertEqual(row["secret_leaks"], [])
                self.assertEqual(row["timing"]["parse_errors"], 0)
                self.assertTrue(row["timing"]["t_ms_on_tick_grid"])
                for case in row["cases"].values():
                    self.assertEqual(case["problems"], [])
                    self.assertEqual(case["packet_recipe"], "fpsdet.packet/5")
        evidence = [row["id"] for row in found["scenarios"] if row["observed"]["evidence"]]
        self.assertEqual(evidence, ["illicit_follower"])

    def test_the_follower_is_evidence_of_a_verified_challenge(self):
        row = scenario("illicit_follower")
        observed = row["observed"]
        self.assertEqual((observed["status"], observed["decision"]), ("followed", "review"))
        self.assertEqual(observed["verified_samples"], observed["eligible_samples"])
        (challenge,) = row["cases"]["pilot-subject"]["challenges"]
        self.assertEqual((challenge["verification"], challenge["plan"]), ("per_sample", row["plan"]["challenges"][0]["plan"]))
        self.assertEqual(row["plan"]["challenges"][0]["version"], 2)
        self.assertEqual(row["cases"]["pilot-subject"]["inputs"]["recipe"], "fpsdet.player-events/3")
        self.assertEqual(row["cases"]["pilot-enemy"]["kinds"], [])

    def test_the_stock_client_was_sent_the_probe_and_never_showed_it(self):
        follower = scenario("illicit_follower")["client"]
        self.assertGreater(follower["subject"]["probe_updates"], 0)
        self.assertGreater(follower["subject"]["probe_checks_drawn"], 0)
        self.assertEqual(follower["subject"]["probe_pixels_max"], 0)
        self.assertEqual(follower["subject"]["unchanged_frame_pixels_max"], 0)
        self.assertGreater(follower["subject"]["enemy_checks_with_pixels"], 0)
        exposed = scenario("exposed_vision")["client"]["subject"]
        self.assertGreater(exposed["probe_checks_with_pixels"], 0)  # placed in the open, it is drawn, and the server said seen
        for row in result()["scenarios"]:
            with self.subTest(row["id"]):
                self.assertEqual(row["client"]["enemy"]["probe_updates"], 0)  # only the subject's client is ever sent it
                self.assertEqual(row["client"]["subject"]["scoreboard_rows"], 2)  # the probe is never on the scoreboard
                sounds = row["client"]["subject"]["probe_sounds"]
                self.assertEqual(bool(sounds), row["id"] == "exposed_audio", sounds)
                verdicts = row["timing"]["verdicts"]
                if row["id"] in ("honest", "accidental_crossing", "illicit_follower", "visible_enemy_cover", "contradictory_channels"):
                    self.assertEqual(set(verdicts["vision"]), {"absent"})

    def test_failures_are_safe_and_explicit(self):
        found = result()
        for row in found["telemetry_failures"]:
            with self.subTest(row["failure"]):
                self.assertFalse(row["stronger_than_undamaged"])
                if row["failure"] not in ("duplicate_events", "malformed_channel_state"):
                    self.assertFalse(row["evidence"])  # what is left of a damaged capture may still be followed, never more
        self.assertEqual({row["failure"] for row in found["server_failures"]},
                         {"missing_plan", "wrong_match", "tampered_plan", "secret_unavailable_on_server"})
        for row in found["server_failures"]:
            with self.subTest(row["failure"]):
                self.assertIn(row["server"], ("missing", "refused", "loaded"))
                self.assertEqual(row["events_naming_a_challenge"], 0)

    def test_the_probe_changes_no_gameplay(self):
        ab = result()["non_interference"]
        self.assertTrue(ab["identical"])
        self.assertGreater(ab["probe_ticks"], 0)
        self.assertEqual(ab["gameplay_digest_with"], ab["gameplay_digest_without"])

    def test_challenge_time_is_the_servers_own(self):
        timing = scenario("illicit_follower")["timing"]
        self.assertEqual(timing["movement_event_spacing_ms"], [100])
        self.assertLess(timing["track_ms_off_grid_max_ms"], 0.001)
        # The server counts every tick; the last part of the window, after the last event in it, is never sent.
        self.assertGreaterEqual(timing["track_ms_server"], timing["track_ms_emitted"])
        self.assertLess(timing["track_ms_server"] - timing["track_ms_emitted"], 100.0)

    def test_the_result_was_made_by_this_pilot_code(self):
        """Change the server, the client or the harness, and the pilot must be qualified again."""
        harness = _harness()
        self.assertEqual(result()["code"], harness.code_identity())
        self.assertEqual(result()["qualification"], harness.sha256_file(PILOT / "qualification.json"))
        for name, entry in result()["media"].items():
            self.assertEqual(harness.sha256_file(ROOT / "docs" / "pilot" / name), entry["sha256"])

    def test_the_committed_capture_reproduces_offline(self):
        self.assertEqual(_harness().verify(), [])


class AddendumTest(unittest.TestCase):
    """The pilot is evidence added after Benchmark v1, with its own class; v1 is untouched."""

    def test_the_addendum_extends_v1_without_changing_it(self):
        from fpsdet import benchmark as bm

        found = json.loads((ROOT / "benchmark" / "addenda" / "p12-challenge-pilot.json").read_text(encoding="utf-8"))
        self.assertEqual(bm.addendum_problems(found), [])
        self.assertEqual(set(found["class"]), {"live_controlled_pilot"})
        self.assertNotIn("live_controlled_pilot", bm.CLASSES)  # Benchmark v1's own classes are unchanged
        release = json.loads((ROOT / "benchmark" / "release.json").read_text(encoding="utf-8"))
        self.assertEqual(found["extends"]["digest"], release["digest"])
        rows = {row["technique"]: row for row in found["capabilities"]}
        self.assertTrue(rows["packet_reader"]["live_engine_pilot"].startswith("yes"))
        self.assertTrue(all(row["real_adversarial_population"] == "no" for row in rows.values()))
        self.assertTrue(all(claim["class"] == "live_controlled_pilot" for claim in found["claims"]))
        doc = (ROOT / "docs" / "pilot.md").read_text(encoding="utf-8")
        self.assertIn(bm.pilot_block(), doc)


if __name__ == "__main__":
    unittest.main()
