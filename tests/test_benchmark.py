"""The reproducible benchmark (fpsdet.benchmark). These tests prove the mechanics: drift is classified,
input changes are caught and explained, nothing is one score, synthetic stays labelled synthetic, and
nothing the scorer runs can read the benchmark. Tests of the committed results are in
PublishedBenchmarkTest, at the end."""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from fpsdet import benchmark as bm

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def synthetic_result() -> dict:
    return bm.run_synthetic(bm.manifest("synthetic"))


class ManifestTest(unittest.TestCase):
    def test_every_manifest_is_complete(self):
        for entry in bm.definition()["datasets"]:
            found = bm.read_json(ROOT / entry["manifest"])
            self.assertEqual(bm.check_manifest(found), [], entry["id"])
            self.assertEqual(bm.manifest(entry["id"])["id"], entry["id"])
        self.assertEqual([bm.manifest(name)["id"] for name in ("synthetic", "tf2", "cs2")], ["synthetic-v1", "tf2-rgl-v1", "cs2cd-v1"])

    def test_real_manifests_name_their_source_licence_labels_and_selection(self):
        for name in ("tf2", "cs2"):
            found = bm.manifest(name)
            self.assertTrue(found["source"]["origins"] and found["source"]["fetched"] and found["source"]["mutable"])
            self.assertTrue(found["license"]["terms"] and found["license"]["committed"] and found["license"]["attribution"])
            self.assertTrue(found["pseudonymization"]["method"])
            self.assertIn("not ground truth", found["label_semantics"]["statement"])
            self.assertTrue(found["selection"]["rules"])
            self.assertTrue(all(isinstance(step, dict) for step in found["acquisition"]["steps"] + found["prepare"]["steps"]))
            for key in ("events", "labels", "cohort"):
                self.assertTrue(found["inputs"][key], (name, key))

    def test_the_tf2_fetch_pins_the_parameters_the_published_run_used(self):
        steps = [step["run"] for step in bm.manifest("tf2")["acquisition"]["steps"]]
        fetch = next(step for step in steps if "fetch" in step)
        self.assertEqual(fetch[fetch.index("--per-account") + 1], "20")
        self.assertEqual(fetch[fetch.index("--min-logs") + 1], "20")

    def test_no_raw_or_original_ids_are_committed(self):
        found = bm.manifest("tf2")
        self.assertIn("Nothing downloaded is committed", found["license"]["committed"])
        for path in (ROOT / "benchmark").rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\[U:1:\d+\]|7656119\d{10}", text), path)  # no SteamID3 or SteamID64


class CompareTest(unittest.TestCase):
    """Every difference has a category, and the status says which kind of difference it is."""

    def setUp(self):
        self.result = copy.deepcopy(synthetic_result())
        self.expected = bm.expected_entry(self.result)

    def status(self, edit) -> dict:
        moved = copy.deepcopy(self.result)
        edit(moved)
        return bm.compare(moved, self.expected)

    def test_match_and_environment(self):
        self.assertEqual(bm.compare(self.result, self.expected)["status"], "MATCH")
        other = self.status(lambda r: r["environment"].update(python="3.99.0"))
        self.assertEqual(other["status"], "ENVIRONMENT_ONLY")
        self.assertEqual(bm.compare(self.result, None)["status"], "NOT_RUN")

    def test_each_kind_of_change(self):
        cases = {
            "detector code changed": (lambda r: r["chain"].update(detector="sha256:" + "1" * 64), "PROVENANCE_ONLY"),
            "decision changed": (lambda r: r["semantic"]["demo"].update(decisions_digest="sha256:" + "2" * 64), "DRIFT"),
            "eligibility changed": (lambda r: r["semantic"]["eligibility_states"].update(speed=[]), "DRIFT"),
            "qualification changed": (lambda r: r["semantic"]["challenge"][0].update(observed="not_caught"), "DRIFT"),
            "input changed": (lambda r: r["chain"]["code"].update({"src/fpsdet/fixtures.py": "sha256:" + "3" * 64}), "INPUT_CHANGED"),
            "profile changed": (lambda r: r["chain"]["profiles"].update(demo="sha256:" + "4" * 64), "INPUT_CHANGED"),
        }
        for category, (edit, status) in cases.items():
            with self.subTest(category):
                found = self.status(edit)
                self.assertEqual(found["status"], status)
                self.assertIn(category, found["categories"])

    def test_categories_are_causes_and_effects_not_their_echoes(self):
        """Packet and artifact digests move with whatever they bind, and a report's text with its numbers:
        they are named only when nothing else explains a change."""
        def inputs_moved(r):
            r["chain"]["code"]["src/fpsdet/fixtures.py"] = "sha256:" + "3" * 64
            r["chain"]["packets"]["demo"] = "sha256:" + "7" * 64
        found = self.status(inputs_moved)
        self.assertEqual((found["status"], found["categories"]), ("INPUT_CHANGED", ["input changed"]))
        found = self.status(lambda r: r["chain"]["packets"].update(demo="sha256:" + "7" * 64))
        self.assertEqual((found["status"], found["categories"]), ("PROVENANCE_ONLY", ["provenance changed"]))

    def test_a_published_mismatch_is_reported_not_raised(self):
        from fpsdet.calibration import PublishedMismatch

        source = (ROOT / "src" / "fpsdet" / "benchmark.py").read_text(encoding="utf-8")
        self.assertIn("except PublishedMismatch", source)
        self.assertTrue(issubclass(PublishedMismatch, Exception))
        self.assertIn(("published.decisions_reproduce", "decision changed", "semantic"), bm.FIELDS)

    def test_a_drift_says_why_with_the_code_change_beside_it(self):
        def edit(r):
            r["chain"]["detector"] = "sha256:" + "1" * 64
            r["semantic"]["demo"]["decisions_digest"] = "sha256:" + "2" * 64
        found = self.status(edit)
        self.assertEqual(found["status"], "DRIFT")
        self.assertEqual(found["categories"], ["decision changed", "detector code changed"])

    def test_missing_cryptography_is_an_environment_difference_not_a_drift(self):
        found = self.status(lambda r: r["semantic"].update(auth={"status": "not_run", "scenarios": []}))
        self.assertEqual(found["status"], "ENVIRONMENT_ONLY")

    def test_a_real_input_change_is_explained_exactly(self):
        found = bm.manifest("tf2")
        now = copy.deepcopy(found["inputs"])
        now["labels"]["digest"] = "sha256:" + "5" * 64
        now["key"] = "sha256:" + "6" * 64
        changes = {change["input"]: change for change in bm.input_changes(found, now)}
        self.assertEqual(changes["labels"]["category"], "label changed")
        self.assertIn("pseudonym key is not the curator's", changes["key"]["detail"])
        self.assertEqual(bm.input_changes(found, found["inputs"]), [])


class PreparedInputTest(unittest.TestCase):
    """prepare and run read the prepared folder the manifest describes, and say exactly what differs."""

    def test_identity_of_a_prepared_folder(self):
        found = bm.manifest("cs2")
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "labels.json").write_text(json.dumps({"wc001-p1": "cheater"}), encoding="utf-8")
            now = bm.prepared_identity(found, data)
            self.assertIsNone(now["events"]["sha256"])
            self.assertEqual(now["labels"]["count"], 1)
            categories = {change["category"] for change in bm.input_changes(found, now)}
            self.assertEqual(categories, {"input changed", "label changed", "cohort changed"})
            with self.assertRaisesRegex(bm.BenchmarkError, "Run fpsdet benchmark prepare first"):
                bm.run_real(found, data, data / "out")

    def test_steps_fill_their_placeholders_and_concatenate(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "a.ndjson").write_text("1\n", encoding="utf-8")
            (data / "b.ndjson").write_text("2\n", encoding="utf-8")
            log = bm.run_steps([{"concat": ["a.ndjson", "b.ndjson"], "out": "c.ndjson"}], data, "python3")
            self.assertEqual((data / "c.ndjson").read_text(encoding="utf-8"), "1\n2\n")
            self.assertTrue(log)
            with self.assertRaises(bm.BenchmarkError):
                bm.run_steps([{"run": ["{python}", "-c", "import sys; sys.exit(3)"]}], data, "python3")

    def test_the_key_commitment_reveals_nothing_and_tells_keys_apart(self):
        with tempfile.TemporaryDirectory() as folder:
            one, two = Path(folder) / "one.key", Path(folder) / "two.key"
            one.write_text("00" * 32, encoding="utf-8")
            two.write_text("01" * 32, encoding="utf-8")
            self.assertNotEqual(bm.key_commitment(one), bm.key_commitment(two))
            self.assertNotIn("00" * 32, bm.key_commitment(one))
            self.assertIsNone(bm.key_commitment(Path(folder) / "missing.key"))


class SyntheticTest(unittest.TestCase):
    def test_every_detector_scenario_and_signature_state_behaves_as_documented(self):
        result = synthetic_result()
        published = result["published"]
        self.assertEqual(published["qualification_passes"], published["detectors"])
        self.assertEqual(published["detectors"], 22)
        self.assertEqual(published["challenge_as_expected"], published["challenge_scenarios"])
        if result["semantic"]["auth"]["status"] == "run":
            self.assertEqual(published["auth_as_expected"], published["auth_scenarios"])
        self.assertEqual(result["semantic"]["demo_failures"], [])
        self.assertEqual(result["semantic"]["fixture_failures"], [])
        for name in ("demo", "fixtures", "week"):
            self.assertEqual(result["chain"]["verified"][name]["problems"], [], name)

    def test_the_result_is_an_identity_not_a_moment(self):
        result = synthetic_result()
        self.assertEqual(result["digest"], bm.result_digest(result))
        again = copy.deepcopy(result)
        again["environment"]["python"] = "0.0"
        again["timings_s"] = {"build": 1e9}
        self.assertEqual(bm.result_digest(again), result["digest"])
        self.assertNotIn("generated", json.dumps({key: result[key] for key in bm.IDENTITY}))


class NoScoreTest(unittest.TestCase):
    """The benchmark is multidimensional: no overall number, anywhere."""

    def test_no_overall_score(self):
        text = json.dumps(synthetic_result())
        for word in ('"score"', '"overall"', '"leaderboard"', '"rank"'):
            self.assertNotIn(word, text)
        self.assertIn("There is no overall score", (ROOT / "src" / "fpsdet" / "benchmark.py").read_text(encoding="utf-8"))


class FirewallTest(unittest.TestCase):
    """Nothing the scorer runs can read the benchmark, its expectations or its results."""

    def test_detection_never_imports_or_reads_the_benchmark(self):
        from fpsdet.provenance import DETECTOR_MODULES, NOT_DETECTOR, PACKAGE_DIR

        self.assertIn("fpsdet.benchmark", NOT_DETECTOR)
        for module in DETECTOR_MODULES:
            path = PACKAGE_DIR / ("__init__.py" if module == "fpsdet" else module.split(".", 1)[1] + ".py")
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\s*(from|import)\s[^\n]*benchmark", source, re.M), module)
            for word in ("expected.json", "benchmark/", "fpsdet.benchmark-"):
                self.assertNotIn(word, source, module)

    def test_score_takes_no_benchmark_option(self):
        from fpsdet.cli import build_parser

        parser = build_parser()
        score = next(action for action in parser._subparsers._group_actions).choices["score"]
        self.assertFalse([option for action in score._actions for option in action.option_strings if "benchmark" in option or "expected" in option])

    def test_scores_are_identical_with_the_benchmark_loaded(self):
        from fpsdet.persist import case_to_dict
        from fpsdet.synthetic import build_demo

        before = [case_to_dict(case) for case in build_demo().cases]
        bm.load_expected()
        bm.context()
        after = [case_to_dict(case) for case in build_demo().cases]
        self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))


class PublishedBenchmarkTest(unittest.TestCase):
    """The committed benchmark: every artifact verifies, the report is what they generate, and the
    synthetic class still matches its pin on this Python."""

    def test_every_committed_artifact_verifies_without_scoring(self):
        failed = [check for check in bm.verify() if not check["ok"]]
        self.assertEqual(failed, [])

    def test_the_report_and_the_readme_block_are_generated(self):
        ctx = bm.context()
        self.assertEqual((ROOT / "docs" / "benchmark.md").read_text(encoding="utf-8"), bm.render_report(ctx))
        self.assertIn(bm.readme_block(ctx), (ROOT / "README.md").read_text(encoding="utf-8"))
        _rows, problems = bm.capability_matrix(ctx)
        self.assertEqual(problems, [])

    def test_the_synthetic_class_matches_its_pin_on_this_python(self):
        comparison = bm.compare(synthetic_result(), bm.load_expected()["datasets"]["synthetic-v1"])
        self.assertIn(comparison["status"], ("MATCH", "ENVIRONMENT_ONLY", "PROVENANCE_ONLY"), comparison)
        self.assertEqual([change for change in comparison["changes"] if change.get("kind") in ("semantic", "input")], [])

    def test_what_a_dataset_cannot_observe_is_never_a_zero(self):
        ctx = bm.context()
        for row in bm.coverage_matrix(ctx):
            for dataset in ctx["evaluations"]:
                cell = row[dataset]
                if not cell["observable"]:
                    self.assertEqual(cell["status"], "not_observable", (row["detector"], dataset))
                    self.assertIn(cell["strength"], ("not_calibrated",), (row["detector"], dataset))
        report = (ROOT / "docs" / "benchmark.md").read_text(encoding="utf-8")
        self.assertLess(report.index("## What this benchmark cannot prove"), report.index("## 1. Benchmark datasets"))
        self.assertIn("Controlled synthetic qualification", report)
        self.assertIn("not real-world calibration", report)

    def test_the_pinned_real_decisions_are_the_published_ones(self):
        expected = bm.load_expected()["datasets"]
        for name, short in (("tf2-rgl-v1", "tf2"), ("cs2cd-v1", "cs2")):
            dataset = json.loads((ROOT / "examples" / short / "evaluation.dataset.json").read_text(encoding="utf-8"))
            pinned = expected[name]["published"]
            self.assertTrue(pinned["decisions_reproduce"])
            for label, counts in dataset["published"]["decisions"].items():
                self.assertEqual(pinned["decisions"][label], counts, (name, label))

    def test_capability_evidence_is_worked_out_not_declared(self):
        rows = {row["id"]: row for row in bm.capability_matrix(bm.context())[0]}
        self.assertEqual(rows["hidden_target_tracking"]["evidence"], "controlled only")
        self.assertIn("which cheat they used is unknown", rows["statistical_aimbot"]["evidence"])
        self.assertIn("no separation shown", rows["speed_hack"]["evidence"])
        self.assertTrue(rows["no_behavior_wallhack"]["evidence"].startswith("architecture statement"))
        declared = json.loads((ROOT / "benchmark" / "capabilities.json").read_text(encoding="utf-8"))
        self.assertFalse(any("evidence" in technique for technique in declared["techniques"]))


if __name__ == "__main__":
    unittest.main()
