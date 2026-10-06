# Measuring the detectors

## What the labels mean

Every rate fpsdet publishes about real play is a rate against someone else's labels. None of those labels is ground truth. This section says exactly what each one is, before any number is computed from it. It was written at `2a6dc90`, and `tests/test_calibration.py` (`LabelAuditTest`) pins the label sets and the published results it describes.

### TF2: RGL bans

Source: RGL's public ban list, joined to server-logged league matches kept by logs.tf (`examples/tf2`).

| Label | Who | What it does not mean |
| --- | --- | --- |
| `cheater`, published as **RGL cheating-ban labelled** | RGL banned the account, and the reason mentions cheating, but not assisting, distributing or developing cheats | That the player cheated in every scored match, or in any particular one. A ban is a league decision, sometimes mirroring another league's |
| `not banned`, published as **never-banned comparison** | RGL never banned the account for anything, and it played in the same lobbies as labelled accounts | That the player never cheated. A few unlabelled cheaters are likely among them |
| `other ban` | RGL banned the account for something other than cheating, such as an alt account | Anything about cheating |
| `vac` | RGL mirrored a Valve ban, possibly from another game | That the player cheated in TF2 |

**How the matches are chosen.**
- **Ban date:** a banned account's matches count only before the ban date.
- **Equal evidence:** every player keeps at most their 20 latest team matches (6v6, prolander or highlander, 12 or more players).
- **Eligibility:** a cheating-ban labelled account needs at least 20 team matches before its ban. A never-banned player needs at least 8 matches in the fetched logs.
- **Baseline split:** never-banned players are split by a fixed bit of their keyed pseudonym. Half build the baseline, and only the other half are scored, so nobody is judged against a baseline that includes them.
- **What is scored:** only aimed weapons are converted (scatterguns, pistols, shotguns, sniper rifles, SMGs and revolvers).

**Counts in the published run (2026-10-04).**

| | Labelled on the scored side | With a scored case |
| --- | --- | --- |
| RGL cheating-ban labelled | 228 | 189 |
| Never-banned comparison | 2,158 | 1,746 |
| Other ban | 1,184 | 821 |
| VAC, mirrored | 20 | 8 |

A labelled player with no scored case had no aimed-weapon shots in their kept matches. They are outside every denominator: fpsdet had nothing to judge.

**Known uncertainty.**
- **Exposure is unequal.** Most never-banned players appear in only a few matches; labelled accounts have up to 20. Rates are therefore also reported at equal evidence.
- **The positive label is about an account, not a match.** A labelled account may have played clean in some, or most, of its scored matches.
- **The comparison is not proven clean.**
- **What the data can show.** Per-match totals only: shots, hits and sniper headshots per weapon, kills per minute. No timing, positions, view angles or movement. Every detector that needs those is not observable here.
- **Timing is not real.** The converter spreads each weapon's shots evenly through the match, so no timing-based detector could be meaningful even if the profile asked for one.

### CS2: the CS2CD dataset

Source: CS2CD, 795 CS2 matchmaking matches parsed from server-recorded demos (`examples/cs2`). Its labels are the dataset authors', and they are not the same kind of label as RGL's.

| Label | Who | What it does not mean |
| --- | --- | --- |
| `cheater`, published as **hand-labelled cheater** | A player the authors labelled as cheating after reviewing a match that contained a VAC-banned player | A ban; the labels are the authors' judgement |
| `clean, reviewed match`, published as **hand-reviewed, not labelled** | A player in a reviewed match whom the authors did not label | That they are proven clean; it is a reviewer's call, but a direct one |
| `clean, unreviewed match`, published as **unreviewed match** | A player in a match with no VAC-banned player. Not reviewed; the authors' spot check of 50 such matches found 97.2% of players showing no cheating | That anyone looked at this player |

**How the matches are chosen.** Each match is pseudonymised on its own, so a player is one match and cannot be followed across matches. The first 120 complete no-cheater matches built the baseline (after `--screen-matches` left 20 lobby-wide outliers out). The remaining complete no-cheater matches and the complete with-cheater matches are scored.

**Counts in the published run (2026-10-04).** 504 hand-labelled cheaters, 575 hand-reviewed players not labelled, and 450 players in unreviewed matches have scored cases (1,529 in all).

**Known uncertainty.**
- **One match per player.** fpsdet is built to judge an account over many matches. Here most players are held for too little data, and that is the dominant effect on every rate.
- **The comparison groups differ by construction.** Reviewed players shared a lobby with a cheater; unreviewed players did not.
- **Unreviewed is not reviewed.** A few cheaters are likely in the unreviewed group, and the baseline screen found whole lobbies of them in the baseline's own window.
- **What the data can show.** Server hits, hitgroups, penetration, distance, view turn before a shot, and movement speed. No recoil kick, no player command, no visibility or audio, no wire timing, no challenges, no parties.

### Synthetic: planted by construction

The planted demo and the synthetic week are built by fpsdet's own code, and each player's behaviour is known because it was planted. They qualify detectors: does a planted behaviour trip the detector it was built for, and does its honest twin stay clean? They are never mixed into a real-world rate. A synthetic rate says how the code behaves on data it was designed for, not how often anything fires on real play.
