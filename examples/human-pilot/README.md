# Running the consented honest-human pilot

This is the operator's checklist for the study in [docs/human-pilot.md](../../docs/human-pilot.md). The design is fixed in `design.json`; change it only by creating a new study version.

## Before anyone plays

1. Get the pinned engine, user-local:

   ```bash
   python examples/pilot/pilot.py godot --dest ~/godot-4.7.2
   ```

2. Run the machine dry run once on the machine you will use. It checks every instrument with scripted stand-ins, and checks that honest-style aiming in this arena does not already reach the bar:

   ```bash
   python examples/human-pilot/study.py dry-run --data /tmp/dry --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64
   ```

3. Choose where the data lives, outside the repository, for example `~/fpsdet-study`. It holds secrets while a session runs and raw telemetry until publication.

## For each participant

1. Give them `CONSENT.md` to read. Answer their questions.
2. If they agree, enroll them. Add `--publish` only if they also ticked the optional box.

   ```bash
   python examples/human-pilot/study.py enroll --data ~/fpsdet-study --agree [--publish]
   ```

   This prints their random id and their session order. Write nothing else down.

3. Run their seven sessions in that order:

   ```bash
   python examples/human-pilot/study.py session --data ~/fpsdet-study --participant hp-... --mode free --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64
   ```

   A game window opens. The participant presses Y, plays for four minutes, and the window closes. Read them the four questions the terminal shows and type their answers. Allow a break between sessions; they may stop at any time.

4. **On another machine on the same private network:** add `--bind 192.168.x.y --remote`. Run the printed client command there, from a copy of `examples/pilot/godot` and the same Godot build. The server never binds a public address.

## If the study stops

- **What stops it:** any stop condition (`design.json`). That includes a probe the server saw or heard, a participant reporting an unexplained avatar or sound, review-grade challenge evidence on an honest session, a leak, personal data, or replay that differs.
- **What happens:** the session command writes `STOP` and no further session starts.
- **What to do:** investigate, then record what you found:

  ```bash
  python examples/human-pilot/study.py clear-stop --data ~/fpsdet-study --reason "what was found and why it is safe to continue"
  ```

- **Rules:** no threshold changes. An honest crossing of the bar is a finding to report.

## After the last session

```bash
python examples/human-pilot/study.py analyze --data ~/fpsdet-study --out examples/human-pilot/result.json
```

- **Before committing,** review `result.json` for anything that could identify a person; it should hold only random ids and numbers.
- **Telemetry examples:** sample sessions are copied into the repository only from participants who ticked the publication box, and only their `public/events.ndjson` and `public/plan.json`.
- **Deletion:** delete the data folder once the results are published, at most 90 days after the last session. The session secrets are already gone: each is deleted right after its post-match check.
