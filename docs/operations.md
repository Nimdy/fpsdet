# Operations

## Lake

`fpsdet ingest` appends each line to `lake/game=<game_id>/dt=<YYYY-MM-DD>/events.ndjson`. The day comes from an `utc` field on the event, else from `--dt`, else from the current UTC day. `t_ms` is match time and is not a calendar day.

That tree is the data lake for a solo dev. A studio can put the same objects in S3 or GCS and run the aggregate in DuckDB or ClickHouse. The case a reviewer opens is small. The shots that built it are not, and they do not belong in Elasticsearch or DynamoDB as the system of record. Index the case JSON if you want a search box.

Scan the reported players first when the lake is too big for one pass:

```bash
PYTHONPATH=src python3 -m fpsdet score --lake ./lake --game your-game \
  --profile profiles/your-game.json \
  --cohort baselines/last-week.json \
  --reports reports.json \
  --reported-only \
  --out cases/priority/
```

The batch for everyone else can run on a schedule. Reports are a queue, not a verdict. The case of a heavily reported player whose numbers are inside the baseline says so in plain text.

## Priority

`scan-index.json` is the hydration order: any reported player, highest report count first, including clean ones. Among everyone else, a leftover-command match with someone already in review comes before a teammate who swung a hidden enemy faster than a voice. `review-index.json` is the reading order: review, then watch, then clean. A person works the reading order. A worker pulling raw shots out of a cold lake follows the scan order.

Those two batch tells are watches. They do not become reviews, and they do not change someone who is already a review. Party notes are context, written after the watches, so an upgraded teammate is named. Two flagged accounts in one stack are worth opening together. The stack does not raise either decision.

## AI briefs

`--ai` calls one chat completion per case that is `review`, `watch`, or reported. Set:

- `FPSDET_AI_BASE_URL` — `https://api.example/v1` or `http://127.0.0.1:11434/v1` or a full `.../chat/completions` URL
- `FPSDET_AI_MODEL`
- `FPSDET_AI_API_KEY` — omit for a local server that does not check one

The default transport speaks the OpenAI chat-completions shape, which is what most hosted APIs and local servers already speak. Another vendor is a function `(request_dict) -> text`. `fpsdet.ai_triage.triage_case` is that hook. Pass `redact_ids=False` only on a machine you trust. By default, before the request is built, every player id the case mentions is replaced with an alias. That covers the player's own id and other accounts named by the batch checks (the leftover twin, the teammate's partner); pass the batch's ids as `known_ids`. Party ids, match ids, and the seal are removed.

The system prompt tells the model not to recommend a ban and not to invent numbers. The brief is stored on the case as `ai_brief`. If the endpoint is down, fix the endpoint. The statistical decision already exists without it.

This is also why an API does not "process the lake faster." A large language model is a slow reader of rows. The fast path is the aggregate. The model spends its time on the queue a person was going to read anyway, and it spends that time on a page of numbers rather than a raid of raw samples.

## Your own model

`features.csv` has the decision, the report count, whether speed was sustained, and which builds were untrained. Join your reviewers' labels (cheat, clean, inconclusive) onto `player_id` offline. Train whatever you already train. Keep the gear-rule findings as rules beside the model. A learned score that can talk a sustained over-cap sprint out of a case has learned the wrong objective.

Export only pseudonyms. Do not put the feature file on a vendor's multi-tenant trainer unless your counsel has already signed that.

## Privacy and appeals

This is not legal advice. It is the shape that keeps the tool usable.

- Store a pseudonym. Keep the account map on your side.
- The reference AI path does not receive the pseudonym, or any other account the case names.
- Do not collect raw mouse HID, kernel memory, or a hardware id here. Those are a different product, with a different privacy cost, and this detector does not need them.
- Keep raw shots only as long as you need to rebuild a case. Cohorts and case files are the long-lived objects. A 14 to 30 day raw window is a reasonable starting point for a live game.
- A case is the appeal packet: the metric, the bound, the human line it was compared to, the sample size, the cap source, how many samples were excluded as blasts or vehicles, the match ids, and the sentence that this is not a ban. `seal` is the SHA-256 of the player, the game, the decision, and the reasons. Reports are not in it. A changed finding is a different packet. Your replay store, which you already have, is how a reviewer watches the match.

[players.md](players.md) is a page you can link for players: what is recorded, how a case is reviewed, how to appeal, and a notice template.

Wire a ban action to your own review tool after a person looks. Do not wire it to `decision == review`. The field `automated_action` is `none` so that a hasty integration has to notice it.

## Cohort health

Watch the gap between the ceiling median and the ceiling maximum. When the maximum runs away from the rest of the band, the population you trained on is contaminated or one account is a real outlier. Prefer to build the baseline from accounts not currently in review. After a wave of confirmed cases, refit. A baseline that includes the cheaters will, correctly, stop calling them unusual.

`fpsdet baseline --previous <last cohort>` compares those ceilings. A jump of at least `poison_jump` (default 0.08) on accuracy, headshot rate, or geometry rate, with both sides thick enough, stamps `integrity.status` as `poison_risk`. Score prints the warning and does not change a decision. Without `--previous` the status stays `unchecked`.
