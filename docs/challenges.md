# Active challenges

## Before the challenge engine: the private replay

This is the private replay as it stood at `eabd87c`, written down before anything about it changed. `tests/test_challenge.py` (`LegacyPrivateReplaySemanticsTest`) pins it.

### The field

`private_track_ms` on a shot: milliseconds that shot's aim cone contained the server's private replay, another player's motion on a different heading, placed where this client's line-of-sight and audio queries both fail. It is the only field. It carries no target, no challenge, no time window and no plan.

- **Parsing.** A number becomes a float. `null` or a missing key is not sent. A string is a parse error, and the line is dropped. A negative number is accepted, although the schema says at least 0, and it adds nothing.
- **Summarizing** (`summarize._track_times`). The player's canonical timeline is read one server moment (match and `t_ms`) at a time. When the shots at a moment that carry the field agree on its value, it adds one sample, cut to the time since the player's previous moment in the same match. The first moment of a match is not cut. When they disagree, the moment adds nothing and the weapon's `knowledge_skipped["private_replay"]["disagreed"]` counts it. A value of 0 or less adds nothing. The sample goes to the shot's aim key.
- **Knowledge.** Every sample has the same knowledge, `knowledge.private_knowledge(profile)`: vision and audio absent, recent perception not applicable. With the default channels it is `unknowable`. A game that declares any other channel gets `unknown`, and every sample is left out as `unchecked`. The shot's own `information_state` is about `enemy_id`, another target, and is not read.
- **The bar** (`signals.private_finding`, the hidden-mover bar). On one aim key, at least `hidden_track_min_samples` (8) samples above 0, and an exact sum of at least `hidden_track_min_ms` (1200). Samples from every match in the window add to one total. Aim keys are not pooled.
- **The reason.** `{key} aim stayed on a private replay for {total} ms across {n} shots`. The check id is `private_replay`, in the dashboards' information group, explained by `tape-replay`. The v1 seal hashes the reason.
- **The observation.** Family `information`, kind `private_replay`, role `review`, key the aim key. `match_ids` is every match the aim key was used in, not only the ones with replay time. Evidence: `shots`, `total_ms`, and `thresholds` (`min_shots`, `min_total_ms`). Context: the line, and the knowledge state with its basis and what it left out.
- **The decision.** A review, even when the weapon has too few shots for aim to be scored.

### Samples in the data

- **Planted.** `replay-lock` has 20 samples of 80 ms, 1,600 ms in all, and is a review. `real-fight` crosses the replay on 4 shots, 160 ms, and is clean.
- **Synthetic week.** About 20% of matches carry a replay. On those matches ESP cheaters lock onto it on 40% of their shots for 80 ms, and anyone crosses it on 3% of shots for 20 to 60 ms. One weekly case and four nightly cases are reviews on it, and in each it is the only finding.
- **CS2 and TF2.** No field, so no sample.

### What it assumed about the target

- The field names no target. Two replays in one match, or a replay and an ordinary hidden enemy, add into one number.
- The body's knowledge comes from the field's definition: the server placed it where this client's line-of-sight and audio queries fail, and it was never perceivable. Nothing checks it.
- Nothing says when the replay was active, so a sample counts at any time.
- Nothing says the server meant to run a replay for this player in this match, or lets anyone reproduce one.

### Where the documentation says it is invisible

- README, "Add one thing: decoys".
- `docs/integration.md`, the `private_track_ms` paragraph: no decoy bit; the stock client drops the body before draw because it is occluded.
- `docs/scoring.md`, "Private replay".
- `docs/games.md`, the field table.
- `docs/culling.md`: a body sent into a culled region is never drawn by the stock client.
- `docs/players.md`: the general disclosure to players.
- `docs/knowledge-engine.md`: the replay body's row.
- `site/decoys.html`: the loop and the rules that keep honest players safe.

### What the tests proved

- The bar: 20 samples of 80 ms fire; 4 of 40 ms and one of 5,000 ms do not (`tests/test_fpsdet.py`).
- The planted pair's decisions, and the desk's drawing of the replay: the rotated, delayed route behind the wall, and the aim on it or away from it.
- The shot's labels and the grace window are not read, and a declared extra channel abstains (`tests/test_knowledge.py`).
- Every planted and synthetic case, field by field (`tests/test_golden.py`).
- Parsing, the cut, a match boundary, simultaneous shots, the bar's edges, aim keys not pooled, matches pooled, the case and the planted ids (`tests/test_challenge.py`).

### What they did not prove

- That the body was invisible and silent to this client. That is the server's claim.
- Which replay a sample came from, or that the samples came from one replay.
- That a replay was running when the sample was taken.
- That the server planned it for this player, or that anyone could reproduce it.
- That a cheat could not predict it.
- That crossings in many unrelated matches cannot add up to the bar. They can: matches are pooled.
- That a real enemy the client could see, in the same aim cone, did not explain the sample.
- That the replay did not change the game. That is the engine's side, outside this repository.
