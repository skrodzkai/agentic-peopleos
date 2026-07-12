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

from foundation.render import evidence as evidence_render

# Agentic PeopleOS navy-glass design tokens (deep navy, glass panels, cyan accent, system fonts),
# shared by every dashboard. Hexes EQUAL foundation/render/charts.py constants byte-for-byte.
_STYLE = """
:root{--bg:#06131d;--panel:#0a1f2c;--panel2:#0f2a3e;--inset:#08283a;--well:#071a26;
--text:#eef7ff;--body:#dbe7f0;--muted:#8db1ce;--soft:#8296ab;--faint:#66809a;
--cyan:#1ba7ff;--cyan2:#48c7ff;--green:#43d477;--red:#ff4d4f;--amber:#f7b955;--indigo:#7c8cff;
--line:rgba(27,167,255,.30);--hair:rgba(141,177,206,.16);--rule:#14364a;--track:rgba(255,255,255,.06);--hover:rgba(27,167,255,.04);
--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;}
*{box-sizing:border-box;}
body{margin:0;background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),var(--bg);background-repeat:no-repeat;color:var(--body);font-family:var(--sans);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;}
.wrap{max-width:920px;margin:0 auto;padding:28px 26px 40px;}.mono{font-family:var(--mono);font-variant-numeric:tabular-nums;}
.brand-row{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--rule);padding-bottom:14px;margin-bottom:22px;}
.brand{font-weight:800;font-size:18px;color:var(--text);letter-spacing:-.01em;}.brand .os{color:var(--cyan);}
.status{font-family:var(--mono);border:1px solid rgba(247,185,85,.45);color:var(--amber);background:rgba(247,185,85,.12);font-size:10.5px;font-weight:700;letter-spacing:.05em;padding:4px 11px;border-radius:999px;text-transform:uppercase;white-space:nowrap;}
.kicker{color:var(--cyan);text-transform:uppercase;font-size:11px;letter-spacing:.14em;font-weight:700;font-family:var(--mono);}
h1{margin:8px 0 4px;font-size:24px;color:var(--text);font-weight:800;letter-spacing:-.01em;}.date{color:var(--muted);font-size:13px;}
.callout{margin:18px 0 4px;background:linear-gradient(98deg,rgba(27,167,255,.12),rgba(27,167,255,.03) 65%,transparent);border:1px solid var(--line);border-left:3px solid var(--cyan);border-radius:0 10px 10px 0;padding:12px 15px;}
.callout .label{color:var(--cyan2);text-transform:uppercase;font-size:10.5px;font-weight:800;letter-spacing:.08em;font-family:var(--mono);}.callout p{margin:4px 0 0;font-size:14px;color:var(--body);}
.meter{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px;margin:20px 0 6px;}
.metric{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:14px 16px 13px;box-shadow:0 10px 30px rgba(0,0,0,.25);}
.metric strong{display:block;font-size:26px;font-weight:800;color:var(--text);letter-spacing:-.02em;font-variant-numeric:tabular-nums;font-family:var(--mono);}.metric span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em;margin-top:5px;font-family:var(--mono);}
.metric.good strong{color:var(--green);}.metric.warn strong{color:var(--amber);}.metric.bad strong{color:var(--red);}
.metric.warn{border-color:rgba(247,185,85,.40);}.metric.bad{border-color:rgba(255,77,79,.40);}
.section-title{display:flex;align-items:center;gap:12px;color:var(--cyan);text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:.14em;font-family:var(--mono);margin:26px 0 12px;}
.section-title::after{content:"";flex:1;height:1px;background:var(--hair);}
.row{display:flex;align-items:center;gap:9px;margin:6px 0;}.lbl{width:130px;font-size:12px;color:var(--muted);}
.bar{flex:1;background:var(--track);border-radius:999px;height:11px;overflow:hidden;}.bar>div{height:11px;border-radius:999px;}.val{width:44px;text-align:right;font-size:12px;font-family:var(--mono);font-variant-numeric:tabular-nums;}
table.data{width:100%;border-collapse:collapse;font-size:12.5px;}
table.data th{text-align:left;color:var(--muted);background:var(--panel2);padding:7px 10px;font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;font-weight:700;font-family:var(--mono);border-bottom:1px solid var(--line);}
table.data td{padding:7px 10px;border-bottom:1px solid var(--hair);}table.data .c{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;}
table.data tbody tr:hover{background:var(--hover);}
.chip{display:inline-block;font-family:var(--mono);font-size:10px;font-weight:700;padding:2px 7px;border-radius:6px;letter-spacing:.02em;}
.chip.up,.chip.ok{color:var(--green);background:rgba(67,212,119,.14);}.chip.down,.chip.bad{color:var(--red);background:rgba(255,77,79,.14);}.chip.warn{color:var(--amber);background:rgba(247,185,85,.14);}.chip.flat{color:var(--cyan2);background:rgba(27,167,255,.14);}
.tile{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px;margin:14px 0;position:relative;overflow:hidden;}
.tile.t-edge::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:var(--edge,var(--cyan));}
.tile h3{margin:0 0 4px;font-size:14.5px;font-weight:700;color:var(--text);}.tile .sub{color:var(--muted);font-size:12px;}
.pending{margin:6px 0;font-size:12.5px;color:var(--soft);}.pending b{color:var(--muted);}
.footer{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;margin-top:24px;border-top:1px solid var(--rule);padding-top:14px;color:var(--soft);font-size:11px;font-family:var(--mono);}
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
    """cards: list of {value, label, tone?}; value may be an EvidenceValue."""
    out = []
    for c in cards:
        cls = _TONES.get(c.get("tone", "neutral"), "metric")
        item = (evidence_render.value(c["value"], c["claim_id"], c.get("evidence_label", ""))
                if c.get("claim_id") else c["value"])
        out.append(f"<div class='{cls}'><strong class='mono'>{evidence_render.render_value(item)}</strong>"
                   f"<span>{_esc(c['label'])}</span></div>")
    return f"<div class='meter'>{''.join(out)}</div>"


def chip(text: str, tone: str = "flat") -> str:
    """A small semantic pill. tone in up|ok|down|bad|warn|flat (unknown -> flat)."""
    t = tone if tone in ("up", "ok", "down", "bad", "warn", "flat") else "flat"
    return f"<span class='chip {t}'>{_esc(text)}</span>"


def tile(title: str, body_html: str, sub: str = "", accent: str = "") -> str:
    """A glass tile with an optional 3px semantic top-edge accent (CSS-safe color only)."""
    edge = _safe_color(accent) if accent else ""
    cls = "tile t-edge" if edge else "tile"
    style = f" style='--edge:{edge}'" if edge else ""
    sub_html = f"<div class='sub'>{_esc(sub)}</div>" if sub else ""
    return f"<div class='{cls}'{style}><h3>{_esc(title)}</h3>{sub_html}{body_html}</div>"


def bars(rows: list) -> str:
    """rows: list of {label, value, max, color?}."""
    out = []
    for r in rows:
        mx = r.get("max") or 1
        raw_value = r["value"].raw if isinstance(r["value"], evidence_render.EvidenceValue) else r["value"]
        if raw_value is None:
            raise ValueError("an evidence-aware bar value needs raw=<number> for scaling")
        pct = round(100 * raw_value / mx) if mx else 0
        pct = max(0, min(100, pct))   # clamp: a width can never exceed the track
        color = _safe_color(r.get("color", "var(--cyan)"))   # CSS-context safe (not just HTML-escaped)
        out.append(f"<div class='row'><div class='lbl'>{evidence_render.render_value(r['label'])}</div>"
                   f"<div class='bar'><div style='width:{pct}%;background:{color};'></div></div>"
                   f"<div class='val mono'>{evidence_render.render_value(r['value'])}</div></div>")
    return "".join(out)


def data_table(headers: list, rows: list, center_from: int = 1) -> str:
    """headers: list[str]; rows: list[list]. Columns >= center_from are right-aligned numeric (mono, tabular-nums)."""
    head = "".join(f"<th{' class=c' if i >= center_from else ''}>{_esc(h)}</th>"
                   for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = "".join(f"<td{' class=c' if i >= center_from else ''}>{evidence_render.render_value(v)}</td>"
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
            items.append(f"<li><b style='color:var(--text)'>{_esc(m['name'])}</b> — {_esc(m['definition'])}</li>")
    body = "".join(items)
    return (f"{section('Metric definitions')}"
            f"<ul style='font-size:12px;color:var(--muted);line-height:1.6;margin:0;padding-left:18px;'>{body}</ul>"
            f"<div style='font-size:11px;color:var(--soft);margin-top:6px;'>Cited from "
            f"<b style='color:var(--muted)'>metrics.registry.json</b>. Per the registry, this agent may "
            f"calculate and flag — it <b style='color:var(--muted)'>reports; a human owns every decision</b>.</div>")


def data_pending_block(items: list, title: str = "Coverage — not yet instrumented") -> str:
    """Honest gap reporting. items: list of {name, needs}. Renders nothing if empty."""
    if not items:
        return ""
    rows = "".join(f"<div class='pending'>○ <b>{_esc(it['name'])}</b> — needs {_esc(it['needs'])}</div>"
                   for it in items)
    return (f"{section(title)}{rows}"
            f"<div style='font-size:11px;color:var(--soft);margin-top:6px;'>"
            f"Instrumentation coverage — these metrics are defined in the registry but their source "
            f"table isn't modeled in the synthetic foundation yet. Shown honestly; never estimated.</div>")


def governance_footer(agent_name: str) -> str:
    return (f"<div class='footer'><span>Generated by the <b style='color:var(--muted)'>{_esc(agent_name)}</b>"
            f" agent &middot; Agentic PeopleOS</span>"
            f"<span>Human-in-the-loop: the agent reports; a human owns every decision</span></div>")
