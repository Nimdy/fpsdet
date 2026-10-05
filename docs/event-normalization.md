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

## Migration

Every case of the planted demo, the synthetic week (weekly and nightly), and the real CS2 and TF2 reruns was compared before and after (`tools/regress.py migrate`).

| | Cases | Moved | Decisions changed | Reasons | Observation ids | Seals |
| --- | --- | --- | --- | --- | --- | --- |
| Planted | 32 | 0 | 0 | 0 | 0 | 0 |
| Synthetic weekly and nightly | 3,069 | 582 | 0 | 0 | 0 | 0 |
| CS2 | 1,529 | 558 | 0 | 0 | 0 | 0 |
| TF2 | 2,764 | 2,148 | 0 | 2 re-ordered | 0 (11 re-ordered) | 2 |

What moved:

- **Synthetic:** 527 context-line orders, 71 metric orders, and the order of one teammate's stored lags (the same lags, weekly and one night).
- **CS2:** 399 context-line orders and 199 metric orders.
- **TF2:** 1,637 context-line orders, 1,629 metric orders, 11 observation orders and 3 check-id orders. Two banned cheaters list their reasons in weapon-key order now, so their legacy seals moved:
  - `tf-73bbbbd072` (review): ambassador before revolver.
  - `tf-90d61778fc` (watch): ambassador, awper hand, scattergun.

  Their findings and decisions are unchanged.

No review, watch or other decision changed in any data set, and no observation's contents changed. Every evidence packet still verifies.
