<!--
Code patches cannot be merged until a contributor agreement is published. See CONTRIBUTING.md.
Issues, false-positive reports, and profile proposals are welcome now.
-->

## What this changes

<!-- One or two sentences. Link the issue it answers. -->

## Checklist

- [ ] If this changes a decision: a planted cheater **and** its innocent twin are in `src/fpsdet/synthetic.py` and the tests, and the new rule separates them.
- [ ] `PYTHONPATH=src python3 -m unittest tests.test_fpsdet` passes.
- [ ] `PYTHONPATH=src python3 -m fpsdet demo` ends with `Planted cases matched`, and the regenerated `demo/board.html` is committed.
- [ ] Docs updated where behavior or fields changed (README, `docs/`, `site/`, `schema/`).
- [ ] No cheat-building instructions: no working cheat code, no step-by-step bypass.
- [ ] Every id in sample data is made up.
