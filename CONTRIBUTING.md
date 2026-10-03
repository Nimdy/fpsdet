# Contributing

## Where things stand

There is no contributor agreement yet. ZeroBandwidth also offers fpsdet under a commercial license, and a patch can only ship in both versions once its author has agreed to that. Until an agreement is published here, code pull requests cannot be merged.

These are welcome now, as issues:

- **False-positive reports.** Honest play that came out as `review` or `watch`. Use the False positive form. Include an event sample of the honest play if you can.
- **Server fields.** A value a game's dedicated server already computes that would separate a cheat from honest play.
- **Profiles.** Weight classes, speed caps, recoil floors, or weapon cycles for a game, with where the numbers came from.
- **Failing tests for false positives.** A test that shows honest play getting flagged is useful even without a fix. Paste it in the issue for now.

## License

fpsdet is under the [PolyForm Small Business License 1.0.0](LICENSE.md). It is source-available, not an OSI-approved open-source license: companies above its size limits need a commercial license from ZeroBandwidth.

## Run the tests and the demo

Python 3.11 or newer. Nothing to install.

```bash
PYTHONPATH=src python3 -m unittest tests.test_fpsdet
PYTHONPATH=src python3 -m fpsdet demo
```

The demo scores thirty-two planted players. A planted player is a synthetic player whose correct decision is written down in advance, in `EXPECT` in `src/fpsdet/synthetic.py`. The last line should be `Planted cases matched profiles/example-loadout.json.` If a decision moves, it prints `DEMO FAILED` and exits with an error.

`fpsdet demo` also rewrites `demo/board.html`, which is committed. CI fails if the committed board does not match what the scorer writes. To look at the output without touching that file, pass a folder:

```bash
PYTHONPATH=src python3 -m fpsdet demo --out /tmp/fpsdet-demo
```

## The rule: plant the cheater and the innocent twin first

Before you write or change a check, add two planted players:

1. **The cheater.** Synthetic events that show the cheat.
2. **The innocent twin.** The closest honest play you can build: the same numbers where possible, with the one difference that makes it innocent.

Then write the rule that separates them. A rule that also flags the twin is not finished.

The demo is built from these pairs:

| Cheater (review) | Innocent twin (clean) | The difference |
| --- | --- | --- |
| `weight-cheat` | `blasted` | Same sprint. The server tagged the blast in `displacement_cause`. |
| `no-recoil` | `modded-recoil` | Low recoil. The mods on the gun allow it. |
| `mirror-script` | `late-compensate` | Same kicks. The honest player pulls down one shot late. |
| `wall-eye` | `angle-holder` | Aim on a corner. The honest player only pre-aimed it. |
| `quiet-radar` | `listened` | Quiet aim. The honest player could hear the target. |
| `replay-lock` | `real-fight` | Aim near the replay path. The honest player was fighting a visible enemy. |
| `wire-lock` | `picture-track` | Same hits. The honest player aimed at what the client draws. |

## Off-topic

- Instructions for building, tuning, or hiding a cheat. Report a bypass privately instead: see [SECURITY.md](SECURITY.md).
- Changes that weaken the innocence rules, so that a blast, a ragdoll, a vehicle, or a short glitch counts against a player. Those rules are what keep a false positive from becoming a ban.
- Anything that wires `automated_action` to a value other than `none`. A person reviews every case.
- Data you do not have the right to share, such as a studio's internal tables.

## Conduct and security

Follow the [code of conduct](CODE_OF_CONDUCT.md). Use made-up ids in every issue. Report bypasses, ways to get an innocent player flagged on purpose, script injection, and privacy leaks privately, as [SECURITY.md](SECURITY.md) describes.
