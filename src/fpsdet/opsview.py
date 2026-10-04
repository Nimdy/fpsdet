"""The operations view as HTML. One payload from ``ops.ops_payload``, no network, no libraries.

The review desk embeds it as its first tab. ``fpsdet dashboard`` writes it as a
page of its own. Charts are inline SVG drawn at the width of their card, every
chart has a table twin, and filters in one row scope every panel above the
server-health section.
"""

from __future__ import annotations

import html
import json

# Decision colors are status colors: each one ships with a shape and a word, never alone.
# Mark steps validated on the panel surface (#0a102c): CVD ΔE 8.2, contrast ≥ 3:1.
OPS_CSS = r"""
#ops {
  --ops-review: #e5455d;
  --ops-watch: #c98c22;
  --ops-clean: #1ba596;
  --ops-held: #6574ec;
  --ops-mute: #3b4470;
  --ops-grid: #18204a;
  --ops-axis: #2a3466;
  --ops-seq-1: #13265a;
  --ops-seq-2: #1c3f82;
  --ops-seq-3: #2a63b8;
  --ops-seq-4: #3987e5;
  --ops-seq-5: #86b6ef;
  display: block;
  padding: 0 1.4rem 3rem;
  color: var(--ink);
}
#ops[hidden] { display: none; }
#ops * { box-sizing: border-box; }
#ops .mono { font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); }
#ops .ops-head { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.6rem 2rem; margin: 1.1rem 0 0.4rem; }
#ops .ops-kicker { margin: 0; color: rgb(56 200 255 / 0.8); font: 500 0.6875rem/1.4 var(--mono, ui-monospace, monospace); letter-spacing: 0.22em; text-transform: uppercase; }
#ops h1.ops-title { margin: 0.35rem 0 0; font: 650 clamp(1.6rem, 2.6vw, 2.3rem)/1.08 var(--sans, system-ui, sans-serif); letter-spacing: -0.03em; color: #fff; max-width: none; text-shadow: none; animation: none; }
#ops .ops-lede { margin: 0.45rem 0 0; max-width: 62rem; color: var(--muted); font-size: 0.95rem; line-height: 1.5; }
#ops .ops-notes { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.7rem 0 0; padding: 0; list-style: none; }
#ops .ops-notes li { border: 1px solid var(--line); border-radius: 999px; padding: 0.15rem 0.65rem; font: 12px/1.6 var(--mono, ui-monospace, monospace); color: var(--muted); }
#ops .ops-notes li b { color: var(--ink); font-weight: 500; }
#ops .synthetic { border-color: rgb(201 140 34 / 0.55); color: #e9c27a; }

#ops .ops-filters { position: sticky; top: 0; z-index: 5; display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.7rem; margin: 1rem -1.4rem 0; padding: 0.65rem 1.4rem; background: rgb(4 7 24 / 0.92); backdrop-filter: blur(6px); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
#ops .ops-filters label { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--muted); }
#ops .ops-filters select, #ops .ops-filters input[type=search] { background: var(--panel); color: var(--ink); border: 1px solid var(--line-bright); border-radius: 6px; padding: 0.3rem 0.45rem; font: inherit; font-size: 0.85rem; }
#ops .ops-filters input[type=search] { width: 10rem; }
#ops .ops-filters input[type=checkbox] { accent-color: var(--signal); }
#ops .ops-filters button { background: transparent; color: var(--signal); border: 1px solid var(--line-bright); border-radius: 6px; padding: 0.3rem 0.6rem; font: inherit; font-size: 0.82rem; cursor: pointer; }
#ops .ops-showing { margin-left: auto; font: 12px/1.4 var(--mono, ui-monospace, monospace); color: var(--muted); }

#ops .ops-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; margin-top: 14px; }
#ops .span-12 { grid-column: span 12; } #ops .span-8 { grid-column: span 8; } #ops .span-7 { grid-column: span 7; }
#ops .span-6 { grid-column: span 6; } #ops .span-5 { grid-column: span 5; } #ops .span-4 { grid-column: span 4; }
@media (max-width: 1100px) {
  #ops .span-8, #ops .span-7, #ops .span-6, #ops .span-5, #ops .span-4 { grid-column: span 12; }
}
#ops .ops-split { display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 5fr); gap: 14px; margin-top: 14px; align-items: start; }
#ops .ops-split .col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
@media (max-width: 1100px) { #ops .ops-split { grid-template-columns: minmax(0, 1fr); } }
#ops .ops-card[hidden] { display: none; }
#ops .ops-card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px 16px; min-width: 0; }
#ops .card-head { display: flex; align-items: flex-start; gap: 0.6rem; margin-bottom: 0.5rem; }
#ops .card-head h2 { margin: 0; font: 600 0.98rem/1.3 var(--sans, system-ui, sans-serif); letter-spacing: -0.01em; color: #fff; }
#ops .card-head p { margin: 0.15rem 0 0; color: var(--muted); font-size: 0.8rem; line-height: 1.45; }
#ops .card-head .twin { margin-left: auto; flex: none; background: transparent; color: var(--muted); border: 1px solid var(--line); border-radius: 6px; padding: 0.15rem 0.5rem; font: 11px/1.5 var(--mono, ui-monospace, monospace); cursor: pointer; }
#ops .card-head .twin[aria-pressed=true] { color: var(--ink); border-color: var(--line-bright); }
#ops .ops-chart svg { display: block; width: 100%; height: auto; overflow: visible; }
#ops .ops-chart text { font: 11px var(--mono, ui-monospace, monospace); fill: var(--muted); }
#ops .ops-chart .ink { fill: var(--ink); }
#ops .ops-legend { display: flex; flex-wrap: wrap; gap: 0.3rem 0.9rem; margin: 0.4rem 0 0; padding: 0; list-style: none; font-size: 0.78rem; color: var(--muted); }
#ops .ops-legend li { display: inline-flex; align-items: center; gap: 0.35rem; }
#ops .ops-legend svg { width: 12px; height: 12px; }
#ops .caption { margin: 0.55rem 0 0; color: var(--muted); font-size: 0.78rem; line-height: 1.45; }

#ops .kpis { display: grid; grid-template-columns: 1.35fr repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 14px; }
@media (max-width: 1100px) { #ops .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } #ops .kpi.hero { grid-column: span 2; } }
#ops .kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; min-width: 0; }
#ops .kpi .label { margin: 0; color: var(--muted); font-size: 0.78rem; }
#ops .kpi .value { margin: 0.15rem 0 0; font: 650 1.9rem/1.1 var(--sans, system-ui, sans-serif); color: #fff; letter-spacing: -0.02em; }
#ops .kpi.hero .value { font-size: 3.1rem; }
#ops .kpi .sub { margin: 0.25rem 0 0; color: var(--muted); font-size: 0.78rem; line-height: 1.4; }
#ops .kpi .sub b { color: var(--ink); font-weight: 500; }
#ops .kpi .row { display: flex; align-items: flex-end; justify-content: space-between; gap: 0.8rem; }
#ops .kpi svg.spark { width: 46%; max-width: 220px; height: 46px; }
#ops .kpi a { color: var(--signal); text-decoration: none; }

#ops .pill { display: inline-flex; align-items: center; gap: 0.35rem; border-radius: 999px; padding: 0.05rem 0.55rem 0.05rem 0.45rem; font: 600 11px/1.7 var(--mono, ui-monospace, monospace); letter-spacing: 0.04em; text-transform: uppercase; border: 1px solid currentColor; white-space: nowrap; }
#ops .pill svg { width: 9px; height: 9px; }
#ops .pill.review { color: #ff7a8c; } #ops .pill.watch { color: #e7b45a; } #ops .pill.clean { color: #45d1bd; } #ops .pill.insufficient_data { color: #97a2ff; }
#ops .ops-chip { display: inline-block; border: 1px solid var(--line-bright); border-radius: 5px; padding: 0 0.4rem; margin: 0 0.25rem 0.2rem 0; font: 11px/1.6 var(--mono, ui-monospace, monospace); color: var(--ink); white-space: nowrap; }
#ops .ops-chip.fam-gear { border-left: 3px solid #a98bff; } #ops .ops-chip.fam-baseline { border-left: 3px solid #5598e7; }
#ops .ops-chip.fam-information { border-left: 3px solid #38c8ff; } #ops .ops-chip.fam-batch { border-left: 3px solid #8a93b8; }

#ops table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
#ops th { text-align: left; font: 500 11px/1.4 var(--mono, ui-monospace, monospace); color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--line-bright); white-space: nowrap; }
#ops th button { all: unset; cursor: pointer; }
#ops th button:focus-visible { outline: 2px solid var(--signal); }
#ops td { padding: 0.42rem 0.5rem; border-bottom: 1px solid var(--line); vertical-align: middle; }
#ops td.num, #ops th.num { text-align: right; font-variant-numeric: tabular-nums; }
#ops .queue-wrap { overflow-x: auto; }
#ops tbody tr.pick { cursor: pointer; }
#ops tbody tr.pick:hover { background: var(--panel-hover); }
#ops tbody tr.pick:focus-visible { outline: 2px solid var(--signal); outline-offset: -2px; }
#ops tbody tr[aria-selected=true] { background: #111a46; box-shadow: inset 3px 0 0 var(--signal); }
#ops td.why { max-width: 14rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
#ops td.player { font-family: var(--mono, ui-monospace, monospace); white-space: nowrap; color: #fff; }
#ops .nights { display: inline-flex; gap: 2px; }
#ops .nights i { width: 10px; height: 14px; border-radius: 2px; background: var(--ops-grid); }
#ops .nights i.R { background: var(--ops-review); } #ops .nights i.W { background: var(--ops-watch); }
#ops .nights i.C { background: #20484a; } #ops .nights i.H { background: #262d5c; }
#ops .more-rows { margin-top: 0.6rem; background: transparent; color: var(--signal); border: 1px solid var(--line-bright); border-radius: 6px; padding: 0.3rem 0.7rem; font: inherit; font-size: 0.82rem; cursor: pointer; }
@media (max-width: 760px) { #ops .hide-sm { display: none; } }

#ops .drawer { position: sticky; top: calc(var(--ops-filter-h, 60px) + 12px); align-self: start; z-index: 1; max-height: calc(100vh - var(--ops-filter-h, 60px) - 24px); overflow: auto; }
@media (max-width: 1100px) { #ops .drawer { position: static; max-height: none; } }
#ops .drawer h3 { margin: 0.2rem 0 0; font: 600 1.25rem/1.2 var(--mono, ui-monospace, monospace); color: #fff; word-break: break-all; }
#ops .drawer h4 { margin: 1rem 0 0.35rem; font: 500 11px/1.4 var(--mono, ui-monospace, monospace); letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
#ops .drawer ul.findings { margin: 0; padding: 0 0 0 1rem; font-size: 0.86rem; line-height: 1.45; }
#ops .drawer ul.findings li { margin: 0 0 0.35rem; }
#ops .drawer ul.context { margin: 0; padding: 0 0 0 1rem; font-size: 0.8rem; color: var(--muted); line-height: 1.45; }
#ops .drawer .meta { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.8rem; }
#ops .drawer .meta b { color: var(--ink); font-weight: 500; }
#ops .drawer .truth { margin: 0.6rem 0 0; padding: 0.45rem 0.6rem; border: 1px dashed rgb(201 140 34 / 0.6); border-radius: 6px; font-size: 0.8rem; color: #e9c27a; }
#ops .drawer a { color: var(--signal); }
#ops .drawer details { margin-top: 0.9rem; }
#ops .drawer summary { cursor: pointer; color: var(--muted); font-size: 0.8rem; }
#ops .drawer pre { margin: 0.4rem 0 0; max-height: 18rem; overflow: auto; padding: 0.6rem; background: var(--elevated); border: 1px solid var(--line); border-radius: 6px; font: 11px/1.45 var(--mono, ui-monospace, monospace); color: var(--light); white-space: pre-wrap; word-break: break-word; }
#ops .bullet { margin: 0.35rem 0 0.6rem; }
#ops .bullet p { margin: 0 0 0.15rem; font-size: 0.8rem; color: var(--muted); }
#ops .bullet p b { color: var(--ink); font-weight: 500; }

#ops .section-title { grid-column: span 12; margin: 1.6rem 0 0; display: flex; align-items: baseline; gap: 0.8rem; flex-wrap: wrap; }
#ops .section-title h2 { margin: 0; font: 600 1.15rem/1.2 var(--sans, system-ui, sans-serif); color: #fff; }
#ops .section-title p { margin: 0; color: var(--muted); font-size: 0.82rem; }
#ops .cov-row { display: grid; grid-template-columns: minmax(0, 15rem) minmax(0, 1fr) 6.5rem 3.5rem; gap: 0.6rem; align-items: center; padding: 0.3rem 0; border-bottom: 1px solid var(--line); font-size: 0.8rem; }
#ops .cov-row .f { font-family: var(--mono, ui-monospace, monospace); color: var(--ink); overflow: hidden; text-overflow: ellipsis; }
#ops .cov-row .n { color: var(--muted); }
#ops .cov-row .d { display: flex; gap: 2px; }
#ops .cov-row .d i { flex: 1; height: 14px; border-radius: 2px; }
#ops .cov-row .p { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }
#ops .cov-row.off .p { color: var(--muted); }
@media (max-width: 760px) { #ops .cov-row { grid-template-columns: minmax(0, 1fr) 5.5rem 3rem; } #ops .cov-row .n { display: none; } }
#ops .heat { display: grid; gap: 3px; font-size: 0.78rem; }
#ops .heat .h { color: var(--muted); font: 11px/1.4 var(--mono, ui-monospace, monospace); padding: 0.2rem 0.3rem; }
#ops .heat .c { border-radius: 4px; padding: 0.45rem 0.4rem; text-align: center; font-variant-numeric: tabular-nums; }
#ops .heat .c small { display: block; font-size: 10px; opacity: 0.85; }
#ops .tip { position: fixed; z-index: 50; pointer-events: none; background: #0e1538; border: 1px solid var(--line-bright); border-radius: 8px; padding: 0.45rem 0.6rem; font-size: 0.8rem; box-shadow: 0 6px 24px rgb(0 0 0 / 0.45); max-width: 18rem; }
#ops .tip[hidden] { display: none; }
#ops .tip .t { color: var(--muted); font-size: 0.74rem; margin-bottom: 0.2rem; }
#ops .tip .r { display: flex; align-items: center; gap: 0.4rem; }
#ops .tip .r b { color: #fff; font-weight: 600; min-width: 2.5rem; }
#ops .tip .k { width: 12px; height: 2px; border-radius: 1px; }
#ops .empty { color: var(--muted); font-size: 0.85rem; padding: 1rem 0; }
"""

OPS_HTML = r"""
<section id="ops" aria-label="Operations">
  <div class="ops-head">
    <div>
      <p class="ops-kicker" id="ops-kicker"></p>
      <h1 class="ops-title" id="ops-title"></h1>
      <p class="ops-lede" id="ops-lede"></p>
      <ul class="ops-notes" id="ops-notes"></ul>
    </div>
  </div>
  <form class="ops-filters" id="ops-filters" aria-label="Filters" onsubmit="return false"></form>
  <div class="kpis" id="ops-kpis"></div>
  <div class="ops-split">
    <div class="col"><article class="ops-card" id="card-nightly"></article><article class="ops-card" id="card-scatter"></article></div>
    <div class="col"><article class="ops-card" id="card-checks"></article><article class="ops-card" id="card-side"></article></div>
  </div>
  <div class="ops-grid">
    <article class="ops-card span-12" id="card-truth" hidden></article>
  </div>
  <!-- Its own grid: the sticky case panel stops where the queue ends. -->
  <div class="ops-grid ops-queue-row">
    <article class="ops-card span-8" id="card-queue"></article>
    <aside class="ops-card span-4 drawer" id="ops-drawer" aria-live="polite"></aside>
  </div>
  <div class="ops-grid">
    <div class="section-title"><h2>Server health</h2><p>The whole server for the week. Not affected by the filters above.</p></div>
    <article class="ops-card span-7" id="card-coverage"></article>
    <article class="ops-card span-5" id="card-cohort"></article>
  </div>
  <div class="tip" id="ops-tip" hidden></div>
</section>
"""

OPS_JS = r"""
(function () {
const OPS = JSON.parse(document.getElementById("ops-payload").textContent);
const root = document.getElementById("ops");
if (!root || !OPS) return;
const DEC = {
  review: {label: "Review", color: "var(--ops-review)", shape: "tri"},
  watch: {label: "Watch", color: "var(--ops-watch)", shape: "square"},
  clean: {label: "Clean", color: "var(--ops-clean)", shape: "dot"},
  insufficient_data: {label: "Held", color: "var(--ops-held)", shape: "ring"}
};
const ORDER = ["review", "watch", "clean", "insufficient_data"];
const SEV = {review: 3, watch: 2, clean: 1, insufficient_data: 0};
const BAND_ORDER = ["developing", "average", "advanced", "elite", "unrated"];
const FAMILY = {gear: "Gear rules", baseline: "Human baseline", information: "Information", batch: "Across players"};
const rows = OPS.rows;
// Labels beside the decisions: planted by the synthetic week, or a real dataset's own. fpsdet never reads them.
const LABELLED = OPS.truth_kind === "labelled";
const HAS_TRUTH = !!OPS.synthetic || LABELLED;
const isHonest = t => t === "honest" || (OPS.honest_labels || []).includes(t);
// A dataset may also have labels that are neither, such as a ban for something other than cheating.
const isCheat = t => !!t && (OPS.cheat_labels ? OPS.cheat_labels.includes(t) : !isHonest(t));
const TRUTH_WORD = LABELLED ? "Labelled" : "Planted as";
const TRUTH_VALUES = [...new Set(rows.map(r => r.truth).filter(Boolean))].sort((a, b) => isHonest(a) - isHonest(b) || a.localeCompare(b));
// A case's checks, most decisive first: the gear and information findings, then batch, then the tails.
const CHECK_ORDER = Object.keys(OPS.checks);
const LAST = ["account_jump", "rank_tail", "supporting"];
const rank = id => (LAST.includes(id) ? 100 + LAST.indexOf(id) : CHECK_ORDER.indexOf(id));
rows.forEach(r => r.checks.sort((a, b) => rank(a) - rank(b)));
const days = OPS.days || [];
const state = {night: "", band: "", check: "", decision: "", truth: "", reported: false, q: "", sort: "queue", dir: -1, limit: 25, selected: null};
const tables = {};
const tip = document.getElementById("ops-tip");
const nf = new Intl.NumberFormat("en-US");

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "on") { for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn); continue; }
    node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) if (kid != null) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
}
function sv(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) if (value != null) node.setAttribute(key, String(value));
  if (text != null) node.textContent = text;
  return node;
}
const pct = (value, digits) => value == null ? "–" : (value * 100).toFixed(digits == null ? 0 : digits) + "%";
function niceMax(value) {
  if (value <= 0) return 1;
  const step = Math.pow(10, Math.floor(Math.log10(value)));
  for (const mult of [1, 2, 2.5, 5, 10]) if (value <= step * mult) return step * mult;
  return step * 10;
}
function countTicks(max) {
  const step = Math.max(1, niceMax(Math.ceil(max / 4)));
  const top = Math.max(step, Math.ceil(max / step) * step);
  const out = [];
  for (let t = 0; t <= top; t += step) out.push(t);
  return out;
}
function ticks(max, count) {
  const out = [];
  const step = max / count;
  for (let i = 0; i <= count; i++) out.push(+(step * i).toFixed(6));
  return out;
}
function marker(shape, x, y, size, color) {
  const ring = "var(--panel)";
  if (shape === "tri") {
    const h = size * 1.15;
    return sv("path", {d: `M${x} ${y - h} L${x + size} ${y + h * 0.75} L${x - size} ${y + h * 0.75} Z`, fill: color, stroke: ring, "stroke-width": 2, "paint-order": "stroke"});
  }
  if (shape === "square") return sv("rect", {x: x - size * 0.85, y: y - size * 0.85, width: size * 1.7, height: size * 1.7, rx: 1.5, fill: color, stroke: ring, "stroke-width": 2, "paint-order": "stroke"});
  if (shape === "ring") return sv("circle", {cx: x, cy: y, r: size * 0.8, fill: "none", stroke: color, "stroke-width": 1.5});
  return sv("circle", {cx: x, cy: y, r: size * 0.8, fill: color, stroke: ring, "stroke-width": 2, "paint-order": "stroke"});
}
function swatch(shape, color) {
  const svg = sv("svg", {viewBox: "0 0 12 12", "aria-hidden": "true"});
  svg.append(shape === "rect" ? sv("rect", {x: 1, y: 1, width: 10, height: 10, rx: 2, fill: color}) : marker(shape, 6, 6.5, 4, color));
  return svg;
}
function pill(decision) {
  const info = DEC[decision];
  return el("span", {class: "pill " + decision}, swatch(info.shape, "currentColor"), info.label);
}
function showTip(event, title, lines) {
  tip.replaceChildren(el("div", {class: "t"}, title));
  for (const line of lines) {
    const row = el("div", {class: "r"});
    if (line.color) row.append(el("span", {class: "k", style: "background:" + line.color}));
    row.append(el("b", null, line.value), el("span", null, line.label));
    tip.append(row);
  }
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  const x = Math.min(window.innerWidth - box.width - 8, event.clientX + 14);
  const y = event.clientY + 14 + box.height > window.innerHeight ? event.clientY - box.height - 10 : event.clientY + 14;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}
function hideTip() { tip.hidden = true; }
function cardHead(card, title, note, twin) {
  const head = el("div", {class: "card-head"});
  const text = el("div", null, el("h2", null, title));
  if (note) text.append(el("p", null, note));
  head.append(text);
  if (twin) {
    const pressed = !!tables[twin];
    head.append(el("button", {type: "button", class: "twin", "aria-pressed": String(pressed), on: {click: () => { tables[twin] = !tables[twin]; render(); }}}, pressed ? "Chart" : "Table"));
  }
  card.replaceChildren(head);
  return card;
}
function dataTable(head, body) {
  const table = el("table");
  table.append(el("thead", null, el("tr", null, head.map((h, i) => el("th", {class: i ? "num" : null}, h)))));
  table.append(el("tbody", null, body.map(r => el("tr", null, r.map((c, i) => el("td", {class: i ? "num" : null}, c))))));
  return table;
}

// ---- filters -------------------------------------------------------------
function filtered() {
  const q = state.q.trim().toLowerCase();
  const night = state.night === "" ? -1 : +state.night;
  return rows.filter(row => {
    if (state.band && row.band !== state.band) return false;
    if (state.check && !row.checks.includes(state.check)) return false;
    if (state.truth && row.truth !== state.truth) return false;
    if (state.reported && !row.reports) return false;
    if (q && !row.id.toLowerCase().includes(q)) return false;
    if (night >= 0 && !["R", "W"].includes(row.nights[night])) return false;
    if (state.decision === "open" && !["review", "watch"].includes(row.decision)) return false;
    if (state.decision && state.decision !== "open" && row.decision !== state.decision) return false;
    return true;
  });
}
function buildFilters() {
  const form = document.getElementById("ops-filters");
  const select = (key, label, options) => {
    const node = el("select", {"aria-label": label, on: {change: e => { state[key] = e.target.value; state.limit = 25; render(); }}});
    for (const [value, text, group] of options) {
      if (group) { const og = el("optgroup", {label: group}); for (const [v, t] of text) og.append(el("option", {value: v}, t)); node.append(og); continue; }
      node.append(el("option", {value}, text));
    }
    node.value = state[key];
    return el("label", null, label, node);
  };
  const bands = BAND_ORDER.filter(b => rows.some(r => r.band === b));
  const present = new Set(rows.flatMap(r => r.checks));
  const groups = Object.entries(FAMILY).map(([fam, title]) => ["", Object.entries(OPS.checks).filter(([id, c]) => c.family === fam && present.has(id)).map(([id, c]) => [id, c.label]), title]).filter(g => g[1].length);
  form.replaceChildren(...[
    days.length ? select("night", "Flagged on", [["", "Any night"], ...days.map((d, i) => [String(i), d.label])]) : null,
    select("decision", "Decision", [["", "All"], ["open", "Open (review + watch)"], ...ORDER.map(d => [d, DEC[d].label])]),
    select("band", "Rank", [["", "All"], ...bands.map(b => [b, b])]),
    select("check", "Check", [["", "Any"], ...groups]),
    HAS_TRUTH ? select("truth", TRUTH_WORD, [["", "Any"], ...TRUTH_VALUES.map(t => [t, t])]) : null,
    el("label", null, el("input", {type: "checkbox", checked: state.reported || null, on: {change: e => { state.reported = e.target.checked; render(); }}}), "Reported only"),
    el("label", null, el("input", {type: "search", placeholder: "Player id", value: state.q, "aria-label": "Search player id", on: {input: e => { state.q = e.target.value; render(); }}})),
    el("button", {type: "button", on: {click: () => { Object.assign(state, {night: "", band: "", check: "", decision: "", truth: "", reported: false, q: "", limit: 25}); buildFilters(); render(); }}}, "Reset"),
    el("span", {class: "ops-showing", id: "ops-showing"})
  ].filter(Boolean));
}

// ---- header --------------------------------------------------------------
function header() {
  const first = days[0], last = days[days.length - 1];
  document.getElementById("ops-kicker").textContent = ["Operations", OPS.game, OPS.synthetic ? "synthetic week" : LABELLED ? "real matches, labelled" : "scored batch"].join(" · ");
  document.getElementById("ops-title").textContent = first ? `Queue for ${first.label} – ${last.label}` : "This batch's queue";
  const t = OPS.totals;
  document.getElementById("ops-lede").textContent = OPS.synthetic
    ? `${nf.format(t.players)} players, ${nf.format(t.events)} server events across ${nf.format(t.matches)} matches, scored every night and once for the week against last week's frozen baseline. Some players were planted as cheats; the rest play honestly. Every number here is invented. The shape is what a real week looks like.`
    : LABELLED
    ? `${nf.format(t.players)} players in ${nf.format(t.matches)} real matches, ${nf.format(t.events)} server events, scored against a baseline built from other players. ${OPS.truth_source || "The dataset"} says who cheated. fpsdet never saw those labels; they are here so you can check its decisions.`
    : `${nf.format(t.players)} players, ${nf.format(t.events)} server events. Read from the case files that fpsdet score wrote.`;
  const notes = document.getElementById("ops-notes");
  notes.replaceChildren();
  if (OPS.synthetic) notes.append(el("li", {class: "synthetic"}, "Synthetic data"));
  for (const text of Object.values(OPS.notes || {})) notes.append(el("li", null, text));
  for (const link of OPS.links || []) notes.append(el("li", null, el("a", {href: link.href, style: "color:var(--signal)"}, link.text)));
  const status = (OPS.integrity || {}).status || "unchecked";
  notes.append(el("li", null, "Baseline integrity: ", el("b", null, status)));
  notes.append(el("li", null, "Automated action: ", el("b", null, "none")));
}

// ---- KPI tiles -------------------------------------------------------------
function nightCounts(list, code) { return days.map((_, i) => list.filter(r => r.nights[i] === code).length); }
function spark(values, color) {
  const svg = sv("svg", {class: "spark", viewBox: "0 0 120 46", preserveAspectRatio: "none", role: "img", "aria-label": "Nightly reviews: " + values.join(", ")});
  if (!values.length) return svg;
  const max = Math.max(1, ...values);
  const x = i => 4 + i * (112 / Math.max(1, values.length - 1));
  const y = v => 40 - (v / max) * 34;
  svg.append(sv("line", {x1: 0, x2: 120, y1: 40.5, y2: 40.5, stroke: "var(--ops-axis)", "stroke-width": 1, "vector-effect": "non-scaling-stroke"}));
  svg.append(sv("polyline", {points: values.map((v, i) => `${x(i)},${y(v)}`).join(" "), fill: "none", stroke: "var(--ops-mute)", "stroke-width": 2, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round"}));
  const lastI = values.length - 1;
  svg.append(sv("circle", {cx: x(lastI), cy: y(values[lastI]), r: 3.5, fill: color, stroke: "var(--panel)", "stroke-width": 2}));
  return svg;
}
function kpis(list) {
  const box = document.getElementById("ops-kpis");
  const count = d => list.filter(r => r.decision === d).length;
  const reviews = count("review");
  const nightly = nightCounts(list, "R");
  const delta = nightly.length > 1 ? nightly[nightly.length - 1] - nightly[nightly.length - 2] : null;
  const reported = list.filter(r => r.reports);
  const reportedReview = reported.filter(r => r.decision === "review").length;
  const tiles = [];
  const hero = el("div", {class: "kpi hero"},
    el("p", {class: "label"}, "Open reviews"),
    el("div", {class: "row"}, el("p", {class: "value"}, nf.format(reviews)), days.length ? spark(nightly, "var(--ops-review)") : null),
    el("p", {class: "sub"}, days.length ? [el("b", null, String(nightly[nightly.length - 1])), ` in last night's batch${delta == null ? "" : ` (${delta >= 0 ? "+" : ""}${delta} vs the night before)`}. A person opens each one. `, el("a", {href: "#card-queue"}, "Go to the queue")] : "A person opens each one."));
  tiles.push(hero);
  tiles.push(el("div", {class: "kpi"}, el("p", {class: "label"}, "Watching"), el("p", {class: "value"}, nf.format(count("watch"))), el("p", {class: "sub"}, "Monitor. Not a case until a review fires.")));
  tiles.push(el("div", {class: "kpi"}, el("p", {class: "label"}, "Clean"), el("p", {class: "value"}, nf.format(count("clean"))), el("p", {class: "sub"}, el("b", null, nf.format(count("insufficient_data"))), " held: too little play, or no baseline yet.")));
  tiles.push(el("div", {class: "kpi"}, el("p", {class: "label"}, "Reported players"), el("p", {class: "value"}, nf.format(reported.length)), el("p", {class: "sub"}, el("b", null, `${reportedReview}`), ` of them in review (${reported.length ? Math.round(100 * reportedReview / reported.length) : 0}%). Reports set the order, never the decision.`)));
  if (HAS_TRUTH) {
    const cheats = list.filter(r => isCheat(r.truth));
    const caught = cheats.filter(r => r.decision === "review").length;
    const honest = list.filter(r => isHonest(r.truth));
    const framed = honest.filter(r => r.decision === "review").length;
    tiles.push(el("div", {class: "kpi"}, el("p", {class: "label"}, LABELLED ? "Answer check · dataset labels" : "Answer check · synthetic only"), el("p", {class: "value"}, `${caught}/${nf.format(cheats.length)}`), el("p", {class: "sub"}, LABELLED ? "labelled cheaters in review. " : "planted cheats in review. ", el("b", null, String(framed)), ` of ${nf.format(honest.length)} ${LABELLED ? "players labelled clean" : "honest players"} in review.`)));
  } else {
    tiles.push(el("div", {class: "kpi"}, el("p", {class: "label"}, "Server events"), el("p", {class: "value"}, nf.format(OPS.totals.events)), el("p", {class: "sub"}, `${nf.format(OPS.totals.shots)} shots, ${nf.format(OPS.totals.movement)} movement samples.`)));
  }
  box.replaceChildren(...tiles);
}

// ---- nightly queue (stacked columns) ---------------------------------------
function nightly(list) {
  const holder = document.getElementById("card-nightly");
  // One run has no nights to compare. fpsdet dashboard with several score --out folders fills this.
  holder.hidden = !days.length;
  if (!days.length) return;
  const card = cardHead(holder, "Queue by night", "Each night's batch on its own. A review opened on any night stays in the queue until a person closes it.", "nightly");
  const R = nightCounts(list, "R"), W = nightCounts(list, "W");
  const opened = days.map((_, i) => list.filter(r => r.first === i).length);
  if (tables.nightly) {
    card.append(dataTable(["Night", "Review", "Watch", "New reviews"], days.map((d, i) => [d.label, R[i], W[i], opened[i]])));
    return;
  }
  const wrap = el("div", {class: "ops-chart"});
  card.append(wrap);
  const width = Math.max(320, wrap.clientWidth || 600), height = 230;
  const pad = {l: 34, r: 10, t: 16, b: 42};
  const yTicks = countTicks(Math.max(1, ...R.map((v, i) => v + W[i])));
  const max = yTicks[yTicks.length - 1];
  const svg = sv("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Reviews and watches per nightly batch"});
  const plotH = height - pad.t - pad.b, plotW = width - pad.l - pad.r;
  const y = v => pad.t + plotH - (v / max) * plotH;
  for (const t of yTicks) {
    svg.append(sv("line", {x1: pad.l, x2: width - pad.r, y1: y(t) + 0.5, y2: y(t) + 0.5, stroke: t ? "var(--ops-grid)" : "var(--ops-axis)", "stroke-width": 1}));
    svg.append(sv("text", {x: pad.l - 6, y: y(t) + 4, "text-anchor": "end"}, String(t)));
  }
  const slot = plotW / days.length, bar = Math.min(24, slot * 0.5);
  const narrow = slot < 64;
  const selected = state.night === "" ? -1 : +state.night;
  days.forEach((d, i) => {
    const cx = pad.l + slot * i + slot / 2;
    const dim = selected >= 0 && selected !== i ? 0.35 : 1;
    let top = y(0);
    const segs = [["review", R[i]], ["watch", W[i]]].filter(s => s[1] > 0);
    segs.forEach(([kind, value], k) => {
      const h = (value / max) * plotH;
      const gap = k > 0 ? 2 : 0;
      const isTop = k === segs.length - 1;
      const yTop = top - h + gap;
      const hh = Math.max(1, h - gap);
      const r = isTop ? Math.min(4, hh / 2) : 0;
      svg.append(sv("path", {d: `M${cx - bar / 2} ${yTop + hh} V${yTop + r} Q${cx - bar / 2} ${yTop} ${cx - bar / 2 + r} ${yTop} H${cx + bar / 2 - r} Q${cx + bar / 2} ${yTop} ${cx + bar / 2} ${yTop + r} V${yTop + hh} Z`, fill: DEC[kind].color, opacity: dim}));
      top -= h;
    });
    svg.append(sv("text", {x: cx, y: height - pad.b + 16, "text-anchor": "middle", class: selected === i ? "ink" : null}, narrow ? d.label.split(" ").pop() : d.label));
    if (opened[i]) svg.append(sv("text", {x: cx, y: height - pad.b + 30, "text-anchor": "middle"}, narrow ? `+${opened[i]}` : `+${opened[i]} new`));
    if (i === days.length - 1 && R[i] + W[i]) svg.append(sv("text", {x: cx, y: top - 6, "text-anchor": "middle", class: "ink"}, String(R[i] + W[i])));
    const hit = sv("rect", {x: pad.l + slot * i, y: pad.t, width: slot, height: plotH + 30, fill: "transparent", tabindex: 0, role: "button", "aria-label": `${d.label}: ${R[i]} review, ${W[i]} watch`});
    hit.style.cursor = "pointer";
    const tell = e => showTip(e, d.label, [{value: R[i], label: "review", color: "var(--ops-review)"}, {value: W[i], label: "watch", color: "var(--ops-watch)"}, {value: opened[i], label: "reviews opened this night"}]);
    hit.addEventListener("pointermove", tell);
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("focus", e => { const b = hit.getBoundingClientRect(); tell({clientX: b.left + b.width / 2, clientY: b.top}); });
    hit.addEventListener("blur", hideTip);
    const pick = () => { state.night = state.night === String(i) ? "" : String(i); buildFilters(); render(); };
    hit.addEventListener("click", pick);
    hit.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    svg.append(hit);
  });
  wrap.append(svg);
  card.append(el("ul", {class: "ops-legend"}, el("li", null, swatch("rect", "var(--ops-review)"), "Review"), el("li", null, swatch("rect", "var(--ops-watch)"), "Watch"), el("li", null, "Click a night to filter the queue to players flagged that night.")));
}

// ---- what fired (horizontal bars by family) ---------------------------------
function checks(list) {
  const card = cardHead(document.getElementById("card-checks"), "What fired", "Players in view with each check on their case. One player can have several.", "checks");
  const counts = Object.keys(OPS.checks).map(id => [id, list.filter(r => r.checks.includes(id)).length]);
  const live = counts.filter(c => c[1] > 0);
  const quiet = counts.filter(c => c[1] === 0).map(c => OPS.checks[c[0]].label.toLowerCase());
  if (tables.checks) {
    card.append(dataTable(["Check", "Players"], counts.map(([id, n]) => [OPS.checks[id].label, n])));
    return;
  }
  if (!live.length) { card.append(el("p", {class: "empty"}, "Nothing fired for the players in view.")); return; }
  const max = niceMax(Math.max(...live.map(c => c[1])));
  const list2 = el("div", {class: "ops-chart"});
  for (const [fam, title] of Object.entries(FAMILY)) {
    const items = live.filter(([id]) => OPS.checks[id].family === fam);
    if (!items.length) continue;
    list2.append(el("p", {class: "ops-kicker", style: "margin:0.6rem 0 0.25rem;font-size:10px"}, title));
    for (const [id, n] of items) {
      const active = state.check === id;
      const row = el("button", {type: "button", "aria-pressed": String(active), title: "Filter the queue to " + OPS.checks[id].label, style: "all:unset;box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);align-items:center;gap:0.6rem;width:100%;padding:3px 0;cursor:pointer;font-size:0.8rem;line-height:1.25", on: {click: () => { state.check = active ? "" : id; buildFilters(); render(); }}});
      const svg = sv("svg", {viewBox: "0 0 200 14", preserveAspectRatio: "none", style: "width:100%;height:14px;overflow:visible"});
      const w = Math.max(2, (n / max) * 170);
      svg.append(sv("rect", {x: 0, y: 1, width: w, height: 12, rx: 3, fill: active ? "var(--ops-seq-5)" : "var(--ops-seq-4)"}));
      const label = el("span", {style: "color:" + (active ? "#fff" : "var(--ink)")}, OPS.checks[id].label);
      const bars = el("span", {style: "display:flex;align-items:center;gap:0.4rem"}, svg, el("b", {style: "font-weight:500;font-variant-numeric:tabular-nums;min-width:2ch"}, String(n)));
      svg.style.width = "calc(100% - 3ch)";
      row.append(label, bars);
      list2.append(row);
    }
  }
  card.append(list2);
  if (quiet.length) card.append(el("p", {class: "caption"}, "Did not fire for anyone in view: " + quiet.join(", ") + "."));
}

// ---- human ceiling (scatter) ----------------------------------------------
function scatter(list) {
  const lines = OPS.lines || {};
  const key = lines.key || "rifle";
  const card = cardHead(document.getElementById("card-scatter"), `Human ceiling · ${key}`, "Each mark is a player's accuracy and headshot rate in this window. The lines are the best humans in the frozen baseline. A review needs a confident lower bound past them, so marks just over a line are usually still inside.", "scatter");
  const points = list.filter(r => r.point && r.decision !== "insufficient_data");
  if (tables.scatter) {
    const top = [...points].sort((a, b) => SEV[b.decision] - SEV[a.decision] || b.point.acc - a.point.acc).slice(0, 40);
    card.append(dataTable(["Player", "Decision", "Accuracy", "Headshots"], top.map(r => [r.id, DEC[r.decision].label, pct(r.point.acc, 1), pct(r.point.hs, 1)])));
    card.append(el("p", {class: "caption"}, `Top ${top.length} of ${points.length} by decision, then accuracy.`));
    return;
  }
  if (!points.length) { card.append(el("p", {class: "empty"}, "No player in view has both numbers on this weapon yet.")); return; }
  const wrap = el("div", {class: "ops-chart"});
  card.append(wrap);
  const width = Math.max(320, wrap.clientWidth || 600), height = 340;
  const pad = {l: 44, r: 16, t: 24, b: 36};
  const accLine = lines.accuracy && lines.accuracy.value, hsLine = lines.headshot_rate && lines.headshot_rate.value;
  const maxX = Math.min(1, Math.max(0.5, ...points.map(p => p.point.acc), accLine || 0) * 1.08);
  const maxY = Math.min(1, Math.max(0.6, ...points.map(p => p.point.hs), hsLine || 0) * 1.08);
  const X = v => pad.l + (v / maxX) * (width - pad.l - pad.r);
  const Y = v => height - pad.b - (v / maxY) * (height - pad.t - pad.b);
  const svg = sv("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Accuracy against headshot rate per player"});
  for (const t of ticks(maxX >= 0.8 ? 1 : maxX >= 0.6 ? 0.8 : 0.6, 4)) {
    if (t > maxX) continue;
    svg.append(sv("line", {x1: X(t) + 0.5, x2: X(t) + 0.5, y1: pad.t, y2: height - pad.b, stroke: t ? "var(--ops-grid)" : "var(--ops-axis)"}));
    svg.append(sv("text", {x: X(t), y: height - pad.b + 15, "text-anchor": "middle"}, pct(t)));
  }
  for (const t of ticks(maxY >= 0.8 ? 1 : maxY >= 0.6 ? 0.8 : 0.6, 4)) {
    if (t > maxY) continue;
    svg.append(sv("line", {x1: pad.l, x2: width - pad.r, y1: Y(t) + 0.5, y2: Y(t) + 0.5, stroke: t ? "var(--ops-grid)" : "var(--ops-axis)"}));
    svg.append(sv("text", {x: pad.l - 6, y: Y(t) + 4, "text-anchor": "end"}, pct(t)));
  }
  svg.append(sv("text", {x: width - pad.r, y: height - 4, "text-anchor": "end"}, "accuracy →"));
  svg.append(sv("text", {x: 0, y: 10}, "↑ headshot rate"));
  if (state.band && lines.accuracy_p95 && lines.accuracy_p95[state.band] != null) {
    const v = lines.accuracy_p95[state.band];
    svg.append(sv("line", {x1: X(v), x2: X(v), y1: pad.t, y2: height - pad.b, stroke: "var(--signal)", "stroke-opacity": 0.55}));
    svg.append(sv("text", {x: X(v) - 4, y: height - pad.b - 6, "text-anchor": "end"}, `${state.band} p95 ${pct(v)}`));
  }
  if (accLine != null) {
    svg.append(sv("line", {x1: X(accLine), x2: X(accLine), y1: pad.t, y2: height - pad.b, stroke: "var(--ink)", "stroke-opacity": 0.55}));
    const flip = X(accLine) > width * 0.7;
    svg.append(sv("text", {x: X(accLine) + (flip ? -5 : 5), y: height - pad.b - 8, "text-anchor": flip ? "end" : "start", class: "ink"}, `best human ${pct(accLine)}`));
  }
  if (hsLine != null) {
    svg.append(sv("line", {x1: pad.l, x2: width - pad.r, y1: Y(hsLine), y2: Y(hsLine), stroke: "var(--ink)", "stroke-opacity": 0.55}));
    const below = Y(hsLine) < pad.t + 16;
    svg.append(sv("text", {x: pad.l + 6, y: Y(hsLine) + (below ? 14 : -5), class: "ink"}, `best human ${pct(hsLine)}`));
  }
  const drawn = [];
  for (const kind of ["clean", "watch", "review"]) {
    for (const r of points.filter(p => p.decision === kind)) {
      const x = X(r.point.acc), y = Y(r.point.hs);
      const size = kind === "clean" ? 3 : 5;
      const color = kind === "clean" ? "var(--ops-mute)" : DEC[kind].color;
      const m = marker(DEC[kind].shape, x, y, size, color);
      if (state.selected === r.id) { m.setAttribute("stroke", "#fff"); m.setAttribute("stroke-width", 2); }
      svg.append(m);
      drawn.push({x, y, r});
    }
  }
  const hit = sv("rect", {x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b, fill: "transparent"});
  const nearest = e => {
    const box = svg.getBoundingClientRect();
    const sx = (e.clientX - box.left) * (width / box.width), sy = (e.clientY - box.top) * (height / box.height);
    let best = null, dist = 24 * (width / box.width);
    for (const d of drawn) { const dd = Math.hypot(d.x - sx, d.y - sy); if (dd < dist) { dist = dd; best = d; } }
    return best;
  };
  hit.addEventListener("pointermove", e => {
    const best = nearest(e);
    if (!best) { hideTip(); hit.style.cursor = "default"; return; }
    hit.style.cursor = "pointer";
    showTip(e, best.r.id, [{value: DEC[best.r.decision].label, label: best.r.band}, {value: pct(best.r.point.acc, 1), label: "accuracy"}, {value: pct(best.r.point.hs, 1), label: "headshot rate"}]);
  });
  hit.addEventListener("pointerleave", hideTip);
  hit.addEventListener("click", e => { const best = nearest(e); if (best) select(best.r.id, true); });
  svg.append(hit);
  wrap.append(svg);
  card.append(el("ul", {class: "ops-legend"}, ...["review", "watch", "clean"].map(k => el("li", null, swatch(DEC[k].shape, k === "clean" ? "var(--ops-mute)" : DEC[k].color), DEC[k].label)), el("li", null, "Pick a Rank filter to see that rank's 95th percentile.")));
}

// ---- reports, and the answer check -----------------------------------------
function side(list) {
  const card = cardHead(document.getElementById("card-side"), "Reports are a queue, not a verdict", "Decisions for players with and without reports. A report moves a player up the scan. It never adds to the score.", "side");
  const groups = [["Reported", list.filter(r => r.reports)], ["Not reported", list.filter(r => !r.reports)]];
  if (tables.side) {
    card.append(dataTable(["Group", ...ORDER.map(d => DEC[d].label)], groups.map(([name, g]) => [name, ...ORDER.map(d => g.filter(r => r.decision === d).length)])));
  } else {
    const wrap = el("div", {class: "ops-chart"});
    card.append(wrap);
    const width = Math.max(300, wrap.clientWidth || 420);
    const svg = sv("svg", {viewBox: `0 0 ${width} 96`, role: "img", "aria-label": "Decision mix for reported and unreported players"});
    groups.forEach(([name, g], gi) => {
      const y = 18 + gi * 44;
      svg.append(sv("text", {x: 0, y: y - 4, class: "ink"}, `${name} · ${g.length}`));
      const total = Math.max(1, g.length);
      let x = 0;
      const span = width - 64;
      const shares = ORDER.map(d => [d, g.filter(r => r.decision === d).length]).filter(s => s[1]);
      shares.forEach(([d, n], k) => {
        const w = (n / total) * span;
        const gap = k < shares.length - 1 ? 2 : 0;
        const seg = sv("rect", {x, y, width: Math.max(1, w - gap), height: 14, rx: 2, fill: DEC[d].color});
        seg.addEventListener("pointermove", e => showTip(e, name, [{value: n, label: DEC[d].label.toLowerCase(), color: DEC[d].color}, {value: pct(n / total), label: "of the group"}]));
        seg.addEventListener("pointerleave", hideTip);
        svg.append(seg);
        x += w;
      });
      const rev = g.filter(r => r.decision === "review").length;
      svg.append(sv("text", {x: width, y: y + 11, "text-anchor": "end", class: "ink"}, `${pct(rev / total)} rev`));
    });
    wrap.append(svg);
    card.append(el("ul", {class: "ops-legend"}, ...ORDER.map(d => el("li", null, swatch("rect", DEC[d].color), DEC[d].label))));
  }
}
function truth(list) {
  const holder = document.getElementById("card-truth");
  if (!HAS_TRUTH) { holder.hidden = true; return; }
  holder.hidden = false;
  const card = LABELLED
    ? cardHead(holder, "Answer check · dataset labels", "The dataset's own labels beside fpsdet's decisions. fpsdet never saw them. In production nobody hands you this column. Pick a label in the filters to browse those players.", null)
    : cardHead(holder, "Answer check · synthetic only", "Who was planted as what, and where they ended up. In production nobody hands you this column. It is here to show what the checks catch, and what still gets through.", null);
  const cheats = list.filter(r => isCheat(r.truth)).sort((a, b) => SEV[b.decision] - SEV[a.decision] || a.truth.localeCompare(b.truth));
  if (cheats.length > 48) {
    // Too many to list one by one: players per label and decision.
    const labels = TRUTH_VALUES.filter(t => list.some(r => r.truth === t));
    card.append(dataTable(["Label", ...ORDER.map(d => DEC[d].label), "Players"], labels.map(t => { const of = list.filter(r => r.truth === t); return [t, ...ORDER.map(d => nf.format(of.filter(r => r.decision === d).length)), nf.format(of.length)]; })));
    return;
  }
  const grid = el("div", {style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(13.5rem,1fr));gap:4px 18px"});
  for (const r of cheats) {
    const row = el("button", {type: "button", title: (OPS.truth_notes || {})[r.truth] || "", style: "all:unset;box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;gap:0.5rem;padding:3px 6px;border-radius:5px;cursor:pointer;font-size:0.8rem", on: {click: () => select(r.id, true)}});
    row.append(el("span", {class: "mono", style: "color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"}, r.truth), pill(r.decision));
    grid.append(row);
  }
  card.append(grid);
  const honest = list.filter(r => isHonest(r.truth));
  card.append(el("p", {class: "meta", style: "margin:0.6rem 0 0;font-size:0.8rem;color:var(--muted)"}, "Honest players: ", ...ORDER.map((d, i) => [el("b", {style: "color:" + (d === "review" ? "#fff" : "var(--ink)")}, nf.format(honest.filter(r => r.decision === d).length)), " " + DEC[d].label.toLowerCase() + (i < ORDER.length - 1 ? " · " : ".")])));
  const missed = list.filter(r => isCheat(r.truth) && r.decision !== "review");
  if (missed.length) card.append(el("p", {class: "caption"}, "Not in review: " + missed.map(r => `${r.truth} (${DEC[r.decision].label.toLowerCase()})`).join(", ") + ". Closet assists tuned under the ceiling, and leftover twins with too little data, are what still gets through."));
}

// ---- queue table -----------------------------------------------------------
function queue(list) {
  const card = cardHead(document.getElementById("card-queue"), "Queue", "Reviews first, then watches; the most reported first inside each. Click a row to open the case.", null);
  const sorted = [...list];
  const cmp = {
    queue: (a, b) => SEV[b.decision] - SEV[a.decision] || b.reports - a.reports || a.id.localeCompare(b.id),
    player: (a, b) => a.id.localeCompare(b.id),
    reports: (a, b) => a.reports - b.reports,
    band: (a, b) => BAND_ORDER.indexOf(a.band) - BAND_ORDER.indexOf(b.band)
  }[state.sort];
  sorted.sort((a, b) => (state.sort === "queue" ? 1 : state.dir) * cmp(a, b));
  const sortBtn = (key, label) => el("button", {type: "button", on: {click: () => { if (state.sort === key) state.dir = -state.dir; else { state.sort = key; state.dir = key === "player" ? 1 : -1; } render(); }}}, label + (state.sort === key && key !== "queue" ? (state.dir > 0 ? " ↑" : " ↓") : ""));
  const head = el("tr", null,
    el("th", null, sortBtn("queue", "Decision")),
    el("th", null, sortBtn("player", "Player"), " · ", sortBtn("band", "rank")),
    el("th", {class: "hide-sm"}, "Why"),
    el("th", {class: "hide-sm"}, "Checks"),
    el("th", {class: "num"}, sortBtn("reports", "Reports")),
    days.length ? el("th", {class: "hide-sm"}, "Nights") : null,
    HAS_TRUTH ? el("th", {class: "hide-sm"}, TRUTH_WORD) : null);
  const body = el("tbody");
  for (const r of sorted.slice(0, state.limit)) {
    const tr = el("tr", {class: "pick", tabindex: 0, "aria-selected": String(state.selected === r.id), on: {click: () => select(r.id), keydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(r.id); } }}});
    const chips = r.checks.slice(0, 1).map(c => el("span", {class: "ops-chip fam-" + (OPS.checks[c] || {}).family}, c));
    if (r.checks.length > 1) chips.push(el("span", {class: "ops-chip", title: r.checks.slice(1).join(", ")}, "+" + (r.checks.length - 1)));
    tr.append(...[
      el("td", null, pill(r.decision)),
      el("td", {class: "player"}, r.id, el("div", {style: "font:11px/1.3 var(--sans,system-ui,sans-serif);color:var(--muted)"}, r.band)),
      el("td", {class: "why hide-sm", title: r.why}, r.why || "–"),
      el("td", {class: "hide-sm", style: "white-space:nowrap"}, chips.length ? chips : el("span", {style: "color:var(--ops-axis)"}, "–")),
      el("td", {class: "num"}, r.reports ? String(r.reports) : "·"),
      days.length ? el("td", {class: "hide-sm"}, el("span", {class: "nights", "aria-label": r.nights.map((n, i) => days[i].label + " " + ({R: "review", W: "watch", C: "clean", H: "held"}[n] || "did not play")).join(", ")}, r.nights.map(n => el("i", {class: n || null})))) : null,
      HAS_TRUTH ? el("td", {class: "hide-sm mono", style: (isHonest(r.truth) ? "color:var(--muted)" : "color:#e9c27a") + (LABELLED ? ";min-width:9rem;white-space:normal" : ";white-space:nowrap")}, r.truth || "–") : null].filter(Boolean));
    body.append(tr);
  }
  const table = el("table", null, el("thead", null, head), body);
  card.append(el("div", {class: "queue-wrap"}, table));
  if (!sorted.length) card.append(el("p", {class: "empty"}, "No player matches the filters."));
  if (sorted.length > state.limit) card.append(el("button", {type: "button", class: "more-rows", on: {click: () => { state.limit = sorted.length; render(); }}}, `Show all ${nf.format(sorted.length)}`));
}

// ---- case drawer -----------------------------------------------------------
function metricLine(m) {
  const rate = ["accuracy", "headshot_rate", "geometry_rate"].includes(m.name);
  const fmt = v => v == null ? "–" : rate ? pct(v, 1) : m.name === "median_distance" ? v.toFixed(0) + " m" : (+v).toPrecision(3);
  const wrap = el("div", {class: "bullet"});
  wrap.append(el("p", null, el("b", null, (m.key ? m.key + " " : "") + m.name.replace(/_/g, " ")), ` ${fmt(m.value)}`, m.bound != null && m.bound !== m.value ? ` · bound ${fmt(m.bound)}` : "", m.rank != null ? ` · rank p95 ${fmt(m.rank)}` : "", m.human != null ? ` · every human ${fmt(m.human)}` : ""));
  const values = [m.value, m.bound, m.rank, m.human].filter(v => v != null);
  const max = Math.max(...values) * 1.15 || 1;
  const svg = sv("svg", {viewBox: "0 0 300 22", preserveAspectRatio: "none", style: "width:100%;height:22px", "aria-hidden": "true"});
  const X = v => 2 + (v / max) * 296;
  svg.append(sv("rect", {x: 2, y: 9, width: 296, height: 4, rx: 2, fill: "var(--ops-grid)"}));
  if (m.rank != null) svg.append(sv("rect", {x: X(m.rank) - 1, y: 4, width: 2, height: 14, fill: "var(--signal)", opacity: 0.8}));
  if (m.human != null) svg.append(sv("rect", {x: X(m.human) - 1, y: 2, width: 2, height: 18, fill: "var(--ink)"}));
  const color = m.past ? "var(--ops-review)" : m.tail ? "var(--ops-watch)" : "var(--ops-clean)";
  if (m.bound != null && m.bound !== m.value) svg.append(sv("rect", {x: Math.min(X(m.bound), X(m.value)), y: 9, width: Math.abs(X(m.value) - X(m.bound)), height: 4, fill: color, opacity: 0.55}));
  svg.append(sv("circle", {cx: X(m.value), cy: 11, r: 4.5, fill: color, stroke: "var(--panel)", "stroke-width": 2}));
  wrap.append(svg);
  return wrap;
}
function select(id, scroll) {
  state.selected = id;
  render();
  if (scroll) document.getElementById("ops-drawer").scrollIntoView({block: "nearest"});
}
function drawer(list) {
  const box = document.getElementById("ops-drawer");
  let r = rows.find(x => x.id === state.selected);
  if (!r) r = list.find(x => x.decision === "review") || list[0];
  box.replaceChildren();
  if (!r) { box.append(el("p", {class: "empty"}, "Pick a player.")); return; }
  const c = r.case || {};
  box.append(el("p", {class: "ops-kicker"}, "Case"));
  box.append(el("h3", null, r.id));
  box.append(el("p", {class: "meta"}, pill(r.decision), " ", el("b", null, r.band), ` · ${r.reports || "no"} report${r.reports === 1 ? "" : "s"} · recommended: `, el("b", null, c.recommended_action || "none"), " · automated: ", el("b", null, "none")));
  if (r.truth) {
    const note = (OPS.truth_notes || {})[r.truth];
    box.append(el("p", {class: "truth"}, TRUTH_WORD + " ", el("b", null, r.truth), note ? ` — ${note}.` : r.truth === "honest" ? " — plays honestly." : "."));
  }
  if (days.length) {
    const strip = el("div", {style: "display:grid;grid-template-columns:repeat(" + days.length + ",1fr);gap:3px;margin-top:0.7rem"});
    r.nights.forEach((n, i) => strip.append(el("div", {style: "text-align:center;font-size:10px;color:var(--muted)"}, el("div", {class: "nights", style: "display:block"}, el("i", {class: n || null, style: "display:block;width:100%;height:12px"})), days[i].label)));
    box.append(strip);
    if (r.first != null) box.append(el("p", {class: "meta"}, `Review opened by the ${days[r.first].label} batch.`));
  }
  const reasons = c.reasons || (r.why && r.decision !== "clean" ? [r.why] : []);
  box.append(el("h4", null, "Findings"));
  box.append(reasons.length ? el("ul", {class: "findings"}, reasons.map(t => el("li", null, t))) : el("p", {class: "meta"}, r.decision === "insufficient_data" ? "Too little play to score, or no baseline yet for this weapon." : "Nothing fired."));
  if (r.checks.length) {
    box.append(el("h4", null, "Checks, and how each works"));
    const links = el("p", {class: "meta"});
    r.checks.forEach(id => {
      const info = OPS.checks[id] || {label: id, family: ""};
      const tape = (OPS.tapes || {})[id];
      links.append(el("span", {class: "ops-chip fam-" + info.family}, id), " ");
      if (tape && OPS.tape_base != null) links.append(el("a", {href: OPS.tape_base + "#" + tape}, info.label), " ");
      else links.append(info.label + " ");
      links.append(el("br"));
    });
    box.append(links);
  }
  if (r.metrics.length) {
    box.append(el("h4", null, "Against the baseline · this week"));
    r.metrics.filter(m => ["accuracy", "headshot_rate", "median_distance", "geometry_rate"].includes(m.name)).slice(0, 6).forEach(m => box.append(metricLine(m)));
    box.append(el("p", {class: "caption"}, "Dot: the player. Bar: back to the conservative bound. Blue tick: this rank's 95th percentile. White tick: the best human measured."));
  }
  const context = (c.observations || []).filter(t => !/^Cohort was fit/.test(t));
  if (context.length) {
    box.append(el("h4", null, "Context"));
    box.append(el("ul", {class: "context"}, context.slice(0, 6).map(t => el("li", null, t))));
  }
  if (c.party_note) box.append(el("p", {class: "meta"}, c.party_note));
  if (c.seal) box.append(el("p", {class: "meta mono", style: "word-break:break-all"}, "seal " + c.seal.slice(0, 24) + "…"));
  if (r.case) {
    const details = el("details", null, el("summary", null, c.limits ? "Case file (JSON), as fpsdet score writes it" : "Case file (JSON), shortened for this page"));
    details.append(el("pre", null, JSON.stringify(r.case, null, 2)));
    box.append(details);
  }
}

// ---- server health ---------------------------------------------------------
function seqColor(share) {
  if (share == null) return "var(--ops-grid)";
  if (share === 0) return "#1a1430";
  const steps = ["var(--ops-seq-1)", "var(--ops-seq-2)", "var(--ops-seq-3)", "var(--ops-seq-4)", "var(--ops-seq-5)"];
  return steps[Math.min(4, Math.floor(share * 4.999))];
}
function coverage() {
  const card = cardHead(document.getElementById("card-coverage"), "What the server sends", "Share of events carrying each optional field, by night. A field at zero turns its check off; it never counts against anyone.", "coverage");
  const cov = OPS.coverage || [];
  if (!cov.length) { card.append(el("p", {class: "empty"}, "This payload has no events, so field coverage is unknown.")); return; }
  if (tables.coverage) {
    card.append(dataTable(["Field", ...(days.length ? days.map(d => d.label) : []), "Week"], cov.map(c => [c.field, ...(c.daily || []).map(v => v == null ? "–" : pct(v)), pct(c.share)])));
    return;
  }
  for (const c of cov) {
    const row = el("div", {class: "cov-row" + (c.share ? "" : " off")});
    row.append(el("span", {class: "f", title: c.field}, c.field), el("span", {class: "n"}, c.needs));
    const strip = el("span", {class: "d", "aria-hidden": "true"});
    (c.daily || [c.share]).forEach((v, i) => {
      const cell = el("i", {style: "background:" + seqColor(v)});
      cell.addEventListener("pointermove", e => showTip(e, c.field, [{value: v == null ? "–" : pct(v), label: days[i] ? days[i].label : "week"}]));
      cell.addEventListener("pointerleave", hideTip);
      strip.append(cell);
    });
    row.append(strip, el("span", {class: "p"}, c.share ? pct(c.share) : "off"));
    card.append(row);
  }
  const off = cov.filter(c => !c.share).map(c => c.needs);
  card.append(el("p", {class: "caption"}, (off.length ? "Off this week: " + off.join(", ") + ". " : "") + "Darker is less. A step up mid-week is a server build shipping a field."));
}
function cohort() {
  const card = cardHead(document.getElementById("card-cohort"), "Who the baseline has", `Players per rank and weapon in the frozen baseline. Under ${OPS.min_cohort} a cell is untrained, and nothing on it is flagged.`, "cohort");
  const co = OPS.cohort;
  if (!co) { card.append(el("p", {class: "empty"}, "No baseline in this payload.")); return; }
  const get = (b, k) => (co.cells.find(c => c.band === b && c.key === k) || {players: 0}).players;
  if (tables.cohort) {
    card.append(dataTable(["Rank", ...co.keys], co.bands.map(b => [b, ...co.keys.map(k => get(b, k))])));
  } else {
    const grid = el("div", {class: "heat", style: `grid-template-columns: 6.5rem repeat(${co.keys.length}, minmax(0, 1fr))`});
    grid.append(el("span", {class: "h"}, ""));
    co.keys.forEach(k => grid.append(el("span", {class: "h", style: "text-align:center"}, k)));
    const max = Math.max(1, ...co.cells.map(c => c.players));
    for (const b of co.bands) {
      grid.append(el("span", {class: "h"}, b));
      for (const k of co.keys) {
        const n = get(b, k);
        const thin = n < co.min;
        const bg = thin ? "transparent" : seqColor(0.25 + 0.75 * (n / max));
        const cell = el("span", {class: "c", style: `background:${bg};border:1px ${thin ? "dashed var(--line-bright)" : "solid transparent"};color:${thin ? "var(--muted)" : "#fff"}`}, String(n), thin ? el("small", null, "untrained") : null);
        grid.append(cell);
      }
    }
    card.append(grid);
  }
  const moves = OPS.movement || [];
  if (moves.length) {
    card.append(el("h2", {style: "margin:1.1rem 0 0.3rem;font:600 0.98rem/1.3 var(--sans,system-ui,sans-serif);color:#fff"}, "Movement left out of the speed check"));
    const max = Math.max(1, ...moves.map(m => m.count));
    for (const m of moves) {
      const svg = sv("svg", {viewBox: "0 0 200 12", preserveAspectRatio: "none", style: "width:100%;height:12px"});
      svg.append(sv("rect", {x: 0, y: 1, width: Math.max(2, (m.count / max) * 200), height: 10, rx: 3, fill: "var(--ops-seq-3)"}));
      card.append(el("div", {style: "display:grid;grid-template-columns:minmax(0,14rem) 1fr 4.5rem;gap:0.6rem;align-items:center;font-size:0.8rem;padding:2px 0"}, el("span", {style: "color:var(--ink)"}, m.label), svg, el("span", {style: "text-align:right;font-variant-numeric:tabular-nums"}, nf.format(m.count))));
    }
    card.append(el("p", {class: "caption"}, "Samples the server tagged as something other than the player moving the body. Tag every cause the game has; the glitch filter is a backstop, not a substitute."));
  }
}

// ---- render ----------------------------------------------------------------
function render() {
  const list = filtered();
  // The filter row is sticky and can wrap; the case panel sits just under it.
  root.style.setProperty("--ops-filter-h", document.getElementById("ops-filters").offsetHeight + "px");
  document.getElementById("ops-showing").textContent = `Showing ${nf.format(list.length)} of ${nf.format(rows.length)} players`;
  kpis(list);
  nightly(list);
  checks(list);
  scatter(list);
  side(list);
  truth(list);
  queue(list);
  drawer(list);
}
header();
buildFilters();
render();
coverage();
cohort();
let resizing = 0;
let lastWidth = root.clientWidth;
window.addEventListener("resize", () => {
  cancelAnimationFrame(resizing);
  resizing = requestAnimationFrame(() => { if (root.clientWidth !== lastWidth && !root.hidden) { lastWidth = root.clientWidth; render(); } });
});
window.fpsdetOpsRender = () => { lastWidth = root.clientWidth; render(); };
})();
"""

_TOKENS = r"""
:root {
  --bg: #040718; --panel: #0a102c; --panel-hover: #0e1538; --elevated: #070b24;
  --line: #171e42; --line-bright: #242e5e; --ink: #e6ebf9; --muted: #8a93b8;
  --signal: #38c8ff; --light: #c9d2f0;
  --sans: system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--ink); }
body { font: 15px/1.45 var(--sans); }
.ops-top { display: flex; align-items: center; gap: 1rem; padding: 0.8rem 1.4rem; border-bottom: 1px solid var(--line); font: 600 1rem var(--mono); color: #fff; }
.ops-top span { color: var(--signal); }
.ops-top .ops-home { font: 500 0.85rem var(--sans); color: var(--signal); text-decoration: none; }
.ops-top .ops-home:hover { text-decoration: underline; }
.ops-top small { margin-left: auto; font: 500 11px var(--mono); color: var(--muted); letter-spacing: 0.08em; text-transform: uppercase; }
"""


def payload_json(payload: dict) -> str:
    """JSON that is safe inside a <script> element."""
    return json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")


def render_dashboard(
    payload: dict,
    tape_base: str | None = "https://nimdy.github.io/detect-FPS-hackers/board.html",
    *,
    home: str | None = None,
    title: str | None = None,
) -> str:
    """One offline page: the operations view over ``payload`` and nothing else.

    ``home`` adds a link back to the review desk. ``title`` replaces the page title.
    """
    data = dict(payload)
    data["tape_base"] = tape_base
    back = f'<a class="ops-home" href="{html.escape(home)}">← Review desk</a>' if home else ""
    name = html.escape(title or f"fpsdet operations · {payload.get('game', '')}")
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{name}</title>\n"
        "<style>" + _TOKENS + OPS_CSS + "</style>\n</head>\n<body>\n"
        f"<header class=\"ops-top\">fps<span>det</span> operations{back}<small>automated action: none</small></header>\n"
        + OPS_HTML
        + "\n<script id=\"ops-payload\" type=\"application/json\">"
        + payload_json(data)
        + "</script>\n<script>"
        + OPS_JS
        + "</script>\n</body>\n</html>\n"
    )
