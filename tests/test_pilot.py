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


if __name__ == "__main__":
    unittest.main()
