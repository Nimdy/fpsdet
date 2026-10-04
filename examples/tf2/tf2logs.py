"""Run fpsdet on real Team Fortress 2 matches: logs.tf server logs, labelled by RGL's cheating bans.

logs.tf keeps the match logs that TF2 community servers upload, with per-weapon shots
and hits from the server's supplemental stats plugin. RGL, a competitive TF2 league,
publishes its bans with a reason. Together they give what a one-match dataset cannot:
players banned for cheating, each with dozens of server-logged matches before the ban,
and the honest players they shared lobbies with.

Only public, unauthenticated endpoints are used. Requests go one at a time, about one a
second, with a User-Agent naming this project, and every response is cached so nothing
is fetched twice. Nothing downloaded is committed. Player ids are replaced by keyed
pseudonyms whose key never leaves your machine, so published results name nobody.

    python examples/tf2/tf2logs.py cohort --out ~/tf2
    python examples/tf2/tf2logs.py fetch --out ~/tf2
    python examples/tf2/tf2logs.py convert ~/tf2
    PYTHONPATH=src python -m fpsdet baseline ~/tf2/baseline.ndjson --profile examples/tf2/tf2.json --screen-matches --out ~/tf2/cohort.json
    PYTHONPATH=src python -m fpsdet score ~/tf2/scored.ndjson --profile examples/tf2/tf2.json --cohort ~/tf2/cohort.json --out ~/tf2/cases
    python examples/tf2/tf2logs.py report ~/tf2/cases --labels ~/tf2/labels.json

Standard library only. Not affiliated with Valve, logs.tf or RGL.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from pathlib import Path

USER_AGENT = "fpsdet-research/0.3 (+https://github.com/Nimdy/detect-FPS-hackers)"
RGL_BANS = "https://api.rgl.gg/v0/bans/paged?take=100&skip={skip}"
LOG_LIST = "https://logs.tf/api/v1/log?player={steamid}&limit=10000"
LOG = "https://logs.tf/api/v1/log/{log_id}"
PAUSE_S = 0.6

# A match counts when it is a team format the leagues play: 6v6, prolander or highlander.
MIN_PLAYERS = 12
# Weapons where the player's aim decides the hit, one shot at a time. Rockets, stickies and
# pipes hit by splash, flamethrowers by stream, and a minigun's bullet count swamps everything.
AIMED = {
    "scattergun", "force_a_nature", "soda_popper", "shortstop", "pep_brawler_blaster", "back_scatter",
    "pistol", "pistol_scout", "winger", "the_winger", "pep_pistol",
    "shotgun_soldier", "shotgun_pyro", "shotgun_hwg", "shotgun_primary", "shotgun", "reserve_shooter",
    "family_business", "frontier_justice", "widowmaker", "the_rescue_ranger", "panic_attack",
    "sniperrifle", "the_machina", "awper_hand", "bazaar_bargain", "shooting_star", "sydney_sleeper",
    "pro_rifle", "the_classic", "the_hitmans_heatmaker", "smg", "pro_smg",
    "revolver", "ambassador", "enforcer", "letranger", "diamondback",
}
# Weapons that can headshot, so the log's headshot count belongs to them.
HEADSHOT = {
    "sniperrifle", "the_machina", "awper_hand", "bazaar_bargain", "shooting_star", "pro_rifle",
    "the_classic", "the_hitmans_heatmaker", "ambassador",
}


def get(url: str, tries: int = 4) -> bytes:
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            return urllib.request.urlopen(request, timeout=90).read()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def ban_label(reason: str) -> str | None:
    """RGL's reason text to a label. Helping a cheater, or selling cheats, is not cheating in a match."""
    text = re.sub(r"<[^>]+>", " ", reason or "").lower()
    if "cheat" in text and not re.search(r"assisting|distribution|developing", text):
        return "cheater"
    if "vac ban" in text:
        return "vac"
    return None


def steamid64(text: str) -> str | None:
    """The 17-digit SteamID in a field. A few RGL records carry trailing junk such as '&r=40'."""
    found = re.match(r"\s*(7656\d{13})", str(text))
    return found.group(1) if found else None


def steam3(steamid64: str) -> str:
    return f"[U:1:{int(steamid64) - 76561197960265728}]"


def cmd_cohort(args: argparse.Namespace) -> int:
    """RGL's public ban list: who was banned, when, and why."""
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    bans: list[dict] = []
    skip = 0
    while True:
        page = json.loads(get(RGL_BANS.format(skip=skip)))
        bans += page
        if len(page) < 100:
            break
        skip += 100
        time.sleep(PAUSE_S)
    banned: dict[str, dict] = {}
    rank = {"cheater": 0, "vac": 1, "other ban": 2}
    for ban in bans:
        steamid = steamid64(ban.get("steamId") or "")
        if steamid is None:
            continue
        label = ban_label(ban.get("reason") or "") or "other ban"
        current = banned.get(steamid)
        if current is None or rank[label] < rank[current["label"]]:
            banned[steamid] = {"label": label, "banned": ban["createdAt"][:10]}
    (out / "bans.json").write_text(json.dumps(banned, indent=1), encoding="utf-8")
    print(f"{len(bans)} RGL bans: {dict(Counter(b['label'] for b in banned.values()))}")
    return 0


def _day(epoch: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def cmd_fetch(args: argparse.Namespace) -> int:
    """Each labelled account's most recent team matches before its ban, and nothing else."""
    root = Path(args.out).expanduser()
    banned = json.loads((root / "bans.json").read_text(encoding="utf-8"))
    (root / "lists").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    chosen: dict[str, list[int]] = {}
    for steamid, ban in banned.items():
        if ban["label"] not in args.labels:
            continue
        listing = root / "lists" / f"{steamid}.json"
        if not listing.exists():
            listing.write_bytes(get(LOG_LIST.format(steamid=steamid)))
            time.sleep(PAUSE_S)
        logs = json.loads(listing.read_text(encoding="utf-8")).get("logs", [])
        before = [log for log in logs if _day(log["date"]) < ban["banned"] and log.get("players", 0) >= MIN_PLAYERS]
        if len(before) < args.min_logs:
            continue
        before.sort(key=lambda log: log["date"], reverse=True)
        chosen[steamid] = [log["id"] for log in before[: args.per_account]]
    wanted = sorted({log_id for ids in chosen.values() for log_id in ids})
    (root / "chosen.json").write_text(json.dumps(chosen), encoding="utf-8")
    labels = Counter(banned[steamid]["label"] for steamid in chosen)
    print(f"{len(chosen)} labelled accounts {dict(labels)}, {len(wanted)} matches to fetch", file=sys.stderr)
    if args.plan:
        return 0
    missing = [log_id for log_id in wanted if not (root / "logs" / f"{log_id}.json").exists()]

    def fetch_one(log_id: int) -> None:
        target = root / "logs" / f"{log_id}.json"
        partial = target.with_suffix(".part")
        partial.write_bytes(get(LOG.format(log_id=log_id)))
        partial.replace(target)  # a run stopped halfway never leaves half a file behind
        time.sleep(PAUSE_S)

    # A couple of requests in flight, each followed by a pause: about two a second in all.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, _ in enumerate(pool.map(fetch_one, missing), start=1):
            if done % 200 == 0:
                print(f"fetched {done} of {len(missing)}", file=sys.stderr)
    print(f"Have {len(wanted)} match logs for {len(chosen)} labelled accounts in {root / 'logs'}")
    return 0


def _pseudonym(key: bytes, steam_id: str) -> str:
    """A keyed hash: stable across runs on your machine, and not reversible by anyone without the key."""
    return "tf-" + hmac.new(key, steam_id.encode(), hashlib.sha256).hexdigest()[:10]


def _key(root: Path) -> bytes:
    path = root / "pseudonym.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return bytes.fromhex(path.read_text(encoding="utf-8").strip())


def match_events(log_id: int, log: dict, player_ids: dict[str, str]) -> dict[str, list[dict]]:
    """One match's aimed shots as fpsdet shot events, per player.

    The log carries per-weapon totals, not single shots. Each shot becomes one event, hits
    first, spread over the time the player spent on that class. On weapons that can headshot,
    the log's headshot count splits the hits into head and body; elsewhere the hitbox is not sent.
    """
    info = log.get("info") or {}
    headshots_known = bool(info.get("hasHS_hit"))
    out: dict[str, list[dict]] = defaultdict(list)
    base = {"game_id": "tf2", "match_id": f"tf{log_id}", "map_id": info.get("map") or "unknown", "skill_band": "unrated"}
    for steam_id, player in (log.get("players") or {}).items():
        pid = player_ids.get(steam_id)
        if pid is None:
            continue
        weapons = [
            (stats.get("type") or "unknown", name, weapon)
            for stats in player.get("class_stats") or []
            for name, weapon in (stats.get("weapon") or {}).items()
            if name in AIMED and isinstance(weapon, dict) and (weapon.get("shots") or 0) > 0
        ]
        head_capable = [(cls, name, w) for cls, name, w in weapons if name in HEADSHOT and (w.get("hits") or 0) > 0]
        head_left = int(player.get("headshots_hit") or 0) if headshots_known else 0
        head_hits: dict[str, int] = {}
        total = sum(min(w["hits"], w["shots"]) for _, _, w in head_capable)
        for cls, name, w in head_capable:
            share = round(head_left * min(w["hits"], w["shots"]) / total) if total else 0
            head_hits[name] = min(share, w["hits"])
        span_ms = max(1, int(info.get("total_length") or 1)) * 1000
        for cls, name, w in weapons:
            shots = int(w["shots"])
            hits = min(int(w.get("hits") or 0), shots)
            heads = head_hits.get(name, 0)
            step = max(1, span_ms // shots)
            for k in range(shots):
                event = {**base, "player_id": pid, "t_ms": k * step, "event_type": "shot",
                         "weapon_class": cls, "weapon_id": name, "hit": k < hits}
                if k < hits and headshots_known and name in HEADSHOT:
                    event["hitbox"] = "head" if k < heads else "upper_torso"
                out[pid].append(event)
    return out


def cmd_convert(args: argparse.Namespace) -> int:
    """Labelled accounts' matches before their ban, and honest players split into baseline and scored halves.

    An honest player is one RGL never banned, seen in the same lobbies as the labelled accounts.
    A fixed bit of each pseudonym sends half of them to the baseline and half to be scored, so no
    one is judged against a baseline that includes them. A banned player's matches count only
    before the ban date. Honest players need at least --min-matches matches in these logs.
    """
    root = Path(args.root).expanduser()
    banned = json.loads((root / "bans.json").read_text(encoding="utf-8"))
    by_steam3 = {steam3(clean): ban for raw, ban in banned.items() if (clean := steamid64(raw))}
    key = _key(root)
    logs = sorted((root / "logs").glob("*.json"), key=lambda path: int(path.stem))
    seen: Counter[str] = Counter()
    for path in logs:
        log = json.loads(path.read_text(encoding="utf-8"))
        for steam_id in (log.get("players") or {}):
            seen[steam_id] += 1
    labels: dict[str, str] = {}
    sides: dict[str, str] = {}
    player_ids: dict[str, str] = {}
    for steam_id, count in seen.items():
        ban = by_steam3.get(steam_id)
        pid = _pseudonym(key, steam_id)
        if ban is None and count < args.min_matches:
            continue
        player_ids[steam_id] = pid
        labels[pid] = ban["label"] if ban else "not banned"
        sides[pid] = "scored" if ban or int(pid[-1], 16) % 2 else "baseline"
    counts: Counter[str] = Counter()
    with (root / "baseline.ndjson").open("w", encoding="utf-8") as baseline, (root / "scored.ndjson").open("w", encoding="utf-8") as scored:
        for path in logs:
            log = json.loads(path.read_text(encoding="utf-8"))
            day = _day(int((log.get("info") or {}).get("date") or 0))
            active = {
                steam_id: pid for steam_id, pid in player_ids.items()
                if steam_id in (log.get("players") or {})
                and (by_steam3.get(steam_id) is None or day < by_steam3[steam_id]["banned"])
            }
            for pid, events in match_events(int(path.stem), log, active).items():
                handle = scored if sides[pid] == "scored" else baseline
                counts[sides[pid]] += len(events)
                for event in events:
                    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    scored_labels = {pid: label for pid, label in labels.items() if sides[pid] == "scored"}
    (root / "labels.json").write_text(json.dumps(scored_labels, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{len(logs)} matches: {counts['baseline']} baseline shots from {sum(1 for s in sides.values() if s == 'baseline')} honest players, "
        f"{counts['scored']} scored shots from {dict(Counter(scored_labels.values()))}"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """fpsdet's decisions against the labels, from the ops.json that fpsdet score --out writes."""
    ops = json.loads((Path(args.cases) / "ops.json").read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    order = ("review", "watch", "clean", "insufficient_data")
    table: dict[str, Counter[str]] = defaultdict(Counter)
    fired: dict[str, Counter[str]] = defaultdict(Counter)
    flagged = []
    for row in ops["rows"]:
        label = labels.get(row["id"], "not in labels")
        table[label][row["decision"]] += 1
        for check in row["checks"]:
            fired[label][check] += 1
        if row["decision"] in ("review", "watch"):
            flagged.append((label, row["decision"], row["id"], row["why"]))
    width = max(len(label) for label in table) + 2
    print("label".ljust(width) + "".join(f"{name:>19}" for name in order) + f"{'players':>9}")
    for label in sorted(table):
        counts = table[label]
        print(label.ljust(width) + "".join(f"{counts[name]:>19}" for name in order) + f"{sum(counts.values()):>9}")
    print("\nChecks that fired, by label:")
    for label in sorted(fired):
        print(f"  {label}: {dict(fired[label].most_common())}")
    print("\nEvery review and watch:")
    for label, decision, pid, why in sorted(flagged, key=lambda item: (order.index(item[1]), item[0], item[2])):
        print(f"  {decision:<6} {label:<11} {pid:<14} {why[:110]}")
    return 0


PROJECT = "https://github.com/Nimdy/detect-FPS-hackers"
LABEL_NAMES = {
    "cheater": "banned for cheating",
    "not banned": "never banned",
    "other ban": "banned for something else",
    "vac": "VAC ban, mirrored by RGL",
}
LABEL_NOTES = {
    "banned for cheating": "RGL banned this account for cheating; only matches before the ban are here",
    "never banned": "RGL never banned this account; a few unlabelled cheaters may be among these",
    "banned for something else": "RGL banned this account for something other than cheating, such as an alt account",
    "VAC ban, mirrored by RGL": "Valve banned this account, possibly in another game, and RGL mirrored the ban",
}
# What the desk's case drawer reads. The page leaves the rest out.
DESK_CASE = ("decision", "recommended_action", "automated_action", "reasons", "observations", "checks", "party_note", "seal")
DESK_METRICS = ("accuracy", "headshot_rate")


def cmd_desk(args: argparse.Namespace) -> int:
    """The scored run as the review desk's TF2 page, with RGL's labels beside each decision."""
    ops = json.loads((Path(args.cases) / "ops.json").read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    for row in ops["rows"]:
        row["truth"] = LABEL_NAMES.get(labels.get(row["id"], ""), "unlabelled")
        row["metrics"] = [m for m in row["metrics"] if m["name"] in DESK_METRICS]
        if row.get("case"):
            row["case"] = {key: row["case"][key] for key in DESK_CASE if key in row["case"]}
    ops.update(
        {
            "synthetic": False,
            "truth_kind": "labelled",
            "truth_source": "RGL's public ban list",
            "honest_labels": ["never banned"],
            "cheat_labels": ["banned for cheating"],
            "truth_notes": LABEL_NOTES,
            "notes": {
                "data": "Team Fortress 2 league matches from logs.tf; labels from RGL's public bans",
                "split": "Honest players split by a keyed pseudonym: half built the baseline, half are scored here",
                "names": "Player names are keyed pseudonyms. Not affiliated with Valve, logs.tf or RGL",
            },
            "links": [
                {"text": "How this was built, and the full results", "href": PROJECT + "/tree/main/examples/tf2"},
                {"text": "Run it yourself and share what you find", "href": PROJECT + "/issues/new?template=real_data_result.yml"},
            ],
        }
    )
    Path(args.out).write_text(json.dumps(ops, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}: {len(ops['rows'])} players, {Path(args.out).stat().st_size // 1024} KB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cohort = sub.add_parser("cohort", help="Download RGL's public ban list")
    cohort.add_argument("--out", required=True)
    cohort.set_defaults(func=cmd_cohort)
    fetch = sub.add_parser("fetch", help="Download each labelled account's recent team matches before its ban")
    fetch.add_argument("--out", required=True)
    fetch.add_argument("--per-account", type=int, default=40)
    fetch.add_argument("--min-logs", type=int, default=20, help="Skip accounts with fewer team matches before the ban")
    fetch.add_argument("--labels", nargs="+", default=["cheater"], choices=["cheater", "vac"], help="Which banned accounts to fetch matches for")
    fetch.add_argument("--workers", type=int, default=2, help="Requests in flight at once; keep it small")
    fetch.add_argument("--plan", action="store_true", help="Fetch only the match lists and say how many matches would be fetched")
    fetch.set_defaults(func=cmd_fetch)
    convert = sub.add_parser("convert", help="Write baseline.ndjson, scored.ndjson and labels.json")
    convert.add_argument("root", help="The --out folder given to fetch")
    convert.add_argument("--min-matches", type=int, default=8, help="Honest players need this many matches in the logs")
    convert.set_defaults(func=cmd_convert)
    report = sub.add_parser("report", help="Decisions against the labels")
    report.add_argument("cases", help="Folder written by fpsdet score --out")
    report.add_argument("--labels", required=True)
    report.set_defaults(func=cmd_report)

    desk = sub.add_parser("desk", help="Write the review desk's TF2 page data from a scored run and the labels")
    desk.add_argument("cases", help="Folder written by fpsdet score --out")
    desk.add_argument("--labels", required=True)
    desk.add_argument("--out", default=str(Path(__file__).with_name("desk.json")))
    desk.set_defaults(func=cmd_desk)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
