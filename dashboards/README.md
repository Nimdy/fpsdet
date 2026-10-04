# Dashboards

fpsdet does not run inside a dashboard. It writes case files, and a dashboard is how the people who run the queue read them. You have three options, and they can be mixed.

## 1. The one that ships: `fpsdet dashboard`

`fpsdet score --out <folder>` writes, next to the case files:

- `ops.json`, the operations payload
- `dashboard.html`, one offline page that renders it

Open the page in a browser. It needs no server, cluster or network.

Score every night into its own folder, then merge the nights into a week:

```bash
PYTHONPATH=src python3 -m fpsdet score --lake ./lake --profile profiles/example-loadout.json \
  --cohort baselines/frozen.json --reports reports.json --out cases/2026-10-01
# ...one folder per night...
PYTHONPATH=src python3 -m fpsdet dashboard cases/2026-09-26 cases/2026-09-27 cases/2026-10-01 --out week.html
```

A review that any night opened stays in the merged queue until a person closes it. A watch is a monitor flag, so it comes from the latest night only.

The review desk (`demo/board.html`, published to GitHub Pages) opens on the same view, filled with a synthetic week: 400 players and 17 planted cheats, scored nightly against a frozen baseline. Every number in it is invented. It shows what a real week looks like before you wire a server.

## 2. Your own tool: chart the case JSON

If the studio already runs Grafana, Kibana, Splunk, Metabase or its own moderation console, index the case JSON (`cases/*.json`, or the `rows` in `ops.json`) and build these panels. Each one reads fields that are already in the output.

| Panel | What it answers | Fields |
| --- | --- | --- |
| Open reviews, watching, clean, held | How big is the queue? | `decision` (`review`, `watch`, `clean`, `insufficient_data`) |
| Queue by night | Is something new happening? | `decision` per nightly run; first night a `review` appeared |
| What fired | Which checks produce the work? | `checks`, a list of ids. The families and labels are in `ops.json` under `checks`. |
| Human ceiling | Where do players sit against the best humans? | `metrics[]` with `name` `accuracy` and `headshot_rate`: `player_value`, `bound`, `own_p95` (the rank's line), `ceiling_extreme` (the best human), `key` (the weapon). Take both numbers from the same `key` |
| Reports are a queue | Do reports track the evidence? | `reports`, `decision` |
| Queue table | Who does a person open next? | `review-index.json` order, or sort by `decision` then `reports`. Show `reasons[0]`. |
| Case detail | What exactly fired? | `reasons`, `observations`, `metrics`, `party_note`, `vendor_twin`, `inherit_lags_ms`, `seal` |
| What the server sends | Which checks are switched off by missing fields? | `ops.json` → `coverage`: share of events per field, and per night when runs are merged |
| Who the baseline has | Which ranks and weapons are still untrained? | `ops.json` → `cohort`: players per band and weapon key, and `min_cohort_players` |
| Movement left out | Are blasts and vehicles being tagged? | `ops.json` → `movement`: counts by `displacement_cause` |

Rules that keep a homemade dashboard honest:

- **Group on `checks`, never on reason text.** The text is for people, and it changes.
- **Never chart raw accuracy as the alarm.** Use `bound` against `ceiling_extreme`. A raw number puts the best honest players at the top of every chart.
- **Keep `automated_action` visible.** It is always `none`. A dashboard is not a ban button.
- **`seal` is the packet a reviewer saw.** Store it with the human decision.

## 3. The search cluster you already pay for

Elasticsearch and Splunk are fine viewers if the building already runs them; see [elastic/](elastic/README.md) and [splunk/](splunk/readme.md). Index the case JSON, not the raw shot stream. The baselines, bounds and gear-rule runs are computed by `fpsdet score` before anything is indexed. A query on raw accuracy brings back the false bans this project exists to avoid.

`scan-index.json` is who to pull from cold storage first (reported players). `review-index.json` is who a person reads first (the evidence).
