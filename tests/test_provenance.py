"""Provenance: the detector code and the parsed profile behind every case's evidence."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from fpsdet import provenance
from fpsdet.baseline import CohortTable, build_cohorts
from fpsdet.cli import main as cli_main
from fpsdet.evidence import Observation
from fpsdet.models import Event
from fpsdet.parse import load_events, parse_event, profile_from_dict
from fpsdet.persist import case_to_dict, cohort_from_dict, cohort_to_dict, event_to_dict
from fpsdet.provenance import ProvenanceMismatch, cohort_digest, integrity_digest, player_digest, player_inputs
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
            self.assertEqual(len({id(case.provenance.run) for case in cases}), 1)
            shared = set()
            for case in cases:
                block = dict(case_to_dict(case)["evidence"]["provenance"])
                self.assertEqual(block.pop("inputs")["digest"], case.provenance.inputs.digest)
                block.pop("history")  # per player too: the history rows the scorer could read for them
                shared.add(json.dumps(block, sort_keys=True))
            self.assertEqual(len(shared), 1)
        block = case_to_dict(self.scored[0])["evidence"]["provenance"]
        self.assertEqual(block["version"], 1)
        self.assertEqual(block["profile"]["digest"], EXAMPLE_PROFILE_DIGEST)
        self.assertEqual((block["detector"]["digest"], block["detector"]["modules"]), (detector_fingerprint().digest, list(DETECTOR_MODULES)))

    def test_provenance_is_names_and_digests_only(self):
        text = json.dumps(case_to_dict(self.scored[0])["evidence"]["provenance"])
        self.assertNotIn(str(PACKAGE_DIR), text)
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn("def ", text)
        self.assertNotIn("/", re.sub(r"fpsdet\.[a-z-]+/1", "", text))

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


# The planted demo's background cohort, and the one player in examples/shot.jsonl. CI checks both on 3.11 and 3.12.
BACKGROUND_COHORT_DIGEST = "sha256:49b3f7e16bd4fc4b895aa1e65ffed3b38c70f38280f9be3666a82c4b4add1941"
SHOT_FILE_INPUT_DIGEST = "sha256:84f184c43e373c5bd170befdddaab430f6b19fdd5cc7b6711f22ccdc96b0f558"


def table_of(rows) -> CohortTable:
    table = CohortTable()
    for band, key, metric, player_id, value in rows:
        table.add(band, key, metric, player_id, value)
    return table


ROWS = [
    ("elite", "rifle", "accuracy", "a", 0.31),
    ("elite", "rifle", "accuracy", "b", 0.35),
    ("average", "rifle", "accuracy", "c", 0.2),
    ("average", "smg", "headshot_rate", "d", 0.4),
]


class CohortFingerprintTest(unittest.TestCase):
    def test_the_same_baseline_in_any_order_has_one_digest(self):
        digest = cohort_digest(table_of(ROWS)._values)
        self.assertRegex(digest, DIGEST)
        self.assertEqual(cohort_digest(table_of(ROWS)._values), digest)
        self.assertEqual(cohort_digest(table_of(list(reversed(ROWS)))._values), digest)

    def test_each_part_of_a_row_counts(self):
        base = cohort_digest(table_of(ROWS)._values)
        changed = {
            "value": ("elite", "rifle", "accuracy", "a", 0.32),
            "player": ("elite", "rifle", "accuracy", "z", 0.31),
            "band": ("advanced", "rifle", "accuracy", "a", 0.31),
            "key": ("elite", "dmr", "accuracy", "a", 0.31),
            "metric": ("elite", "rifle", "headshot_rate", "a", 0.31),
        }
        for what, row in changed.items():
            self.assertNotEqual(cohort_digest(table_of([row] + ROWS[1:])._values), base, what)
        # The same numbers held by other players are a different baseline: scoring leaves the subject out by id.
        swapped = [("elite", "rifle", "accuracy", "b", 0.31), ("elite", "rifle", "accuracy", "a", 0.35)] + ROWS[2:]
        self.assertNotEqual(cohort_digest(table_of(swapped)._values), base)
        # Every occurrence counts.
        self.assertNotEqual(cohort_digest(table_of(ROWS + ROWS[:1])._values), base)
        # Nothing is rounded: values one float apart are different baselines.
        nudged = [("elite", "rifle", "accuracy", "a", math.nextafter(0.31, 1.0))] + ROWS[1:]
        self.assertNotEqual(cohort_digest(table_of(nudged)._values), base)

    def test_the_digest_is_the_same_on_every_python(self):
        demo = build_demo()
        cohort = build_cohorts(summarize(demo.population, demo.profile), demo.profile)
        self.assertEqual(cohort_digest(cohort._values), BACKGROUND_COHORT_DIGEST)

    def test_a_file_in_any_layout_loads_to_the_same_baseline(self):
        table = table_of(ROWS)
        written = cohort_to_dict(table)
        shuffled = dict(reversed(list(written.items())))
        shuffled["metrics"] = [dict(row, players=list(reversed(row["players"]))) for row in reversed(written["metrics"])]
        for text in (json.dumps(written, indent=2), json.dumps(shuffled, separators=(",", ":"))):
            loaded = cohort_from_dict(json.loads(text))
            self.assertEqual(cohort_digest(loaded._values), cohort_digest(table._values))
            self.assertEqual(loaded.stored_digest, "matched")

    def test_an_older_file_still_loads_and_is_digested(self):
        legacy = cohort_to_dict(table_of(ROWS))
        del legacy["provenance"]
        loaded = cohort_from_dict(legacy)
        self.assertEqual(loaded.stored_digest, "absent")
        self.assertEqual(provenance.cohort_fingerprint(loaded, "external").digest, cohort_digest(table_of(ROWS)._values))

    def test_a_file_that_does_not_match_its_digest_is_refused(self):
        written = cohort_to_dict(table_of(ROWS))
        edited = json.loads(json.dumps(written))
        edited["metrics"][0]["players"][0]["value"] = 0.99
        with self.assertRaises(ProvenanceMismatch):
            cohort_from_dict(edited)
        claimed = json.loads(json.dumps(written))
        claimed["provenance"]["cohort"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ProvenanceMismatch):
            cohort_from_dict(claimed)
        hidden = json.loads(json.dumps(written))
        hidden["integrity"] = {"status": "ok", "alarms": []}  # someone clears a poison warning
        with self.assertRaises(ProvenanceMismatch):
            cohort_from_dict(hidden)
        future = json.loads(json.dumps(written))
        future["provenance"]["cohort"]["recipe"] = "fpsdet.cohort/9"
        with self.assertRaises(ProvenanceMismatch):
            cohort_from_dict(future)

    def test_the_integrity_stamp_is_separate_from_the_baseline(self):
        table = table_of(ROWS)
        before = provenance.cohort_fingerprint(table, "external")
        table.integrity = {"status": "poison_risk", "alarms": ["match m9: the lobby hit 92%"]}
        after = provenance.cohort_fingerprint(table, "external")
        self.assertEqual(after.digest, before.digest)
        self.assertNotEqual(after.integrity_digest, before.integrity_digest)
        self.assertEqual((before.integrity_status, after.integrity_status), ("unchecked", "poison_risk"))
        self.assertEqual(integrity_digest({"alarms": [], "status": "ok"}), integrity_digest({"status": "ok", "alarms": []}))

    def test_the_command_line_writes_a_sealed_baseline_and_scores_against_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / "week.ndjson"
            cohort = Path(tmp) / "cohort.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                cli_main(["sample", "--out", str(events)])
                cli_main(["baseline", str(events), "--profile", str(PROFILE_PATH), "--out", str(cohort)])
                cli_main(["score", str(events), "--profile", str(PROFILE_PATH), "--cohort", str(cohort), "--out", str(Path(tmp) / "cases")])
            stored = json.loads(cohort.read_text(encoding="utf-8"))
            case = json.loads((Path(tmp) / "cases" / "rage.json").read_text(encoding="utf-8"))
            block = case["evidence"]["provenance"]["cohort"]
            self.assertEqual((block["mode"], block["stored_digest"]), ("external", "matched"))
            self.assertEqual(block["digest"], stored["provenance"]["cohort"]["digest"])
            self.assertEqual(block["integrity"]["status"], stored["integrity"]["status"])
            stored["metrics"][0]["players"][0]["value"] += 0.01
            cohort.write_text(json.dumps(stored), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stop:
                cli_main(["score", str(events), "--profile", str(PROFILE_PATH), "--cohort", str(cohort)])
            self.assertIn("do not match the digest", str(stop.exception))

    def test_a_baseline_fitted_on_the_scored_events_says_so(self):
        demo = build_demo()
        cases = run_score(demo.events, demo.profile)
        block = case_to_dict(cases[0])["evidence"]["provenance"]["cohort"]
        fitted = build_cohorts(summarize(demo.events, demo.profile), demo.profile)
        self.assertEqual((block["mode"], block["stored_digest"], block["digest"]), ("in_file", "not_from_file", cohort_digest(fitted._values)))
        external = run_score(demo.events, demo.profile, build_cohorts(summarize(demo.population, demo.profile), demo.profile))
        self.assertEqual(case_to_dict(external[0])["evidence"]["provenance"]["cohort"]["mode"], "external")


def shot(player_id: str = "p", t_ms: int = 0, **values) -> Event:
    fields = dict(game_id="g", match_id="m1", player_id=player_id, t_ms=t_ms, event_type="shot", skill_band="average", weapon_class="rifle")
    fields.update(values)
    return Event(**fields)


class InputFingerprintTest(unittest.TestCase):
    def test_the_same_parsed_events_have_the_same_digest(self):
        events = [shot(t_ms=index * 100, hit=index % 3 == 0, distance_m=20.0 + index) for index in range(10)]
        self.assertEqual(player_digest(events), player_digest([shot(t_ms=index * 100, hit=index % 3 == 0, distance_m=20.0 + index) for index in range(10)]))
        self.assertRegex(player_digest(events), DIGEST)

    def test_raw_formatting_does_not_matter(self):
        raw = [{"game_id": "g", "match_id": "m1", "player_id": "p", "t_ms": 100 * i, "hit": True, "distance_m": 31, "dmg": 4.5, "zap": 1} for i in range(5)]
        spaced = [parse_event(json.loads(json.dumps(row, indent=3))) for row in raw]
        reordered = [parse_event(dict(reversed(list(row.items())))) for row in raw]
        self.assertEqual(player_digest(spaced), player_digest(reordered))
        self.assertEqual(reordered[0].extras, {"zap": 1.0, "dmg": 4.5})  # extras arrive in another order and still match

    def test_a_changed_event_moves_only_its_own_player(self):
        events = [shot("a", 0, hit=True), shot("a", 100), shot("b", 0, hit=True), shot("b", 100)]
        before = player_inputs(events)
        changed = events[:3] + [shot("b", 100, distance_m=40.0)]
        after = player_inputs(changed)
        self.assertEqual(after["a"], before["a"])
        self.assertNotEqual(after["b"].digest, before["b"].digest)

    def test_every_occurrence_counts(self):
        events = [shot(t_ms=0, hit=True), shot(t_ms=100)]
        digest = player_digest(events)
        self.assertNotEqual(player_digest(events + events[:1]), digest)
        self.assertNotEqual(player_digest(events[:1]), digest)
        found = player_inputs(events + [shot(t_ms=50, match_id="m2")])["p"]
        self.assertEqual((found.events, found.matches), (3, 2))

    def test_absent_and_present_stay_distinct(self):
        self.assertEqual(player_digest([shot(mod_set=())]), player_digest([shot()]))  # an empty mod set is not sent
        self.assertNotEqual(player_digest([shot(hidden_track_ms=0.0)]), player_digest([shot()]))  # zero is a value
        self.assertNotEqual(player_digest([shot(distance_m=30.0)]), player_digest([shot(distance_m=30)]))

    def test_values_that_are_not_finite_are_hashed_the_same_way_every_time(self):
        nan = parse_event(json.loads('{"game_id": "g", "match_id": "m", "player_id": "p", "t_ms": 0, "distance_m": NaN}'))
        inf = parse_event(json.loads('{"game_id": "g", "match_id": "m", "player_id": "p", "t_ms": 0, "distance_m": Infinity}'))
        self.assertEqual(player_digest([nan]), player_digest([parse_event(event_to_dict(nan))]))
        self.assertEqual(len({player_digest([nan]), player_digest([inf]), player_digest([shot(match_id="m", distance_m=1.0)])}), 3)

    def test_the_digest_is_the_same_on_every_python(self):
        events, _ = load_events(ROOT / "examples" / "shot.jsonl")
        self.assertEqual(player_inputs(events)["p-1044"].digest, SHOT_FILE_INPUT_DIGEST)

    def test_player_events_1_binds_arrival_order(self):
        # Since event normalization the scorer reads each player's canonical timeline, so reversing one
        # player's events no longer changes the case. player-events/1 still describes the arrival order.
        events = [shot("w", index * 300, weapon_class="rifle", hidden_track_ms=200.0) for index in range(10)]
        events += [shot("w", 5000 + index * 300, weapon_class="smg", hidden_track_ms=200.0) for index in range(10)]
        profile = build_demo().profile
        forward, backward = run_score(events, profile)[0], run_score(list(reversed(events)), profile)[0]
        self.assertEqual(len(forward.reasons), 2)
        self.assertEqual((forward.reasons, forward.seal), (backward.reasons, backward.seal))
        self.assertNotEqual(player_digest(events), player_digest(list(reversed(events))))

    def test_interleaving_players_moves_nothing(self):
        # Each player's own order kept, players interleaved differently: the same cases and the same digests.
        demo = build_demo()
        cohort = build_cohorts(summarize(demo.population, demo.profile), demo.profile)
        by_player: dict[str, list] = {}
        for event in demo.events:
            by_player.setdefault(event.player_id, []).append(event)
        regrouped = [event for player in sorted(by_player, reverse=True) for event in by_player[player]]
        first = {case.player_id: case_to_dict(case) for case in run_score(demo.events, demo.profile, cohort)}
        second = {case.player_id: case_to_dict(case) for case in run_score(regrouped, demo.profile, cohort)}
        self.assertEqual(first, second)

    def test_inputs_leave_observation_ids_and_seals_alone(self):
        demo = build_demo()
        for case in demo.cases:
            stamped = case_to_dict(case)["evidence"]
            kept, case.provenance = case.provenance, provenance.CaseProvenance(case.provenance.run, None)
            try:
                self.assertEqual(case_to_dict(case)["evidence"]["observations"], stamped["observations"])
                self.assertEqual(evidence_seal(case), case.seal)
            finally:
                case.provenance = kept
            self.assertEqual(stamped["provenance"]["inputs"]["events"], sum(1 for event in demo.events if event.player_id == case.player_id))


if __name__ == "__main__":
    unittest.main()
