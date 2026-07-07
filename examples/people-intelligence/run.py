#!/usr/bin/env python3
"""Acme Corp — People Intelligence, Executive View (Agentic PeopleOS marquee).

The showpiece of the Analytics arm: one dark, executive dashboard that composes headline People
metrics — led by the People<->Finance linkage (Revenue/FTE, operating leverage) — from the shared
compute engine. Like every arm agent it is PRESENTATION + GOVERNANCE ONLY: it does no metric math
(every number comes from foundation/compute/engine.py), draws with the deterministic SVG toolkit
(foundation/render/charts.py), shows not-yet-instrumented metrics honestly, fails closed, and stops
at a human publish gate.

    python3 run.py                                              # draft only
    python3 run.py --publish                                    # refused: needs a valid approver
    python3 run.py --publish --approved-by "People Analytics Lead"

Standard library only; deterministic; offline. Each sparkline/trend point is the SAME engine
re-evaluated at a past quarter-end (engine.series_multi) — real history, never faked.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from foundation.render import charts as ch                 # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "Jan 2026"
PERIOD = "Q4 FY26 · as of Jan 2026 · synthetic data"
AGENT = "people-intelligence"
SCOPE = "publish.people_intelligence"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

# Revenue/FTE benchmark anchors (value $K -> percentile) — ILLUSTRATIVE, representative public-SaaS RPE
# ranges (not a specific published dataset; see SPEC.md). Used ONLY to POSITION the engine's value on
# the market axis (presentation), never to compute the metric.
BENCH = [(94, 5), (130, 25), (200, 50), (300, 75), (400, 90), (420, 95)]
RPF_TARGET_K = 300

# The curated executive headline set (NOT registry-routed — this is a cross-domain view). The engine
# decides what is computable; the agent never hardcodes a value.
KPI_IDS = ["revenue_per_fte", "headcount", "voluntary_attrition", "compa_ratio", "out_of_band_rate"]
# Business-operations headline set — financial efficiency, talent risk, org + comp health. (The
# registry defines other reporting domains too; they are deliberately not on this executive view.)
MARQUEE_METRICS = KPI_IDS + ["operating_leverage", "workforce_cost_ratio", "net_headcount_growth",
                             "span_of_control", "management_layers", "range_penetration",
                             "regrettable_attrition", "total_turnover_rate", "twelve_month_retention",
                             "promotion_rate", "nine_box"]


class ReportError(RuntimeError):
    """Raised when the executive view cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_engine():
    from foundation.compute.engine import MetricEngine
    return MetricEngine()


def _registry():
    from core.metrics import MetricRegistry
    return MetricRegistry.load()


def _pctile(value_k):
    """Piecewise-linear position of a $K Revenue/FTE on the illustrative SaaS benchmark anchors."""
    if value_k <= BENCH[0][0]:
        return BENCH[0][1]
    if value_k >= BENCH[-1][0]:
        return BENCH[-1][1]
    for (x0, p0), (x1, p1) in zip(BENCH, BENCH[1:]):
        if x0 <= value_k <= x1:
            return round(p0 + (p1 - p0) * (value_k - x0) / (x1 - x0))
    return BENCH[-1][1]


def _qlabel(iso):
    y, m, _d = iso.split("-")
    return f"Q{(int(m) - 1) // 3 + 1}·{y[2:]}"


# ---------------------------------------------------------------- compute (no math here)
def build_report(engine):
    reg = _registry()
    results = {mid: engine.compute(mid) for mid in MARQUEE_METRICS}
    for mid in KPI_IDS + ["revenue_per_fte", "net_headcount_growth", "nine_box"]:
        if results[mid]["status"] != "ok":
            raise ReportError(f"executive headline '{mid}' is not computable (status "
                              f"'{results[mid]['status']}') — the view must not ship with a missing headline")

    # Point-in-time history for the sparklines + the operating-leverage chart (one engine per quarter).
    quarters, series = engine.series_multi(KPI_IDS, 8)
    # Talent-risk hotspots: where voluntary attrition is concentrated (the retention view leaders watch).
    attrition_by_team = engine.segment("voluntary_attrition", "job_family")

    # Coverage across the whole registry (honest measured-vs-defined).
    coverage = {}
    for dom in sorted({m["domain"] for m in reg.all()}):
        ids = [m["id"] for m in reg.by_domain(dom)]
        okc = sum(1 for mid in ids if engine.compute(mid)["status"] == "ok")
        coverage[dom] = (okc, len(ids))

    return {"results": results, "quarters": quarters, "series": series, "coverage": coverage,
            "attrition_by_team": attrition_by_team, "registry": reg, "narrative": _narrative(results)}


def _narrative(r):
    rpf, ol = r["revenue_per_fte"], r["operating_leverage"]
    ng, ob = r["net_headcount_growth"], r["out_of_band_rate"]
    p = _pctile(rpf["value"] / 1000)
    parts = [f"Revenue per FTE is <b>${round(rpf['value']/1000)}K</b> "
             f"<span class='up'>(~{ch.ordinal(p)} percentile vs an illustrative SaaS benchmark)</span>."]
    if ol["status"] == "ok":
        e = ol["extras"]
        lead = "operating leverage" if e["revenue_growth_pct"] > e["headcount_growth_pct"] else "headcount-led"
        parts.append(f"Revenue/FTE is up <b>{ol['value']}%</b> YoY (revenue {e['revenue_growth_pct']:+}% vs "
                     f"headcount {e['headcount_growth_pct']:+}%) — the gain is <b>{lead}</b>.")
    parts.append(f"Headcount {ng['value']:+} over 12 months; "
                 f"<span class='warn'>{ob['extras']['above_max_rate']}% of pay sits above band</span> "
                 f"(hot-market drift to watch).")
    return " ".join(parts)


# ---------------------------------------------------------------- presentation (charts + layout)
def _kpi_card(label, value, spark_vals, chip, ctx, direction):
    color = {"up": ch.GREEN, "down": ch.RED, "flat": ch.CYAN}[direction]
    # Honest trend: only draw the sparkline if the FULL history is present. A missing quarter is shown
    # as "trend n/a" — never silently dropped (which would fake a continuous 8-quarter line).
    if spark_vals and len(spark_vals) >= 2 and all(v is not None for v in spark_vals):
        spark = ch.sparkline(spark_vals, color)
    else:
        spark = "<span class='k-na'>trend n/a</span>"
    return (f"<div class='kpi'><div class='k-top'><div class='k-label'>{_e(label)}</div>"
            f"<div class='k-spark'>{spark}</div></div>"
            f"<div class='k-val mono'>{_e(value)}</div>"
            f"<div class='k-foot'><span class='chip {direction}'>{_e(chip)}</span>"
            f"<span class='k-ctx'>{_e(ctx)}</span></div></div>")


def _tile(title, sub, chart, extra=""):
    return (f"<div class='tile'><div class='t-head'><div><h3>{_e(title)}</h3>"
            f"<div class='t-sub'>{_e(sub)}</div></div><span class='t-scope'>Company</span></div>"
            f"<div class='chart'>{chart}</div>{extra}</div>")


def _legend(items):
    return "<div class='legend'>" + "".join(
        f"<span><i style='background:{c}'></i>{_e(l)}</span>" for l, c in items) + "</div>"


def _e(v):
    import html
    return html.escape(str(v))


def render_html(report):
    r, reg = report["results"], report["registry"]
    rpf = r["revenue_per_fte"]
    rpf_k = round(rpf["value"] / 1000)
    pctile = _pctile(rpf_k)

    # ---- KPI cards (each a real metric + its 8-quarter sparkline) ----
    hc, va, cr, ob = r["headcount"], r["voluntary_attrition"], r["compa_ratio"], r["out_of_band_rate"]
    ng = r["net_headcount_growth"]
    s = report["series"]
    kpis = [
        _kpi_card("Revenue / FTE", f"${rpf_k}K", s["revenue_per_fte"], f"~{ch.ordinal(pctile)} pctile",
                  f"target ${RPF_TARGET_K}K", "up"),
        _kpi_card("Headcount", str(hc["value"]), s["headcount"], f"{ng['value']:+} / 12mo",
                  f"{hc['extras']['active']} active · {hc['extras']['on_leave']} on leave",
                  "down" if ng["value"] < 0 else "up"),
        _kpi_card("Voluntary attrition", f"{va['value']}%", s["voluntary_attrition"], "annualized",
                  "trailing 12mo", "flat"),
        _kpi_card("Avg compa-ratio", str(cr["value"]), s["compa_ratio"], "1.00 = mid",
                  "midpoint reference", "flat"),
        _kpi_card("Out-of-band pay", f"{ob['value']}%",   # the engine's value, not a sum of rounded parts
                  s["out_of_band_rate"], f"{ob['extras']['above_max_rate']}% above",
                  f"{ob['extras']['below_min_rate']}% below · {ob['extras']['above_max_rate']}% above", "flat"),
    ]

    body = []
    # header with the brand mark
    body.append("<header class='topbar'>"
                f"<div class='brandwrap'>{MARK_SVG}"
                "<div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                "<div class='brand-sub'>People Intelligence</div></div></div>"
                "<div class='title'><h1>People Intelligence — Executive View</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                "<div class='spacer'></div>"
                "<span class='status'>Draft · awaiting publish approval</span></header>")

    # AI-insight ribbon (deterministic narrator, no model)
    body.append("<section class='insight'>"
                "<svg class='glyph' viewBox='0 0 24 24'><path d='M12 2 L13.7 8.3 L20 10 L13.7 11.7 L12 18 "
                "L10.3 11.7 L4 10 L10.3 8.3 Z' fill='#1ba7ff'/><circle cx='18.5' cy='4.5' r='1.6' fill='#f7b955'/>"
                "<circle cx='5' cy='17' r='1.2' fill='#7c8cff'/></svg>"
                "<div><div class='tag'>Generated insight · engine read of this quarter</div>"
                f"<p>{report['narrative']}</p></div></section>")

    # signature: Revenue/FTE percentile instrument
    body.append("<section class='beacon'><div class='head'>"
                "<div><div class='label'>Revenue / FTE — efficiency vs the SaaS market</div>"
                f"<div class='hero'><span class='v mono'>${rpf_k}K</span>"
                f"<span class='pct'>~{ch.ordinal(pctile)} percentile</span></div></div>"
                "<div class='sub'>Company-level (trailing-12mo revenue / FTE). Illustrative SaaS range "
                "≈ $94K (early) to $420K+ (PLG / infra); median ≈ $200K.</div></div>"
                "<div class='chart'>"
                + ch.percentile_strip(rpf_k, 94, 420,
                                      [(130, "25th · $130K"), (200, "median · $200K"),
                                       (300, "75th · $300K"), (400, "top · $400K+")],
                                      target=RPF_TARGET_K, you_label="Acme")
                + "</div></section>")

    body.append("<section class='kpis'>" + "".join(kpis) + "</section>")

    # ---- chart grid ----
    grid = []
    # headcount bridge
    e = ng["extras"]
    bridge = ch.waterfall([("Begin", e["beginning"], "total"), ("Hires", e["hires"], "add"),
                           ("Vol. exit", -e["voluntary_exits"], "sub"),
                           ("Invol.", -e["involuntary_exits"], "sub"), ("End", e["ending"], "total")])
    grid.append("<div class='col-6'>" + _tile(
        "Headcount bridge", "Roster movement, beginning → ending · 12 mo", bridge,
        _legend([("Adds", ch.GREEN), ("Exits", ch.RED), ("Balance", ch.CYAN)])) + "</div>")

    # operating leverage (rev/fte vs headcount)
    qlabels = [_qlabel(q) for q in report["quarters"]]
    lev = ch.dual_axis_line(qlabels, [round(v / 1000) for v in s["revenue_per_fte"]], s["headcount"],
                            left_fmt=lambda v: f"${v}", right_fmt=lambda v: str(v))
    grid.append("<div class='col-6'>" + _tile(
        "Operating leverage", "Revenue / FTE ($K) vs headcount · 8 quarters", lev,
        _legend([("Revenue / FTE ($K)", ch.CYAN), ("Headcount", ch.INDIGO)])) + "</div>")

    # pay positioning — range-penetration distribution (the in-band spread + the out-of-band tails)
    pen = r["range_penetration"]["extras"]["by_penetration"]
    pen_bins, pen_labels = list(pen.values()), list(pen.keys())
    paypos = ch.histogram(pen_bins, pen_labels, highlight={0: ch.RED, len(pen_bins) - 1: ch.AMBER})
    pp_stats = (f"<div class='statrow'>"
                f"<div class='stat'><span class='s-v mono' style='color:var(--red)'>{ob['extras']['below_min_rate']}%</span>"
                f"<span class='s-l'>below minimum</span></div>"
                f"<div class='stat'><span class='s-v mono' style='color:var(--green)'>{ob['extras']['in_band_rate']}%</span>"
                f"<span class='s-l'>in band</span></div>"
                f"<div class='stat'><span class='s-v mono' style='color:var(--amber)'>{ob['extras']['above_max_rate']}%</span>"
                f"<span class='s-l'>above maximum</span></div></div>")
    grid.append("<div class='col-6'>" + _tile(
        "Pay positioning", "Range penetration · in-band vs out-of-band", paypos, pp_stats) + "</div>")

    # attrition hotspots — voluntary attrition by team, sorted; above-company-rate teams flagged red
    seg = report["attrition_by_team"]
    items = sorted(seg.items(), key=lambda kv: -kv[1])
    a_vals, a_labels = [v for _, v in items], [k for k, _ in items]
    company_va = va["value"]
    a_hl = {i: ch.RED for i, (_k, v) in enumerate(items) if v >= company_va}
    attr_chart = ch.histogram(a_vals, a_labels, highlight=a_hl)
    reg_attr = r["regrettable_attrition"]
    a_stats = (f"<div class='statrow'>"
               f"<div class='stat'><span class='s-v mono'>{company_va}%</span><span class='s-l'>company voluntary</span></div>"
               f"<div class='stat'><span class='s-v mono' style='color:var(--red)'>{_e(a_labels[0])}</span>"
               f"<span class='s-l'>top hotspot ({a_vals[0]}%)</span></div>"
               + (f"<div class='stat'><span class='s-v mono'>{reg_attr['value']}%</span>"
                  f"<span class='s-l'>regrettable</span></div>" if reg_attr['status'] == 'ok' else "")
               + "</div>")
    grid.append("<div class='col-6'>" + _tile(
        "Attrition by team", "Voluntary attrition by function · above-company-rate flagged", attr_chart,
        _legend([("Above company rate", ch.RED), ("Below", ch.GREEN)])) + a_stats + "</div>")

    # org shape — managers vs ICs by level (the population-pyramid / "diamond"); + span-of-control stats
    sp = r["span_of_control"]["extras"]
    mlx = r["management_layers"]["extras"]
    by_level = sp["by_level"]
    diamond_rows = [(lvl, by_level[lvl]["managers"], by_level[lvl]["ics"])
                    for lvl in sorted(by_level, reverse=True)]   # senior (L7) at the top
    org = ch.org_diamond(diamond_rows)
    sub_scale = sp["by_span"]["1-2"]
    span_stats = (f"<div class='statrow'>"
                  f"<div class='stat'><span class='s-v mono'>{sp['mean']}</span><span class='s-l'>avg span</span></div>"
                  f"<div class='stat'><span class='s-v mono'>{sp['managers']}</span><span class='s-l'>people-managers</span></div>"
                  f"<div class='stat'><span class='s-v mono'>{mlx['max_depth']}</span><span class='s-l'>layers (CEO→IC)</span></div>"
                  f"<div class='stat'><span class='s-v mono' style='color:{'var(--amber)' if sub_scale else 'var(--green)'}'>"
                  f"{sub_scale}</span><span class='s-l'>sub-scale mgrs</span></div></div>")
    grid.append("<div class='col-7'>" + _tile(
        "Org shape — managers vs ICs by level", "Bar length = headcount; centered (a diamond = senior-balanced)",
        org, _legend([("Managers", ch.CYAN), ("Individual contributors", "#345a7d")]) + span_stats) + "</div>")

    # 9-box
    g = r["nine_box"]["value"]
    matrix = [[g[p]["Low"], g[p]["Med"], g[p]["High"]] for p in ("High", "Med", "Low")]
    grid.append("<div class='col-5'>" + _tile(
        "Talent grid (9-box)", "Performance × potential · rated population", ch.heatmap_9box(matrix)) + "</div>")
    body.append("<section class='grid'>" + "".join(grid) + "</section>")

    # Total Rewards & retention business strip + footer.
    # Honest, never silent: a metric that isn't instrumented shows 'data_pending', it doesn't vanish.
    def _stat(label, metric_id):
        m = r[metric_id]
        val, sty = (f"{m['value']}%", "") if m["status"] == "ok" else ("data_pending", " style='color:var(--soft)'")
        return f"<div class='ld'><span class='mono'{sty}>{_e(val)}</span><small>{_e(label)}</small></div>"
    strip = (_stat("Regrettable attrition", "regrettable_attrition")
             + _stat("Workforce cost / revenue", "workforce_cost_ratio")
             + _stat("Total turnover", "total_turnover_rate")
             + _stat("12-mo retention", "twelve_month_retention")
             + _stat("Promotion rate", "promotion_rate"))
    body.append(f"<section class='leadrow'><div class='ld-h'>Total Rewards &amp; retention</div>{strip}</section>")

    instrumented = sum(o for o, _ in report["coverage"].values())
    total = sum(t for _, t in report["coverage"].values())
    body.append("<footer class='foot'>"
                f"<div>Composed by the <b>{AGENT}</b> agent from the Agentic PeopleOS metric registry · "
                "every number is engine-computed and cited; a human owns what is published.</div>"
                "<div class='pills'>"
                f"<span class='pill'>Synthetic data</span>"
                f"<span class='pill'>{instrumented}/{total} metrics instrumented</span>"
                "<span class='pill'>Aggregation floor n ≥ 5</span>"
                "<span class='pill'>ISO 30414 aligned</span></div></footer>")

    return _page("".join(body))


def render_digest(report):
    r = report["results"]
    rpf_k = round(r["revenue_per_fte"]["value"] / 1000)
    instrumented = sum(o for o, _ in report["coverage"].values())
    total = sum(t for _, t in report["coverage"].values())

    def v(mid, suffix="%"):
        # Honest in the digest too: an optional metric that isn't computable reads 'data_pending',
        # never a bare 'None%'. (Required headlines already fail closed upstream.)
        m = r[mid]
        return f"{m['value']}{suffix}" if m["status"] == "ok" else "data_pending"

    lines = [f"# {COMPANY} — People Intelligence (Executive View) digest",
             f"_As of {AS_OF} · draft for review_", "",
             f"- Revenue/FTE **${rpf_k}K** (~{ch.ordinal(_pctile(rpf_k))} pctile vs an illustrative SaaS benchmark); operating leverage "
             f"**{v('operating_leverage')}** YoY.",
             f"- Headcount **{r['headcount']['value']}** ({r['net_headcount_growth']['value']:+} / 12mo); "
             f"voluntary attrition **{v('voluntary_attrition')}** (regrettable "
             f"**{v('regrettable_attrition')}**); avg compa **{v('compa_ratio', '')}**.",
             f"- Workforce cost **{v('workforce_cost_ratio')}** of revenue; total turnover "
             f"**{v('total_turnover_rate')}**; 12-month retention **{v('twelve_month_retention')}**.",
             f"- Instrumentation coverage: **{instrumented}/{total}** registry metrics computed (measured vs defined).",
             "", "_Every number is engine-computed and cited from metrics.registry.json._",
             "", "_Publish gate: a human (People Analytics) must approve before this is distributed._"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- brand mark + style
MARK_SVG = (
    "<svg class='logomark' viewBox='36 12 118 107' xmlns='http://www.w3.org/2000/svg' "
    "role='img' aria-label='Agentic PeopleOS'>"
    "<g fill='none' stroke-linejoin='round' shape-rendering='geometricPrecision'>"
    "<path d='M102.50 94.00 C102.50 95.00 102.50 96.00 102.50 97.00 C102.50 104.80 111.10 108.20 "
    "121.60 108.40 C133.50 108.60 142.50 103.60 143.80 96.10 C145.00 89.00 140.20 84.20 132.00 79.70 "
    "C125.20 76.00 116.70 71.60 114.50 64.40 C112.30 57.10 117.20 50.40 124.90 49.00 C132.70 47.60 "
    "139.60 51.20 142.20 55.70' stroke='#1ba7ff' stroke-width='10.5' stroke-linecap='round'/>"
    "<path d='M46.5 101.5 C46.5 99.5 46.5 98.0 46.5 97.0 C47.0 73.7 54.1 57.5 68.4 52.8 C72.2 51.55 "
    "76.8 51.55 80.6 52.8 C94.9 57.5 102.0 73.7 102.5 97' stroke='#E7F0FA' stroke-width='10.5' "
    "stroke-linecap='butt'/>"
    "<circle cx='46.5' cy='101.5' r='5.25' fill='#E7F0FA' stroke='none'/>"
    "<circle cx='74.5' cy='36.8' r='14.6' stroke='#1ba7ff' stroke-width='9.3'/>"
    "</g></svg>"
)

_STYLE = """
:root{--bg:#06131d;--panel:#0a1f2c;--panel2:#0f2a3e;--text:#eef7ff;--muted:#8db1ce;--soft:#8296ab;
--cyan:#1ba7ff;--cyan2:#48c7ff;--green:#43d477;--red:#ff4d4f;--amber:#f7b955;--indigo:#7c8cff;
--line:rgba(27,167,255,.30);--hair:rgba(141,177,206,.16);}
*{box-sizing:border-box;}
body{margin:0;background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),var(--bg);
color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.45;padding:24px;}
.mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;}
.wrap{max-width:1280px;margin:0 auto;}
.topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;border-bottom:2px solid var(--cyan);padding-bottom:16px;}
.brandwrap{display:flex;align-items:center;gap:12px;min-width:0;}
.logomark{height:42px;width:auto;flex:0 0 auto;display:block;}
.brand{font-weight:800;font-size:18px;color:#fff;}.brand .os{color:var(--cyan);}
.brand-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--soft);margin-top:2px;}
.title h1{margin:0;font-size:20px;color:#fff;font-weight:800;letter-spacing:-.01em;}
.title .meta{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;color:var(--muted);margin-top:3px;}
.spacer{flex:1;}
.status{border:1px solid rgba(247,185,85,.5);color:var(--amber);background:rgba(247,185,85,.12);font-size:11px;font-weight:800;padding:5px 12px;border-radius:999px;text-transform:uppercase;}
.insight{display:flex;gap:13px;align-items:flex-start;margin:18px 0;background:linear-gradient(98deg,rgba(27,167,255,.12),rgba(27,167,255,.03) 65%,transparent);
border:1px solid var(--line);border-left:3px solid var(--cyan);border-radius:12px;padding:14px 17px;}
.insight .glyph{flex:0 0 auto;width:24px;height:24px;margin-top:2px;}
.insight .tag{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--cyan2);font-weight:700;margin-bottom:4px;}
.insight p{margin:0;font-size:14.5px;line-height:1.5;}.insight .up{color:var(--green);font-weight:600;}.insight .warn{color:var(--amber);font-weight:600;}
.beacon{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px 22px 12px;margin-bottom:16px;box-shadow:0 14px 40px rgba(0,0,0,.5);}
.beacon .head{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;}
.beacon .label{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);}
.beacon .hero{display:flex;align-items:baseline;gap:12px;margin-top:3px;}
.beacon .hero .v{font-size:40px;font-weight:800;letter-spacing:-.02em;line-height:1;color:#fff;}
.beacon .hero .pct{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12.5px;color:var(--cyan2);background:rgba(27,167,255,.14);border:1px solid var(--line);padding:3px 9px;border-radius:999px;}
.beacon .sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;color:var(--soft);text-align:right;max-width:300px;}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:16px;}
.kpi{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:14px 15px;display:flex;flex-direction:column;gap:9px;}
.kpi .k-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
.kpi .k-label{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);line-height:1.3;}
.kpi .k-spark{flex:0 0 auto;width:84px;}
.k-na{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:8.5px;color:var(--soft);opacity:.8;}
.kpi .k-val{font-size:26px;font-weight:800;letter-spacing:-.02em;line-height:1;color:#fff;}
.kpi .k-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.chip{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;font-weight:700;padding:2px 7px;border-radius:6px;white-space:nowrap;}
.chip.up{color:var(--green);background:rgba(67,212,119,.14);}.chip.down{color:var(--red);background:rgba(255,77,79,.14);}
.chip.flat{color:var(--cyan2);background:rgba(27,167,255,.14);}
.k-ctx{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;}
.tile{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px 14px;display:flex;flex-direction:column;}
.col-6{grid-column:span 6;}.col-7{grid-column:span 7;}.col-5{grid-column:span 5;}
.t-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:2px;}
.t-head h3{margin:0;font-size:14.5px;color:#fff;font-weight:700;}
.t-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:3px;}
.t-scope{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);border:1px solid var(--hair);border-radius:999px;padding:3px 9px;white-space:nowrap;}
.chart{margin-top:10px;}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--muted);}
.legend span{display:inline-flex;align-items:center;gap:6px;}.legend i{width:10px;height:10px;border-radius:3px;}
.statrow{display:flex;gap:20px;flex-wrap:wrap;margin-top:12px;padding-top:12px;border-top:1px solid var(--hair);}
.stat .s-v{font-size:18px;font-weight:800;color:#fff;}.stat .s-l{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);display:block;margin-top:2px;}
.leadrow{display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-top:16px;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:14px 18px;}
.ld-h{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);}
.ld{display:flex;flex-direction:column;}.ld span{font-size:22px;font-weight:800;color:#fff;}.ld small{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;text-transform:uppercase;color:var(--soft);}
.foot{margin-top:22px;padding-top:14px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10.5px;color:var(--soft);}
.foot .pills{display:flex;gap:8px;flex-wrap:wrap;}.foot .pill{border:1px solid var(--hair);border-radius:999px;padding:3px 9px;}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — People Intelligence</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _fail_closed(message) -> int:
    # Stale the report, the digest, AND any prior approval record — a fail-closed run must never leave
    # a PUBLISHED.json that looks like a current approval for a report we just invalidated.
    for p in (REPORT, DIGEST, OUT / "PUBLISHED.json"):
        if not p.exists():
            continue
        try:
            p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            try:                       # if we can't stale it, at least remove it (no lingering approval)
                p.unlink()
            except OSError:
                pass
    print(f"FAIL CLOSED: {_one_line(message)}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp People Intelligence executive view (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (People Analytics).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        engine = _load_engine()
        report = build_report(engine)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"compute engine unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)   # remove any stale approval BEFORE writing — a draft or a
    #                                  # failed run must never inherit a prior run's "published" flag
    try:
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        if args.publish:
            _atomic_write(pub_path,
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF}, indent=2) + "\n")
        elif pub_path.exists():
            pub_path.unlink()   # a redrawn DRAFT invalidates any prior approval record
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    r = report["results"]
    print(f"{COMPANY} People Intelligence — Executive View ({AS_OF})")
    print(f"  Revenue/FTE ${round(r['revenue_per_fte']['value']/1000)}K | headcount {r['headcount']['value']} | "
          f"operating leverage {r['operating_leverage']['value']}%")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (People Analytics) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
