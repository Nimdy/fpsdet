"""Fetch CS2CD at one pinned revision, and check every file against the benchmark's source list.

cs2cd.py fetch reads whatever the dataset's main branch holds on the day it runs. This fetcher reads one
exact revision, a 40-hex commit named in a source list such as benchmark/sources/cs2cd-v2.json, and stops
if the hub does not have it: it never falls back to the latest. For every match it fetches, it checks:

- the event file, byte for byte, against its git blob id at that revision;
- the tick table's size and LFS SHA-256, as the hub reports them at that revision. Only the columns the
  converter reads are downloaded, so the table's own bytes are hashed only with --full, which downloads
  each table whole first (about 18 GB for the benchmark's matches).

Any difference stops the fetch with SOURCE_CHANGED, the file, and what was expected and found; nothing is
substituted. A receipt (receipts/<split>.json beside the --out folder) records what was checked.

    pip install -r examples/cs2/requirements.txt
    python examples/cs2/cs2fetch.py --sources benchmark/sources/cs2cd-v2.json --split no_cheater_present --out ~/cs2v2/raw

Writes the same files cs2cd.py fetch does, so cs2cd.py convert and labels read them unchanged.
Not affiliated with Valve or with the dataset's authors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cs2cd import COLUMNS, EVENTS, SPLITS, _rounds  # noqa: E402

RECEIPT = "fpsdet.cs2cd-fetch-receipt/1"


def git_blob(data: bytes) -> str:
    """The id git gives a file's bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _lfs(info: dict) -> tuple[int | None, str | None]:
    lfs = info.get("lfs")
    if lfs is None:
        return None, None
    get = (lambda name: getattr(lfs, name, None)) if not isinstance(lfs, dict) else lfs.get
    return get("size"), get("sha256")


def cmd_fetch(args: argparse.Namespace) -> int:
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, HfFileSystem
    from huggingface_hub.utils import RevisionNotFoundError

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    repo, revision = sources["repo"], sources["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SystemExit(f"{args.sources}: the revision must be a full 40-hex commit, not {revision!r}")
    try:
        resolved = HfApi().dataset_info(repo, revision=revision).sha
    except RevisionNotFoundError:
        raise SystemExit(f"SOURCE_CHANGED: {repo} has no revision {revision}. Nothing was fetched, and the latest revision is not used instead.")
    if resolved != revision:
        raise SystemExit(f"SOURCE_CHANGED: the hub resolved {revision} to {resolved}. Nothing was fetched.")
    pick = sources["selection"][args.split]
    expected = sources["files"][args.split]
    fs = HfFileSystem()
    base = f"datasets/{repo}@{revision}/{args.split}"
    numbers = sorted(int(Path(name).stem) for name in fs.ls(base, detail=False) if name.endswith(".json"))
    chosen = numbers[pick["first"] : pick["first"] + pick["count"]]
    want = sorted(int(name.split(".")[0]) for name in expected if name.endswith(".json"))
    if chosen != want:
        raise SystemExit(f"SOURCE_CHANGED: at {revision}, {args.split} lists other matches than the source list names. Nothing was fetched.")
    target = Path(args.out).expanduser() / args.split
    target.mkdir(parents=True, exist_ok=True)
    receipts = Path(args.out).expanduser().parent / "receipts"
    receipts.mkdir(exist_ok=True)
    rows: list[dict] = []
    changed: list[dict] = []
    kept = skipped = 0
    for number in chosen:
        name = f"{number}.json"
        raw = fs.cat_file(f"{base}/{name}")
        found = {"size": len(raw), "git_blob_sha1": git_blob(raw)}
        rows.append({"file": name, "checked": "bytes", "expected": expected[name], "found": found})
        if found != expected[name]:
            changed.append(rows[-1])
            continue
        info = {key: value for key, value in json.loads(raw).items() if key in EVENTS}
        (target / name).write_text(json.dumps(info, separators=(",", ":")), encoding="utf-8")
        if _rounds(info) < args.min_rounds:
            skipped += 1
            continue
        name = f"{number}.parquet"
        size, sha256 = _lfs(fs.info(f"{base}/{name}"))
        found = {"size": size, "lfs_sha256": sha256}
        if args.full:
            with tempfile.TemporaryDirectory() as tmp:
                whole = Path(tmp) / name
                fs.get_file(f"{base}/{name}", str(whole))
                found = {"size": whole.stat().st_size, "lfs_sha256": hashlib.sha256(whole.read_bytes()).hexdigest()}
                table = pq.read_table(whole, columns=COLUMNS) if found == expected[name] else None
        rows.append({"file": name, "checked": "bytes" if args.full else "hub metadata at the revision", "expected": expected[name], "found": found})
        if found != expected[name]:
            changed.append(rows[-1])
            continue
        if not args.full:
            with fs.open(f"{base}/{name}", "rb", block_size=1 << 20) as handle:
                table = pq.read_table(handle, columns=COLUMNS)
        pq.write_table(table, target / name)
        kept += 1
        print(f"{args.split}/{number}: {_rounds(info)} rounds", file=sys.stderr)
    receipt = {
        "format": RECEIPT,
        "repo": repo,
        "revision": revision,
        "split": args.split,
        "selection": pick,
        "status": "SOURCE_CHANGED" if changed else "MATCH",
        "changed": changed,
        "files": rows,
    }
    (receipts / f"{args.split}.json").write_text(json.dumps(receipt, indent=1) + "\n", encoding="utf-8")
    if changed:
        for row in changed[:10]:
            print(f"SOURCE_CHANGED {args.split}/{row['file']}: expected {row['expected']}, found {row['found']}", file=sys.stderr)
        raise SystemExit(f"SOURCE_CHANGED: {len(changed)} of {len(rows)} files differ from the source list at {revision}. They were not kept.")
    print(f"Kept {kept} matches, skipped {skipped} short ones, in {target}; every file as the source list says, at {revision}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sources", required=True, help="The benchmark's source list: repo, revision, selection and file digests")
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument("--min-rounds", type=int, default=13, help="Skip abandoned matches with fewer rounds")
    parser.add_argument("--full", action="store_true", help="Download each tick table whole and hash it")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    return cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
