#!/usr/bin/env python3
"""Shared dashboard renderer for Agentic PeopleOS reporting agents.

One presentation component every Analytics-arm agent composes, so the dark "skrodzkai" dashboard
style lives in exactly one place and the visuals match across reports. Pure + stdlib-only +
deterministic: same inputs -> identical HTML (so a committed report.sample.html can't drift). Every
interpolated value is HTML-escaped, including values that originate from the dataset.

This module does NOT compute metrics (that's foundation/compute/engine.py) and does NOT write files
(the agent owns I/O). It only turns already-computed values into HTML fragments + a full page.
"""
from __future__ import annotations

import html
import re

# The skrodzkai dark design tokens (pure black, cyan accent, Inter), shared by every dashboard.
_STYLE = """
:root{--bg:#000;--text:#eef7ff;--muted:#8db1ce;--soft:#6d8294;--cyan:#1ba7ff;--cyan2:#48c7ff;
--green:#43d477;--red:#ff4d4f;--amber:#f7b955;--line:rgba(27,167,255,.46);--track:rgba(255,255,255,.06);}
*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,-apple-system,'Segoe UI',sans-serif;font-size:15px;line-height:1.45;}
.wrap{max-width:920px;margin:0 auto;padding:28px 26px 40px;}.mono{font-family:'JetBrains Mono',ui-monospace,monospace;}
.brand-row{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid var(--cyan);padding-bottom:14px;margin-bottom:22px;}
.brand{font-weight:800;font-size:17px;color:#fff;}.brand .os{color:var(--cyan);}
.status{border:1px solid rgba(247,185,85,.5);color:var(--amber);background:rgba(247,185,85,.12);font-size:11px;font-weight:800;padding:4px 11px;border-radius:999px;text-transform:uppercase;}
.kicker{color:var(--cyan);text-transform:uppercase;font-size:11px;letter-spacing:.1em;font-weight:800;}
h1{margin:6px 0 4px;font-size:25px;color:#fff;font-weight:800;}.date{color:var(--muted);font-size:13px;}
.callout{margin:18px 0 4px;background:rgba(27,167,255,.06);border:1px solid rgba(27,167,255,.22);border-left:3px solid var(--cyan);border-radius:0 8px 8px 0;padding:12px 15px;}
.callout .label{color:var(--cyan2);text-transform:uppercase;font-size:10.5px;font-weight:800;letter-spacing:.06em;}.callout p{margin:4px 0 0;font-size:14px;}
.meter{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:20px 0 6px;}
.metric{background:#000;border:1px solid var(--line);border-radius:10px;padding:13px 15px;}
.metric strong{display:block;font-size:24px;font-weight:700;color:#fff;}.metric span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;margin-top:4px;}
.metric.good strong{color:var(--green);}.metric.warn strong{color:var(--amber);}.metric.bad strong{color:var(--red);}
.metric.warn{border-color:rgba(247,185,85,.5);}.metric.bad{border-color:rgba(255,77,79,.5);}
.section-title{color:var(--cyan);text-transform:uppercase;font-size:13px;font-weight:900;letter-spacing:.05em;margin:24px 0 10px;}
.row{display:flex;align-items:center;gap:9px;margin:6px 0;}.lbl{width:130px;font-size:12px;color:var(--muted);}
.bar{flex:1;background:var(--track);border-radius:4px;height:13px;overflow:hidden;}.bar>div{height:13px;border-radius:4px;}.val{width:40px;text-align:right;font-size:12px;}
table.data{width:100%;border-collapse:collapse;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:12.5px;}
table.data th{text-align:left;color:var(--cyan);background:#07101a;padding:7px 8px;font-size:10px;text-transform:uppercase;font-weight:800;}
table.data td{padding:7px 8px;border-top:1px solid rgba(141,177,206,.20);}table.data .c{text-align:center;}
.pending{margin:6px 0;font-size:12.5px;color:var(--soft);}.pending b{color:var(--muted);}
.footer{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;margin-top:24px;border-top:1px solid #193044;padding-top:14px;color:var(--soft);font-size:11px;}
"""

_TONES = {"neutral": "metric", "good": "metric good", "warn": "metric warn", "bad": "metric bad"}


def _esc(v) -> str:
    return html.escape(str(v))


# A color is interpolated into a CSS context (style="..."), where html.escape is NOT sufficient — a
# value like "red;width:999%;background:url(x)" stays valid after escaping but breaks out of the
# property. Brand discipline + safety: allow ONLY an Agentic PeopleOS CSS var() token or a #hex
# literal; anything else (incl. arbitrary named colors) falls back to the cyan accent (fail safe).
_CSS_VAR = re.compile(r"^var\(--[a-z0-9-]{1,40}\)$")
_HEX = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _safe_color(value, default: str = "var(--cyan)") -> str:
    v = str(value).strip()
    if _CSS_VAR.match(v) or _HEX.match(v):
        return v
    return default


def page(title: str, body_html: str) -> str:
    """Wrap composed body HTML in a full, self-contained dark document."""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_esc(title)}</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body_html}</div></body></html>")


def brand_header(status: str = "Draft · awaiting publish approval") -> str:
    return (f"<div class='brand-row'><span class='brand'>Agentic People<span class='os'>OS</span></span>"
            f"<span class='status'>{_esc(status)}</span></div>")


def title_block(kicker: str, heading: str, subtitle: str) -> str:
    return (f"<div class='kicker'>{_esc(kicker)}</div><h1>{_esc(heading)}</h1>"
            f"<div class='date'>{_esc(subtitle)}</div>")


def narrator(text: str, label: str = "What needs attention") -> str:
    return (f"<div class='callout'><div class='label'>{_esc(label)}</div>"
            f"<p>{_esc(text)}</p></div>")


def section(title: str) -> str:
    return f"<div class='section-title'>{_esc(title)}</div>"


def kpi_cards(cards: list) -> str:
    """cards: list of {value, label, tone?}  (tone in neutral|good|warn|bad)."""
    out = []
    for c in cards:
        cls = _TONES.get(c.get("tone", "neutral"), "metric")
        out.append(f"<div class='{cls}'><strong class='mono'>{_esc(c['value'])}</strong>"
                   f"<span>{_esc(c['label'])}</span></div>")
    return f"<div class='meter'>{''.join(out)}</div>"


def bars(rows: list) -> str:
    """rows: list of {label, value, max, color?}."""
    out = []
    for r in rows:
        mx = r.get("max") or 1
        pct = round(100 * r["value"] / mx) if mx else 0
        pct = max(0, min(100, pct))   # clamp: a width can never exceed the track
        color = _safe_color(r.get("color", "var(--cyan)"))   # CSS-context safe (not just HTML-escaped)
        out.append(f"<div class='row'><div class='lbl'>{_esc(r['label'])}</div>"
                   f"<div class='bar'><div style='width:{pct}%;background:{color};'></div></div>"
                   f"<div class='val mono'>{_esc(r['value'])}</div></div>")
    return "".join(out)


def data_table(headers: list, rows: list, center_from: int = 1) -> str:
    """headers: list[str]; rows: list[list]. Columns >= center_from are centered."""
    head = "".join(f"<th{' class=c' if i >= center_from else ''}>{_esc(h)}</th>"
                   for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = "".join(f"<td{' class=c' if i >= center_from else ''}>{_esc(v)}</td>"
                        for i, v in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    return (f"<table class='data'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def metric_definitions(registry, ids: list) -> str:
    """A CITED definitions block: name + definition pulled from the registry (never redefined)."""
    items = []
    for mid in ids:
        m = registry.get(mid)
        if m:
            items.append(f"<li><b style='color:#eef7ff'>{_esc(m['name'])}</b> — {_esc(m['definition'])}</li>")
    body = "".join(items)
    return (f"{section('Metric definitions')}"
            f"<ul style='font-size:12px;color:#8db1ce;line-height:1.6;margin:0;padding-left:18px;'>{body}</ul>"
            f"<div style='font-size:11px;color:#687b95;margin-top:6px;'>Cited from "
            f"<b style='color:#8db1ce'>metrics.registry.json</b>. Per the registry, this agent may "
            f"calculate and flag — it <b style='color:#8db1ce'>reports; a human owns every decision</b>.</div>")


def data_pending_block(items: list, title: str = "Coverage — not yet instrumented") -> str:
    """Honest gap reporting. items: list of {name, needs}. Renders nothing if empty."""
    if not items:
        return ""
    rows = "".join(f"<div class='pending'>○ <b>{_esc(it['name'])}</b> — needs {_esc(it['needs'])}</div>"
                   for it in items)
    return (f"{section(title)}{rows}"
            f"<div style='font-size:11px;color:#687b95;margin-top:6px;'>"
            f"Instrumentation coverage — these metrics are defined in the registry but their source "
            f"table isn't modeled in the synthetic foundation yet. Shown honestly; never estimated.</div>")


def governance_footer(agent_name: str) -> str:
    return (f"<div class='footer'><span>Generated by the <b style='color:var(--muted)'>{_esc(agent_name)}</b>"
            f" agent &middot; Agentic PeopleOS</span>"
            f"<span>Human-in-the-loop: the agent reports; a human owns every decision</span></div>")
