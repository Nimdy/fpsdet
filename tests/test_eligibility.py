"""Detector eligibility: why each native detector could or could not run, per unit, recorded by the scorer.

Instrumentation only. These tests prove the record is complete and closed, that every finding names a unit
its detector could run on, that packet/5 binds it while every older recipe keeps its meaning, and that
nothing a decision reads depends on it.
"""

from __future__ import annotations

import copy
import json
import unittest
from functools import lru_cache
from pathlib import Path

from fpsdet.evidence import ELIGIBILITY, NATIVE_KINDS, eligibility_problems, eligibility_unit, implied_decision, rollup
from fpsdet.graph import graph_block
from fpsdet.persist import case_to_dict
from fpsdet.provenance import packet_block, packet_material, verify_packet

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@lru_cache(maxsize=1)
def planted() -> dict[str, dict]:
    from fpsdet.synthetic import build_demo

    return {case.player_id: case_to_dict(case) for case in build_demo().cases}


@lru_cache(maxsize=1)
def weekly() -> dict[str, dict]:
    from fpsdet.week import build_week

    return {case.player_id: case_to_dict(case) for case in build_week().cases}


def status(case: dict, kind: str, unit: str) -> str:
    entry = case["evidence"]["detector_eligibility"]["detectors"][kind]
    (found,) = [name for name, units in entry.items() if unit in units]
    return found


class EligibilityRecordTest(unittest.TestCase):
    def test_every_case_lists_every_native_detector_with_a_closed_status(self):
        for cases in (planted(), weekly()):
            for pid, case in cases.items():
                block = case["evidence"]["detector_eligibility"]
                self.assertEqual(block["recipe"], "fpsdet.detector-eligibility/1")
                self.assertEqual(list(block["detectors"]), list(NATIVE_KINDS), pid)
                for kind, entry in block["detectors"].items():
                    self.assertTrue(entry, (pid, kind))
                    self.assertLessEqual(set(entry), set(ELIGIBILITY), (pid, kind))
                    self.assertEqual(list(entry), [name for name in ELIGIBILITY if name in entry], (pid, kind))
                    units = [unit for keys in entry.values() for unit in keys]
                    self.assertEqual(len(units), len(set(units)), (pid, kind))

    def test_every_finding_names_a_unit_its_detector_could_run_on(self):
        from tests.test_external import FUSED

        for cases in (planted(), weekly(), FUSED):
            for pid, case in cases.items():
                self.assertEqual(eligibility_problems(case["evidence"]), [], pid)
                for obs in case["evidence"]["observations"]:
                    if obs["source"] == "fpsdet":
                        self.assertEqual(status(case, obs["kind"], eligibility_unit(obs)), "eligible", (pid, obs["kind"]))

    def test_the_rollup_is_the_unit_that_came_closest_to_running(self):
        self.assertEqual(rollup({"insufficient_samples": ["smg"], "eligible": ["rifle"]}), "eligible")
        self.assertEqual(rollup({"telemetry_unavailable": ["rifle"], "baseline_too_thin": ["smg"]}), "baseline_too_thin")
        self.assertEqual(rollup({"disabled": [""]}), "disabled")
        with self.assertRaises(ValueError):
            rollup({})

    def test_eligibility_is_not_free_text(self):
        from fpsdet.evidence import eligibility_block

        with self.assertRaises(ValueError):
            eligibility_block({kind: {"": "skipped"} for kind in NATIVE_KINDS})
        with self.assertRaises(ValueError):
            eligibility_block({kind: {"": "eligible"} for kind in NATIVE_KINDS[1:]})


class Packet5Test(unittest.TestCase):
    def case(self) -> dict:
        return copy.deepcopy(planted()["rage"])

    def test_new_cases_bind_eligibility_in_packet_5(self):
        case = self.case()
        self.assertEqual(case["evidence"]["packet"]["recipe"], "fpsdet.packet/5")
        self.assertEqual(verify_packet(case), [])
        material = packet_material(case)
        self.assertEqual(material["detector_eligibility"], case["evidence"]["detector_eligibility"])
        self.assertEqual(sorted(material), sorted([*packet_material(case, "fpsdet.packet/4"), "detector_eligibility"]))

    def test_an_edited_eligibility_is_caught_and_older_recipes_never_saw_it(self):
        case = self.case()
        before = {recipe: packet_block(case, recipe) for recipe in ("fpsdet.packet/1", "fpsdet.packet/2", "fpsdet.packet/3", "fpsdet.packet/4")}
        entry = case["evidence"]["detector_eligibility"]["detectors"]["hidden"]
        entry["eligible"] = entry.pop("telemetry_unavailable")
        problems = verify_packet(case)
        self.assertIn("the packet digest does not match the evidence and provenance it covers", problems)
        for recipe, block in before.items():
            self.assertEqual(packet_block(case, recipe), block, recipe)

    def test_a_finding_on_a_unit_that_could_not_run_is_caught(self):
        case = self.case()
        entry = case["evidence"]["detector_eligibility"]["detectors"]["accuracy"]
        entry["baseline_too_thin"] = entry.pop("eligible")
        case["evidence"]["packet"] = packet_block(case)
        problems = verify_packet(case)
        self.assertTrue(any(problem.startswith("eligibility: accuracy fired on 'rifle'") for problem in problems), problems)

    def test_a_packet_5_without_eligibility_is_incomplete(self):
        case = self.case()
        del case["evidence"]["detector_eligibility"]
        self.assertEqual(packet_block(case), {"recipe": "fpsdet.packet/5", "status": "incomplete", "missing": ["detector eligibility"]})

    def test_older_packets_still_verify(self):
        expected = {
            "historical-packets-p23.json": "fpsdet.packet/1", "historical-packets-p3.json": "fpsdet.packet/1",
            "historical-packets-p4.json": "fpsdet.packet/1", "historical-packets-p5.json": "fpsdet.packet/2",
            "historical-packets-p6.json": "fpsdet.packet/3", "historical-packets-p8.json": "fpsdet.packet/4",
        }
        for name, recipe in expected.items():
            for case in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"]:
                with self.subTest(name, player=case["player_id"]):
                    self.assertEqual(case["evidence"]["packet"]["recipe"], recipe)
                    self.assertNotIn("detector_eligibility", case["evidence"])
                    self.assertEqual(verify_packet(case), [])


class NothingReadsItBackTest(unittest.TestCase):
    """Eligibility is recorded, never read: no decision, finding, id, seal or graph depends on it."""

    def test_decisions_findings_and_graph_ignore_it(self):
        for pid, case in planted().items():
            stripped = copy.deepcopy(case)
            del stripped["evidence"]["detector_eligibility"]
            self.assertEqual(implied_decision(stripped["evidence"]), case["decision"], pid)
            self.assertEqual(graph_block(stripped)["digest"], case["evidence"]["graph"]["digest"], pid)
            self.assertNotIn("detector_eligibility", json.dumps(case["evidence"]["graph"]))

    def test_the_scorer_never_reads_it(self):
        import ast

        from fpsdet.provenance import PACKAGE_DIR

        for name in ("score.py", "pipeline.py", "external.py", "challenge.py", "graph.py"):
            tree = ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))
            reads = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr == "detector_eligibility" and isinstance(node.ctx, ast.Load)
            ]
            # score.py only adds to it in the batch passes, through setdefault; nothing compares or branches on it.
            for node in reads:
                parent = next(p for p in ast.walk(tree) if any(child is node for child in ast.iter_child_nodes(p)))
                self.assertIsInstance(parent, ast.Attribute, name)
                self.assertEqual(parent.attr, "setdefault", name)


@lru_cache(maxsize=1)
def fixture_world():
    from fpsdet.fixtures import build_fixtures

    return build_fixtures()


# What each detector can be: eligible and fired, eligible and quiet, and every way it could not run that its
# rules allow. A player with no unit for a detector at all (no shot, say) lists "" as telemetry_unavailable. Each must be shown by a planted demo player, a controlled fixture or a unit test below.
REACHABLE = {
    "speed": {"telemetry_unavailable", "insufficient_samples", "disabled"},
    "fire_rate": {"telemetry_unavailable", "insufficient_samples", "disabled"},
    "metronome": {"telemetry_unavailable", "insufficient_samples", "disabled", "not_applicable"},
    "recoil_floor": {"telemetry_unavailable", "insufficient_samples", "disabled"},
    "mirror": {"telemetry_unavailable", "insufficient_samples", "conflict"},
    "recoil_learned": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin", "not_applicable"},
    "accuracy": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin"},
    "headshot_rate": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin"},
    "median_distance": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin"},
    "geometry_rate": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin"},
    "extra": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin", "disabled"},
    "rank_tail": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin", "not_applicable"},
    "view_snaps": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin"},
    "acquire_timing": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin"},
    "supporting_extra": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin", "disabled"},
    "account_jump": {"telemetry_unavailable", "insufficient_samples"},
    "hidden": {"telemetry_unavailable", "insufficient_samples", "conflict"},
    "quiet_aim": {"telemetry_unavailable", "insufficient_samples", "conflict", "not_applicable"},
    "wire": {"telemetry_unavailable", "insufficient_samples"},
    "occluded_motion_replay": {"telemetry_unavailable", "insufficient_samples", "conflict", "disabled", "not_applicable"},
    "leftover": {"telemetry_unavailable", "insufficient_samples", "baseline_too_thin", "not_applicable"},
    "voice": {"telemetry_unavailable", "insufficient_samples", "not_applicable"},
}


def shown(cases) -> dict[str, set[str]]:
    """Every (kind, status) the cases show, with eligible split into fired and quiet."""
    found: dict[str, set[str]] = {kind: set() for kind in NATIVE_KINDS}
    for case in cases:
        fired = {(obs["kind"], eligibility_unit(obs)) for obs in case["evidence"]["observations"] if obs["source"] == "fpsdet"}
        for kind, entry in case["evidence"]["detector_eligibility"]["detectors"].items():
            for name, units in entry.items():
                for unit in units:
                    found[kind].add(name if name != "eligible" else ("fired" if (kind, unit) in fired else "quiet"))
    return found


class EveryDetectorEveryStatusTest(unittest.TestCase):
    """For every detector: eligible and fires, eligible and does not, and each way it could not run."""

    @classmethod
    def setUpClass(cls):
        world = [case_to_dict(case) for case in fixture_world().cases]
        cls.found = shown([*planted().values(), *world])
        cls.found["supporting_extra"] |= cls.unit_supporting_disabled()
        cls.found["leftover"] |= cls.unit_leftover_alone()
        cls.found["occluded_motion_replay"] |= cls.unit_planned_challenges()

    @staticmethod
    def unit_supporting_disabled() -> set[str]:
        import dataclasses

        from fpsdet.pipeline import run_score

        world = fixture_world()
        profile = dataclasses.replace(world.profile, extra_metrics=[])
        events = [event for event in world.events if event.player_id == "fx-human-00"]
        (case,) = run_score(events, profile)
        return {status for status in case.detector_eligibility["supporting_extra"].values()}

    @staticmethod
    def unit_leftover_alone() -> set[str]:
        """Scored alone, without the batch pass, nothing pairs a player with anyone."""
        from fpsdet.baseline import build_cohorts
        from fpsdet.score import assess_player
        from fpsdet.summarize import summarize

        world = fixture_world()
        records = {record.player_id: record for record in summarize(world.events, world.profile)}
        case = assess_player(records["fx-lone-script"], build_cohorts(list(records.values()), world.profile), world.profile)
        return set(case.detector_eligibility["leftover"].values())

    @staticmethod
    def unit_planned_challenges() -> set[str]:
        """Planned challenges: each result status maps to one eligibility status."""
        from fpsdet.challenge import ABSTAINED, FOLLOWED, NO_SAMPLES, NOT_FOLLOWED, UNPLANNED, ChallengeResult
        from fpsdet.score import _challenge_eligibility

        profile = fixture_world().profile
        bar = profile.hidden_track_min_samples
        rows = {
            "disabled": ChallengeResult("ch-a", None, UNPLANNED),
            "not_applicable": ChallengeResult("ch-b", None, ABSTAINED, cause="other_subject"),
            "conflict": ChallengeResult("ch-c", None, NO_SAMPLES, not_counted={"disagreed": 2}),
            "telemetry_unavailable": ChallengeResult("ch-d", None, NO_SAMPLES),
            "insufficient_samples": ChallengeResult("ch-e", None, NOT_FOLLOWED, eligible=bar - 1),
            "eligible": ChallengeResult("ch-f", None, NOT_FOLLOWED, eligible=bar),
        }
        found = set()
        for expected, result in rows.items():
            assert _challenge_eligibility(result, profile) == expected, expected
            found.add(expected)
        assert _challenge_eligibility(ChallengeResult("ch-g", None, FOLLOWED, eligible=bar), profile) == "eligible"
        assert _challenge_eligibility(ChallengeResult("ch-h", None, ABSTAINED, cause="unchecked"), profile) == "disabled"
        return found - {"eligible"}

    def test_every_detector_fires_and_stays_quiet_where_eligible(self):
        for kind in NATIVE_KINDS:
            with self.subTest(kind):
                self.assertIn("fired", self.found[kind])
                self.assertIn("quiet", self.found[kind])

    def test_every_way_a_detector_could_not_run_is_shown(self):
        self.assertEqual(set(REACHABLE), set(NATIVE_KINDS))
        for kind, statuses in REACHABLE.items():
            with self.subTest(kind):
                self.assertLessEqual(statuses, self.found[kind])
                # And nothing beyond what the rules allow.
                self.assertLessEqual(self.found[kind] - {"fired", "quiet"}, statuses)

    def test_the_fixture_world_shows_what_it_was_built_to_show(self):
        from fpsdet.fixtures import fixture_failures

        self.assertEqual(fixture_failures(fixture_world()), [])
        for case in fixture_world().cases:
            self.assertEqual(eligibility_problems(case_to_dict(case)["evidence"]), [], case.player_id)


if __name__ == "__main__":
    unittest.main()
