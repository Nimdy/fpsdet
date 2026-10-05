"""Behavior lock: every field a consumer reads today, on every planted case and every case in the synthetic week.

The evidence work adds structure beside the case JSON. Nothing a consumer already reads may move while that
happens. tests/golden/ records the case fields as they were (decision, reasons, observations, checks, seal,
metrics, speed, the structured evidence, and the rest of ``case_to_dict``). This test scores the same inputs
again and compares those fields. A new key on a case is allowed. A changed or missing one fails, and the message
names the player. The evidence's ``provenance`` is left out: it names the code that ran, so it moves with any
detector change, and tests/test_provenance.py checks it instead.

To re-record after a deliberate change, run

    FPSDET_REGOLD=1 PYTHONPATH=src python -m unittest tests.test_golden

and say in the commit why every case that moved, moved.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import unittest
from collections import Counter
from pathlib import Path

from fpsdet.persist import case_to_dict
from fpsdet.synthetic import ROOT, build_demo
from fpsdet.week import build_week

GOLDEN = Path(__file__).resolve().parent / "golden"
PLANTED = GOLDEN / "planted.json"
WEEK = GOLDEN / "week.json"
REGOLD = os.environ.get("FPSDET_REGOLD") == "1"


def canon(value):
    """Floats to 10 significant digits. Python 3.11 and 3.12 can differ in the last bit of a float sum."""
    if isinstance(value, float):
        return float(f"{value:.10g}") if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {key: canon(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canon(item) for item in value]
    return value


def locked(case, keys: list[str]) -> dict:
    row = case_to_dict(case)
    if isinstance(row.get("evidence"), dict):
        # Provenance names the detector code and profile behind the evidence, so it moves whenever
        # detector source does. tests/test_provenance.py checks it; this lock covers the evidence itself.
        row["evidence"] = {key: value for key, value in row["evidence"].items() if key != "provenance"}
    return canon({key: row.get(key, "<missing>") for key in keys})


def digest(row: dict) -> str:
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stamp(case, keys: list[str]) -> str:
    return f"{case.decision}/{digest(locked(case, keys))}"


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


class GoldenPlantedTest(unittest.TestCase):
    """The 32 planted players: the whole case, field by field."""

    @classmethod
    def setUpClass(cls):
        cls.demo = build_demo()
        if REGOLD:
            keys = list(case_to_dict(cls.demo.cases[0]))
            _write(PLANTED, {"keys": keys, "cases": {case.player_id: locked(case, keys) for case in cls.demo.cases}})
        cls.golden = json.loads(PLANTED.read_text(encoding="utf-8"))

    def test_the_demo_still_passes(self):
        self.assertEqual(self.demo.failures, [])

    def test_every_planted_case_is_unchanged(self):
        keys = self.golden["keys"]
        now = {case.player_id: locked(case, keys) for case in self.demo.cases}
        self.assertEqual(sorted(now), sorted(self.golden["cases"]), "the planted players changed")
        for player_id, then in sorted(self.golden["cases"].items()):
            moved = [key for key in keys if now[player_id][key] != then[key]]
            self.assertEqual(moved, [], f"{player_id}: {', '.join(moved)} moved")

    def test_no_case_acts_on_its_own(self):
        self.assertEqual({case.automated_action for case in self.demo.cases}, {"none"})


class GoldenWeekTest(unittest.TestCase):
    """The synthetic week: 400 players, the weekly batch and each nightly batch."""

    @classmethod
    def setUpClass(cls):
        cls.week = build_week()
        if REGOLD:
            keys = list(case_to_dict(cls.week.cases[0]))
            nightly: dict[str, list[str]] = {}
            for night, cases in enumerate(cls.week.nightly):
                for case in cases:
                    nightly.setdefault(case.player_id, [""] * len(cls.week.nightly))[night] = stamp(case, keys)
            _write(WEEK, {
                "keys": keys,
                "weekly": {case.player_id: stamp(case, keys) for case in cls.week.cases},
                "nightly": nightly,
            })
        cls.golden = json.loads(WEEK.read_text(encoding="utf-8"))

    def test_every_weekly_case_is_unchanged(self):
        keys = self.golden["keys"]
        now = {case.player_id: stamp(case, keys) for case in self.week.cases}
        moved = sorted(pid for pid in set(now) | set(self.golden["weekly"]) if now.get(pid) != self.golden["weekly"].get(pid))
        self.assertEqual(moved, [], f"{len(moved)} weekly cases moved, first: {moved[:5]}")

    def test_every_nightly_case_is_unchanged(self):
        keys = self.golden["keys"]
        moved = []
        for night, cases in enumerate(self.week.nightly):
            now = {case.player_id: stamp(case, keys) for case in cases}
            then = {pid: stamps[night] for pid, stamps in self.golden["nightly"].items() if stamps[night]}
            moved += [(night, pid) for pid in sorted(set(now) | set(then)) if now.get(pid) != then.get(pid)]
        self.assertEqual(moved, [], f"{len(moved)} nightly cases moved, first: {moved[:5]}")

    def test_no_case_acts_on_its_own(self):
        everything = self.week.cases + [case for night in self.week.nightly for case in night]
        self.assertEqual({case.automated_action for case in everything}, {"none"})


class RealDataContractTest(unittest.TestCase):
    """The committed results of the real-match runs. A rerun that moves them has to say why.

    The scorer never sees the labels. These counts are what the README, the home page and the desk
    report, and tools/regress.py checks a local rerun against them case by case.
    """

    def counts(self, game: str) -> dict[str, dict[str, int]]:
        rows = json.loads((ROOT / "examples" / game / "desk.json").read_text(encoding="utf-8"))["rows"]
        table: dict[str, Counter] = {}
        for row in rows:
            table.setdefault(row["truth"], Counter())[row["decision"]] += 1
        return {label: dict(counter) for label, counter in table.items()}

    def test_tf2_counts_are_the_published_ones(self):
        self.assertEqual(self.counts("tf2"), {
            "banned for cheating": {"review": 3, "watch": 48, "clean": 131, "insufficient_data": 7},
            "never banned": {"watch": 30, "clean": 1575, "insufficient_data": 141},
            "banned for something else": {"watch": 16, "clean": 729, "insufficient_data": 76},
            "VAC ban, mirrored by RGL": {"clean": 7, "insufficient_data": 1},
        })

    def test_cs2_counts_are_the_published_ones(self):
        self.assertEqual(self.counts("cs2"), {
            "cheater": {"watch": 14, "clean": 92, "insufficient_data": 398},
            "clean, reviewed match": {"clean": 302, "insufficient_data": 273},
            "clean, unreviewed match": {"watch": 4, "clean": 435, "insufficient_data": 11},
        })


class RegressToolTest(unittest.TestCase):
    """tools/regress.py, which checks a local rerun of the real-match examples case by case."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        spec = importlib.util.spec_from_file_location("regress", ROOT / "tools" / "regress.py")
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_a_new_field_is_allowed_and_a_moved_one_is_not(self):
        before = {"a": {"player_id": "a", "decision": "clean", "reasons": []}, "b": {"player_id": "b", "decision": "watch", "reasons": ["x"]}}
        added = {pid: {**row, "evidence": {"version": 1}} for pid, row in before.items()}
        result = self.tool.compare(before, added)
        self.assertEqual((result["moved"], result["added"]["evidence"]), ({}, 2))
        moved = {**added, "b": {**added["b"], "decision": "review", "reasons": ["x", "y"]}}
        result = self.tool.compare(before, moved)
        self.assertEqual(result["moved"], {"b": ["decision", "reasons"]})
        self.assertEqual(result["transitions"], Counter({("watch", "review"): 1}))
        nested = {"a": {"player_id": "a", "evidence": {"version": 1, "observations": [{"kind": "x"}]}}}
        deeper = {"a": {"player_id": "a", "evidence": {"version": 1, "observations": [{"kind": "x", "note": 1}], "provenance": {}}}}
        result = self.tool.compare(nested, deeper)
        self.assertEqual((result["moved"], sorted(result["added"])), ({}, ["evidence.observations[].note", "evidence.provenance"]))
        self.assertEqual(self.tool.compare(deeper, nested)["moved"], {"a": ["evidence"]})
        # The detector digest moves with any code change; --ignore leaves it out of both sides.
        old = {"a": {"evidence": {"provenance": {"detector": {"digest": "x"}, "profile": {"digest": "p"}}}}}
        new = {"a": {"evidence": {"provenance": {"detector": {"digest": "y"}, "profile": {"digest": "p"}}}}}
        self.assertEqual(self.tool.compare(old, new)["moved"], {"a": ["evidence"]})
        ignored = ["evidence.provenance.detector"]
        self.assertEqual(self.tool.compare(self.tool.without(old, ignored), self.tool.without(new, ignored))["moved"], {})

    def test_a_snapshot_of_the_same_run_diffs_clean(self):
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap.jsonl"
            args = ["snapshot", str(ROOT / "examples" / "shot.jsonl"), "--profile", str(ROOT / "profiles" / "example-loadout.json"), "--out", str(out)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.tool.main(args), 0)
                self.assertEqual(self.tool.main(["diff", str(out), str(out)]), 0)
            rows = self.tool.read_snapshot(out)
        self.assertEqual({pid: row["decision"] for pid, row in rows.items()}, {"p-1044": "insufficient_data"})


if __name__ == "__main__":
    unittest.main()
