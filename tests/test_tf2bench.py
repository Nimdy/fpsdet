"""tf2-rgl-v2's selection: a third party with another key, or none, rebuilds the same data and draws."""

from __future__ import annotations

import calendar
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / "tf2" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tf2logs = _load("tf2logs")
tf2bench = _load("tf2bench")
BASE = 76561197960265728


def _epoch(day: str) -> int:
    return calendar.timegm(tuple(int(part) for part in day.split("-")) + (12, 0, 0))


def _log(day: str, players: list[int], seed: int) -> dict:
    return {
        "info": {"date": _epoch(day), "map": "cp_test", "total_length": 1800, "hasHS_hit": True, "hasHS": True},
        "players": {
            f"[U:1:{n}]": {"class_stats": [{"type": "scout", "kills": 10 + (n + seed) % 7, "total_time": 1800,
                                            "weapon": {"scattergun": {"shots": 20 + (n * seed) % 11, "hits": 8 + (n + seed) % 9}}}]}
            for n in players
        },
    }


def _world(root: Path) -> None:
    """Two accounts banned for cheating, one banned after the freeze, and a lobby of never-banned players.
    Every list and log is on disk, so nothing touches the network."""
    (root / "lists").mkdir(parents=True)
    (root / "logs").mkdir()
    lobby = list(range(100, 124))
    bans = {str(BASE + 1): {"label": "cheater", "banned": "2026-09-20"}, str(BASE + 2): {"label": "cheater", "banned": "2026-09-25"},
            str(BASE + 110): {"label": "other ban", "banned": "2026-09-30"}, str(BASE + 111): {"label": "cheater", "banned": "2026-10-05"}}
    (root / "bans.json").write_text(json.dumps(bans), encoding="utf-8")
    log_id = 1000
    lists: dict[int, list[dict]] = {}
    for cheater in (1, 2):
        for k in range(5):
            day = f"2026-09-{10 + k:02d}"
            players = [cheater, *lobby[(cheater * 3 + k) % 6 : (cheater * 3 + k) % 6 + 14]]
            (root / "logs" / f"{log_id}.json").write_text(json.dumps(_log(day, players, log_id)), encoding="utf-8")
            for n in players:
                lists.setdefault(n, []).append({"id": log_id, "date": _epoch(day), "players": len(players)})
            log_id += 1
    for n in lobby:
        for k in range(4):  # each never-banned player's own matches, one after the freeze
            day = "2026-10-04" if k == 3 else f"2026-09-{20 + k:02d}"
            (root / "logs" / f"{log_id}.json").write_text(json.dumps(_log(day, [n, *range(500, 511)], log_id)), encoding="utf-8")
            lists.setdefault(n, []).append({"id": log_id, "date": _epoch(day), "players": 12})
            log_id += 1
    for n, logs in lists.items():
        (root / "lists" / f"{BASE + n}.json").write_text(json.dumps({"logs": logs}), encoding="utf-8")


def _quiet(func, argv: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return func(argv)


def _files(folder: Path) -> dict[str, bytes]:
    return {name: (folder / name).read_bytes() for name in ("baseline.ndjson", "scored.ndjson", "labels.json")}


class ThirdPartyReproductionTest(unittest.TestCase):
    """The curator and a third party with another pseudonym key build the same tf2-rgl-v2 data and draws;
    tf2-rgl-v1's converter, keyed, gives them different ids and a different baseline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.curator, cls.third = cls.tmp / "curator", cls.tmp / "third"
        _world(cls.curator)
        shutil.copytree(cls.curator, cls.third)
        (cls.curator / "pseudonym.key").write_text("11" * 32, encoding="utf-8")
        (cls.third / "pseudonym.key").write_text("22" * 32, encoding="utf-8")
        for root in (cls.curator, cls.third):
            assert _quiet(tf2bench.main, ["select", "--out", str(root), "--per-account", "3", "--min-logs", "3", "--players", "6"]) == 0
            for draw in (0, 1):
                _quiet(tf2bench.main, ["convert", str(root), "--draw", str(draw), "--min-matches", "2", "--out", str(root / "draws" / f"draw-{draw}")])
            _quiet(tf2logs.main, ["convert", str(root), "--min-matches", "2"])

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp)

    def test_v2_is_identical_without_the_curators_key(self) -> None:
        for draw in (0, 1):
            place = Path("draws") / f"draw-{draw}"
            self.assertEqual(_files(self.curator / place), _files(self.third / place))
        self.assertEqual(tf2bench.sources(self.curator, tf2bench.FREEZE), tf2bench.sources(self.third, tf2bench.FREEZE))

    def test_v1_differs_under_another_key(self) -> None:
        self.assertNotEqual(_files(self.curator), _files(self.third))
        curator = json.loads((self.curator / "labels.json").read_text(encoding="utf-8"))
        third = json.loads((self.third / "labels.json").read_text(encoding="utf-8"))
        self.assertFalse(set(curator) & set(third), "v1 ids are keyed pseudonyms, so no id is shared")

    def test_the_draws_differ_and_never_move_a_banned_account(self) -> None:
        labels = [json.loads((self.curator / "draws" / f"draw-{draw}" / "labels.json").read_text(encoding="utf-8")) for draw in (0, 1)]
        self.assertNotEqual(labels[0], labels[1])
        banned = [{pid for pid, label in found.items() if label != "not banned"} for found in labels]
        self.assertEqual(banned[0], banned[1])
        self.assertEqual(len(banned[0]), 3, "two cheaters and the other ban; the ban after the freeze is not a ban")

    def test_the_freeze_drops_later_bans_and_matches(self) -> None:
        late = tf2bench.benchmark_pid("[U:1:111]")
        labels = json.loads((self.curator / "draws" / "draw-0" / "labels.json").read_text(encoding="utf-8"))
        baseline = (self.curator / "draws" / "draw-0" / "baseline.ndjson").read_text(encoding="utf-8")
        self.assertTrue(labels.get(late) == "not banned" or late in baseline)
        honest = json.loads((self.curator / "honest.json").read_text(encoding="utf-8"))
        late_logs = {log["id"] for path in (self.curator / "lists").glob("*.json")
                     for log in json.loads(path.read_text(encoding="utf-8"))["logs"] if log["date"] > _epoch("2026-10-03")}
        self.assertTrue(honest)
        self.assertFalse({log_id for ids in honest.values() for log_id in ids} & late_logs)

    def test_ids_are_public_hashes_of_the_steamid(self) -> None:
        self.assertEqual(tf2bench.benchmark_pid("[U:1:5]"), tf2bench.benchmark_pid("[U:1:5]"))
        self.assertTrue(tf2bench.benchmark_pid("[U:1:5]").startswith("tfb-"))
        self.assertIn(tf2bench.draw_side("[U:1:5]", 0), ("baseline", "scored"))
        source = (ROOT / "examples" / "tf2" / "tf2bench.py").read_text(encoding="utf-8")
        self.assertNotIn("pseudonym.key", source.replace("tf2logs.py", ""))
        self.assertNotIn("_key(", source)

    def test_a_changed_log_changes_the_source_digest(self) -> None:
        before = tf2bench.sources(self.third, tf2bench.FREEZE)
        log_id = json.loads((self.third / "cheaters.json").read_text(encoding="utf-8"))[str(BASE + 1)][0]
        path = self.third / "logs" / f"{log_id}.json"
        saved = path.read_bytes()
        try:
            path.write_bytes(saved.replace(b"cp_test", b"cp_edit"))
            after = tf2bench.sources(self.third, tf2bench.FREEZE)
        finally:
            path.write_bytes(saved)
        self.assertNotEqual(before["logs"], after["logs"])
        self.assertEqual(before["bans"], after["bans"])


if __name__ == "__main__":
    unittest.main()
