"""The public pages. GitHub Pages serves them plus a freshly generated desk."""

from __future__ import annotations

from pathlib import Path

from .board import write_board
from .synthetic import Demo

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
PAGE = SITE / "index.html"
HTML = ("index.html", "scoring.html", "wire.html", "games.html", "source.html", "decoys.html", "evidence.html", "try.html")
# A prefix, so a deep link such as board.html#card-wire-lock is rewritten too.
LOCAL_DESK = 'href="../demo/board.html'
LOCAL_SITE = 'href="../site/'
LOCAL_DEMO = 'href="../demo/'


def write_pages(demo: Demo, dest: str | Path | None = None) -> Path:
    """Write the site and board.html into one folder a static host can serve."""
    target = Path(dest) if dest else Path.cwd() / "_site"
    if not PAGE.is_file():
        raise FileNotFoundError(PAGE)
    target.mkdir(parents=True, exist_ok=True)
    for name in HTML:
        src = SITE / name
        if not src.is_file():
            raise FileNotFoundError(src)
        text = src.read_text(encoding="utf-8")
        if LOCAL_DESK not in text:
            raise ValueError(f"{name} does not link demo/board.html")
        # Published files sit side by side. The repo copies sit in site/ and demo/, so every
        # ../demo/ link (the desk, and the real-match pages beside it) loses its folder.
        (target / name).write_text(text.replace(LOCAL_DEMO, 'href="'), encoding="utf-8")
    css = SITE / "fpsdet.css"
    if not css.is_file():
        raise FileNotFoundError(css)
    (target / css.name).write_bytes(css.read_bytes())
    for name in ("fonts", "img"):  # the type, and the images the pages show (site/build_quest.py draws them)
        source = SITE / name
        if not source.is_dir():
            raise FileNotFoundError(source)
        for path in source.iterdir():
            if path.is_file():
                folder = target / name
                folder.mkdir(exist_ok=True)
                (folder / path.name).write_bytes(path.read_bytes())
    board = write_board(demo, target / "board.html")
    text = board.read_text(encoding="utf-8").replace(LOCAL_SITE, 'href="')
    if LOCAL_SITE in text or 'href="scoring.html"' not in text:
        raise ValueError("the desk does not link the site pages")
    board.write_text(text, encoding="utf-8")
    return target
