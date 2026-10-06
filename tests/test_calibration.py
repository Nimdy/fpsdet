"""Measuring native detectors against labelled populations. Measurement only: nothing here changes scoring.

LabelAuditTest was written before the evaluation harness (P8, phase C0). It pins the label sets of the
committed real-data desks and recomputes the decision results the README and the example READMEs publish,
straight from those desks, so a published number that no longer follows from the data fails a test.
"""

from __future__ import annotations

import copy
import json
import math
import re
import tempfile
import unittest
from collections import Counter
from functools import lru_cache
from pathlib import Path

from fpsdet import calibration as cal
from fpsdet.calibration import EvaluationError, PublishedMismatch
from fpsdet.parse import load_profile
from fpsdet.persist import case_to_dict, event_to_dict
from fpsdet.provenance import profile_digest

ROOT = Path(__file__).resolve().parents[1]


def desk(name: str) -> list[dict]:
    return json.loads((ROOT / "examples" / name / "desk.json").read_text(encoding="utf-8"))["rows"]


def decisions(rows: list[dict]) -> dict[str, Counter]:
    table: dict[str, Counter] = {}
    for row in rows:
        table.setdefault(row["truth"], Counter())[row["decision"]] += 1
    return table


class LabelAuditTest(unittest.TestCase):
    """The labels on the committed desks, and the decision results published from them."""

    def test_the_tf2_labels(self):
        rows = desk("tf2")
        self.assertEqual(Counter(row["truth"] for row in rows), {
            "never banned": 1746, "banned for something else": 821, "banned for cheating": 189, "VAC ban, mirrored by RGL": 8,
        })

    def test_the_tf2_results_the_readmes_publish(self):
        table = decisions(desk("tf2"))
        self.assertEqual(dict(table["banned for cheating"]), {"review": 3, "watch": 48, "clean": 131, "insufficient_data": 7})
        self.assertEqual(dict(table["never banned"]), {"watch": 30, "clean": 1575, "insufficient_data": 141})
        self.assertEqual(dict(table["banned for something else"]), {"watch": 16, "clean": 729, "insufficient_data": 76})
        flagged = sum(c["review"] + c["watch"] for c in table.values())
        cheaters_flagged = table["banned for cheating"]["review"] + table["banned for cheating"]["watch"]
        self.assertEqual((flagged, cheaters_flagged), (97, 51))
        tf2 = (ROOT / "examples" / "tf2" / "README.md").read_text(encoding="utf-8")
        for claim in ("picked 97 of the 2,764 scored players", "3 to review and 94 to watch", "51 of the 97 are banned cheaters (53%)",
                      "| Banned for cheating (189 with aimed shots) | 3 | 48 | 131 | 7 | 27% |", "| Never banned (1,746) | 0 | 30 | 1,575 | 141 | 1.7% |",
                      "| Banned for something else (821) | 0 | 16 | 729 | 76 | 1.9% |"):
            self.assertIn(claim, tf2)
        self.assertIn("131 of 189 banned cheaters still looked clean", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_the_cs2_labels_and_results(self):
        rows = desk("cs2")
        table = decisions(rows)
        self.assertEqual(Counter(row["truth"] for row in rows), {"clean, reviewed match": 575, "cheater": 504, "clean, unreviewed match": 450})
        self.assertEqual(dict(table["cheater"]), {"watch": 14, "clean": 92, "insufficient_data": 398})
        self.assertEqual(dict(table["clean, reviewed match"]), {"clean": 302, "insufficient_data": 273})
        self.assertEqual(dict(table["clean, unreviewed match"]), {"watch": 4, "clean": 435, "insufficient_data": 11})
        cs2 = (ROOT / "examples" / "cs2" / "README.md").read_text(encoding="utf-8")
        for claim in ("picked 18 of 1,529 players to watch, and 14 of them (78%) are labelled cheaters", "| Labelled cheaters (504) | 0 | 14 | 92 | 398 |",
                      "| Clean, reviewed matches (575) | 0 | 0 | 302 | 273 |", "| Clean, unreviewed matches (450) | 0 | 4 | 435 | 11 |"):
            self.assertIn(claim, cs2)



def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """The two-sided Wilson interval, written out here independently of statsutil."""
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


@lru_cache(maxsize=1)
def demo():
    from fpsdet.synthetic import build_demo

    return build_demo()


def demo_cases() -> list[dict]:
    return [case_to_dict(case) for case in demo().cases]


def demo_telemetry() -> list[str]:
    found = set()
    for event in demo().events:
        found |= {key for key, value in event_to_dict(event).items() if value is not None and key not in cal.IDENTITY_FIELDS}
    return sorted(found)


def demo_labels() -> dict[str, str]:
    from fpsdet.synthetic import EXPECT

    return {player: ("planted" if decision in ("review", "watch") else "honest") for player, decision in EXPECT.items()}


def demo_dataset(**changes) -> dict:
    """A dataset definition for testing the mechanics on the planted demo. Never a published rate."""
    dataset = {
        "schema": cal.DATASET_SCHEMA,
        "dataset": "planted-demo-mechanics",
        "title": "Planted demo, for testing the evaluation mechanics",
        "source": "fpsdet.synthetic.build_demo",
        "unit": "one planted player",
        "match_level": "not meaningful: planted players",
        "not_scored": "Every planted player is scored.",
        "labels": [
            {"label": "planted", "role": "positive", "name": "planted cheat", "meaning": "Built to cheat.", "not_meaning": "A real player."},
            {"label": "honest", "role": "comparison", "name": "planted honest", "meaning": "Built not to.", "not_meaning": "A real player."},
        ],
        "telemetry": demo_telemetry(),
        "server_shot_timing": True,
        "caveats": ["Planted by construction."],
    }
    dataset.update(changes)
    return dataset


class RateTest(unittest.TestCase):
    """C6, C7: counts, denominators, two-sided Wilson intervals, and ratios with no infinity."""

    def test_wilson_two_sided(self):
        for k, n in ((51, 182), (0, 1605), (30, 1605), (182, 182), (1, 20)):
            cell = cal.rate(k, n)
            lo, hi = wilson(k, n)
            self.assertEqual((cell["count"], cell["denominator"]), (k, n))
            self.assertAlmostEqual(cell["rate"], k / n, places=6)
            self.assertAlmostEqual(cell["ci95"][0], lo, places=6)
            self.assertAlmostEqual(cell["ci95"][1], hi, places=6)
        # Two-sided, not the scorer's one-sided bound: the interval is wider than the one-sided 95% one.
        from fpsdet.statsutil import wilson_bound

        self.assertLess(cal.rate(51, 182)["ci95"][0], wilson_bound(51, 182, upper=False))

    def test_too_small_a_denominator_shows_no_rate(self):
        for k, n in ((0, 0), (3, 3), (5, 19)):
            cell = cal.rate(k, n)
            self.assertIsNone(cell["rate"])
            self.assertIsNone(cell["ci95"])
            self.assertTrue(cell["insufficient_sample"])
        self.assertIsNotNone(cal.rate(0, 20)["rate"])

    def test_enrichment_handles_zeros_without_infinity(self):
        both = cal.enrichment(cal.rate(0, 100), cal.rate(0, 500))
        self.assertEqual((both["ratio"], both["range"], both["note"]), (None, None, "no fires in either group"))
        none_compared = cal.enrichment(cal.rate(11, 182), cal.rate(0, 1605))
        self.assertIsNone(none_compared["ratio"])
        self.assertEqual(none_compared["note"], "no comparison fires observed")
        self.assertIsNone(none_compared["range"][1])
        self.assertAlmostEqual(none_compared["range"][0], wilson(11, 182)[0] / wilson(0, 1605)[1], places=4)
        normal = cal.enrichment(cal.rate(44, 182), cal.rate(28, 1605))
        self.assertAlmostEqual(normal["ratio"], (44 / 182) / (28 / 1605), places=5)
        self.assertAlmostEqual(normal["range"][0], wilson(44, 182)[0] / wilson(28, 1605)[1], places=4)
        self.assertAlmostEqual(normal["range"][1], wilson(44, 182)[1] / wilson(28, 1605)[0], places=4)
        zero = cal.enrichment(cal.rate(0, 182), cal.rate(28, 1605))
        self.assertEqual(zero["ratio"], 0.0)
        thin = cal.enrichment(cal.rate(3, 3), cal.rate(0, 1605))
        self.assertEqual((thin["ratio"], thin["note"]), (None, "insufficient calibration sample"))
        for cell in (both, none_compared, normal, zero, thin):
            self.assertNotIn("Infinity", json.dumps(cell))

    def test_statuses(self):
        self.assertEqual(cal.status_of(19, 1000), "insufficient_sample")
        self.assertEqual(cal.status_of(20, 99), "descriptive_only")
        self.assertEqual(cal.status_of(100, 100), "measured")
        self.assertNotIn("calibrated", cal.STATUSES)


class ObservabilityTest(unittest.TestCase):
    """C3, C16, C18: what a dataset cannot show is not observable, and says why."""

    def tf2(self):
        dataset = cal.load_dataset(ROOT / "examples" / "tf2" / "evaluation.dataset.json")
        return cal.observability(load_profile(ROOT / "examples" / "tf2" / "tf2.json"), dataset["telemetry"], server_shot_timing=False, history=False)

    def cs2(self):
        dataset = cal.load_dataset(ROOT / "examples" / "cs2" / "evaluation.dataset.json")
        return cal.observability(load_profile(ROOT / "examples" / "cs2" / "cs2.json"), dataset["telemetry"], server_shot_timing=True, history=False)

    def test_the_detector_list_is_every_native_kind(self):
        from fpsdet.evidence import KINDS

        self.assertEqual(set(cal.NATIVE_KINDS), {kind for kind, (family, _c, _r) in KINDS.items() if family != "external"} - {"private_replay"})
        self.assertEqual(set(cal.NEEDS), set(cal.NATIVE_KINDS))

    def test_tf2_and_cs2(self):
        tf2 = {kind for kind, row in self.tf2().items() if row["observable"]}
        cs2 = {kind for kind, row in self.cs2().items() if row["observable"]}
        self.assertEqual(tf2, {"accuracy", "headshot_rate", "extra", "rank_tail", "supporting_extra"})
        self.assertEqual(cs2, {"speed", "accuracy", "headshot_rate", "median_distance", "geometry_rate", "rank_tail", "view_snaps"})

    def test_information_detectors_are_not_observable_with_this_data(self):
        for found in (self.tf2(), self.cs2()):
            for kind in ("hidden", "quiet_aim", "wire", "voice", "occluded_motion_replay"):
                self.assertFalse(found[kind]["observable"])
                self.assertIn("missing_telemetry", found[kind]["reasons"])
                self.assertEqual(found[kind]["text"], "not observable with this dataset")
        self.assertIn("no_server_shot_timing", self.tf2()["fire_rate"]["reasons"])
        self.assertIn("profile_declares_none", self.cs2()["fire_rate"]["reasons"])
        self.assertEqual(self.cs2()["account_jump"]["reasons"], ["no_history_input"])

    def test_telemetry_that_feeds_a_detector_whose_eligibility_cases_do_not_record(self):
        found = cal.observability(demo().profile, demo_telemetry(), server_shot_timing=True, history=True)
        self.assertEqual(found["hidden"]["reasons"], ["eligibility_not_recorded"])
        self.assertTrue(found["accuracy"]["observable"])

    def test_a_census_checks_the_declaration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.ndjson"
            path.write_text("".join(json.dumps(event_to_dict(event)) + "\n" for event in demo().events), encoding="utf-8")
            found = cal.census(path)
        self.assertEqual(found["events"], len(demo().events))
        self.assertEqual(cal.check_census(demo_telemetry(), found), [])
        problems = cal.check_census([*demo_telemetry(), "wire_error_deg_typo"], found)
        self.assertEqual(problems, ["wire_error_deg_typo is declared but no event carries it"])
        self.assertTrue(cal.check_census([name for name in demo_telemetry() if name != "hitbox"], found)[0].startswith("hitbox is on"))
        with self.assertRaises(EvaluationError):
            cal.evaluate(demo_cases(), demo_labels(), demo_dataset(telemetry=[*demo_telemetry(), "wire_error_deg_typo"]), demo().profile, telemetry_census=found)


class HarnessTest(unittest.TestCase):
    """C1, C4, C5, C23, C24: the harness on planted cases. These test mechanics, never a published rate."""

    @classmethod
    def setUpClass(cls):
        cls.cases = demo_cases()
        cls.artifact = cal.evaluate(copy.deepcopy(cls.cases), demo_labels(), demo_dataset(), demo().profile)

    def test_evaluation_changes_no_case_and_no_decision(self):
        cases = demo_cases()
        before = json.dumps(cases, sort_keys=True)
        cal.evaluate(cases, demo_labels(), demo_dataset(), demo().profile)
        self.assertEqual(json.dumps(cases, sort_keys=True), before)
        from fpsdet.synthetic import build_demo

        self.assertEqual([(case.player_id, case.decision, case.seal) for case in build_demo().cases], [(case.player_id, case.decision, case.seal) for case in demo().cases])

    def test_fired_is_always_inside_evaluated(self):
        for row in self.artifact["rows"]:
            for kind, units in row["fired"].items():
                if cal.NEEDS[kind].get("case"):
                    self.assertLessEqual(set(units), set(row["evaluated"].get(kind, ())), (row["player"], kind))

    def test_only_players_a_detector_could_run_on_are_in_its_denominator(self):
        rows = self.artifact["rows"]
        for entry in self.artifact["statistics"]["detectors"]:
            if entry["status"] == "not_observable":
                self.assertNotIn("rates", entry)
                continue
            for label, cell in entry["rates"].items():
                self.assertEqual(cell["denominator"], sum(row["label"] == label and entry["kind"] in row["evaluated"] for row in rows))
        small = next(row for row in rows if row["player"] == "small-sample")
        self.assertEqual(small["decision"], "insufficient_data")
        self.assertNotIn("accuracy", small["evaluated"])

    def test_a_detector_the_cases_cannot_give_a_denominator_for_is_counted_not_rated(self):
        hidden = next(entry for entry in self.artifact["statistics"]["detectors"] if entry["kind"] == "hidden")
        self.assertEqual(hidden["status"], "not_observable")
        self.assertEqual(hidden["fired_players"], {"planted": 1, "honest": 0})
        self.assertNotIn("rates", hidden)

    def test_a_finding_on_an_uncompared_weapon_is_refused(self):
        cases = demo_cases()
        rage = next(case for case in cases if case["player_id"] == "rage")
        rage["evidence"]["eligibility"]["compared"] = []
        with self.assertRaises(EvaluationError):
            cal.player_row(rage, "planted", demo().profile, {})

    def test_tampered_mixed_or_mislabelled_inputs_are_refused(self):
        cases = demo_cases()
        tampered = next(case for case in cases if case["player_id"] == "rank-outlier")
        tampered["evidence"]["observations"][0]["evidence"]["observed"] = 0.99
        with self.assertRaisesRegex(EvaluationError, "does not verify"):
            cal.evaluate(cases, demo_labels(), demo_dataset(), demo().profile)
        other = load_profile(ROOT / "examples" / "tf2" / "tf2.json")
        with self.assertRaisesRegex(EvaluationError, "not the one these cases were scored with"):
            cal.evaluate(demo_cases(), demo_labels(), demo_dataset(), other)
        with self.assertRaisesRegex(EvaluationError, "does not explain"):
            cal.evaluate(demo_cases(), {**demo_labels(), "rage": "suspicious"}, demo_dataset(), demo().profile)
        mixed = demo_cases()
        mixed[0]["evidence"]["provenance"]["detector"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(EvaluationError):
            cal.evaluate(mixed, demo_labels(), demo_dataset(), demo().profile)
        with self.assertRaisesRegex(EvaluationError, "what every label means|must be said"):
            cal.evaluate(demo_cases(), demo_labels(), demo_dataset(labels=[{**demo_dataset()["labels"][0], "not_meaning": ""}, demo_dataset()["labels"][1]]), demo().profile)

    def test_published_numbers_that_no_longer_reproduce_stop_the_evaluation(self):
        published = {"where": "a README", "decisions": {"planted": {"review": 99, "watch": 0, "clean": 0, "insufficient_data": 0}}}
        with self.assertRaises(PublishedMismatch):
            cal.evaluate(demo_cases(), demo_labels(), demo_dataset(published=published), demo().profile)

    def test_the_artifact_binds_its_inputs_and_verifies(self):
        artifact = json.loads(cal.dumps(self.artifact))
        self.assertEqual(artifact["schema"], "fpsdet.evaluation/1")
        self.assertEqual(cal.verify_artifact(artifact), [])
        inputs = artifact["inputs"]
        for field in ("digest", "detector", "profile", "cohort"):
            self.assertTrue(str(inputs[field]).startswith("sha256:"), field)
        self.assertEqual(inputs["digest"], cal.inputs_digest(reversed(self.cases)))
        self.assertTrue(artifact["labels"]["digest"].startswith("sha256:"))
        self.assertEqual(artifact["evaluator"]["digest"], cal.evaluator_digest())
        self.assertEqual(cal.evaluate(demo_cases(), demo_labels(), demo_dataset(), demo().profile)["digest"], artifact["digest"])
        moved = copy.deepcopy(artifact)
        moved["rows"][0]["decision"] = "review" if moved["rows"][0]["decision"] != "review" else "clean"
        self.assertIn("the statistics do not follow from the rows", cal.verify_artifact(moved))
        moved["digest"] = cal.artifact_digest(moved)
        self.assertEqual(cal.verify_artifact(moved), ["the statistics do not follow from the rows"])
        edited = copy.deepcopy(artifact)
        edited["statistics"]["decisions"]["by_group"]["planted"]["counts"]["review"] += 1
        self.assertIn("the artifact digest does not match its contents", cal.verify_artifact(edited))
        relabelled = copy.deepcopy(artifact)
        relabelled["labels"]["counts"]["planted"] += 1
        self.assertTrue(cal.verify_artifact(relabelled))

    def test_the_evaluation_is_not_written_into_a_packet(self):
        cases = demo_cases()
        packets = [case["evidence"]["packet"]["digest"] for case in cases]
        cal.evaluate(cases, demo_labels(), demo_dataset(), demo().profile)
        self.assertEqual([case["evidence"]["packet"]["digest"] for case in cases], packets)
        self.assertNotIn("evaluation", json.dumps([case["evidence"].get("packet") for case in cases]))

    def test_no_p_values_and_no_calibrated_status(self):
        text = json.dumps(self.artifact)
        self.assertNotIn("p_value", text)
        self.assertNotIn("p-value", text)
        self.assertNotIn('"calibrated"', text)
        report = cal.render_markdown(self.artifact)
        self.assertLess(report.index("## Read this first"), report.index("## Population"))
        self.assertLess(report.index("## What the labels mean"), report.index("## Decisions"))

    def test_the_challenge_detector_has_no_real_world_calibration(self):
        entry = next(entry for entry in self.artifact["statistics"]["detectors"] if entry["kind"] == "occluded_motion_replay")
        self.assertEqual(entry["real_world_calibration"], "unavailable")


class SplitTest(unittest.TestCase):
    """C13: a fixed split from the pseudonym alone, that the scorer never reads."""

    def test_split_is_fixed_and_about_half(self):
        ids = [f"player-{index}" for index in range(4000)]
        halves = Counter(cal.split_of(player) for player in ids)
        self.assertEqual([cal.split_of(player) for player in ids], [cal.split_of(player) for player in ids])
        self.assertLess(abs(halves["dev"] - halves["eval"]), 200)

    def test_detection_never_reads_the_evaluation(self):
        from fpsdet.provenance import DETECTOR_MODULES, NOT_DETECTOR, PACKAGE_DIR

        self.assertIn("fpsdet.calibration", NOT_DETECTOR)
        for module in DETECTOR_MODULES:
            path = PACKAGE_DIR / ("__init__.py" if module == "fpsdet" else module.split(".", 1)[1] + ".py")
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\s*(from|import)\s[^\n]*calibration", source, re.M), module)
            self.assertNotIn(cal.SPLIT_RECIPE, source, module)


class FixtureTest(unittest.TestCase):
    """C17, C28: synthetic players qualify code; they are never a rate."""

    def test_every_planted_behaviour_trips_its_detector_and_no_twin_does(self):
        result = cal.qualify_fixtures()
        self.assertEqual((result["demo_failures"], result["fixture_failures"]), ([], []))
        outcomes = {entry["kind"]: entry["outcome"] for entry in result["detectors"]}
        # P8 found six detectors with no plant; the controlled fixtures (P9) plant each one, beside an honest twin.
        self.assertEqual(set(outcomes.values()), {"passes_controlled_fixture"})
        for entry in result["detectors"]:
            self.assertTrue(entry["planted"] and entry["honest_twins"], entry["kind"])
        self.assertTrue(all(result["honest_fixtures_clean"].values()))
        self.assertEqual(outcomes["occluded_motion_replay"], "passes_controlled_fixture")
        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)

        self.assertFalse({"rate", "rates", "ci95", "ratio"} & set(keys(result)))
        self.assertIn("never a real-world rate", result["what"])


# How each desk names a raw label.
DESK_NAMES = {
    "tf2": {"cheater": "banned for cheating", "not banned": "never banned", "other ban": "banned for something else", "vac": "VAC ban, mirrored by RGL"},
    "cs2": {"cheater": "cheater", "clean, reviewed match": "clean, reviewed match", "clean, unreviewed match": "clean, unreviewed match"},
}


def evaluation(name: str) -> dict:
    return json.loads((ROOT / "examples" / name / "evaluation.json").read_text(encoding="utf-8"))


def text(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def plain(cell: dict, ci: bool = False) -> str:
    """A rate the way the READMEs write it: 44 of 182 (24.2%, 95% CI 18.5–30.9%)."""
    lo, hi = cell["ci95"]
    return f"{cell['count']} of {cell['denominator']:,} ({cell['rate']:.1%}, {'95% CI ' if ci else ''}{lo * 100:.1f}–{hi * 100:.1f}%)"


def detector(artifact: dict, kind: str) -> dict:
    return next(entry for entry in artifact["statistics"]["detectors"] if entry["kind"] == kind)


class PublishedEvaluationTest(unittest.TestCase):
    """C9, C25-C27: the committed evaluations verify, match the desks player for player, and every number the
    READMEs and docs publish is recomputed from them. A published number that drifts from its artifact fails."""

    def test_each_evaluation_verifies_and_its_report_is_current(self):
        for name in ("tf2", "cs2"):
            artifact = evaluation(name)
            self.assertEqual(cal.verify_artifact(artifact), [], name)
            self.assertEqual(text("examples", name, "evaluation.md"), cal.render_markdown(artifact), name)
            self.assertEqual(artifact["dataset"], cal.load_dataset(ROOT / "examples" / name / "evaluation.dataset.json"))
            self.assertTrue(artifact["published"]["reproduced"])
            self.assertEqual(cal.check_census(artifact["dataset"]["telemetry"], artifact["telemetry"]["census"]), [])
            self.assertEqual(artifact["inputs"]["packets_verified"], artifact["inputs"]["cases"])
            self.assertEqual(artifact["inputs"]["profile"], profile_digest(load_profile(ROOT / "examples" / name / f"{name}.json")))

    def test_rows_match_the_desk_player_for_player(self):
        for name in ("tf2", "cs2"):
            rows = {row["player"]: row for row in evaluation(name)["rows"]}
            desk_rows = {row["id"]: row for row in desk(name)}
            self.assertEqual(set(rows), set(desk_rows), name)
            for player, row in rows.items():
                self.assertEqual(row["decision"], desk_rows[player]["decision"], player)
                self.assertEqual(DESK_NAMES[name][row["label"]], desk_rows[player]["truth"], player)

    def test_the_published_decisions_are_the_evaluation_s(self):
        for name in ("tf2", "cs2"):
            artifact = evaluation(name)
            by_group = artifact["statistics"]["decisions"]["by_group"]
            for label, counts in artifact["dataset"]["published"]["decisions"].items():
                self.assertEqual(by_group[label]["counts"], counts)
                self.assertEqual(dict(decisions(desk(name))[DESK_NAMES[name][label]]), {d: n for d, n in counts.items() if n})

    def test_the_tf2_numbers_follow_from_the_evaluation(self):
        stats = evaluation("tf2")["statistics"]
        queue = stats["decisions"]["queues"]["review_or_watch"]
        at_random = queue["size"] * queue["scored_prevalence"]
        self.assertEqual((queue["size"], queue["by_label"]["cheater"], queue["scored_pool"]), (97, 51, 2764))
        self.assertEqual(round(51 / 97 * 100), 53)
        self.assertEqual(round(at_random), 7)
        self.assertEqual(round(51 / at_random, 1), 7.7)  # "nearly 8 times better than chance"
        equal = next(cell for cell in stats["evidence_amount"] if cell["matches"] == "15-20")["groups"]
        tf2 = text("examples", "tf2", "README.md")
        self.assertEqual([equal[label]["scored"] for label in ("cheater", "not banned", "other ban")], [80, 228, 61])
        for label, title in (("cheater", "Banned for cheating"), ("not banned", "Never banned"), ("other ban", "Banned for something else")):
            cell = equal[label]
            self.assertEqual(cell["scored"], cell["evaluated"])
            self.assertIn(f"| {title} | {cell['scored']} | {cell['review_or_watch']['rate']:.1%} | {cell['review']} |", tf2)
        self.assertEqual(round(equal["cheater"]["review_or_watch"]["rate"] / equal["not banned"]["review_or_watch"]["rate"]), 14)  # "about 14 times"
        readme = text("README.md")
        self.assertIn("With the same evidence per player, it flagged 37.5% of banned cheaters and 2.6% of never-banned players.", readme)
        rank = detector(evaluation("tf2"), "rank_tail")["rates"]
        self.assertIn(f"it fired on 44 of 182 RGL cheating-ban labelled accounts ({rank['cheater']['rate']:.1%}, 18.5–30.9%) and 28 of 1,605 never-banned ones ({rank['not banned']['rate']:.1%}, 1.2–2.5%)", readme)
        self.assertEqual(plain(rank["cheater"]), "44 of 182 (24.2%, 18.5–30.9%)")
        self.assertEqual(plain(rank["not banned"]), "28 of 1,605 (1.7%, 1.2–2.5%)")
        unseen = [entry for entry in stats["detectors"] if entry["status"] == "not_observable"]
        self.assertIn(f"{len(unseen)} of {len(stats['detectors'])} detectors cannot be observed", readme)
        for kind, name, ci in (("rank_tail", "rank tail (above the rank's range)", True), ("accuracy", "accuracy past every human", False), ("headshot_rate", "headshot rate past every human", False)):
            rates = detector(evaluation("tf2"), kind)["rates"]
            first = plain(rates["cheater"], ci)
            self.assertIn(f"| {name} | {first} | {plain(rates['not banned'])} |", tf2)
        self.assertEqual({entry["kind"] for entry in stats["detectors"] if entry["status"] == "descriptive_only"}, {"extra", "supporting_extra"})
        self.assertIn(f"The other {len(unseen)} detectors are not observable on per-match totals", tf2)

    def test_the_cs2_numbers_follow_from_the_evaluation(self):
        artifact = evaluation("cs2")
        stats = artifact["statistics"]
        cs2 = text("examples", "cs2", "README.md")
        speed = detector(artifact, "speed")
        everyone = sum(cell["denominator"] for cell in speed["rates"].values())
        self.assertEqual((everyone, sum(cell["count"] for cell in speed["rates"].values())), (1529, 0))
        self.assertIn(f"could run on all {everyone:,} players and fired on none of them", cs2)
        rank = detector(artifact, "rank_tail")["rates"]
        self.assertIn(f"fired on {rank['cheater']['count']} of {rank['cheater']['denominator']} hand-labelled cheaters fpsdet could compare ({rank['cheater']['rate']:.1%}, 95% CI 6.6–18.8%)", cs2)
        self.assertEqual(plain(rank["cheater"], True), "12 of 106 (11.3%, 95% CI 6.6–18.8%)")
        self.assertEqual(plain(rank["clean, reviewed match"]), "0 of 302 (0.0%, 0.0–1.3%)")
        queue = stats["decisions"]["queues"]["review_or_watch"]
        self.assertEqual((queue["size"], queue["by_label"]["cheater"]), (18, 14))
        self.assertIsNone(queue["positive_share"]["rate"])  # under 20: not a rated share
        self.assertEqual(round(14 / 18 * 100), 78)
        lo, hi = wilson(14, 18)
        self.assertIn(f"the 95% interval runs from {lo:.0%} to {hi:.0%}", cs2)
        self.assertIn(f"could run on only {detector(artifact, 'median_distance')['rates']['cheater']['denominator']} hand-labelled cheaters each", cs2)
        unseen = [entry for entry in stats["detectors"] if entry["status"] == "not_observable"]
        self.assertIn(f"**Not observable here:** {len(unseen)} detectors", cs2)

    def test_the_results_in_the_docs_are_the_evaluation_s(self):
        doc = text("docs", "calibration.md")
        for name in ("tf2", "cs2"):
            artifact = evaluation(name)
            stats = artifact["statistics"]
            positive, primary = stats["population"]["positive"], stats["population"]["primary_comparison"]
            for entry in stats["detectors"]:
                if entry["status"] == "not_observable":
                    continue
                row = f"| {entry['kind']} | {entry['status']} | {cal._pct(entry['rates'][positive])} | {cal._pct(entry['rates'][primary])} | {cal._ratio(entry['enrichment'][primary])} |"
                self.assertIn(row, doc)
            unseen = [entry["kind"] for entry in stats["detectors"] if entry["status"] == "not_observable"]
            self.assertIn(f"**Not observable here ({len(unseen)}).** " + ", ".join(unseen[:-1]) + f" and {unseen[-1]}.", doc)
            self.assertIn(artifact["inputs"]["detector"][:15], doc)
        tf2 = evaluation("tf2")["statistics"]["decisions"]
        self.assertIn(f"RGL cheating-ban labelled: {cal._pct(tf2['by_group']['cheater']['of_evaluated']['review_or_watch'])}", doc)
        self.assertIn(f"never-banned comparison: {cal._pct(tf2['by_group']['not banned']['of_evaluated']['review_or_watch'])}", doc)
        self.assertIn(f"ratio: {cal._ratio(tf2['enrichment']['not banned']['review_or_watch'])}", doc)
        self.assertIn(f"51/97 = 52.6% (42.7%–62.2%), against a chance level of {tf2['queues']['review_or_watch']['prevalence']:.1%}", doc)
        cs2 = evaluation("cs2")["statistics"]["decisions"]["by_group"]
        self.assertIn(f"{cal._pct(cs2['cheater']['of_evaluated']['review_or_watch'])} of hand-labelled cheaters went to watch", doc)

    def test_the_fixture_table_in_the_docs(self):
        doc = text("docs", "calibration.md")
        result = cal.qualify_fixtures()
        for outcome in ("passes_controlled_fixture", "fails_controlled_fixture", "no_controlled_fixture"):
            kinds = [entry["kind"] for entry in result["detectors"] if entry["outcome"] == outcome]
            if kinds:
                self.assertIn(f"| {outcome} | {', '.join(kinds)} |", doc)
            else:
                self.assertNotIn(f"| {outcome} |", doc)
        self.assertIn(f"All {len(result['honest_fixtures_clean'])} honest fixtures in the demo have no finding at all.", doc)


if __name__ == "__main__":
    unittest.main()
