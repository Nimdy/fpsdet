"""Write a case as JSON and a single offline HTML page."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path

from .models import Case
from .persist import case_to_dict, write_json


def _rows(case: Case) -> str:
    body = []
    for metric in case.metrics:
        if metric.skipped:
            continue
        bound = "" if metric.bound is None else f"{metric.bound:.3f}"
        value = "" if metric.player_value is None else f"{metric.player_value:.3f}"
        ceiling = "" if metric.ceiling_extreme is None else f"{metric.ceiling_extreme:.3f}"
        flag = "yes" if metric.beyond_human or metric.beyond_band else ""
        body.append(
            "<tr>"
            f"<td>{html.escape(metric.name)}</td>"
            f"<td>{html.escape(value)}</td>"
            f"<td>{html.escape(bound)}</td>"
            f"<td>{html.escape(metric.ceiling_band or '')}</td>"
            f"<td>{html.escape(ceiling)}</td>"
            f"<td>{html.escape(flag)}</td>"
            "</tr>"
        )
    if not body:
        return "<p>No scored combat metrics.</p>"
    return (
        "<table><thead><tr><th>Metric</th><th>Player</th><th>Bound</th>"
        "<th>Ceiling</th><th>Human line</th><th>Flag</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _list(items: list[str]) -> str:
    if not items:
        return "<p>None.</p>"
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"


def render_html(case: Case) -> str:
    speed = case.speed
    speed_text = "No movement samples."
    if speed is not None:
        speed_text = (
            f"Eligible {speed.eligible}, over cap {speed.violations}, "
            f"longest run {speed.longest_run}, sustained {speed.sustained}. "
            f"Excluded innocence {speed.excluded_innocent}, unknown cause {speed.excluded_unknown}, "
            f"airborne or missing ground flag {speed.excluded_airborne}, missing cap {speed.missing_cap}. "
            f"Cap source {speed.cap_source}. {speed.detail}"
        )
    brief = f"<h2>AI brief</h2><p>{html.escape(case.ai_brief)}</p>" if case.ai_brief else ""
    party = f"<p>{html.escape(case.party_note)}</p>" if case.party_note else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(case.player_id)} {html.escape(case.decision)}</title>
<style>
body {{ font-family: Georgia, serif; margin: 2rem; max-width: 52rem; color: #1c1917; }}
h1 {{ font-size: 1.6rem; }}
.decision {{ display: inline-block; padding: 0.15rem 0.5rem; border: 1px solid #44403c; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border-bottom: 1px solid #d6d3d1; text-align: left; padding: 0.35rem; font-family: ui-monospace, monospace; font-size: 0.9rem; }}
.limits {{ color: #44403c; }}
</style>
</head>
<body>
<h1><span class="decision">{html.escape(case.decision)}</span> {html.escape(case.player_id)}</h1>
<p>Recommended action: {html.escape(case.recommended_action)}. Automated action: none.
Reports: {case.reports}. Rank band: {html.escape(case.skill_band)}.</p>
{party}
<h2>Findings</h2>
{_list(case.reasons)}
<h2>Context</h2>
{_list(case.observations)}
<h2>Movement</h2>
<p>{html.escape(speed_text)}</p>
<h2>Combat</h2>
{_rows(case)}
<h2>Builds still training</h2>
{_list(case.untrained)}
<h2>Matches</h2>
<p>{html.escape(", ".join(case.match_ids) or "none")}</p>
{brief}
<p class="limits">{html.escape(case.limits)}</p>
</body>
</html>
"""


def safe_name(player_id: str) -> str:
    """A file name no other player id can share, on any filesystem.

    ``p 1`` and ``p_1`` clean to the same text, and ``Bob`` and ``bob`` are one
    file on Windows and macOS. Those ids get a short hash of the real id.
    """
    cleaned = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in player_id)
    name = (cleaned or "player")[:80]
    if name != player_id or name != name.lower():
        digest = hashlib.sha256(player_id.encode("utf-8")).hexdigest()[:10]
        name = f"{name[:69]}-{digest}"
    return name


def write_case(directory: str | Path, case: Case) -> None:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_name(case.player_id)
    write_json(folder / f"{name}.json", case_to_dict(case))
    (folder / f"{name}.html").write_text(render_html(case), encoding="utf-8")
