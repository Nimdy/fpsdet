"""Writes site/img/setup-quest.svg (wide) and site/img/setup-quest-tall.svg (phones). Edit this file, not the images.

The Try it page's setup, drawn as a game quest: five levels, a HUD, an achievement and a loading tip. The jokes
are cheesy on purpose; the facts in them are the page's, and a test holds the two together:
nothing is installed on a server or a player's PC, scoring sends nothing anywhere, names and addresses are
dropped and ids are salted, and every case says automated_action: none.

    python site/build_quest.py

Plain SVG: no script, no external font, image or link. The pages load it with <img>, so it falls back to
the reader's own monospace font.
"""

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "img"

BG, PANEL, PLOT, LINE, LINE_BRIGHT = "#040718", "#0a102c", "#070b24", "#171e42", "#242e5e"
INK, MUTED, FAINT = "#e6ebf9", "#8a93b8", "#4b5478"
SIGNAL, VIOLET, CLEAN, WATCH, REVIEW = "#38c8ff", "#a855f7", "#2dd4bf", "#d8b26c", "#ff5470"
FONT = "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace"

TITLE = "Quest: catch cheaters without touching prod"
DIFFICULTY = "DIFFICULTY ★☆☆☆☆ TUTORIAL"
HUD = [
    ("PROD HP", "100/100", "damage taken: 0", "hp"),
    ("PLAYERS BANNED", "0", "ban hammer not included", "hammer"),
    ("NETWORK", "OFFLINE", "no packets were harmed", "offline"),
]
LEVELS = [
    ("LOOT THE REPO", "git clone + demo", ["Zero dependencies.", "Inventory weight: 0 kg."], "floppy"),
    ("EXPORT YOUR LOGS", "a week of matches", ["Raid your own log", "server. No aggro."], "scroll"),
    ("GO INCOGNITO", "1 line: convert.py", ["Names & IPs dropped.", "Ids salted: +100 stealth."], "mask"),
    ("SCORE IT", "1 line: fpsdet score", ["Runs offline. Prod", "never even alt-tabs."], "crosshair"),
    ("READ THE CASES", "open dashboard.html", ["A human reviews.", "Nobody gets banned. GG."], "trophy"),
]
ACHIEVEMENT = ("ACHIEVEMENT UNLOCKED", "Touched the logs, not prod", "+50 XP: send us your results")
TIP = ("LOADING TIP:", "The ban hammer is not in this build. automated_action: none")


def text(x, y, value, size, fill=INK, weight=400, anchor="start", spacing=0):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
            f"{escape(value)}</text>")


def icon(kind, cx, cy, s=1.0):
    """A small line icon centred on (cx, cy), about 44 px across at s=1."""
    g = f'<g transform="translate({cx} {cy}) scale({s})" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">'
    if kind == "floppy":
        body = (f'<path d="M-18 -20 h28 l8 8 v32 h-36 z" stroke="{SIGNAL}"/>'
                f'<rect x="-11" y="-20" width="16" height="11" stroke="{SIGNAL}"/>'
                f'<rect x="-11" y="4" width="22" height="16" stroke="{VIOLET}"/>')
    elif kind == "scroll":
        body = (f'<path d="M-14 -18 h26 a5 5 0 0 1 0 10 h-3 v26 a5 5 0 0 1 -5 5 h-24 a5 5 0 0 1 0 -10 h3 v-26 a5 5 0 0 1 5 -5" stroke="{SIGNAL}"/>'
                f'<path d="M-6 -4 h14 M-6 4 h14 M-6 12 h9" stroke="{VIOLET}"/>')
    elif kind == "mask":
        body = (f'<path d="M-22 -6 c6 -10 38 -10 44 0 c2 10 -6 18 -14 16 c-4 -1 -6 -6 -8 -6 c-2 0 -4 5 -8 6 c-8 2 -16 -6 -14 -16 z" stroke="{SIGNAL}"/>'
                f'<ellipse cx="-9" cy="-1" rx="5" ry="3.5" stroke="{VIOLET}"/><ellipse cx="9" cy="-1" rx="5" ry="3.5" stroke="{VIOLET}"/>')
    elif kind == "crosshair":
        body = (f'<circle r="15" stroke="{SIGNAL}"/><circle r="3" fill="{REVIEW}" stroke="{REVIEW}"/>'
                f'<path d="M0 -22 v10 M0 12 v10 M-22 0 h10 M12 0 h10" stroke="{VIOLET}"/>')
    elif kind == "trophy":
        body = (f'<path d="M-12 -18 h24 v10 a12 12 0 0 1 -24 0 z" stroke="{WATCH}"/>'
                f'<path d="M-12 -14 h-7 a7 7 0 0 0 9 12 M12 -14 h7 a7 7 0 0 1 -9 12" stroke="{WATCH}"/>'
                f'<path d="M0 4 v8 M-9 18 h18 M-6 12 h12" stroke="{WATCH}"/>')
    elif kind == "hammer":
        body = (f'<path d="M-14 -14 l12 -6 l8 14 l-12 6 z M-1 -3 l14 22" stroke="{MUTED}"/>'
                f'<path d="M-20 18 L20 -18" stroke="{REVIEW}"/>')
    elif kind == "offline":
        body = (f'<path d="M-18 -4 a26 26 0 0 1 36 0 M-11 3 a16 16 0 0 1 22 0 M-4 10 a6 6 0 0 1 8 0" stroke="{MUTED}"/>'
                f'<path d="M-20 18 L20 -18" stroke="{REVIEW}"/>')
    else:
        raise ValueError(kind)
    return g + body + "</g>"


def hp_bar(x, y, width):
    cells = 10
    gap = 4
    cell = (width - gap * (cells - 1)) / cells
    return "".join(f'<rect x="{x + i * (cell + gap):.1f}" y="{y}" width="{cell:.1f}" height="14" rx="2" fill="{CLEAN}"/>' for i in range(cells))


def hud_panel(x, y, w, h, item, scale=1.0):
    """One HUD readout. scale enlarges the type for the tall, phone-sized image."""
    label, value, note, kind = item
    label_size, note_size = round(15 * scale), round(14 * scale)
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{PANEL}" stroke="{LINE_BRIGHT}"/>']
    if kind == "hp":
        parts += [text(x + 22, y + 14 + label_size, label, label_size, MUTED, 600, spacing=2),
                  text(x + w - 22, y + 14 + label_size, value, round(20 * scale), CLEAN, 700, "end"),
                  hp_bar(x + 22, y + 28 + label_size, w - 44), text(x + 22, y + h - 14, note, note_size, MUTED)]
    else:
        parts += [icon(kind, x + 44, y + h / 2 - 2, 0.95), text(x + 86, y + 14 + label_size, label, label_size, MUTED, 600, spacing=2),
                  text(x + 86, y + h / 2 + 12 * scale, value, round((26 if kind == "hammer" else 22) * scale), CLEAN if kind == "hammer" else WATCH, 700),
                  text(x + 86, y + h - 14, note, note_size, MUTED)]
    return "".join(parts)


def node(cx, cy, number, kind, last):
    ring = (f'<circle cx="{cx}" cy="{cy}" r="46" fill="{PLOT}" stroke="{SIGNAL if not last else WATCH}" stroke-width="3"/>'
            f'<circle cx="{cx}" cy="{cy}" r="54" fill="none" stroke="{SIGNAL if not last else WATCH}" stroke-opacity="0.25" stroke-width="2"/>')
    badge = (f'<rect x="{cx - 30}" y="{cy - 74}" width="60" height="22" rx="11" fill="{VIOLET if not last else WATCH}"/>'
             + text(cx, cy - 58, f"LVL {number}", 13, BG, 700, "middle", 1))
    return ring + icon(kind, cx, cy) + badge


def toast(x, y, w, h, title_size, body_size):
    head, body, xp = ACHIEVEMENT
    return "".join([
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{PANEL}" stroke="{WATCH}" stroke-width="2"/>',
        f'<rect x="{x}" y="{y}" width="8" height="{h}" rx="4" fill="{WATCH}"/>',
        icon("trophy", x + 54, y + h / 2, 1.05),
        text(x + 100, y + h / 2 - 8, head, title_size, WATCH, 700, spacing=2),
        text(x + 100, y + h / 2 + body_size + 4, body, body_size, INK, 700),
    ]), xp


def frame(width, height, inner):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
            f'aria-labelledby="quest-title quest-desc" font-family="{FONT}">'
            f'<title id="quest-title">{escape(TITLE)}</title>'
            f'<desc id="quest-desc">{escape(description())}</desc>'
            f'<rect width="{width}" height="{height}" rx="20" fill="{BG}"/>'
            f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="19" fill="none" stroke="{LINE_BRIGHT}" stroke-width="2"/>'
            f"{inner}</svg>\n")


def description() -> str:
    steps = "; ".join(f"level {i}, {title.lower()}: {cost}, {' '.join(quip)}" for i, (title, cost, quip, _) in enumerate(LEVELS, 1))
    return (f"A game-style quest for setting up fpsdet on logs you already have. HUD: prod HP 100 of 100, players banned 0, "
            f"network offline. {steps}. {ACHIEVEMENT[0].capitalize()}: {ACHIEVEMENT[1]}. {TIP[0].capitalize()} {TIP[1]}")


def wide() -> str:
    w, h = 1200, 700
    parts = [f'<rect x="1" y="1" width="{w - 2}" height="62" rx="19" fill="{PANEL}"/>',
             f'<rect x="1" y="44" width="{w - 2}" height="20" fill="{PANEL}"/>',
             f'<line x1="0" y1="64" x2="{w}" y2="64" stroke="{LINE_BRIGHT}" stroke-width="2"/>',
             "".join(f'<circle cx="{30 + i * 20}" cy="32" r="6" fill="{c}"/>' for i, c in enumerate((REVIEW, WATCH, CLEAN))),
             text(100, 40, TITLE, 22, INK, 700), text(w - 32, 39, DIFFICULTY, 15, WATCH, 700, "end", 1)]
    for i, item in enumerate(HUD):
        parts.append(hud_panel(40 + i * 380, 92, 360, 96, item))
    xs = [140 + i * 230 for i in range(len(LEVELS))]
    cy = 300
    parts.append(f'<path d="M{xs[0]} {cy} H{xs[-1]}" stroke="{LINE_BRIGHT}" stroke-width="4" stroke-dasharray="2 14" stroke-linecap="round"/>')
    for i, (title, cost, quip, kind) in enumerate(LEVELS):
        x = xs[i]
        last = i == len(LEVELS) - 1
        parts.append(node(x, cy, i + 1, kind, last))
        parts.append(text(x, cy + 86, title, 17, WATCH if last else INK, 700, "middle"))
        parts.append(f'<rect x="{x - 98}" y="{cy + 100}" width="196" height="26" rx="6" fill="{PLOT}" stroke="{LINE}"/>')
        parts.append(text(x, cy + 118, cost, 13, SIGNAL, 600, "middle"))
        for j, line in enumerate(quip):
            parts.append(text(x, cy + 152 + j * 21, line, 14, MUTED, 400, "middle"))
    body, xp = toast(40, 532, 1120, 84, 15, 22)
    parts.append(body)
    parts.append(text(1130, 582, xp, 16, SIGNAL, 700, "end"))
    parts.append(f'<circle cx="58" cy="660" r="9" fill="none" stroke="{LINE_BRIGHT}" stroke-width="3"/>'
                 f'<path d="M58 651 a9 9 0 0 1 9 9" fill="none" stroke="{SIGNAL}" stroke-width="3" stroke-linecap="round"/>')
    parts.append(text(80, 665, TIP[0], 15, SIGNAL, 700, spacing=1) + text(220, 665, TIP[1], 15, MUTED))
    return frame(w, h, "".join(parts))


def tall() -> str:
    w = 600
    parts = [f'<rect x="1" y="1" width="{w - 2}" height="150" rx="19" fill="{PANEL}"/>',
             f'<rect x="1" y="132" width="{w - 2}" height="20" fill="{PANEL}"/>',
             f'<line x1="0" y1="152" x2="{w}" y2="152" stroke="{LINE_BRIGHT}" stroke-width="2"/>',
             "".join(f'<circle cx="{34 + i * 22}" cy="34" r="7" fill="{c}"/>' for i, c in enumerate((REVIEW, WATCH, CLEAN))),
             text(110, 41, "QUEST", 22, WATCH, 700, spacing=3),
             text(34, 88, "Catch cheaters without", 27, INK, 700),
             text(34, 126, "touching prod", 27, INK, 700),
             text(34, 194, DIFFICULTY, 18, WATCH, 700, spacing=1)]
    y = 222
    for item in HUD:
        parts.append(hud_panel(30, y, w - 60, 124, item, scale=1.3))
        y += 140
    y += 30
    x = 92
    rows = []
    for i, (title, cost, quip, kind) in enumerate(LEVELS):
        last = i == len(LEVELS) - 1
        cy = y + 64
        rows.append((cy, last))
        parts.append(text(170, cy - 28, title, 24, WATCH if last else INK, 700))
        parts.append(f'<rect x="170" y="{cy - 14}" width="{len(cost) * 14 + 24}" height="34" rx="7" fill="{PLOT}" stroke="{LINE}"/>')
        parts.append(text(182, cy + 9, cost, 19, SIGNAL, 600))
        for j, line in enumerate(quip):
            parts.append(text(170, cy + 52 + j * 27, line, 20, MUTED))
        y += 196
    parts.append(f'<path d="M{x} {rows[0][0]} V{rows[-1][0]}" stroke="{LINE_BRIGHT}" stroke-width="4" stroke-dasharray="2 14" stroke-linecap="round"/>')
    for i, (cy, last) in enumerate(rows):
        parts.append(node(x, cy, i + 1, LEVELS[i][3], last))
    body, xp = toast(30, y + 10, w - 60, 110, 17, 22)
    parts.append(body)
    parts.append(text(130, y + 158, xp, 19, SIGNAL, 700))
    y += 200
    parts.append(f'<circle cx="46" cy="{y}" r="10" fill="none" stroke="{LINE_BRIGHT}" stroke-width="3"/>'
                 f'<path d="M46 {y - 10} a10 10 0 0 1 10 10" fill="none" stroke="{SIGNAL}" stroke-width="3" stroke-linecap="round"/>')
    parts.append(text(70, y + 7, TIP[0], 19, SIGNAL, 700, spacing=1))
    words, lines, current = TIP[1].split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > 38:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    lines.append(current)
    for j, line in enumerate(lines):
        parts.append(text(34, y + 42 + j * 28, line, 20, MUTED))
    height = y + 42 + len(lines) * 28 + 24
    return frame(w, height, "".join(parts))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "setup-quest.svg").write_text(wide(), encoding="utf-8")
    (OUT / "setup-quest-tall.svg").write_text(tall(), encoding="utf-8")
    print(f"wrote {OUT / 'setup-quest.svg'} and {OUT / 'setup-quest-tall.svg'}")
