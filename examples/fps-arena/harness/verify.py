"""Offline check of the committed arena captures against result.json: no engine, no secret.

    python examples/fps-arena/harness/verify.py

The same as ``arena.py verify``; tests/test_fps_arena.py calls the same function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arena  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(arena.main(["verify"]))
