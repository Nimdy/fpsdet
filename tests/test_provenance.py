"""Provenance: the detector code and the parsed profile behind every case's evidence."""

from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from fpsdet import provenance
from fpsdet.baseline import build_cohorts
from fpsdet.evidence import Observation
from fpsdet.parse import profile_from_dict
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.provenance import (
    DETECTOR_MODULES,
    NOT_DETECTOR,
    PACKAGE_DIR,
    _fingerprint_dir,
    _module_file,
    detector_closure,
    detector_fingerprint,
    manifest_problems,
    profile_digest,
)
from fpsdet.score import assess_player
from fpsdet.signals import evidence_seal
from fpsdet.summarize import summarize
from fpsdet.synthetic import PROFILE_PATH, ROOT, build_demo

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# profiles/example-loadout.json as the scorer sees it. CI checks this on Python 3.11 and 3.12.
EXAMPLE_PROFILE_DIGEST = "sha256:a9cc799c5df2cae8cd7f82c75d33cea5ff0066310e8a9bb590a5585546a0929b"


def raw_profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def digest_of(raw: dict) -> str:
    return profile_digest(profile_from_dict(raw))


def reversed_keys(value):
    if isinstance(value, dict):
        return {key: reversed_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reversed_keys(item) for item in value]
    return value


def package_sources(folder: Path = PACKAGE_DIR) -> dict[str, str]:
    return {("fpsdet" if path.stem == "__init__" else f"fpsdet.{path.stem}"): path.read_text(encoding="utf-8") for path in folder.glob("*.py")}


class ProfileFingerprintTest(unittest.TestCase):
    def test_the_same_parsed_profile_has_the_same_digest(self):
        self.assertEqual(digest_of(raw_profile()), digest_of(raw_profile()))
        self.assertRegex(digest_of(raw_profile()), DIGEST)

    def test_formatting_and_key_order_do_not_matter(self):
        raw = raw_profile()
        spaced = json.loads(json.dumps(raw, indent=7))
        squashed = json.loads(json.dumps(raw, separators=(",", ":")))
        self.assertEqual({digest_of(spaced), digest_of(squashed), digest_of(reversed_keys(raw))}, {digest_of(raw)})

    def test_a_threshold_change_moves_it(self):
        base = digest_of(raw_profile())
        for change in ({"min_shots": 41}, {"hidden_track_min_ms": 1300}, {"vendor_min_z": 4.5}, {"game_id": "another"}):
            self.assertNotEqual(digest_of({**raw_profile(), **change}), base, change)
        rules = raw_profile()
        rule = next(iter(rules["weapons"]))
        rules["weapons"][rule] = {**rules["weapons"][rule], "min_shot_interval_ms": rules["weapons"][rule]["min_shot_interval_ms"] + 1}
        self.assertNotEqual(digest_of(rules), base)

    def test_what_the_scorer_sees_is_what_counts(self):
        base = {**raw_profile()}
        base.pop("min_shots", None)
        # The parser reads an explicit 0 as the default 40. The digest describes the 40 that ran.
        self.assertEqual({digest_of({**base, "min_shots": 0}), digest_of({**base, "min_shots": 40})}, {digest_of(base)})
        self.assertEqual(digest_of({**base, "recoil_pattern": None}), digest_of({**base, "recoil_pattern": "learnable"}))

    def test_notes_are_not_detection(self):
        self.assertEqual(digest_of({**raw_profile(), "notes": "rewritten"}), digest_of(raw_profile()))

    def test_order_counts_only_where_the_scorer_reads_it_in_order(self):
        raw = raw_profile()
        self.assertGreater(len(raw["weight_classes"]), 1)
        # Weight classes are matched first to last, so their order is configuration.
        self.assertNotEqual(digest_of({**raw, "weight_classes": list(reversed(raw["weight_classes"]))}), digest_of(raw))
        # A recoil floor's mods are matched as a sorted build key, so their order is not.
        floors = copy.deepcopy(raw["recoil_floors"])
        floor = next(row for row in floors if len(row.get("mod_set") or []) > 1)
        floor["mod_set"] = list(reversed(floor["mod_set"]))
        self.assertEqual(digest_of({**raw, "recoil_floors": floors}), digest_of(raw))
        grouped = {**raw, "extra_metrics": [{"name": "n", "source": "n", "direction": "high", "group_by": ["skill_band", "weapon_class"]}]}
        regrouped = {**raw, "extra_metrics": [{"name": "n", "source": "n", "direction": "high", "group_by": ["weapon_class", "skill_band"]}]}
        self.assertNotEqual(digest_of(grouped), digest_of(regrouped))

    def test_a_threshold_that_is_not_finite_is_still_plain_json(self):
        never = digest_of({**raw_profile(), "vendor_min_z": float("inf")})
        self.assertRegex(never, DIGEST)
        self.assertNotEqual(never, digest_of(raw_profile()))

    def test_the_example_profile_digest_is_the_same_on_every_python(self):
        self.assertEqual(digest_of(raw_profile()), EXAMPLE_PROFILE_DIGEST)


class DetectorFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.package = Path(self.tmp.name) / "checkout" / "src" / "fpsdet"
        shutil.copytree(PACKAGE_DIR, self.package, ignore=shutil.ignore_patterns("__pycache__"))
        self.base = _fingerprint_dir(self.package).digest

    def tearDown(self):
        self.tmp.cleanup()

    def edited(self, module: str, add: bytes = b"\nMATERIAL = 1\n") -> str | None:
        path = _module_file(self.package, module)
        original = path.read_bytes()
        path.write_bytes(original + add)
        try:
            return _fingerprint_dir(self.package).digest
        finally:
            path.write_bytes(original)

    def test_the_same_source_has_the_same_digest_wherever_it_sits(self):
        self.assertRegex(self.base, DIGEST)
        self.assertEqual(self.base, detector_fingerprint().digest)
        self.assertEqual(self.base, _fingerprint_dir(self.package).digest)
        for path in self.package.glob("*.py"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(_fingerprint_dir(self.package).digest, self.base)  # a Windows checkout

    def test_every_detection_module_moves_it(self):
        for module in DETECTOR_MODULES:
            self.assertNotEqual(self.edited(module), self.base, module)

    def test_presentation_and_other_modules_do_not(self):
        for module in NOT_DETECTOR:
            self.assertEqual(self.edited(module), self.base, module)
        self.assertIn("fpsdet.board", NOT_DETECTOR)
        self.assertIn("fpsdet.opsview", NOT_DETECTOR)

    def test_a_static_page_does_not(self):
        page = self.package.parents[1] / "site" / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text("<p>one</p>", encoding="utf-8")
        page.write_text("<p>two</p>", encoding="utf-8")
        self.assertEqual(_fingerprint_dir(self.package).digest, self.base)

    def test_a_missing_source_is_reported_not_guessed(self):
        (self.package / "score.py").unlink()
        found = _fingerprint_dir(self.package)
        self.assertEqual((found.digest, found.missing), (None, ("fpsdet.score",)))


class DetectorBoundaryTest(unittest.TestCase):
    """The module list is checked against the imports, so a detection module cannot fall outside it."""

    def test_the_list_is_what_detection_imports(self):
        sources = package_sources()
        self.assertEqual(manifest_problems(sources), [])
        self.assertEqual(detector_closure(sources), sorted(DETECTOR_MODULES))
        self.assertFalse(set(DETECTOR_MODULES) & set(NOT_DETECTOR))
        self.assertEqual(set(DETECTOR_MODULES) | set(NOT_DETECTOR), set(sources))

    def test_a_new_detection_dependency_fails_until_it_is_listed(self):
        sources = package_sources()
        sources["fpsdet.newcheck"] = "THRESHOLD = 3\n"
        sources["fpsdet.score"] += "\n\ndef late():\n    from .newcheck import THRESHOLD\n    return THRESHOLD\n"
        self.assertEqual(manifest_problems(sources), ["fpsdet.newcheck is imported by detection code but is not in DETECTOR_MODULES"])
        self.assertEqual(manifest_problems(sources, manifest=(*DETECTOR_MODULES, "fpsdet.newcheck")), [])

    def test_detection_cannot_import_presentation(self):
        sources = package_sources()
        sources["fpsdet.signals"] += "\nfrom fpsdet import opsview\n"
        self.assertEqual(manifest_problems(sources), [
            "fpsdet.opsview is imported by detection code but is not in DETECTOR_MODULES",
            "fpsdet.opsview is listed as not detector code, but detection code imports it",
        ])

    def test_the_package_init_is_detection_code(self):
        # It runs on every import of the package, before any module, so it is fingerprinted and guarded.
        self.assertIn("fpsdet", DETECTOR_MODULES)
        self.assertNotIn("fpsdet", NOT_DETECTOR)
        sources = package_sources()
        sources["fpsdet"] += "\nfrom . import opsview\n"
        self.assertEqual(manifest_problems(sources), [
            "fpsdet.opsview is imported by detection code but is not in DETECTOR_MODULES",
            "fpsdet.opsview is listed as not detector code, but detection code imports it",
        ])

    def test_a_new_module_must_be_classified(self):
        sources = {**package_sources(), "fpsdet.export": "import json\n"}
        self.assertEqual(manifest_problems(sources), ["fpsdet.export is not classified: add it to DETECTOR_MODULES or NOT_DETECTOR"])

    def test_module_names_cannot_leave_the_package(self):
        for name in ("os", "fpsdet.../score", "fpsdet.score/../../x", "fpsdet.Score", "../fpsdet.score", "fpsdet."):
            with self.assertRaises(ValueError, msg=name):
                _module_file(PACKAGE_DIR, name)
        self.assertEqual(_module_file(PACKAGE_DIR, "fpsdet.score"), PACKAGE_DIR / "score.py")


class CaseProvenanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = build_demo()
        cohort = build_cohorts(summarize(cls.demo.population, cls.demo.profile), cls.demo.profile)
        cls.scored = run_score(cls.demo.events, cls.demo.profile, cohort)

    def test_every_case_of_a_run_shares_one_provenance(self):
        for cases in (self.scored, self.demo.cases):
            self.assertEqual(len({id(case.provenance) for case in cases}), 1)
            blocks = {json.dumps(case_to_dict(case)["evidence"]["provenance"], sort_keys=True) for case in cases}
            self.assertEqual(len(blocks), 1)
        block = case_to_dict(self.scored[0])["evidence"]["provenance"]
        self.assertEqual(block["version"], 1)
        self.assertEqual(block["profile"]["digest"], EXAMPLE_PROFILE_DIGEST)
        self.assertEqual((block["detector"]["digest"], block["detector"]["modules"]), (detector_fingerprint().digest, list(DETECTOR_MODULES)))

    def test_provenance_is_names_and_digests_only(self):
        text = json.dumps(case_to_dict(self.scored[0])["evidence"]["provenance"])
        self.assertNotIn(str(PACKAGE_DIR), text)
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn("def ", text)
        self.assertNotIn("/", text.replace("fpsdet.profile/1", "").replace("fpsdet.detector/1", ""))

    def test_observation_ids_and_seals_do_not_depend_on_provenance(self):
        for case in self.demo.cases:
            stamped = case_to_dict(case)["evidence"]
            seal = evidence_seal(case)
            kept, case.provenance = case.provenance, None
            try:
                bare = case_to_dict(case)["evidence"]
                self.assertEqual(stamped["observations"], bare["observations"], case.player_id)
                self.assertEqual(stamped["eligibility"], bare["eligibility"])
                self.assertEqual(evidence_seal(case), seal)
                self.assertEqual(case.seal, seal)
                self.assertIsNone(bare["provenance"])
            finally:
                case.provenance = kept
        material = Observation(family="physics", kind="speed", role="review", subject_id="p").material()
        self.assertFalse({"provenance", "profile", "detector"} & set(material))

    def test_another_profile_moves_provenance_and_nothing_else(self):
        other = profile_from_dict({**raw_profile(), "notes": "same detection", "poison_jump": 0.09})
        record = next(r for r in summarize(self.demo.events, self.demo.profile) if r.player_id == "rage")
        cohort = build_cohorts(summarize(self.demo.population, self.demo.profile), self.demo.profile)
        first, second = assess_player(record, cohort, self.demo.profile), assess_player(record, cohort, other)
        provenance.stamp([first], self.demo.profile)
        provenance.stamp([second], other)
        one, two = case_to_dict(first)["evidence"], case_to_dict(second)["evidence"]
        self.assertNotEqual(one["provenance"]["profile"], two["provenance"]["profile"])
        self.assertEqual(one["provenance"]["detector"], two["provenance"]["detector"])
        self.assertEqual(one["observations"], two["observations"])

    def test_a_case_scored_outside_a_run_says_it_has_none(self):
        record = summarize(self.demo.events, self.demo.profile)[0]
        cohort = build_cohorts(summarize(self.demo.population, self.demo.profile), self.demo.profile)
        self.assertIsNone(case_to_dict(assess_player(record, cohort, self.demo.profile))["evidence"]["provenance"])


if __name__ == "__main__":
    unittest.main()
