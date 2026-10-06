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


if __name__ == "__main__":
    unittest.main()
