"""Writes site/decoys.html. Edit this file, not the page.

The chart and its caption are drawn from examples/tf2/desk.json, so the numbers are the TF2 run's.
After rebuilding the TF2 desk data, run `python site/build_decoys.py` and then `npm run css`.
A test fails if the page and the run disagree.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = json.loads((ROOT / "examples" / "tf2" / "desk.json").read_text(encoding="utf-8"))["rows"]


def sniper(label):
    out = []
    for row in rows:
        if row["truth"] != label:
            continue
        for m in row["metrics"]:
            if m["key"] == "sniperrifle" and m["name"] == "accuracy" and m.get("value") is not None:
                out.append((m["value"], m.get("human")))
    return out


honest, cheats = sniper("never banned"), sniper("banned for cheating")
best = max(h for _, h in honest + cheats if h is not None)
past = sum(1 for v, _ in cheats if v > best)
med = lambda xs: sorted(v for v, _ in xs)[len(xs) // 2]

# Histogram: share of each group per 5-point bin, 10% to 80%.
lo, hi, step = 0.10, 0.80, 0.05
bins = [round(lo + i * step, 2) for i in range(int(round((hi - lo) / step)))]
def shares(xs):
    counts = [0] * len(bins)
    for v, _ in xs:
        i = min(len(bins) - 1, max(0, int((v - lo) / step)))
        counts[i] += 1
    return [c / len(xs) for c in counts]
hs, cs = shares(honest), shares(cheats)
W, H, L, R, T, B = 720, 260, 46, 14, 16, 34
pw, ph = W - L - R, H - T - B
import math
ymax = math.ceil(max(hs + cs) / 0.05) * 0.05  # the scale fits the tallest bar, so none is clipped
X = lambda v: L + (v - lo) / (hi - lo) * pw
Y = lambda s: T + ph - min(s, ymax) / ymax * ph
bw = pw / len(bins)
parts = []
for t in [round(i * 0.05, 2) for i in range(int(round(ymax / 0.05)) + 1)]:
    y = Y(t)
    parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="stroke-line" stroke-width="1"/>')
    parts.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" class="fill-muted" font-size="11">{t:.0%}</text>')
for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
    parts.append(f'<text x="{X(v):.1f}" y="{H - B + 16}" text-anchor="middle" class="fill-muted" font-size="11">{v:.0%}</text>')
for i, b in enumerate(bins):
    x0 = L + i * bw
    for k, (series, cls) in enumerate(((hs, "fill-clean"), (cs, "fill-review"))):
        s = series[i]
        if s <= 0:
            continue
        x = x0 + bw * (0.12 + k * 0.40)
        y = Y(s)
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.36:.1f}" height="{T + ph - y:.1f}" rx="2" class="{cls}"/>')
bx = X(best)
parts.append(f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{T}" y2="{T + ph}" class="stroke-ink" stroke-width="2"/>')
parts.append(f'<text x="{bx - 6:.1f}" y="{T + 12}" text-anchor="end" class="fill-ink" font-size="12">best human measured {best:.0%}</text>')
parts.append(f'<text x="{W - R}" y="{H - 4}" text-anchor="end" class="fill-muted" font-size="11">sniper rifle accuracy, each player over their matches →</text>')
chart = "\n        ".join(parts)

HEAD_NAV = """<header class="relative z-50 border-b border-line bg-bg/80 backdrop-blur-xl md:sticky md:top-0">
  <div class="mx-auto flex w-full max-w-[1800px] flex-wrap items-center gap-x-5 gap-y-2 px-10 py-3 max-md:px-4">
    <a class="zb-logo flex items-center gap-2.5 font-mono text-[1.05rem] font-semibold tracking-tight text-white no-underline" href="index.html"><span class="pulse-dot" aria-hidden="true"></span><span>fps<span class="text-signal">det</span></span></a>
    <a class="font-mono text-[11px] text-muted no-underline hover:text-ink" href="https://zerobandwidth.com">by Zero<span class="text-signal">Bandwidth</span></a>
    <nav class="flex flex-wrap items-center gap-x-0.5 gap-y-1 text-[0.9rem]" aria-label="Site">
      <a class="nav-link" href="index.html"><span>01</span>What this is</a>
      <a class="nav-link" href="scoring.html"><span>02</span>Scoring</a>
      <a class="nav-link" href="wire.html"><span>03</span>Wire a game</a>
      <a class="nav-link" href="games.html"><span>04</span>Games</a>
      <a class="nav-link" href="source.html"><span>05</span>Source</a>
      <a class="nav-link" href="../demo/board.html"><span>06</span>Desk</a>
      <a class="nav-link" href="decoys.html" aria-current="page"><span>07</span>Decoys</a>
    </nav>
    <p class="status-pill ml-auto max-md:ml-0"><span class="pulse-dot" aria-hidden="true"></span>Automated action: none</p>
  </div>
</header>"""

H2 = 'class="m-0 font-mono text-[1.95rem] font-semibold leading-[1.12] tracking-tight text-white text-balance"'
CARD = 'class="rounded-xl flex flex-col border border-line panel p-5"'
LABEL = 'class="m-0 font-mono text-[11px] uppercase tracking-[0.12em] text-muted"'
TH = 'class="border-b border-line py-3 pr-6 font-mono text-xs font-normal uppercase tracking-[0.12em] text-muted"'
TD = 'class="border-b border-line py-3 pr-6 align-top"'
ARROW = '<div class="flex items-center justify-center px-2 max-lg:py-1"><svg class="h-5 w-10 text-muted max-lg:rotate-90" viewBox="0 0 48 20" fill="none" aria-hidden="true"><path d="M2 10h40m-7-7 7 7-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'

# One moment, three views. Schematics, not data.
MAP = """<svg class="block h-auto w-full" viewBox="0 0 300 210" role="img" aria-label="Top-down map: you on the left, a wall in the middle, a hidden enemy and a decoy behind it">
          <rect x="0" y="0" width="300" height="210" rx="8" class="fill-plot"/>
          <path d="M44 105 L156 40 L156 170 Z" class="fill-signal" opacity="0.10"/>
          <rect x="150" y="18" width="12" height="174" rx="2" class="fill-line-bright"/>
          <circle cx="40" cy="105" r="9" class="fill-signal"/>
          <text x="40" y="132" text-anchor="middle" class="fill-ink" font-size="11">you</text>
          <circle cx="232" cy="58" r="9" class="fill-muted"/>
          <text x="232" y="84" text-anchor="middle" class="fill-muted" font-size="11">enemy, hidden</text>
          <path d="M196 188 C 210 176, 214 164, 226 152" fill="none" class="stroke-watch" stroke-width="1.5" stroke-dasharray="3 4"/>
          <circle cx="232" cy="146" r="9" fill="none" class="stroke-watch" stroke-width="2" stroke-dasharray="4 3"/>
          <text x="232" y="124" text-anchor="middle" class="fill-watch" font-size="11">decoy</text>
          <text x="150" y="205" text-anchor="middle" class="fill-muted" font-size="10">the server knows: neither can be seen or heard from here</text>
        </svg>"""
SCENE = """<svg class="block h-auto w-full" viewBox="0 0 300 210" role="img" aria-label="{label}">
          <rect x="0" y="0" width="300" height="210" rx="8" class="fill-plot"/>
          <rect x="0" y="150" width="300" height="60" rx="0" class="fill-panel"/>
          <rect x="120" y="22" width="180" height="168" class="fill-line-bright"/>
          {boxes}
          <line x1="{cx}" x2="{cx}" y1="{cy0}" y2="{cy1}" class="stroke-ink" stroke-width="2"/>
          <line x1="{cxa}" x2="{cxb}" y1="{cy}" y2="{cy}" class="stroke-ink" stroke-width="2"/>
          <text x="150" y="205" text-anchor="middle" class="fill-muted" font-size="10">{note}</text>
        </svg>"""
def scene(label, boxes, note, cx, cy):
    return SCENE.format(label=label, boxes=boxes, note=note, cx=cx, cy=cy, cy0=cy - 8, cy1=cy + 8, cxa=cx - 8, cxb=cx + 8)
GAME = scene("What the game draws: a wall and nothing behind it", "", "the stock client draws neither: both are behind the wall", 100, 100)
HACK_BOXES = """<rect x="214" y="44" width="30" height="62" rx="2" fill="none" class="stroke-review" stroke-width="2"/>
          <text x="229" y="38" text-anchor="middle" class="fill-review" font-size="11">?</text>
          <rect x="168" y="86" width="30" height="62" rx="2" fill="none" class="stroke-review" stroke-width="2"/>
          <text x="183" y="80" text-anchor="middle" class="fill-review" font-size="11">?</text>"""
HACK = scene("What a wallhack draws: two identical boxes through the wall, one of them the decoy, with the crosshair on it",
             HACK_BOXES, "two boxes it cannot tell apart; the aim follows one", 183, 112)

PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Decoys — fpsdet</title>
<meta name="description" content="One server feature that skill cannot explain: a body the game never draws, that only software reading memory or packets can follow.">
<link rel="icon" href="data:,">
<link rel="stylesheet" href="fpsdet.css">
</head>
<body class="relative m-0 bg-bg font-sans text-[17px] leading-relaxed text-ink antialiased">
<div class="zb-backdrop" aria-hidden="true"></div>
<a class="sr-only rounded-md bg-signal px-4 py-2 font-medium text-bg focus:not-sr-only focus:absolute focus:left-4 focus:top-4" href="#main">Skip to content</a>
{HEAD_NAV}

<main id="main">
<section class="mx-auto grid w-full max-w-[1800px] grid-cols-12 items-end gap-8 px-10 py-12 max-lg:grid-cols-1 max-md:px-4">
  <div class="col-span-6 max-lg:col-span-1">
    <p class="prompt m-0"><span class="text-signal-soft">zb@zerobandwidth</span><span class="text-faint">:</span><span class="text-prototype">~/fpsdet</span><span class="text-faint">$</span> <span class="text-ink">Add one thing</span><span class="caret" aria-hidden="true"></span></p>
    <h1 class="m-0 mt-5 font-mono text-[3.4rem] font-bold leading-[1.04] tracking-tighter text-white text-balance max-xl:text-[2.9rem] max-md:text-[2.05rem]">A body no one can see. <span class="text-gradient-brand">Only a cheat follows it.</span></h1>
  </div>
  <p class="col-span-6 m-0 text-lg max-lg:col-span-1">Baselines catch players past every human measured. A careful cheat stays inside the human range, so skill alone cannot separate it from a very good player. A decoy is evidence that does not depend on skill. The server sends one client a body that client cannot see or hear. The game never draws it. Software that reads memory or packets does, and its aim follows. This does not end cheating.</p>
</section>

<div class="mx-auto w-full max-w-[1800px] px-10 pb-16 max-md:px-4">
  <section id="overlap" class="mb-16" aria-labelledby="overlap-title">
    <h2 id="overlap-title" {H2}>Why skill runs out</h2>
    <p class="m-0 mt-4 max-w-3xl">From the <a class="text-ink underline decoration-line underline-offset-4" href="../demo/tf2.html">real TF2 test</a>: sniper rifle accuracy for {len(honest)} never-banned players and {len(cheats)} players banned for cheating, each over their matches. The median honest player hits {med(honest):.1%} of shots and the median banned cheater {med(cheats):.1%}. The best human measured hits {best:.0%}. Only {past} of the {len(cheats)} banned cheaters are past that line. The rest look like people, and fpsdet will not accuse a player for being good.</p>
    <figure class="m-0 mt-6 rounded-xl border border-line panel p-5">
      <p class="m-0 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted"><span class="flex items-center gap-2"><span class="h-2.5 w-2.5 bg-clean" aria-hidden="true"></span>Never banned ({len(honest)})</span><span class="flex items-center gap-2"><span class="h-2.5 w-2.5 bg-review" aria-hidden="true"></span>Banned for cheating ({len(cheats)})</span><span>Share of each group per 5-point band.</span></p>
      <svg class="mt-3 block h-auto w-full" viewBox="0 0 {W} {H}" role="img" aria-label="Sniper rifle accuracy: honest players and banned cheaters overlap almost entirely, and nearly all cheaters sit inside the best human">
        {chart}
      </svg>
      <figcaption class="m-0 mt-2 text-sm text-muted">Real data: logs.tf match logs and RGL bans, scored by fpsdet in <code class="font-mono text-[0.92em]">examples/tf2</code>.</figcaption>
    </figure>
  </section>

  <section id="views" class="mb-16" aria-labelledby="views-title">
    <h2 id="views-title" {H2}>One moment, three views</h2>
    <p class="m-0 mt-4 max-w-3xl">The server already decides who can see whom: it needs that for hit registration and for culling. A decoy uses the same answer. It goes only where this client can neither see nor hear anyone, and it moves like a person because it replays another player's real movement on a different heading. Illustrations, not data.</p>
    <div class="mt-8 grid grid-cols-3 gap-4 max-lg:grid-cols-1">
      <figure {CARD}>
        <p {LABEL}>What the server knows</p>
        <div class="mt-3">{MAP}</div>
      </figure>
      <figure {CARD}>
        <p {LABEL}>What the game draws</p>
        <div class="mt-3">{GAME}</div>
        <p class="m-0 mt-3 text-sm text-muted">An honest player sees a wall. There is nothing to aim at, so there is nothing to follow.</p>
      </figure>
      <figure {CARD}>
        <p {LABEL}>What a wallhack draws</p>
        <div class="mt-3">{HACK}</div>
        <p class="m-0 mt-3 text-sm text-muted">The cheat reads both bodies. They look the same to it. When the aim follows the decoy, the server notices.</p>
      </figure>
    </div>
  </section>

  <section id="loop" class="mb-16" aria-labelledby="loop-title">
    <h2 id="loop-title" {H2}>The loop</h2>
    <div class="mt-8 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] max-lg:grid-cols-1">
      <div {CARD}><p {LABEL}>1 · Pick a moment</p><p class="m-0 mt-2 text-sm">The server finds a space this client can neither see nor hear, from its own visibility and audio checks.</p></div>
      {ARROW}
      <div {CARD}><p {LABEL}>2 · Send a decoy</p><p class="m-0 mt-2 text-sm">A body there, replaying another player's real movement on a different heading. Same fields as any player.</p></div>
      {ARROW}
      <div {CARD}><p {LABEL}>3 · Nobody honest sees it</p><p class="m-0 mt-2 text-sm">The stock client never draws it: it is behind a wall. A wallhack, an ESP, or a packet reader shows it.</p></div>
      {ARROW}
      <div {CARD}><p {LABEL}>4 · Log one number</p><p class="m-0 mt-2 text-sm">On each shot, <code class="font-mono text-[0.92em]">private_track_ms</code>: how long the aim stayed on the decoy since the last shot.</p></div>
      {ARROW}
      <div class="rounded-xl flex flex-col border border-line border-t-[3px] border-t-review panel p-5"><p {LABEL}>5 · A person decides</p><p class="m-0 mt-2 text-sm">fpsdet opens a review after at least 8 shots and 1,200 ms of tracking. One crossing is never a case.</p></div>
    </div>
  </section>

  <section id="rules" class="mb-16" aria-labelledby="rules-title">
    <h2 id="rules-title" {H2}>Rules that keep honest players safe</h2>
    <ul class="m-0 mt-6 grid list-none grid-cols-2 gap-x-10 gap-y-3 p-0 max-lg:grid-cols-1">
      <li class="flex gap-3"><span class="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-clean" aria-hidden="true"></span><span><strong class="font-medium">Only where this client cannot perceive.</strong> Place a decoy only in space the server's own line-of-sight and audio checks say this client cannot see or hear. A decoy an honest player could see manufactures a case.</span></li>
      <li class="flex gap-3"><span class="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-clean" aria-hidden="true"></span><span><strong class="font-medium">No sound, no collision, no damage.</strong> It makes no footsteps, blocks nothing, and cannot be hit or hurt anyone. It changes no match.</span></li>
      <li class="flex gap-3"><span class="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-clean" aria-hidden="true"></span><span><strong class="font-medium">No decoy bit.</strong> Send it with the same fields as a real player. The safest form is a real enemy's own identity, sent to this one client at a replayed position while that enemy is hidden from it, then switched back with a snap when the enemy comes into view.</span></li>
      <li class="flex gap-3"><span class="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-clean" aria-hidden="true"></span><span><strong class="font-medium">Tracking, not crossing.</strong> A crosshair passing a common angle is not evidence. The check needs sustained following across many shots.</span></li>
      <li class="flex gap-3"><span class="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-clean" aria-hidden="true"></span><span><strong class="font-medium">Rotate it.</strong> Change where, when and how often decoys appear, on the server, without a client patch.</span></li>
      <li class="flex gap-3"><span class="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-clean" aria-hidden="true"></span><span><strong class="font-medium">Tell players.</strong> Say in general terms that the server may send bodies a player cannot see, and what is logged. <a class="text-ink underline decoration-line underline-offset-4" href="https://github.com/Nimdy/detect-FPS-hackers/blob/main/docs/players.md">docs/players.md</a> has the wording.</span></li>
    </ul>
  </section>

  <section id="counter" class="mb-16" aria-labelledby="counter-title">
    <h2 id="counter-title" {H2}>Can it be countered?</h2>
    <p class="m-0 mt-4 max-w-3xl">Yes. Every counter costs the cheat what it was paying for. A cheat that cannot trust hidden positions is no longer a reliable wallhack, and the server can change its decoys without shipping anything to the client.</p>
    <div class="mt-6 overflow-x-auto">
    <table class="w-full border-collapse text-left">
      <thead><tr><th {TH}>The cheat's counter</th><th {TH}>What it costs the cheat</th><th {TH}>What still gets through</th></tr></thead>
      <tbody>
        <tr><td {TD}>Drop bodies that are not real players</td><td {TD}>Nothing, if the decoy has any tell. So it must not: same fields, ideally a real enemy's own identity.</td><td {TD}>A decoy with a tell. That is a server bug to fix.</td></tr>
        <tr><td {TD}>Trust only positions that move like a person</td><td {TD}>The decoy replays real human movement, so this filter keeps it.</td><td {TD}>Decoys with impossible paths, again a server bug.</td></tr>
        <tr><td {TD}>Cross-check with footsteps and sounds</td><td {TD}>Decoys only go where this client hears no one, so there is nothing to cross-check.</td><td {TD}>Nothing new.</td></tr>
        <tr><td {TD}>Show hidden players, but never aim at them</td><td {TD}>The aim stops being assisted through walls. What is left is map awareness.</td><td {TD}><strong class="font-medium">Information-only cheating.</strong> fpsdet's other checks still apply, but a decoy does not catch it.</td></tr>
        <tr><td {TD}>Read the screen, not memory (a pixel aimbot)</td><td {TD}>It cannot see through walls at all.</td><td {TD}><strong class="font-medium">Pixel aimbots.</strong> Only aim statistics catch them, when they are past every human.</td></tr>
        <tr><td {TD}>Learn the decoy pattern</td><td {TD}>An update every time the server changes the pattern. The server changes it without a patch.</td><td {TD}>A cheat that has learned today's pattern, until it changes.</td></tr>
        <tr><td {TD}>Follow hidden targets only now and then</td><td {TD}>Most of the advantage. fpsdet adds tracking up across shots and matches.</td><td {TD}>Very sparse use, slowly.</td></tr>
      </tbody>
    </table>
    </div>
    <p class="m-0 mt-4 max-w-3xl text-sm text-muted">Decoys stack with server-side culling: a server that stops sending players a client cannot see removes most of what a wallhack shows, and the few bodies left in the hidden space can be decoys. See <a class="text-ink underline decoration-line underline-offset-4" href="https://github.com/Nimdy/detect-FPS-hackers/blob/main/docs/culling.md">docs/culling.md</a>.</p>
  </section>

  <section id="log" class="mb-16" aria-labelledby="log-title">
    <h2 id="log-title" {H2}>What the server writes</h2>
    <p class="m-0 mt-4 max-w-3xl">One more field on the shot line fpsdet already reads. Leave it out, or send 0, and nothing happens. Its <a class="text-ink underline decoration-line underline-offset-4" href="../demo/board.html#tape-replay">planted case is in the desk</a>, and <a class="text-ink underline decoration-line underline-offset-4" href="scoring.html">Scoring</a> has the rule.</p>
    <pre class="m-0 mt-6 overflow-x-auto whitespace-pre-wrap rounded-r-lg border-l-[3px] border-signal bg-plot px-4 py-3 font-mono text-[13px] leading-normal"><code>{{"game_id": "your-game", "match_id": "m-1", "player_id": "p-7", "t_ms": 412300,
 "event_type": "shot", "weapon_id": "rifle", "hit": false,
 "private_track_ms": 140}}</code></pre>
  </section>

  <section id="next" class="mb-4" aria-labelledby="next-title">
    <h2 id="next-title" {H2}>Next: one frequency per client</h2>
    <p class="m-0 mt-4 max-w-3xl">Not built yet. Give every client its own decoys. Then a cheater's aim shows whose data it read, not only that it read hidden data. If a teammate who runs nothing starts following one client's private decoy, that client is calling out positions. fpsdet already flags a teammate who reacts faster than a voice can travel; a per-client decoy would name the source.</p>
    <p class="m-0 mt-6 max-w-3xl">Running a dedicated server? Add the decoy, send <code class="font-mono text-[0.92em]">private_track_ms</code>, and <a class="text-ink underline decoration-line underline-offset-4" href="https://github.com/Nimdy/detect-FPS-hackers/issues/new?template=real_data_result.yml">tell us what you find</a>.</p>
  </section>
</div>
</main>

<footer class="relative border-t border-line bg-bg/60">
  <div class="mx-auto flex w-full max-w-[1800px] flex-wrap items-end justify-between gap-6 px-10 py-8 max-md:px-4">
    <p class="m-0 basis-full font-mono text-sm"><a class="text-white no-underline" href="https://zerobandwidth.com">Zero<span class="text-signal">Bandwidth</span></a> <span class="text-muted">· the original signal · a ZeroBandwidth system</span></p>
    <p class="m-0 text-sm text-muted">A person still reviews. This does not end cheating.</p>
    <p class="m-0 flex flex-wrap gap-x-5 gap-y-2 text-sm">
      <a class="text-ink no-underline hover:underline" href="index.html">What this is</a>
      <a class="text-ink no-underline hover:underline" href="scoring.html">Scoring</a>
      <a class="text-ink no-underline hover:underline" href="games.html">Games</a>
      <a class="text-ink no-underline hover:underline" href="source.html">Source</a>
      <a class="text-ink no-underline hover:underline" href="../demo/board.html">Desk</a>
      <a class="text-ink no-underline hover:underline" href="decoys.html">Decoys</a>
      <a class="text-ink no-underline hover:underline" href="https://github.com/Nimdy/detect-FPS-hackers/blob/main/LICENSE.md">License</a>
    </p>
  </div>
</footer>
</body>
</html>
"""
(ROOT / "site" / "decoys.html").write_text(PAGE, encoding="utf-8")
print(f"wrote site/decoys.html: honest {len(honest)}, cheaters {len(cheats)}, best {best:.0%}, past {past}, medians {med(honest):.1%} / {med(cheats):.1%}")
