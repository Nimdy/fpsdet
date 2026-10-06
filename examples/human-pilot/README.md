# Running the consented honest-human pilot

This is the operator's checklist for the study in [docs/human-pilot.md](../../docs/human-pilot.md). The design is fixed in `design.json`, and `amendment-1.json` and `amendment-2.json` add the collection rules below. Change any of them only by creating a new study version.

Do not edit, pull or switch the repository between the first session and the analysis: each session records the study code it ran.

## Before anyone plays

1. Get the pinned engine, user-local:

   ```bash
   python examples/pilot/pilot.py godot --dest ~/godot-4.7.2
   ```

2. Run the machine dry run once on the machine you will use. It checks every instrument with scripted stand-ins, and checks that honest-style aiming in this arena does not already reach the bar:

   ```bash
   python examples/human-pilot/study.py dry-run --data /tmp/dry --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64
   ```

3. Check the human client's controls on the machine participants will use. No person is needed: the check presses the keys and moves the mouse itself, in a virtual display.

   ```bash
   python examples/human-pilot/study.py controls-check --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64
   ```

   Every line must say `ok`.

4. Choose two folders outside the repository:
   - **the study's data,** for example `~/fpsdet-study`, which holds secrets while a session runs and raw telemetry until publication;
   - **practice,** for example `~/fpsdet-practice`, separate and never analysed.

## For each participant

1. Give them `CONSENT.md` to read. Answer their questions.
2. If they agree, enroll them. Add `--publish` only if they also ticked the optional box.

   ```bash
   python examples/human-pilot/study.py enroll --data ~/fpsdet-study --agree [--publish]
   ```

   This prints their random id and their session order. Write nothing else down.

   At most 4 participants are in play until 4 have completed every mode, then at most 12; `enroll` refuses otherwise. Someone who withdraws, is discontinued or fails practice frees a place for a replacement.

3. Run their practice: 90 seconds, no challenge.

   ```bash
   python examples/human-pilot/study.py practice --data ~/fpsdet-practice --study ~/fpsdet-study --participant hp-... --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64
   ```

   They press Y, look with the mouse and with the arrow keys, press Esc and click to take the mouse back, walk, and shoot a bot. Then ask them what the terminal asks.
   - **Under 30 frames a second, or a control that does not work:** the practice fails, and no session starts for them. They may practise again while there is a place.
   - **Mouse not captured:** they play with the arrow keys.

   No session starts without a passed practice.

4. Run their seven sessions in the order `enroll` printed; `session` refuses any other:

   ```bash
   python examples/human-pilot/study.py session --data ~/fpsdet-study --participant hp-... --mode free --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64
   ```

   A game window opens. The participant presses Y, plays for four minutes, and the window closes. Then:
   - Read them the six questions the terminal shows, and type their answers. A short comment is optional; it stays in the session's private folder.
   - Say whether the session ran as the protocol says. If it did not, choose the reason from the list. You answer before the session is scored, and a crossing of the bar is never a reason.

   A mode whose session did not count is played again. Allow a break between sessions; they may stop at any time.

5. **On another machine on the same private network:** add `--bind 192.168.x.y --remote`. Run the printed client command there, from a copy of `examples/pilot/godot` and the same Godot build. The server never binds a public address.

## If a participant leaves

- **They ask for their data to be deleted:** this deletes every session, the practice and the comments. Only an anonymous record that someone withdrew stays.

  ```bash
  python examples/human-pilot/study.py withdraw --data ~/fpsdet-study --participant hp-... --reason participant_request --practice ~/fpsdet-practice
  ```

  If any of their samples were published in the repository, the command lists them: delete those too.
- **They cannot continue, but did not ask for deletion:** use `--reason unable_to_continue`. Their sessions stay, and their place is freed.

## If the study stops

- **What stops it:** any stop condition (`design.json`). That includes a probe the server saw or heard, a participant reporting an unexplained avatar or sound, review-grade challenge evidence on an honest session, a leak, personal data, or replay that differs.
- **What happens:** the session command writes `STOP` and no further session starts.
- **What to do:** investigate, then record what you found:

  ```bash
  python examples/human-pilot/study.py clear-stop --data ~/fpsdet-study --reason "what was found and why it is safe to continue"
  ```

- **Review-grade evidence on a protocol-valid honest session ends collection.** `clear-stop` refuses it. A crossing on a session you had already marked invalid still stops the study, but it may be cleared after investigation. Keep the session folder as it is, and report it. The investigation is:
  1. replay the session offline;
  2. verify its packet and graph;
  3. inspect its overlap episodes;
  4. classify what the participant was doing.
- **A participant who saw or heard something they could not explain** stops the study until the server's verdicts, the placement and the knowledge state are inspected.
- **Rules:** no threshold changes. An honest crossing of the bar is a finding to report.

## After the last session

```bash
python examples/human-pilot/study.py analyze --data ~/fpsdet-study --out examples/human-pilot/result.json
```

- **The headline:** `primary` answers whether any protocol-valid honest session produced an unexplained review-grade finding. With a finding there is no rate and no interval. With none, bounds appear only once the planned group has completed.
- **Mixed versions:** `analyze` refuses participants enrolled under different consent notices, designs or amendments.
- **Before committing,** review `result.json` for anything that could identify a person; it should hold only random ids and numbers.
- **Telemetry examples:** sample sessions are copied into the repository only from participants who ticked the publication box, and only their `public/events.ndjson` and `public/plan.json`.
- **Deletion:** delete the data folder once the results are published, at most 90 days after the last session. The session secrets are already gone: each is deleted right after its post-match check.
