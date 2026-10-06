"""Benchmark tf2-rgl-v2: the TF2 data rebuilt by anyone, with no private key, in several baseline draws.

tf2logs.py builds the published TF2 run (benchmark tf2-rgl-v1). Its ids are keyed pseudonyms, and the key
also decides which never-banned players are extra and which half builds the baseline, so nobody without
the curator's key can rebuild it. This script makes the same kind of data with public rules only:

- Sources are frozen at a date. RGL bans dated after it, and matches played after it, are left out, so a
  later download of the same public sources selects the same accounts and matches.
- Extra never-banned players come from the labelled accounts' lobbies in an order fixed by a public hash.
- Each player gets a public benchmark id, and each baseline draw sends a never-banned player to the baseline
  or to be scored by a public hash of the draw number and that id.

A public id is not pseudonymization: anyone holding a SteamID can compute it. It exists so two independent
rebuilds agree on who is who. Nothing keyed by it is published per player; publish aggregates and digests.

    python examples/tf2/tf2logs.py cohort --out ~/tf2v2
    python examples/tf2/tf2bench.py select --out ~/tf2v2
    python examples/tf2/tf2bench.py sources ~/tf2v2 --out ~/tf2v2/sources.json
    python examples/tf2/tf2bench.py convert ~/tf2v2 --draw 0 --out ~/tf2v2/draws/draw-0

Standard library only. Not affiliated with Valve, logs.tf or RGL.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tf2logs import LOG_LIST, MIN_PLAYERS, PAUSE_S, _day, _download, _team_matches, get, match_events, steam3, steamid64  # noqa: E402

BENCHMARK_IDENTITY = "fpsdet.tf2-benchmark-identity/1"
BASELINE_DRAW = "fpsdet.tf2-baseline-draw/1"
HONEST_ORDER = "fpsdet.tf2-honest-order/1"
SOURCES = "fpsdet.tf2-sources/1"
# The last whole day (UTC) before the published sources were downloaded on 2026-10-04.
FREEZE = "2026-10-03"


def benchmark_identity(steam_id: str) -> str:
    """A public hash of a SteamID3. The same for anyone; not a pseudonym, because anyone can recompute it."""
    return hashlib.sha256(f"{BENCHMARK_IDENTITY}\0{steam_id}".encode("utf-8")).hexdigest()


def benchmark_pid(steam_id: str) -> str:
    return "tfb-" + benchmark_identity(steam_id)[:12]


def draw_side(steam_id: str, draw: int) -> str:
    """Which side a never-banned player is on in one baseline draw."""
    digest = hashlib.sha256(f"{BASELINE_DRAW}\0{draw}\0{benchmark_identity(steam_id)}".encode("utf-8")).digest()
    return "baseline" if digest[0] < 128 else "scored"


def honest_order(steam_id: str) -> str:
    return hashlib.sha256(f"{HONEST_ORDER}\0{benchmark_identity(steam_id)}".encode("utf-8")).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _canon(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def frozen_bans(root: Path, freeze: str) -> dict[str, dict]:
    """RGL's bans dated on or before the freeze, by SteamID64. A later ban is not a ban here."""
    return {steamid: ban for steamid, ban in _read(root / "bans.json").items() if ban["banned"] <= freeze}


def _listing(root: Path, steamid: str) -> list[dict]:
    listing = root / "lists" / f"{steamid}.json"
    if not listing.exists():
        import time

        listing.parent.mkdir(exist_ok=True)
        listing.write_bytes(get(LOG_LIST.format(steamid=steamid)))
        time.sleep(PAUSE_S)
    return _read(listing).get("logs", [])


def select_cheaters(root: Path, freeze: str, per_account: int, min_logs: int) -> dict[str, list[int]]:
    """Accounts RGL banned for cheating on or before the freeze, with at least min_logs team matches before
    the ban: their per_account latest. The same rule as tf2logs.py fetch, on frozen bans."""
    chosen: dict[str, list[int]] = {}
    for steamid, ban in sorted(frozen_bans(root, freeze).items()):
        if ban["label"] != "cheater":
            continue
        before = [log for log in _listing(root, steamid) if _day(log["date"]) < ban["banned"] and log.get("players", 0) >= MIN_PLAYERS]
        if len(before) < min_logs:
            continue
        before.sort(key=lambda log: (log["date"], log["id"]), reverse=True)
        chosen[steamid] = [log["id"] for log in before[:per_account]]
    return chosen


def select_honest(root: Path, freeze: str, cheaters: dict[str, list[int]], players: int, per_account: int) -> dict[str, list[int]]:
    """Never-banned players seen at least three times in the labelled accounts' matches, taken in the public
    hash order until there are enough with per_account team matches up to the freeze: their latest."""
    flagged = {steam3(clean) for raw in frozen_bans(root, freeze) if (clean := steamid64(raw))}
    seen: Counter[str] = Counter()
    for log_id in sorted({log_id for ids in cheaters.values() for log_id in ids}):
        for steam_id in (_read(root / "logs" / f"{log_id}.json").get("players") or {}):
            seen[steam_id] += 1
    pool = sorted((sid for sid, count in seen.items() if sid not in flagged and count >= 3), key=honest_order)
    after = (datetime.date.fromisoformat(freeze) + datetime.timedelta(days=1)).isoformat()
    chosen: dict[str, list[int]] = {}
    for steam_id in pool:
        if len(chosen) >= players:
            break
        steamid = str(int(steam_id[5:-1]) + 76561197960265728)
        team = sorted(_team_matches(root, steamid, before=after), key=lambda log: (log["date"], log["id"]), reverse=True)
        if len(team) >= per_account:
            chosen[steamid] = [log["id"] for log in team[:per_account]]
    return chosen


def cmd_select(args: argparse.Namespace) -> int:
    """Choose the labelled accounts and the extra never-banned players, and download their matches."""
    root = Path(args.out).expanduser()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    cheaters = select_cheaters(root, args.freeze, args.per_account, args.min_logs)
    (root / "cheaters.json").write_text(json.dumps(cheaters, sort_keys=True), encoding="utf-8")
    wanted = sorted({log_id for ids in cheaters.values() for log_id in ids})
    missing = [log_id for log_id in wanted if not (root / "logs" / f"{log_id}.json").exists()]
    print(f"{len(cheaters)} labelled accounts, {len(wanted)} matches, {len(missing)} not yet downloaded", file=sys.stderr)
    if missing and args.plan:
        return 0  # the extra players come from these matches, so they cannot be chosen before the download
    _download(root, wanted, args.workers)
    honest = select_honest(root, args.freeze, cheaters, args.players, args.per_account)
    (root / "honest.json").write_text(json.dumps(honest, sort_keys=True), encoding="utf-8")
    extra = sorted({log_id for ids in honest.values() for log_id in ids} - set(wanted))
    missing = [log_id for log_id in extra if not (root / "logs" / f"{log_id}.json").exists()]
    print(f"{len(honest)} extra never-banned players, {len(extra)} more matches, {len(missing)} not yet downloaded", file=sys.stderr)
    if not args.plan:
        _download(root, extra, args.workers)
    return 0


def selected_logs(root: Path) -> list[int]:
    return sorted({log_id for name in ("cheaters.json", "honest.json") for ids in _read(root / name).values() for log_id in ids})


def sources(root: Path, freeze: str) -> dict:
    """The frozen sources as digests and counts: the bans, both selections and every selected log. It names
    nobody, and two rebuilds from the same upstream data get the same one."""
    bans = sorted([steamid, ban["label"], ban["banned"]] for steamid, ban in frozen_bans(root, freeze).items())
    logs = selected_logs(root)
    missing = [log_id for log_id in logs if not (root / "logs" / f"{log_id}.json").exists()]
    if missing:
        raise SystemExit(f"{len(missing)} selected matches are not downloaded: run select")
    files = [[log_id, hashlib.sha256((root / "logs" / f"{log_id}.json").read_bytes()).hexdigest()] for log_id in logs]
    cheaters, honest = _read(root / "cheaters.json"), _read(root / "honest.json")
    return {
        "format": SOURCES,
        "freeze": freeze,
        "bans": {"count": len(bans), "labels": dict(sorted(Counter(row[1] for row in bans).items())), "digest": _canon(bans)},
        "cheaters": {"accounts": len(cheaters), "digest": _canon(cheaters)},
        "honest": {"accounts": len(honest), "digest": _canon(honest)},
        "logs": {"count": len(files), "digest": _canon(files)},
    }


def cmd_sources(args: argparse.Namespace) -> int:
    found = sources(Path(args.root).expanduser(), args.freeze)
    Path(args.out).expanduser().write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(found))
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    """One baseline draw: baseline.ndjson, scored.ndjson and labels.json, by tf2logs.py convert's rules on the
    selected matches only, with public ids, and the draw choosing which never-banned players build the baseline.
    A banned player is always scored."""
    root = Path(args.root).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    by_steam3 = {steam3(clean): ban for raw, ban in frozen_bans(root, args.freeze).items() if (clean := steamid64(raw))}
    logs = selected_logs(root)
    eligible: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for log_id in logs:
        log = _read(root / "logs" / f"{log_id}.json")
        day = _day(int((log.get("info") or {}).get("date") or 0))
        if day > args.freeze:
            continue
        for steam_id in (log.get("players") or {}):
            ban = by_steam3.get(steam_id)
            if ban is None or day < ban["banned"]:
                eligible[steam_id].append((day, log_id))
    labels: dict[str, str] = {}
    sides: dict[str, str] = {}
    player_ids: dict[str, str] = {}
    kept: set[tuple[str, int]] = set()
    for steam_id, matches in sorted(eligible.items()):
        ban = by_steam3.get(steam_id)
        if ban is None and len(matches) < args.min_matches:
            continue
        pid = benchmark_pid(steam_id)
        player_ids[steam_id] = pid
        labels[pid] = ban["label"] if ban else "not banned"
        sides[pid] = "scored" if ban else draw_side(steam_id, args.draw)
        kept.update((steam_id, log_id) for _, log_id in sorted(matches, reverse=True)[: args.max_matches])
    counts: Counter[str] = Counter()
    with (out / "baseline.ndjson").open("w", encoding="utf-8") as baseline, (out / "scored.ndjson").open("w", encoding="utf-8") as scored:
        for log_id in logs:
            log = _read(root / "logs" / f"{log_id}.json")
            active = {
                steam_id: player_ids[steam_id] for steam_id in (log.get("players") or {})
                if steam_id in player_ids and (steam_id, log_id) in kept
            }
            for pid, events in match_events(log_id, log, active).items():
                handle = scored if sides[pid] == "scored" else baseline
                counts[sides[pid]] += len(events)
                for event in events:
                    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    scored_labels = {pid: label for pid, label in labels.items() if sides[pid] == "scored"}
    (out / "labels.json").write_text(json.dumps(scored_labels, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"draw {args.draw}: {len(logs)} matches, {counts['baseline']} baseline shots from {sum(1 for s in sides.values() if s == 'baseline')} "
        f"never-banned players, {counts['scored']} scored shots from {dict(sorted(Counter(scored_labels.values()).items()))}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select", help="Choose the labelled accounts and the extra never-banned players, and download their matches")
    select.add_argument("--out", required=True, help="The folder tf2logs.py cohort wrote bans.json to")
    select.add_argument("--freeze", default=FREEZE)
    select.add_argument("--per-account", type=int, default=20)
    select.add_argument("--min-logs", type=int, default=20)
    select.add_argument("--players", type=int, default=300, help="Extra never-banned players")
    select.add_argument("--workers", type=int, default=2, help="Requests in flight at once; keep it small")
    select.add_argument("--plan", action="store_true", help="Choose and count; download no match")
    select.set_defaults(func=cmd_select)
    found = sub.add_parser("sources", help="Write the frozen sources' digests")
    found.add_argument("root")
    found.add_argument("--freeze", default=FREEZE)
    found.add_argument("--out", required=True)
    found.set_defaults(func=cmd_sources)
    convert = sub.add_parser("convert", help="Write one baseline draw's baseline.ndjson, scored.ndjson and labels.json")
    convert.add_argument("root")
    convert.add_argument("--draw", type=int, required=True)
    convert.add_argument("--out", required=True)
    convert.add_argument("--freeze", default=FREEZE)
    convert.add_argument("--min-matches", type=int, default=8)
    convert.add_argument("--max-matches", type=int, default=20)
    convert.set_defaults(func=cmd_convert)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
