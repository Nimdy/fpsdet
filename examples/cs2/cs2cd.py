"""Turn CS2CD matches into fpsdet events, so fpsdet can be tried on real Counter-Strike 2 play.

CS2CD (Counter-Strike 2 Cheat Detection) is 795 CS2 matchmaking matches parsed from
server-recorded demos with demoparser2. Cheaters in the matches that contain one were
labelled by hand. It is published under CC BY 4.0 by Mille Mei Zhen Loo and Gert Lužkov:
https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection

This script only reads it. Nothing from the dataset is committed to this repo.

    pip install -r examples/cs2/requirements.txt
    python examples/cs2/cs2cd.py fetch no_cheater_present --first 0 --count 60 --out ~/cs2cd
    python examples/cs2/cs2cd.py convert ~/cs2cd/no_cheater_present --out baseline.ndjson
    python examples/cs2/cs2cd.py labels ~/cs2cd --out labels.json
    python examples/cs2/cs2cd.py report cases/ --labels labels.json

fpsdet itself stays dependency-free. Only this example needs pyarrow, pandas and
huggingface_hub. Not affiliated with Valve or with the dataset's authors.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = "datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection"
PROJECT = "https://github.com/Nimdy/detect-FPS-hackers"
SHARE = PROJECT + "/issues/new?template=real_data_result.yml"
HONEST_LABELS = ("clean, reviewed match", "clean, unreviewed match")
# What the desk's case drawer reads. The page leaves the rest out.
DESK_CASE = ("decision", "recommended_action", "automated_action", "reasons", "observations", "checks", "party_note", "seal")
DESK_METRICS = ("accuracy", "headshot_rate", "median_distance", "geometry_rate")
LABEL_NOTES = {
    "cheater": "banned by VAC and judged a cheater by the dataset's reviewers",
    "clean, reviewed match": "in a match the dataset's reviewers checked by hand",
    "clean, unreviewed match": "in a match nobody reviewed, so a few of these may be cheating",
}
SPLITS = ("no_cheater_present", "with_cheater_present")
SHORT = {"no_cheater_present": "nc", "with_cheater_present": "wc"}

# CS2 matchmaking servers run at 64 ticks a second. One Hammer unit is one inch.
TICK_MS = 1000 / 64
UNIT_M = 0.0254
# 250 units/s with the knife out is the fastest anything moves on foot. Every gun is slower,
# so this cap is loose on purpose: per-weapon caps live in compiled game data we do not ship.
FOOT_CAP_MPS = round(250 * UNIT_M, 3)
# A position jump faster than this is the demo teleporting someone (spawn, reconnect), not running.
TELEPORT_MPS = 40.0
MOVETYPE_LADDER = 9

# Only the events the converter reads. The rest (footsteps, purchases, cvars) is dropped on fetch.
EVENTS = ("weapon_fire", "player_hurt", "bullet_damage", "round_officially_ended", "cheaters", "CSstats_info")
# Only the tick columns the converter reads. A remote read fetches about a sixth of the file.
COLUMNS = [
    "tick", "steamid", "team_num", "X", "Y", "Z", "pitch", "yaw",
    "velocity_X", "velocity_Y", "is_airborne", "is_alive", "move_type",
    "shots_fired", "rank", "comp_rank_type", "is_warmup_period", "is_freeze_period",
]

CLASSES = {
    "rifle": ("ak47", "m4a1", "m4a1_silencer", "galilar", "famas", "sg556", "aug"),
    "sniper": ("awp", "ssg08", "g3sg1", "scar20"),
    "smg": ("mac10", "mp9", "mp7", "mp5sd", "ump45", "p90", "bizon"),
    "pistol": ("glock", "hkp2000", "usp_silencer", "p250", "fiveseven", "tec9", "cz75a", "deagle", "revolver", "elite"),
    "shotgun": ("nova", "xm1014", "sawedoff", "mag7"),
    "mg": ("negev", "m249"),
}
WEAPON_CLASS = {weapon: cls for cls, weapons in CLASSES.items() for weapon in weapons}

HITBOX = {
    "head": "head",
    "neck": "upper_torso",
    "chest": "upper_torso",
    "stomach": "lower_torso",
    "left_arm": "limbs",
    "right_arm": "limbs",
    "left_leg": "limbs",
    "right_leg": "limbs",
}
HITBOX_ORDER = ("head", "upper_torso", "lower_torso", "limbs")


def skill_band(rank_type: int | None, rank: int | None) -> str:
    """Matchmaking rank to fpsdet's bands. The rank is what they queued at, not their stats here.

    Both scales are cut on the game's own tiers, so a band means roughly the same on each.
    Type 12 is per-map Competitive, skill groups 1 (Silver I) to 18 (Global Elite): Silver,
    Gold Nova, Master Guardian to DMG, then Legendary Eagle and up.
    Type 11 is Premier, a rating: grey under 5,000, light blue under 10,000, blue and purple
    under 20,000, then pink and up.
    """
    if not rank or rank <= 0:
        return "unrated"
    if rank_type == 12 and rank <= 18:
        return "developing" if rank <= 6 else "average" if rank <= 10 else "advanced" if rank <= 14 else "elite"
    if rank_type == 11 and rank >= 100:
        return "developing" if rank < 5000 else "average" if rank < 10000 else "advanced" if rank < 20000 else "elite"
    return "unrated"


def _angle(pitch_a: float, yaw_a: float, pitch_b: float, yaw_b: float) -> float:
    """Degrees between two view directions."""
    def vec(pitch: float, yaw: float) -> tuple[float, float, float]:
        p, y = math.radians(pitch), math.radians(yaw)
        return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))

    a, b = vec(pitch_a, yaw_a), vec(pitch_b, yaw_b)
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(dot))


def player_id(split: str, number: int, steamid: str) -> str:
    return f"{SHORT[split]}{number:03d}-{steamid.replace('Player_', 'p')}"


def convert_match(json_path: Path, parquet_path: Path, *, movement_hz: float = 4.0) -> list[dict]:
    """One match as fpsdet event lines: one per shot fired from a gun, plus movement samples."""
    import pandas as pd

    split = json_path.parent.name
    number = int(json_path.stem)
    info = json.loads(json_path.read_text(encoding="utf-8"))
    meta = (info.get("CSstats_info") or [{}])[0]
    match_id = f"{SHORT[split]}{number:03d}"
    base = {"game_id": "cs2", "match_id": match_id, "map_id": meta.get("map") or "unknown"}

    ticks = pd.read_parquet(parquet_path, columns=COLUMNS)
    # A bot that fills an emptied team has no steamid. It is not a player under test.
    ticks = ticks[ticks["steamid"].astype(str).str.startswith("Player_")].sort_values(["steamid", "tick"])
    ticks = ticks.reset_index(drop=True)
    position = {key: index for index, key in enumerate(zip(ticks["steamid"], ticks["tick"]))}
    cols = {name: ticks[name].tolist() for name in COLUMNS}

    def row(steamid: str, tick: int, back: int = 8) -> int | None:
        """The tick row for this player, or the nearest one before it: demos skip the odd tick."""
        for offset in range(back):
            found = position.get((steamid, tick - offset))
            if found is not None:
                return found
        return None

    bands: dict[str, str] = {}
    for steamid, group in ticks.groupby("steamid"):
        ranked = group[group["rank"].fillna(0) > 0]
        if ranked.empty:
            bands[steamid] = "unrated"
            continue
        # The rank at the start of the match is the one they queued at. The end can include this result.
        first = ranked.iloc[0]
        bands[steamid] = skill_band(int(first["comp_rank_type"]), int(first["rank"]))

    hurts: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for hurt in info.get("player_hurt") or []:
        hurts[(hurt.get("attacker_steamid"), hurt["tick"])].append(hurt)
    bullets = {(b.get("attacker_steamid"), b.get("victim_steamid"), b["tick"]): b for b in info.get("bullet_damage") or []}

    events: list[dict] = []
    for fire in info.get("weapon_fire") or []:
        steamid = fire.get("user_steamid")
        weapon = str(fire.get("weapon") or "").removeprefix("weapon_")
        weapon_class = WEAPON_CLASS.get(weapon)
        if weapon_class is None or not str(steamid).startswith("Player_"):
            continue  # knives, grenades, the taser, the bomb
        tick = int(fire["tick"])
        at = row(steamid, tick)
        if at is None or cols["is_warmup_period"][at]:
            continue
        team = cols["team_num"][at]
        enemy_hits = []
        for hurt in hurts.get((steamid, tick), []):
            victim = hurt.get("user_steamid")
            victim_at = row(victim, tick) if victim else None
            if victim_at is None or cols["team_num"][victim_at] == team:
                continue  # team damage is not a hit on an enemy
            enemy_hits.append((hurt, victim_at))
        event = {
            **base,
            "player_id": player_id(split, number, steamid),
            "t_ms": int(round(tick * TICK_MS)),
            "event_type": "shot",
            "skill_band": bands.get(steamid, "unrated"),
            "weapon_class": weapon_class,
            "weapon_id": weapon,
            "hit": bool(enemy_hits),
        }
        shots_fired = cols["shots_fired"][at]
        if shots_fired is not None and not math.isnan(shots_fired) and shots_fired >= 1:
            event["spray_index"] = int(shots_fired) - 1
        before = row(steamid, tick - 2, back=3)
        if before is not None and before != at:
            event["view_delta_deg"] = round(
                _angle(cols["pitch"][before], cols["yaw"][before], cols["pitch"][at], cols["yaw"][at]), 3
            )
        if enemy_hits:
            boxes = [HITBOX.get(str(hurt.get("hitgroup"))) for hurt, _ in enemy_hits]
            known = [box for box in boxes if box]
            if known:
                event["hitbox"] = min(known, key=HITBOX_ORDER.index)
            hurt, victim_at = enemy_hits[0]
            bullet = bullets.get((steamid, hurt.get("user_steamid"), tick))
            if bullet is not None and bullet.get("distance") is not None:
                event["distance_m"] = round(float(bullet["distance"]) * UNIT_M, 2)
                event["through_geometry"] = int(bullet.get("num_penetrations") or 0) > 0
            else:
                gap = math.dist(
                    (cols["X"][at], cols["Y"][at], cols["Z"][at]),
                    (cols["X"][victim_at], cols["Y"][victim_at], cols["Z"][victim_at]),
                )
                event["distance_m"] = round(gap * UNIT_M, 2)
        events.append(event)

    if movement_hz > 0:
        step = max(1, round(64 / movement_hz))
        for index in range(len(ticks)):
            tick = cols["tick"][index]
            if tick % step or not cols["is_alive"][index] or cols["is_freeze_period"][index] or cols["is_warmup_period"][index]:
                continue
            vx, vy = cols["velocity_X"][index], cols["velocity_Y"][index]
            if vx is None or vy is None or math.isnan(vx) or math.isnan(vy):
                continue
            speed = math.hypot(vx, vy) * UNIT_M
            cause = "none"
            if cols["move_type"][index] == MOVETYPE_LADDER:
                cause = "ladder"
            elif speed > TELEPORT_MPS:
                cause = "unknown"
            steamid = cols["steamid"][index]
            events.append(
                {
                    **base,
                    "player_id": player_id(split, number, steamid),
                    "t_ms": int(round(tick * TICK_MS)),
                    "event_type": "movement",
                    "skill_band": bands.get(steamid, "unrated"),
                    "speed_mps": round(speed, 3),
                    "on_ground": not cols["is_airborne"][index],
                    "displacement_cause": cause,
                    "expected_max_ground_speed_mps": FOOT_CAP_MPS,
                }
            )
    events.sort(key=lambda event: (event["player_id"], event["t_ms"]))
    return events


def _rounds(info: dict) -> int:
    return len(info.get("round_officially_ended") or [])


def cmd_fetch(args: argparse.Namespace) -> int:
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    fs = HfFileSystem()
    names = fs.ls(f"{REPO}/{args.split}", detail=False)
    numbers = sorted(int(Path(name).stem) for name in names if name.endswith(".json"))
    chosen = numbers[args.first : args.first + args.count]
    target = Path(args.out).expanduser() / args.split
    target.mkdir(parents=True, exist_ok=True)
    kept = skipped = 0
    for number in chosen:
        json_path = target / f"{number}.json"
        parquet_path = target / f"{number}.parquet"
        if json_path.exists():
            info = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            with fs.open(f"{REPO}/{args.split}/{number}.json", "rb") as handle:
                info = {key: value for key, value in json.load(handle).items() if key in EVENTS}
            json_path.write_text(json.dumps(info, separators=(",", ":")), encoding="utf-8")
        if _rounds(info) < args.min_rounds:
            skipped += 1
            print(f"{args.split}/{number}: {_rounds(info)} rounds, under {args.min_rounds}. Skipped.", file=sys.stderr)
            continue
        if not parquet_path.exists():
            with fs.open(f"{REPO}/{args.split}/{number}.parquet", "rb", block_size=1 << 20) as handle:
                table = pq.read_table(handle, columns=COLUMNS)
            pq.write_table(table, parquet_path)
        kept += 1
        print(f"{args.split}/{number}: {_rounds(info)} rounds", file=sys.stderr)
    print(f"Kept {kept} matches, skipped {skipped} short ones, in {target}")
    return 0


def _matches(folder: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for json_path in sorted(folder.glob("*.json"), key=lambda path: int(path.stem)):
        parquet_path = json_path.with_suffix(".parquet")
        if parquet_path.exists():
            pairs.append((json_path, parquet_path))
    return pairs


def cmd_convert(args: argparse.Namespace) -> int:
    pairs = [pair for folder in args.folders for pair in _matches(Path(folder).expanduser())]
    if args.skip_first or args.count:
        pairs = pairs[args.skip_first : args.skip_first + args.count if args.count else None]
    counts: Counter[str] = Counter()
    with Path(args.out).open("w", encoding="utf-8") as out:
        for json_path, parquet_path in pairs:
            for event in convert_match(json_path, parquet_path, movement_hz=args.movement_hz):
                counts[event["event_type"]] += 1
                out.write(json.dumps(event, separators=(",", ":")) + "\n")
    print(f"Wrote {counts['shot']} shots and {counts['movement']} movement samples from {len(pairs)} matches to {args.out}")
    return 0


def cmd_labels(args: argparse.Namespace) -> int:
    """Who the dataset says cheated. fpsdet never reads this; the report does."""
    root = Path(args.root).expanduser()
    labels: dict[str, str] = {}
    for split in SPLITS:
        for json_path, parquet_path in _matches(root / split):
            info = json.loads(json_path.read_text(encoding="utf-8"))
            number = int(json_path.stem)
            cheaters = {entry["steamid"] for entry in info.get("cheaters") or []}
            import pyarrow.parquet as pq

            players = set(pq.read_table(parquet_path, columns=["steamid"]).column("steamid").to_pylist())
            for steamid in sorted(p for p in players if str(p).startswith("Player_")):
                if steamid in cheaters:
                    label = "cheater"
                elif split == "with_cheater_present":
                    label = "clean, reviewed match"
                else:
                    label = "clean, unreviewed match"
                labels[player_id(split, number, steamid)] = label
    Path(args.out).write_text(json.dumps(labels, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(labels)} labels to {args.out}: {dict(Counter(labels.values()))}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Decisions against the dataset's labels, from the ops.json that fpsdet score --out writes."""
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
        print(f"  {decision:<6} {label:<24} {pid:<12} {why[:110]}")
    return 0


def cmd_desk(args: argparse.Namespace) -> int:
    """The scored run as the review desk's CS2 page, with the dataset's labels beside each decision.

    fpsdet never reads the labels. The page shows them so a visitor can check its decisions.
    To keep the page light, each case keeps only what the drawer shows, and each row keeps
    only the numbers the drawer charts. The full cases stay in the scored run's folder.
    """
    ops = json.loads((Path(args.cases) / "ops.json").read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    for row in ops["rows"]:
        row["truth"] = labels.get(row["id"], "unlabelled")
        row["metrics"] = [m for m in row["metrics"] if m["name"] in DESK_METRICS]
        if row.get("case"):
            row["case"] = {key: row["case"][key] for key in DESK_CASE if key in row["case"]}
    left_out = len((ops.get("integrity") or {}).get("left_out") or [])
    ops.update(
        {
            "synthetic": False,
            "truth_kind": "labelled",
            "truth_source": "CS2CD, a public dataset with cheaters labelled by hand,",
            "label_source": "CS2CD's hand labels",
            "data_source": "shots, hits, hitgroups, wall penetrations and movement, recorded by the CS2 servers in their demos",
            "honest_labels": list(HONEST_LABELS),
            "truth_notes": LABEL_NOTES,
            "notes": {
                "data": "Counter-Strike 2 matchmaking matches from CS2CD by Mille Mei Zhen Loo and Gert Lužkov, CC BY 4.0",
                "baseline": f"Baseline from other \u201cno cheater\u201d matches; {left_out} hack-vs-hack lobbies screened out",
                "names": "Player names are the dataset's own placeholders. Not affiliated with Valve",
            },
            "links": [
                {"text": "How this was built, and the full results", "href": PROJECT + "/tree/main/examples/cs2"},
                {"text": "Run it yourself and share what you find", "href": SHARE},
            ],
        }
    )
    Path(args.out).write_text(json.dumps(ops, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}: {len(ops['rows'])} players, {Path(args.out).stat().st_size // 1024} KB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Download matches: the event JSON, and only the tick columns the converter reads")
    fetch.add_argument("split", choices=SPLITS)
    fetch.add_argument("--first", type=int, default=0, help="Index into the split's matches, sorted by number")
    fetch.add_argument("--count", type=int, default=10)
    fetch.add_argument("--min-rounds", type=int, default=13, help="Skip abandoned matches with fewer rounds")
    fetch.add_argument("--out", required=True)
    fetch.set_defaults(func=cmd_fetch)

    convert = sub.add_parser("convert", help="Write fpsdet NDJSON from fetched matches")
    convert.add_argument("folders", nargs="+", help="Split folders written by fetch")
    convert.add_argument("--out", required=True)
    convert.add_argument("--skip-first", type=int, default=0, help="Skip this many matches (sorted by number)")
    convert.add_argument("--count", type=int, default=0, help="Convert at most this many matches")
    convert.add_argument("--movement-hz", type=float, default=4.0, help="Movement samples per second; 0 for none")
    convert.set_defaults(func=cmd_convert)

    labels = sub.add_parser("labels", help="Write the dataset's cheater labels, keyed by fpsdet player id")
    labels.add_argument("root", help="The --out folder given to fetch")
    labels.add_argument("--out", required=True)
    labels.set_defaults(func=cmd_labels)

    report = sub.add_parser("report", help="Decisions against the labels")
    report.add_argument("cases", help="Folder written by fpsdet score --out")
    report.add_argument("--labels", required=True)
    report.set_defaults(func=cmd_report)

    desk = sub.add_parser("desk", help="Write the review desk's CS2 page data from a scored run and the labels")
    desk.add_argument("cases", help="Folder written by fpsdet score --out")
    desk.add_argument("--labels", required=True)
    desk.add_argument("--out", default=str(Path(__file__).with_name("desk.json")))
    desk.set_defaults(func=cmd_desk)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
