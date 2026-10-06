"""Measuring native detectors against labelled populations. Measurement only: nothing here changes scoring.

LabelAuditTest was written before the evaluation harness (P8, phase C0). It pins the label sets of the
committed real-data desks and recomputes the decision results the README and the example READMEs publish,
straight from those desks, so a published number that no longer follows from the data fails a test.
"""

from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
