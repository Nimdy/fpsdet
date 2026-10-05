# Event normalization

Until this change, fpsdet read the order events arrived in as if it were game state. The same parsed events, listed in a different order in the file, could produce a different case. Since this change, one set of server events produces one case, whatever order the file listed them in. Chronology comes from the server's clock and the fields that carry sequence. It never comes from line order.

## What depended on arrival order

`tests/test_event_order.py` was first committed (d45c0be) as a characterisation. Each test fed the same parsed events in two orders and showed what the order decided:

| What | Effect |
| --- | --- |
| The weapon, recoil build or declared-metric group seen first | The order of metrics, reasons, observations, context lines and untrained builds, and with the reasons the legacy seal |
| The player seen first | The order of cases, and which wallhacker a teammate's voice watch was processed against first |
| Two shots at the same time with different hidden-track times | Which one counted: a hidden-mover review or not |
| Recoil shots at the same time | The order of their spray positions: a floor-run review or not; the mirror lag; the leftover signature |
| Movement samples at the same time | Whether a run reached 25 samples: a speed review or not |
| The match seen first | The order of the fire-rate evidence, and so its observation id |
| A float sum on Python 3.11 | Whether eight hidden-track times reached 1,200 ms. Python 3.12's `sum()` compensates, so the same file could decide differently on the two versions |
| The last wallhacker processed | Whose lags the teammate's `inherit_lags_ms` kept |
| Arrival order alone | The `player-events/1` input digest, and so the evidence packet |

None of these was a server statement. All of them were accidents of how a file happened to be written.

## The canonical player timeline

`fpsdet.timeline` puts each player's events in one order before anything reads them:

1. `match_id`. Matches are independent, and `t_ms` restarts in each one.
2. `t_ms`, the server clock.
3. The event kind: movement (rank 0), then shot (rank 1). No check reads a shot against a movement sample at the same time, so this only makes the order total. It does not rely on the words sorting alphabetically.
4. `spray_index`, on shots that carry it: the server's own count within a spray. A shot without one sorts first.
5. The event's canonical text, only for events that 1 to 4 cannot tell apart.

The canonical text is every field the parsed event sets, as JSON with sorted keys and extras as one nested object. It is built from parsed values, so the file's key order, spacing and number spelling do not show, while `30` and `30.0` stay different, as they are to the scorer. It is only built where the first four keys tie. Identical events are kept side by side. A duplicate is never merged or dropped.

Events that share 1 to 4 are simultaneous as far as the server's telemetry says. The canonical text gives them one order, but no check is allowed to treat that order as chronology. Each check that reads sequence decides separately what simultaneous events mean to it.

## Simultaneous events, check by check

| Check | Rule | Kind of rule |
| --- | --- | --- |
| Fire rate, metronome | Gaps between a gun's shots in time order. Two shots at one time are a zero gap, whichever is listed first. A zero gap is under the floor, as before; the per-match rate rule keeps a duplicated stamp from making a match count | Authoritative chronology; simultaneous shots give the same gaps in any order |
| Recoil floor run | Ordered by match, time, `spray_index`. Shots sharing all three are one moment, which extends a low run only when every shot in it is an eligible low one | Same-timestamp grouped |
| Same-tick mirror | Reads which kick came first. A build where the server sent different kicks at one time and spray index has no such order: the check does not run on it, and the case says so. Identical kicks at one moment are a duplicate, not an ambiguity | Explicitly ambiguous, so the check abstains |
| Shared leftover | Same as the mirror: such a build has no leftover signature | Explicitly ambiguous, so the check abstains |
| Speed run | Samples at one match and time are one moment. It extends a run by its sample count only when every sample in it is an eligible ground sample over its cap; any other sample there breaks the run | Same-timestamp grouped |
| Hidden mover, private replay | Shots at one moment are one aim, so a moment adds at most one track sample. It adds one when the shots that report a time agree on it (for the hidden mover, also on `information_state` and `since_perceived_ms`), and none when they disagree. The window is the time since the player's previous moment in that match | Same-timestamp grouped; disagreement abstains |
| Wire and picture, quiet aim | Each shot is its own comparison, and the results are counts, medians and exact sums | Order-independent |
| Voice-speed teammate | Contacts in match, time and enemy order | Authoritative chronology |

None of the CS2, TF2, planted or synthetic data has simultaneous events that disagree within one of these checks. The rules are there so that such data cannot be decided by line order:

- CS2 has 2 pairs of identical duplicate shots.
- TF2 has 20,092 simultaneous groups, all of them shots of different weapons. The logs.tf converter spreads each weapon's shots across the match from zero.

## Players and batch checks

- Players are summarised and scored in player-id order.
- The shared-leftover pass goes through weapons in key order and pairs in id order. When one player has two builds on a weapon with the same number of samples, the first in build-key order supplies the signature.
- The voice pass goes through parties in id order, partners in id order, and each player's contacts in match, time and enemy order.
- Party notes were already sorted.

### A teammate timed against several wallhackers

A teammate in a party with two confirmed wallhackers is timed against each one. Every partner whose swings reach the watch bar has always had its own voice observation, naming the partner, the party and the fast lags. The legacy field `inherit_lags_ms` holds one list, though. It used to keep whichever partner was processed last: the last seen in the file before normalization, the highest id after it.

It now keeps the strongest relationship by the check's own terms:

1. a partner whose swings reach the watch bar (`inherit_min_events` swings under `voice_min_ms`) before one whose swings do not;
2. then more swings faster than a voice;
3. then the lower median of those swings;
4. then the partner id and the party id.

In the planted demo and the synthetic week every teammate has one partner, and the real data has no voice inputs, so no case moved.

## Output order

Set-like lists come out in key order:

- **Weapons** (and so metrics and per-weapon findings): weapon key.
- **Recoil builds:** build key.
- **Declared metrics:** name, then group.
- **Fire-rate and metronome evidence:** match, then gun.

Within that, reasons, context lines, check ids and observations keep the scorer's detector order. That is now deterministic, because everything it walks is in key order. Observation ids never depended on list position, and they do not move when a list is re-ordered.

## Numeric determinism

The material float sums now use `math.fsum`, which is correctly rounded: the same result in any order and on any Python version. They are:

- the hidden-mover, private-replay and wire totals;
- the sample standard deviation (acquire-time spread, metronome spread, the leftover residual check);
- the shared-leftover centring and its per-index means.

When every term is an integer, the totals add as integers and stay integers, as they did before.

On the synthetic fixture where a plain sum reached exactly 1200.0 on Python 3.11, the exact sum is 1199.9999999999998, so that fixture is no longer a hidden-mover review on any version or in any order. No real or synthetic case moved because of this.

## Input provenance: `player-events/1` and `/2`

| Recipe | Binds | Written by | Verifies |
| --- | --- | --- | --- |
| `fpsdet.player-events/1` | A player's events in the order they arrived, when the scorer read that order | fpsdet up to P2.3 | Yes, forever. Its meaning never changes (`fpsdet.provenance.arrival_digest`, with its P2.2 test digest pinned) |
| `fpsdet.player-events/2` | The same columns over the canonical timeline the scorer now reads | fpsdet since this change | Yes |

`run_score` builds each player's timeline once and hands the same list to the scorer and to the digest, so `/2` cannot describe a different order from the one the scorer read; a test checks it case by case. The same events in any file order, players interleaved any way, or extras keys in any order give one `/2` digest. Adding, removing or changing an event, duplicates included, gives another.

## Packet compatibility

`fpsdet.packet/1` binds the input part by its recipe and digest (and its counts). It never assumed which input recipe it covered, so a `/1` packet over `player-events/2` means exactly what it says: this packet's inputs are those events under that recipe. The packet recipe stays `/1`. `verify_packet` recognises both input recipes. Two serialized P2.3 cases are kept in `tests/fixtures/historical-packets-p23.json`; they carry `player-events/1` and still verify, and editing them is still caught.

## Permutation proof

`tests/test_event_order.py` scores the same parsed events in every order a file could list them:

1. as given;
2. fully shuffled;
3. player blocks shuffled;
4. each player's own events shuffled, players interleaved;
5. weapons interleaved;
6. same-time events reversed;
7. extras keys reordered;
8. partner order reversed.

Duplicates sit in different places in each. It requires every case field, every observation id, the legacy seal, the `player-events/2` digest and the evidence packet to match. It runs on the planted demo, on fixtures for each kind of simultaneous event and for two partners, and on the synthetic week (178,020 events, three of the forms).

On real data: TF2 (4,139,445 events, 20,092 groups of simultaneous shots) scored as written and fully shuffled gives 2,764 of 2,764 byte-identical cases.

## Migration

Every case was compared from before normalization (P2.3, `d5f1961`) to after all of it (`tools/regress.py migrate`):

- the planted demo;
- the synthetic week, weekly and nightly;
- the real CS2 and TF2 reruns.

Every case's input identity moved from `player-events/1` to `/2`, and so did its packet digest. That is the intended change of recipe, not a change of evidence. Everything else:

| | Cases | Decisions | Reviews | Watches | Reasons | Metrics | Context lines | Observations | Observation ids | Seals |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Planted | 32 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Synthetic weekly | 400 | 0 | 0 | 0 | 0 | 65 re-ordered | 64 re-ordered | 0 | 0 | 0 |
| Synthetic nightly | 2,669 | 0 | 0 | 0 | 0 | 6 re-ordered | 463 re-ordered | 0 | 0 | 0 |
| CS2 | 1,529 | 0 | 0 | 0 | 0 | 199 re-ordered | 399 re-ordered | 0 | 0 | 0 |
| TF2 | 2,764 | 0 | 0 | 0 | 2 re-ordered | 1,629 re-ordered | 1,637 re-ordered | 11 re-ordered | 0 | 2 |

Other changes, all orders:

- one synthetic teammate's stored voice lags, the same lags in contact order (weekly and one night);
- three TF2 check-id lists.

**The two TF2 seals.** Two banned cheaters' reasons are now in weapon-key order, which moves their legacy seals. Their findings and decisions are unchanged.

- `tf-73bbbbd072` (review): ambassador headshots before revolver accuracy.
- `tf-90d61778fc` (watch): ambassador, awper hand, scattergun accuracy.

**No review or watch was added or removed in any data set.** No observation's contents changed, so there is no review or watch movement to explain. Every packet in both real runs verifies.

The planted honest controls are unchanged. `tests/golden/planted.json` did not move. `tests/golden/week.json` was re-recorded for exactly the 582 order-only cases above.

## Cost

Measured back to back against P2.3 on one machine:

| | P2.3 | Now | Normalization alone |
| --- | --- | --- | --- |
| Synthetic weekly batch (178,020 events) | 1.28–1.34 s | 1.48–1.51 s | 0.10 s |
| Synthetic nightly batches | 3.00–3.13 s | 3.22–3.26 s | |
| TF2 scoring (4.14 M events) | 15.7–16.0 s | 17.4–18.7 s | 3.3–3.5 s |
| CS2 scoring (4.07 M events) | 13.2–13.6 s | 15.5–16.0 s | 2.8–2.9 s |
| Peak memory, TF2 / CS2 | 5,486 / 5,630 MB | 5,486 / 5,630 MB | |

Events are grouped once and sorted per player, never as one global sort. A timeline holds references to the same `Event` objects, so nothing is copied. Canonical text is built only for events whose clock keys tie. The scoring timing includes the input digest.

## What is still ambiguous

- **Kicks the server did not order.** Different recoil kicks at one match, time and spray index have no order. The mirror and leftover checks abstain on that build, and the case says so. No data seen so far has them; a server that sends them should send `spray_index`.
- **Content order within simultaneous events** decides only where events sit in a list. No check reads it as time. A check that later starts to read sequence must state its own rule for simultaneous events.
- **The logs.tf converter makes simultaneity.** It spreads each weapon's shots from zero, so different weapons collide in time. No current check reads one weapon's shots against another's at one moment. The hidden and private windows would, but logs.tf has neither field.
- **The speed cap source** names the first source seen in time order, the server's own cap always winning. Within one moment the server cap wins, then a weight curve, then an unset curve.
- **A relationship observation** can still depend on another player's events that the subject's input digest does not cover. This is unchanged from P2.2 and belongs to a later dependency binding.
