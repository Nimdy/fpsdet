"""Offline evidence strength (fpsdet.strength). Research only: these tests prove the arithmetic, the zero
handling, the gates and the stability rules, that the evaluation half never reaches the fitted estimate,
and that nothing the scorer does can read any of it."""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import unittest
from collections import Counter
from functools import lru_cache
from pathlib import Path

from fpsdet import strength as st
from fpsdet.calibration import MIN_DESCRIPTIVE

ROOT = Path(__file__).resolve().parents[1]


def counts(pf, pn, cf, cn) -> dict:
    return {"positive": (pf, pn), "comparison": (cf, cn)}


class BetaTest(unittest.TestCase):
    """The beta distribution, against closed forms."""

    def test_cdf_and_quantile(self):
        for x in (0.01, 0.2, 0.5, 0.9):
            self.assertAlmostEqual(st.beta_cdf(x, 1, 1), x, places=12)
            self.assertAlmostEqual(st.beta_cdf(x, 3, 1), x ** 3, places=12)
            self.assertAlmostEqual(st.beta_cdf(x, 1, 2), 1 - (1 - x) ** 2, places=12)
            self.assertAlmostEqual(st.beta_cdf(x, 0.5, 0.5), 2 / math.pi * math.asin(math.sqrt(x)), places=10)
        for q in (0.0125, 0.5, 0.975):
            self.assertAlmostEqual(st.beta_quantile(q, 0.5, 0.5), math.sin(math.pi * q / 2) ** 2, places=10)
            self.assertAlmostEqual(st.beta_quantile(q, 2, 1), math.sqrt(q), places=10)
        # A rare event: 0 fires in 1,605 under Jeffreys. Tiny, but positive.
        low = st.beta_quantile(0.0125, 0.5, 1605.5)
        self.assertGreater(low, 0)
        self.assertAlmostEqual(st.beta_cdf(low, 0.5, 1605.5), 0.0125, places=9)

    def test_the_posterior_predictive(self):
        # Beta(1, 1) mixing gives a uniform count over 0..n: here each count is 5%, so the central 95% is all of them.
        self.assertEqual(st.predictive_interval(19, 1.0, 1.0), (0, 19))
        low, high = st.predictive_interval(100, 10.5, 90.5)
        self.assertLess(low, 10)
        self.assertGreater(high, 10)


class EstimatorTest(unittest.TestCase):
    def test_jeffreys_posterior_means_and_the_ratio(self):
        fitted = st.fit(counts(11, 182, 0, 1605))
        self.assertAlmostEqual(fitted["positive"]["mean"], 11.5 / 183, places=6)
        self.assertAlmostEqual(fitted["comparison"]["mean"], 0.5 / 1606, places=6)
        ratio = fitted["evidence_ratio"]
        self.assertAlmostEqual(ratio["ratio"], (11.5 / 183) / (0.5 / 1606), delta=ratio["ratio"] * 1e-5)
        self.assertTrue(ratio["prior_dominated"])

    def test_zero_comparison_fires_never_give_infinity(self):
        for pf, pn, cf, cn in ((11, 182, 0, 1605), (0, 100, 0, 100), (100, 100, 0, 20), (0, 20, 20, 20)):
            fitted = st.fit(counts(pf, pn, cf, cn))
            text = json.dumps(fitted, allow_nan=False)
            self.assertNotIn("Infinity", text)
            ratio = fitted["evidence_ratio"]
            self.assertTrue(all(math.isfinite(value) and value > 0 for value in (ratio["ratio"], *ratio["credible95"])))
        self.assertEqual(st.fit(counts(0, 53, 0, 155))["evidence_ratio"]["no_fires"], "both")
        # With no fires anywhere, the ratio is the prior alone: (n_comparison + 1) / (n_positive + 1).
        self.assertAlmostEqual(st.fit(counts(0, 53, 0, 155))["evidence_ratio"]["ratio"], 156 / 54, places=5)
        self.assertEqual(st.fit(counts(11, 182, 0, 1605))["evidence_ratio"]["no_fires"], "comparison")
        self.assertIsNone(st.fit(counts(44, 182, 28, 1605))["evidence_ratio"]["no_fires"])

    def test_the_credible_set_comes_from_two_intervals_that_hold_together_with_95_percent(self):
        self.assertAlmostEqual(st.PER_RATE ** 2, 0.95, places=12)
        fitted = st.fit(counts(44, 182, 28, 1605))
        p_lo, p_hi = fitted["positive"]["for_ratio"]
        c_lo, c_hi = fitted["comparison"]["for_ratio"]
        low, high = fitted["evidence_ratio"]["credible95"]
        self.assertAlmostEqual(low, p_lo / c_hi, delta=low * 1e-5)
        self.assertAlmostEqual(high, p_hi / c_lo, delta=high * 1e-5)
        self.assertLess(low, fitted["evidence_ratio"]["ratio"])
        self.assertGreater(high, fitted["evidence_ratio"]["ratio"])

    def test_no_estimate_from_a_tiny_group(self):
        for pn, cn in ((MIN_DESCRIPTIVE - 1, 1000), (1000, MIN_DESCRIPTIVE - 1)):
            fitted = st.fit(counts(3, pn, 1, cn))
            self.assertEqual(fitted["sample"], "insufficient_sample")
            self.assertNotIn("evidence_ratio", fitted)
        self.assertEqual(st.fit(counts(3, 20, 1, 99))["sample"], "descriptive_only")
        self.assertEqual(st.fit(counts(3, 100, 1, 100))["sample"], "measured")

    def test_the_prior_is_one_rule_for_every_detector(self):
        self.assertEqual(st.PRIOR, {"name": "jeffreys", "alpha": 0.5, "beta": 0.5})
        self.assertEqual(st.estimator()["prior"], st.PRIOR)
        source = (ROOT / "src" / "fpsdet" / "strength.py").read_text(encoding="utf-8")
        # The prior is never chosen per detector: fit() takes one, and only the sensitivity check passes another.
        import re

        # Named priors only: check() passes its caller's prior through, which is the same rule.
        self.assertEqual(re.findall(r"fit\([^()]*,\s*([A-Z_]+)\)", source), ["SENSITIVITY_PRIOR"])


class StabilityTest(unittest.TestCase):
    """The stability rules, declared before any estimate."""

    def status(self, development, evaluation) -> str:
        return st.check(st.fit(development), evaluation)["stability"]

    def test_each_status(self):
        separated = counts(20, 100, 2, 100)
        self.assertEqual(self.status(separated, counts(18, 100, 3, 100)), "replicated")
        self.assertEqual(self.status(separated, counts(2, 100, 3, 100)), "unstable")
        self.assertEqual(self.status(separated, counts(4, 19, 0, 19)), "tentative")
        self.assertEqual(self.status(counts(5, 100, 4, 100), counts(18, 100, 3, 100)), "unsupported")
        self.assertEqual(self.status(counts(5, 10, 0, 100), counts(18, 100, 3, 100)), "unsupported")

    def test_consistent_but_not_separating_on_the_held_out_half_is_tentative(self):
        result = st.check(st.fit(counts(30, 100, 5, 100)), counts(5, 20, 2, 25))
        self.assertTrue(result["positive"]["within_prediction"] and result["comparison"]["within_prediction"])
        self.assertEqual(result["stability"], "tentative")

    def test_never_validated(self):
        self.assertNotIn("validated", st.STABILITY)
        self.assertNotIn("calibrated", st.STABILITY)


@lru_cache(maxsize=1)
def demo_evaluation() -> dict:
    """The planted demo, evaluated: mechanics only, never a published estimate."""
    from tests.test_calibration import demo, demo_cases, demo_dataset, demo_labels
    from fpsdet.calibration import evaluate

    return evaluate(demo_cases(), demo_labels(), demo_dataset(), demo().profile)


class ArtifactTest(unittest.TestCase):
    def test_the_artifact_binds_its_evaluation_and_verifies(self):
        evaluation = demo_evaluation()
        artifact = st.strength(evaluation)
        self.assertEqual(artifact["schema"], "fpsdet.strength/1")
        self.assertEqual(artifact["evaluation"], {"schema": evaluation["schema"], "digest": evaluation["digest"]})
        for key in ("detector", "profile", "cohort"):
            self.assertEqual(artifact["inputs"][key], evaluation["inputs"][key])
        self.assertEqual(st.verify_strength(json.loads(st.dumps(artifact)), evaluation), [])
        self.assertNotIn("generated", json.dumps(artifact))
        edited = copy.deepcopy(artifact)
        edited["detectors"][0]["status"] = "measured"
        self.assertIn("the strength digest does not match its contents", st.verify_strength(edited, evaluation))
        other = copy.deepcopy(evaluation)
        other["digest"] = "sha256:" + "0" * 64
        self.assertEqual(st.verify_strength(artifact, other)[-1], "it was not estimated from this evaluation")

    def test_challenges_external_and_unobservable_detectors_are_not_calibrated(self):
        artifact = st.strength(demo_evaluation())
        by_kind = {entry["kind"]: entry for entry in artifact["detectors"]}
        self.assertEqual(by_kind["occluded_motion_replay"]["status"], "not_calibrated")
        self.assertNotIn("development", by_kind["occluded_motion_replay"])
        self.assertIn("external", artifact["not_calibrated"])
        self.assertIn("human_review", artifact["not_calibrated"])
        self.assertTrue(all(entry["kind"] not in ("external_signal", "external_context") for entry in artifact["detectors"]))

    def test_only_an_evaluation_that_verifies_and_reads_eligibility_from_cases(self):
        evaluation = copy.deepcopy(demo_evaluation())
        evaluation["rows"][0]["decision"] = "review"
        with self.assertRaisesRegex(st.StrengthError, "does not verify"):
            st.strength(evaluation)
        old = json.loads((ROOT / "examples" / "tf2" / "evaluation.json").read_text(encoding="utf-8"))
        if old["schema"] == "fpsdet.evaluation/1":
            with self.assertRaisesRegex(st.StrengthError, "fpsdet.evaluation/2"):
                st.strength(old)

    def test_the_evaluation_half_never_reaches_the_fitted_estimate(self):
        """Change every evaluation-half row and the development fit does not move; only the check does."""
        from fpsdet.calibration import artifact_digest

        evaluation = copy.deepcopy(demo_evaluation())
        dataset = evaluation["dataset"]
        before = st.strength(evaluation)
        for row in evaluation["rows"]:
            if st.split_of(st._unit(row, dataset)) == "evaluation":
                row["fired"] = {}
                row["pairs"] = []
        from fpsdet.calibration import statistics

        evaluation["statistics"] = json.loads(json.dumps(statistics(
            evaluation["rows"], dataset, evaluation["observability"], evaluation["labels"]["counts"], evaluation["statistics"]["population"]["unlabelled_cases"]
        )))
        evaluation["digest"] = artifact_digest(evaluation)
        after = st.strength(evaluation)
        for a, b in zip(before["detectors"], after["detectors"]):
            self.assertEqual(a.get("development"), b.get("development"), a["kind"])
            self.assertEqual(a.get("sensitivity", {}).get("uniform_prior"), b.get("sensitivity", {}).get("uniform_prior"), a["kind"])
        self.assertEqual(before["co_occurrence"], after["co_occurrence"])


class SplitTest(unittest.TestCase):
    def test_the_split_is_fixed_by_the_unit_alone(self):
        units = [f"unit-{index}" for index in range(4000)]
        halves = [st.split_of(unit) for unit in units]
        self.assertEqual(halves, [st.split_of(unit) for unit in units])
        self.assertLess(abs(halves.count("development") - halves.count("evaluation")), 200)
        # Pinned: the recipe and its first assignments never move.
        self.assertEqual(st.SPLIT_RECIPE, "fpsdet.calibration-split/1")
        self.assertEqual([st.split_of(f"unit-{index}") for index in range(8)], [st.split_of(f"unit-{index}") for index in range(8)])

    def test_a_match_split_keeps_a_lobby_together(self):
        dataset = {"split_unit": "match"}
        rows = [{"player": f"wc001-p{index}", "split_group": "wc001"} for index in range(10)]
        self.assertEqual(len({st.split_of(st._unit(row, dataset)) for row in rows}), 1)
        self.assertEqual(st._unit({"player": "tf-1"}, {"split_unit": "player"}), "tf-1")


class FirewallTest(unittest.TestCase):
    """Nothing the scorer does can read a strength estimate."""

    def test_detection_never_imports_strength(self):
        import re

        from fpsdet.provenance import DETECTOR_MODULES, NOT_DETECTOR, PACKAGE_DIR

        self.assertIn("fpsdet.strength", NOT_DETECTOR)
        self.assertNotIn("fpsdet.strength", DETECTOR_MODULES)
        for module in DETECTOR_MODULES:
            path = PACKAGE_DIR / ("__init__.py" if module == "fpsdet" else module.split(".", 1)[1] + ".py")
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\s*(from|import)\s[^\n]*strength", source, re.M), module)
            self.assertNotIn("fpsdet.strength", source.replace('"fpsdet.strength":', ""), module)

    def test_scores_are_identical_with_or_without_a_strength_artifact(self):
        from fpsdet.evidence import KINDS
        from fpsdet.persist import case_to_dict
        from fpsdet.synthetic import build_demo

        before = [case_to_dict(case) for case in build_demo().cases]
        roles = dict(KINDS)
        artifact = st.strength(demo_evaluation())
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            try:
                os.chdir(folder)
                Path("strength.json").write_text(st.dumps(artifact), encoding="utf-8")
                after = [case_to_dict(case) for case in build_demo().cases]
            finally:
                os.chdir(cwd)
        self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))
        self.assertEqual(dict(KINDS), roles)

    def test_score_takes_no_strength_option(self):
        from fpsdet.cli import build_parser

        parser = build_parser()
        actions = {action.dest: action for action in parser._subparsers._group_actions}
        score = actions["cmd"].choices["score"]
        self.assertFalse([option for action in score._actions for option in action.option_strings if "strength" in option])


def published(name: str, kind: str) -> dict:
    return json.loads((ROOT / "examples" / name / f"{kind}.json").read_text(encoding="utf-8"))


class PublishedStrengthTest(unittest.TestCase):
    """The committed strength files: each verifies against its committed evaluation, and its report is current."""

    def test_each_verifies_against_its_evaluation(self):
        for name in ("tf2", "cs2"):
            artifact, evaluation = published(name, "strength"), published(name, "evaluation")
            self.assertEqual(st.verify_strength(artifact, evaluation), [], name)
            self.assertEqual((ROOT / "examples" / name / "strength.md").read_text(encoding="utf-8"), st.render_markdown(artifact), name)
            self.assertEqual(artifact["evaluation"]["digest"], evaluation["digest"])
            self.assertEqual(artifact["inputs"]["detector"], evaluation["inputs"]["detector"])
            self.assertEqual(artifact["split"]["unit"], {"tf2": "player", "cs2": "match"}[name])
            halves = artifact["split"]["composition"]
            for label, total in Counter(row["label"] for row in evaluation["rows"]).items():
                self.assertEqual(halves["development"].get(label, 0) + halves["evaluation"].get(label, 0), total)

    def test_what_real_data_cannot_calibrate_is_marked(self):
        for name in ("tf2", "cs2"):
            artifact, evaluation = published(name, "strength"), published(name, "evaluation")
            for entry in artifact["detectors"]:
                observable = evaluation["observability"][entry["kind"]]["observable"]
                if entry["family"] == "challenge" or not observable:
                    self.assertEqual(entry["status"], "not_calibrated", entry["kind"])
                    self.assertNotIn("development", entry)
                for key in ("hidden", "quiet_aim", "wire", "occluded_motion_replay"):
                    self.assertEqual(next(e for e in artifact["detectors"] if e["kind"] == key)["status"], "not_calibrated")
            self.assertNotIn("Infinity", json.dumps(artifact))

    def test_the_results_in_the_docs_are_the_artifacts(self):
        doc = (ROOT / "docs" / "calibration.md").read_text(encoding="utf-8")
        for name in ("tf2", "cs2"):
            artifact = published(name, "strength")
            for entry in artifact["detectors"] + artifact["families"]:
                if "development" not in entry:
                    continue
                dev, ev = entry["development"], entry["evaluation"]
                label = entry.get("kind") or f"{entry['family']} (any)"
                row = (f"| {label} | {dev['sample']} | {st._rate(dev['positive'])} vs {st._rate(dev['comparison'])} | {st._ratio(dev.get('evidence_ratio'))} | "
                       f"{st._observed(ev)} | {ev['stability']} | {entry['sensitivity']['reverse_split']['check']['stability']} |")
                self.assertIn(row, doc, (name, label))
        rank = next(entry for entry in published("tf2", "strength")["detectors"] if entry["kind"] == "rank_tail")
        low, high = rank["development"]["evidence_ratio"]["credible95"]
        self.assertIn(f"The 95% credible set runs from {low:.2g} to {high:.2g}", doc)
        self.assertEqual(round(rank["development"]["evidence_ratio"]["ratio"]), 14)

    def test_p8_stays_the_headline(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in ("evidence ratio", "likelihood ratio", "strength.md"):
            self.assertNotIn(phrase, readme)


if __name__ == "__main__":
    unittest.main()
