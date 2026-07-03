#!/usr/bin/env python3
"""Acme Corp — Executive Compensation Benchmarking (Compensation Committee view).

The second agent of the Executive Compensation arm. Once the peer group is approved, it composes a
dark, board-ready dashboard that positions the subject's Named Executive Officers against the peer
group's REAL, publicly-disclosed proxy pay — the pay-positioning read a committee reviews before it
sets pay:

  For each benchmarked role (CEO/CFO/COO/CLO) and each pay ELEMENT (base, annual cash incentive, total
  cash, LTI/equity, total direct comp), it shows where the subject sits as a PERCENTILE of the peer
  distribution, versus the committee's target-percentile band, with the peer P25/median/P75 and a
  below/within/above call. Roles with too few peer observations are SUPPRESSED, never given a spurious
  percentile.

Like every arm agent it is PRESENTATION + GOVERNANCE ONLY: it runs no positioning math (every
percentile, quantile and status comes from foundation/compute/benchmarking.py, over the committed real
peer proxy data in foundation/data/acme/proxy_comp.csv), draws with the deterministic SVG toolkit
(foundation/render/charts.py), is honest that peer figures are ACTUAL SCT-disclosed pay (not target
opportunity), fails closed, and stops at a human approval gate — because pay decisions are the
committee's, not the model's.

    python3 run.py                                              # draft only
    python3 run.py --publish                                    # refused: needs a named committee approver
    python3 run.py --publish --approved-by "Compensation Committee Chair"

Standard library only; deterministic; offline. The PEER figures are real public-company proxy pay (a
dated, illustrative snapshot; provenance in governance/proxy-comp-data.md); only the subject (Acme) is
synthetic.
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
AS_OF = "FY2026"
PERIOD = "FY2026 proxy season · real public peers · illustrative snapshot"
AGENT = "executive-comp-benchmarking"
SCOPE = "publish.exec_comp_benchmarking"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

# the pay element chosen as the marquee (the single most-scrutinized number in a proxy)
HERO_ROLE, HERO_ELEMENT = "CEO", "tdc"
_CASH_ELEMENTS = ("base", "sti", "total_cash")
_EQUITY_ELEMENTS = ("ltie", "tdc")
# a benchmarked role above the engine's MIN_PEER_N floor but with few peers is still THIN — a single
# incumbent choice can swing a quartile — so it carries a "read with care" caveat (transparency, not
# suppression). Roles at/above this many peer observations render without the caveat.
_THIN_PEER_N = 10


class ReportError(RuntimeError):
    """Raised when the benchmarking view cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_benchmark():
    from foundation.compute.benchmarking import benchmark
    return benchmark()


def _e(v):
    import html
    return html.escape(str(v))


def _money(usd) -> str:
    """Compact comp money: $8.47M / $675K (proxy pay lives in the $100K–$80M range)."""
    usd = float(usd)
    if usd >= 1_000_000:
        return f"${usd / 1_000_000:.2f}M"
    return f"${round(usd / 1_000)}K"


# ---------------------------------------------------------------- compute (no positioning math here)
def build_report(result):
    positions = result.get("positions", [])
    if not positions:
        raise ReportError("benchmarking returned no positions — a pay-positioning view must not ship empty")

    roles = result["roles_benchmarked"]
    elements = result["elements"]                        # [{key,label,band:[lo,hi]}...] — the policy, from the engine
    el_order = [e["key"] for e in elements]
    el_label = {e["key"]: e["label"] for e in elements}
    el_band = {e["key"]: tuple(e["band"]) for e in elements}

    by_rc = {(p["role"], p["element"]): p for p in positions}
    below = [p for p in positions if p["status"] == "below"]
    below_sorted = sorted(below, key=lambda p: (-p["gap"], p["role"], el_order.index(p["element"])))

    # the honest story: is cash competitive while equity lags (or the reverse)?
    cash = [p for p in positions if p["element"] in _CASH_ELEMENTS]
    equity = [p for p in positions if p["element"] in _EQUITY_ELEMENTS]
    cash_below = [p for p in cash if p["status"] == "below"]
    equity_below = [p for p in equity if p["status"] == "below"]
    # where the below-target positions actually cluster — derived from the data, NEVER hardcoded, so the
    # narrative can't claim "concentrated in equity" when it isn't (the below set could be cash, or mixed).
    concentration = _concentration(below, equity_below)

    hero = by_rc.get((HERO_ROLE, HERO_ELEMENT)) or positions[0]

    report = {
        "result": result, "positions": positions, "roles": roles, "elements": elements,
        "el_order": el_order, "el_label": el_label, "el_band": el_band, "by_rc": by_rc,
        "below": below, "below_sorted": below_sorted,
        "cash": cash, "equity": equity, "cash_below": cash_below, "equity_below": equity_below,
        "concentration": concentration, "hero": hero,
        "n_peers": result["n_peers_total"], "n_positions": result["n_positions"],
        "n_below": result["n_below_target"], "suppressed": result["roles_suppressed"],
    }
    report["narrative"] = _narrative(report)
    return report


def _concentration(below, equity_below):
    """A data-driven phrase for WHERE the below-target positions cluster (equity / cash / mixed / none).
    Returned as (short, long) so the ribbon and digest tell the same, true story."""
    n, e = len(below), len(equity_below)
    if n == 0:
        return ("", "")
    if e == n:
        return ("all in long-term equity", "all in long-term equity (LTI &amp; total direct comp)")
    if e * 2 >= n:
        return ("concentrated in long-term equity", "concentrated in long-term equity (LTI &amp; total direct comp)")
    if e == 0:
        return ("all in annual cash", "all in annual cash (no equity position is below target)")
    return ("spread across cash and equity", "spread across cash and equity")


def _narrative(report):
    result = report["result"]
    n_below, n_pos, n_peers = report["n_below"], report["n_positions"], report["n_peers"]
    supp = report["suppressed"]
    cash_below, equity_below, hero = report["cash_below"], report["equity_below"], report["hero"]
    # honest phrasing: cash is "at or above target", not the softer "competitive" (it is ABOVE policy on
    # most cash positions here); only claim a shortfall the numbers support.
    cash_line = ("annual cash sits <b>at or above</b> the committee's target band across roles"
                 if not cash_below else
                 f"annual cash trails target in <b>{len(cash_below)}</b> cash positions")
    equity_line = (f"long-term equity (LTI &amp; total direct comp) sits <b>below</b> the committee's target "
                   f"in <b>{len(equity_below)}</b> of the equity positions" if equity_below else
                   "long-term equity is at or above target")
    supp_line = ""
    if supp:
        names = ", ".join(_e(s["role"]) for s in supp)     # escape: role text is inserted as HTML
        supp_line = (f" <span class='warn'>{names} {'is' if len(supp) == 1 else 'are'} suppressed — too few "
                     f"peers disclose the role to position it honestly.</span>")
    conc = report["concentration"][1]
    conc_clause = f", {conc}" if conc else ""
    return (f"Positioned <b>{COMPANY}</b>'s <b>{len(report['roles'])} benchmarked NEOs</b> across <b>{n_pos} pay "
            f"positions</b> against <b>{n_peers} real peers'</b> SEC-disclosed proxy pay. "
            f"The headline: <span class='up'>{cash_line}</span>, but {equity_line} — "
            f"<b>{n_below} of {n_pos}</b> positions land below target{conc_clause}. "
            f"{COMPANY}'s CEO total direct comp sits at the <b>{ch.ordinal(round(hero['percentile']))} percentile</b> "
            f"(peer median <b>{_money(hero['peer_median'])}</b>).{supp_line} "
            f"<span class='warn'>Peer figures are actual SCT-disclosed pay, not target opportunity.</span>")


# ---------------------------------------------------------------- presentation (charts + layout)
_STATUS_COL = {"below": "var(--red)", "within": "var(--green)", "above": "var(--amber)"}
_STATUS_HEX = {"below": ch.RED, "within": ch.GREEN, "above": ch.AMBER}


def _tile(title, sub, chart, extra="", scope="Peer group"):
    return (f"<div class='tile'><div class='t-head'><div><h3>{_e(title)}</h3>"
            f"<div class='t-sub'>{_e(sub)}</div></div><span class='t-scope'>{_e(scope)}</span></div>"
            f"<div class='chart'>{chart}</div>{extra}</div>")


def _legend(items):
    return "<div class='legend'>" + "".join(
        f"<span><i style='background:{_e(c)}'></i>{_e(l)}</span>" for l, c in items) + "</div>"


def _posbar(pct, lo, hi, status):
    """Compact percentile-position bar (id-free SVG): a 0–100 track, the committee's target band shaded,
    and the subject's percentile needle coloured by status. Presentation of the engine's numbers only."""
    w, h = 168.0, 18.0
    x0, x1 = 3.0, w - 3.0
    X = lambda v: x0 + (x1 - x0) * max(0.0, min(100.0, float(v))) / 100.0
    col = _STATUS_HEX[status]
    b = [f"<svg viewBox='0 0 {int(w)} {int(h)}' width='{int(w)}' height='{int(h)}' "
         f"xmlns='http://www.w3.org/2000/svg' class='pbar'>"]
    b.append(f"<rect x='{x0:.1f}' y='6' width='{x1 - x0:.1f}' height='6' rx='3' fill='rgba(141,177,206,.18)'/>")
    # target band
    b.append(f"<rect x='{X(lo):.1f}' y='4.5' width='{max(2.0, X(hi) - X(lo)):.1f}' height='9' rx='2' "
             f"fill='rgba(67,212,119,.22)' stroke='rgba(67,212,119,.55)' stroke-width='.6'/>")
    # median reference tick
    b.append(f"<line x1='{X(50):.1f}' y1='3' x2='{X(50):.1f}' y2='15' stroke='rgba(141,177,206,.45)' stroke-width='.8'/>")
    # subject needle
    nx = X(pct)
    b.append(f"<line x1='{nx:.1f}' y1='2' x2='{nx:.1f}' y2='16' stroke='{col}' stroke-width='2.4'/>")
    b.append(f"<circle cx='{nx:.1f}' cy='9' r='3.1' fill='{col}' stroke='#06131d' stroke-width='1'/>")
    b.append("</svg>")
    return "".join(b)


def _hero_strip(pct, lo, hi, label):
    """The marquee: the subject's percentile on a 0–100 track with the committee's target band SHADED.
    Band-edge labels sit ABOVE the bar and the subject needle label BELOW it, so they can never collide
    (a plain percentile tick would overprint the needle when the value is near a band edge)."""
    w, h = 1000.0, 120.0
    x0, x1, by, bh = 22.0, 978.0, 62.0, 20.0
    X = lambda v: x0 + (x1 - x0) * max(0.0, min(100.0, float(v))) / 100.0
    mono = "font-family=\"'JetBrains Mono',monospace\""
    b = ["<defs><linearGradient id='herotrack' x1='0' y1='0' x2='1' y2='0'>"
         "<stop offset='0' stop-color='#0c2233'/><stop offset='.5' stop-color='#0e5f86'/>"
         f"<stop offset='1' stop-color='{ch.CYAN}'/></linearGradient>"
         "<filter id='heroglow' x='-50%' y='-50%' width='200%' height='200%'>"
         "<feGaussianBlur stdDeviation='3.2' result='bl'/><feMerge>"
         "<feMergeNode in='bl'/><feMergeNode in='SourceGraphic'/></feMerge></filter></defs>"]
    b.append(f"<rect x='{x0}' y='{by - bh/2}' width='{x1 - x0}' height='{bh}' rx='{bh/2}' fill='url(#herotrack)' opacity='.9'/>")
    # shaded target band
    b.append(f"<rect x='{X(lo):.1f}' y='{by - bh/2 - 2}' width='{X(hi) - X(lo):.1f}' height='{bh + 4}' rx='4' "
             f"fill='rgba(67,212,119,.20)' stroke='rgba(67,212,119,.6)' stroke-width='1'/>")
    # end labels + band-edge labels ABOVE the bar
    b.append(f"<text x='{x0}' y='{by - bh/2 - 10}' {mono} font-size='11' fill='{ch.SOFT}'>P0</text>")
    b.append(f"<text x='{x1}' y='{by - bh/2 - 10}' text-anchor='end' {mono} font-size='11' fill='{ch.SOFT}'>P100</text>")
    for edge in (lo, hi):
        b.append(f"<text x='{X(edge):.1f}' y='{by - bh/2 - 10}' text-anchor='middle' {mono} font-size='10.5' "
                 f"font-weight='700' fill='{ch.GREEN}'>target P{edge}</text>")
    # median reference tick
    b.append(f"<line x1='{X(50):.1f}' y1='{by - bh/2 - 3}' x2='{X(50):.1f}' y2='{by + bh/2 + 3}' "
             f"stroke='rgba(141,177,206,.5)' stroke-width='1'/>")
    # subject needle + label BELOW the bar
    nx = X(pct)
    b.append(f"<g filter='url(#heroglow)'><line x1='{nx:.1f}' y1='{by - bh/2 - 2}' x2='{nx:.1f}' y2='{by + bh/2 + 2}' "
             f"stroke='#fff' stroke-width='3'/><circle cx='{nx:.1f}' cy='{by}' r='7.5' fill='#fff' "
             f"stroke='{ch.CYAN}' stroke-width='3'/></g>")
    fw = 132.0
    fx = min(max(nx - fw / 2, x0), x1 - fw)
    b.append(f"<rect x='{fx:.1f}' y='{by + bh/2 + 8}' width='{fw}' height='23' rx='6' fill='#06222f' "
             f"stroke='{ch.CYAN}' stroke-width='1'/>")
    b.append(f"<text x='{fx + fw/2:.1f}' y='{by + bh/2 + 23.5}' text-anchor='middle' {mono} font-size='11.5' "
             f"font-weight='700' fill='{ch.CYAN2}'>{_e(label)} · P{pct:.0f}</text>")
    return f"<svg viewBox='0 0 {int(w)} {int(h)}' width='100%' xmlns='http://www.w3.org/2000/svg'>" + "".join(b) + "</svg>"


def _status_chip(status, gap):
    label = {"below": "below", "within": "on target", "above": "above"}[status]
    g = f" · {gap:.0f}pt" if gap else ""
    return f"<span class='schip {status}'>{_e(label)}{g}</span>"


def _matrix(report):
    """Role × element status matrix — the whole positioning on one grid (below/on-target/above)."""
    els = report["elements"]
    head = "".join(f"<th title='target P{b[0]}-{b[1]}'>{_e(_short_el(e['label']))}</th>"
                   for e in els for b in [e["band"]])
    rows = []
    for role in report["roles"]:
        cells = []
        for e in els:
            p = report["by_rc"].get((role, e["key"]))
            if p is None:
                cells.append("<td class='mcell na'>–</td>")
                continue
            cells.append(f"<td class='mcell {p['status']}' title='P{p['percentile']:.0f} vs target "
                         f"P{p['target_lo']}-{p['target_hi']}'>P{p['percentile']:.0f}</td>")
        rows.append(f"<tr><th class='rl'>{_e(role)}</th>{''.join(cells)}</tr>")
    return ("<table class='matrix'><thead><tr><th class='rl'></th>" + head + "</tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _short_el(label):
    return {"Base salary": "Base", "Annual cash incentive": "Cash inc.", "Total cash (actual)": "Total cash",
            "LTI / equity": "LTI", "Total direct comp": "TDC"}.get(label, label)


def _gap_list(report):
    """Largest below-target gaps as horizontal bars — makes the equity shortfall pop."""
    below = report["below_sorted"]
    if not below:
        return "<div class='t-sub'>No position is below the committee's target band.</div>"
    gmax = max(p["gap"] for p in below) or 1
    rows = []
    for p in below:
        w = max(4, round(100 * p["gap"] / gmax))
        rows.append(
            f"<div class='gaprow'><span class='gl mono'>{_e(p['role'])} · {_e(_short_el(report['el_label'][p['element']]))}</span>"
            f"<span class='gbar'><span style='width:{w}%'></span></span>"
            f"<span class='gv mono'>P{p['percentile']:.0f} → target P{p['target_lo']} · {p['gap']:.0f}pt</span></div>")
    return "<div class='gaps'>" + "".join(rows) + "</div>"


def _role_table(report, role):
    rows = []
    for key in report["el_order"]:
        p = report["by_rc"].get((role, key))
        if p is None:
            continue
        lo, hi = report["el_band"][key]
        rows.append(
            f"<tr><td class='nm'>{_e(report['el_label'][key])}</td>"
            f"<td class='mono r'>{_e(_money(p['subject_value']))}</td>"
            f"<td class='pos'>{_posbar(p['percentile'], lo, hi, p['status'])}"
            f"<span class='pctag mono'>P{p['percentile']:.0f}</span></td>"
            f"<td class='mono r muted'>{_e(_money(p['peer_p25']))}</td>"
            f"<td class='mono r'>{_e(_money(p['peer_median']))}</td>"
            f"<td class='mono r muted'>{_e(_money(p['peer_p75']))}</td>"
            f"<td class='mono r sm'>P{p['target_lo']}–{p['target_hi']}</td>"
            f"<td>{_status_chip(p['status'], p['gap'])}</td></tr>")
    return (
        "<table class='ptable postbl'><thead><tr>"
        "<th>Pay element</th><th class='r'>" + _e(COMPANY) + "</th>"
        "<th title='0–100 percentile of the peer distribution; green band = committee target'>Position vs peers</th>"
        "<th class='r'>Peer P25</th><th class='r'>Median</th><th class='r'>P75</th>"
        "<th class='r'>Target</th><th>Call</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def render_html(report):
    result = report["result"]
    hero = report["hero"]
    body = []
    body.append("<header class='topbar'>"
                f"<div class='brandwrap'>{MARK_SVG}"
                "<div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                "<div class='brand-sub'>Executive Compensation</div></div></div>"
                "<div class='title'><h1>Pay Positioning vs Peers — Compensation Committee</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                "<div class='spacer'></div>"
                "<span class='status'>Draft · awaiting committee approval</span></header>")

    body.append("<section class='insight'>"
                "<svg class='glyph' viewBox='0 0 24 24'><path d='M12 2 L13.7 8.3 L20 10 L13.7 11.7 L12 18 "
                "L10.3 11.7 L4 10 L10.3 8.3 Z' fill='#1ba7ff'/><circle cx='18.5' cy='4.5' r='1.6' fill='#f7b955'/>"
                "<circle cx='5' cy='17' r='1.2' fill='#7c8cff'/></svg>"
                "<div><div class='tag'>Generated insight · positioning vs real peer proxy pay</div>"
                f"<p>{report['narrative']}</p></div></section>")

    # hero — the CEO total-direct-comp percentile on a 0–100 scale, with the target band + median
    lo, hi = report["el_band"][HERO_ELEMENT]
    facts = [("Peers", str(report["n_peers"])), ("Roles", str(len(report["roles"]))),
             ("Positions", str(report["n_positions"])), ("Below target", str(report["n_below"]))]
    factrow = "".join(f"<div class='sf'><span class='sf-v mono'>{_e(v)}</span>"
                      f"<span class='sf-l'>{_e(l)}</span></div>" for l, v in facts)
    strip = _hero_strip(hero["percentile"], lo, hi, f"{HERO_ROLE} TDC")
    body.append("<section class='beacon'><div class='head'>"
                f"<div><div class='label'>Headline · {_e(HERO_ROLE)} total direct comp</div>"
                f"<div class='hero'><span class='v mono'>{_e(_money(hero['subject_value']))}</span>"
                f"<span class='pct'>{ch.ordinal(round(hero['percentile']))} percentile of {report['n_peers']} peers "
                f"· target P{lo}–{hi}</span></div></div>"
                f"<div class='subjfacts'>{factrow}</div></div>"
                f"<div class='chart'>{strip}"
                "<div class='cap'>Percentile of the peer distribution (real public peers, actual SCT pay); "
                "green band is the committee's target policy.</div></div></section>")

    # grid: status matrix + gap-to-target
    grid = []
    grid.append("<div class='col-6'>" + _tile(
        "Positioning at a glance", "Every role × pay element · percentile vs target band",
        _matrix(report),
        _legend([("Below target", ch.RED), ("On target", ch.GREEN), ("Above target", ch.AMBER)]),
        scope="All roles") + "</div>")
    grid.append("<div class='col-6'>" + _tile(
        "Gap to target", "Below-band positions, widest shortfall first",
        _gap_list(report), scope="Shortfalls") + "</div>")
    body.append("<section class='grid'>" + "".join(grid) + "</section>")

    # per-role positioning tables. The scope badge reads "Positioning", NOT "Recommend-only" — this agent
    # positions pay against the market; it never recommends a pay level (that is the committee's call).
    for role in report["roles"]:
        role_n = next((p["peer_n"] for p in report["positions"] if p["role"] == role), 0)
        thin = (" · <span class='thin'>thin peer set — read with care</span>"
                if role_n < _THIN_PEER_N else "")
        subtitle = (f"Each element vs the peer distribution · <b>{role_n} peers</b> disclose this role · "
                    f"actual SCT pay · committee target band shaded{thin}")
        body.append(f"<section class='tile wide'><div class='t-head'><div>"
                    f"<h3>{_e(role)} — pay positioning</h3><div class='t-sub'>{subtitle}</div>"
                    "</div><span class='t-scope'>Positioning</span></div>"
                    + _role_table(report, role) + "</section>")

    # suppressed roles — honest about thin disclosure
    if report["suppressed"]:
        min_n = _min_n()
        chips = "".join(
            f"<div class='supp'><span class='sup-r mono'>{_e(s['role'])}</span>"
            f"<span class='sup-n'>peer n = {s['peer_n']} &lt; {min_n}</span>"
            f"<span class='sup-w'>{_e(s['reason'])}</span></div>" for s in report["suppressed"])
        body.append("<section class='tile wide'><div class='t-head'><div>"
                    "<h3>Suppressed roles · thin peer disclosure</h3>"
                    "<div class='t-sub'>Too few peers disclose these roles to position them honestly — shown, "
                    "never given a spurious percentile</div></div>"
                    "<span class='t-scope'>Transparency</span></div>"
                    f"<div class='supprow'>{chips}</div></section>")

    body.append("<footer class='foot'>"
                f"<div>Built by the <b>{AGENT}</b> agent · it <b>positions</b> pay against real peer proxy "
                "disclosure; the <b>Compensation Committee</b> sets pay. The agent runs no positioning math — "
                "every percentile, quartile and call comes from the shared benchmarking engine.</div>"
                "<div class='pills'>"
                "<span class='pill'>Real peers · synthetic subject</span>"
                f"<span class='pill'>{report['n_peers']} peers · {len(report['roles'])} roles</span>"
                "<span class='pill'>Actual SCT pay (not target)</span>"
                "<span class='pill'>Committee approves</span></div></footer>")

    return _page("".join(body))


def _min_n():
    from foundation.compute.benchmarking import MIN_PEER_N
    return MIN_PEER_N


def render_digest(report):
    result = report["result"]
    hero = report["hero"]
    supp = report["suppressed"]
    lines = [
        f"# {COMPANY} — Executive Comp Benchmarking (Compensation Committee) digest",
        f"_{PERIOD} · draft for committee review_", "",
        f"- Positioned **{COMPANY}**'s **{len(report['roles'])} benchmarked NEOs** "
        f"({', '.join(report['roles'])}) across **{report['n_positions']} pay positions** vs "
        f"**{report['n_peers']} real peers'** SEC-disclosed proxy pay.",
        f"- **{report['n_below']} of {report['n_positions']}** positions are **below** the committee's "
        f"target band"
        + (f" — {report['concentration'][0].replace('&amp;', '&')}" if report['concentration'][0] else "")
        + f"; annual cash is {'at or above target' if not report['cash_below'] else 'mixed vs target'}.",
        f"- {HERO_ROLE} total direct comp **{_money(hero['subject_value'])}** sits at the "
        f"**~{ch.ordinal(round(hero['percentile']))} percentile** (peer median **{_money(hero['peer_median'])}**; "
        f"target P{report['el_band'][HERO_ELEMENT][0]}–{report['el_band'][HERO_ELEMENT][1]}).",
    ]
    if report["below_sorted"]:
        widest = report["below_sorted"][0]
        lines.append(
            f"- Widest shortfall: **{widest['role']} {report['el_label'][widest['element']]}** at "
            f"**P{widest['percentile']:.0f}** vs target **P{widest['target_lo']}** "
            f"(**{widest['gap']:.0f}pt** below the band).")
    if supp:
        supp_names = ", ".join(f"{s['role']} (n={s['peer_n']})" for s in supp)
        lines.append(
            f"- Suppressed (thin peer disclosure, n &lt; {_min_n()}): "
            f"**{supp_names}** — shown, not given a spurious percentile.")
    lines += [
        "",
        "_Peer figures are **actual SCT-disclosed** proxy pay (equity at grant-date fair value), **not** "
        "target opportunity; positioned against the committee's target-percentile policy._",
        "_Real public-company peers (as-disclosed public financials, an illustrative snapshot — see "
        "governance/proxy-comp-data.md); the subject (Acme) is synthetic._",
        "_The agent POSITIONS pay; the Compensation Committee sets it (human-in-the-loop). Publish gate: a "
        "named committee approver must sign off._",
    ]
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
:root{--bg:#000;--panel:#060d15;--panel2:#0a141f;--text:#eef7ff;--muted:#8db1ce;--soft:#6d8294;
--cyan:#1ba7ff;--cyan2:#48c7ff;--green:#43d477;--red:#ff4d4f;--amber:#f7b955;--indigo:#7c8cff;
--line:rgba(27,167,255,.30);--hair:rgba(141,177,206,.16);}
*{box-sizing:border-box;}
body{margin:0;background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),var(--bg);
color:var(--text);font-family:Inter,-apple-system,'Segoe UI',sans-serif;font-size:14px;line-height:1.45;padding:24px;}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums;}
.wrap{max-width:1280px;margin:0 auto;}
.topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;border-bottom:2px solid var(--cyan);padding-bottom:16px;}
.brandwrap{display:flex;align-items:center;gap:12px;min-width:0;}
.logomark{height:42px;width:auto;flex:0 0 auto;display:block;}
.brand{font-weight:800;font-size:18px;color:#fff;}.brand .os{color:var(--cyan);}
.brand-sub{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--soft);margin-top:2px;}
.title h1{margin:0;font-size:20px;color:#fff;font-weight:800;letter-spacing:-.01em;}
.title .meta{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);margin-top:3px;}
.spacer{flex:1;}
.status{border:1px solid rgba(247,185,85,.5);color:var(--amber);background:rgba(247,185,85,.12);font-size:11px;font-weight:800;padding:5px 12px;border-radius:999px;text-transform:uppercase;}
.insight{display:flex;gap:13px;align-items:flex-start;margin:18px 0;background:linear-gradient(98deg,rgba(27,167,255,.12),rgba(27,167,255,.03) 65%,transparent);
border:1px solid var(--line);border-left:3px solid var(--cyan);border-radius:12px;padding:14px 17px;}
.insight .glyph{flex:0 0 auto;width:24px;height:24px;margin-top:2px;}
.insight .tag{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--cyan2);font-weight:700;margin-bottom:4px;}
.insight p{margin:0;font-size:14.5px;line-height:1.5;}.insight .up{color:var(--green);font-weight:600;}.insight .warn{color:var(--amber);font-weight:600;}
.beacon{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px 22px 14px;margin-bottom:16px;box-shadow:0 14px 40px rgba(0,0,0,.5);}
.beacon .head{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:16px;}
.beacon .label{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);}
.beacon .hero{display:flex;align-items:baseline;gap:12px;margin-top:3px;flex-wrap:wrap;}
.beacon .hero .v{font-size:34px;font-weight:800;letter-spacing:-.02em;line-height:1;color:#fff;}
.beacon .hero .pct{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--cyan2);background:rgba(27,167,255,.14);border:1px solid var(--line);padding:3px 9px;border-radius:999px;}
.subjfacts{display:flex;gap:22px;flex-wrap:wrap;}
.sf{display:flex;flex-direction:column;text-align:right;}
.sf-v{font-size:17px;font-weight:800;color:#fff;}
.sf-l{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);margin-top:2px;}
.cap{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--soft);margin-top:6px;}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;}
.tile{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px 14px;display:flex;flex-direction:column;}
.tile.wide{margin-top:16px;}
.col-6{grid-column:span 6;}
.t-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:2px;}
.t-head h3{margin:0;font-size:14.5px;color:#fff;font-weight:700;}
.t-sub{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--soft);margin-top:3px;}
.t-sub .thin{color:var(--amber);font-weight:700;}
.t-scope{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);border:1px solid var(--hair);border-radius:999px;padding:3px 9px;white-space:nowrap;}
.chart{margin-top:10px;}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);}
.legend span{display:inline-flex;align-items:center;gap:6px;}.legend i{width:10px;height:10px;border-radius:3px;}
.matrix{width:100%;border-collapse:collapse;margin-top:4px;font-family:'JetBrains Mono',monospace;}
.matrix th{font-size:9px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);padding:6px 4px;text-align:center;font-weight:700;}
.matrix th.rl{text-align:left;}
.matrix .rl{color:var(--cyan2);font-weight:700;font-size:12px;padding-left:2px;text-align:left;}
.matrix .mcell{text-align:center;font-size:11px;font-weight:700;padding:7px 4px;border-radius:5px;border:1px solid transparent;}
.matrix .mcell.below{color:#ffd0d0;background:rgba(255,77,79,.16);border-color:rgba(255,77,79,.4);}
.matrix .mcell.within{color:#c6f4d8;background:rgba(67,212,119,.15);border-color:rgba(67,212,119,.4);}
.matrix .mcell.above{color:#ffe6bd;background:rgba(247,185,85,.15);border-color:rgba(247,185,85,.4);}
.matrix .mcell.na{color:var(--soft);}
.gaps{display:flex;flex-direction:column;gap:9px;margin-top:6px;}
.gaprow{display:grid;grid-template-columns:130px 1fr auto;align-items:center;gap:10px;}
.gaprow .gl{font-size:10.5px;color:var(--text);}
.gbar{height:8px;background:rgba(141,177,206,.16);border-radius:5px;overflow:hidden;}
.gbar>span{display:block;height:8px;background:linear-gradient(90deg,#ff4d4f,#ff8a5b);border-radius:5px;}
.gaprow .gv{font-size:9.5px;color:var(--soft);white-space:nowrap;}
.ptable{width:100%;border-collapse:collapse;margin-top:12px;font-size:12.5px;}
.ptable th{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);font-weight:700;}
.ptable td{padding:7px 10px;border-bottom:1px solid var(--hair);color:var(--text);}
.ptable tbody tr:hover{background:rgba(27,167,255,.05);}
.ptable .r,.ptable th.r{text-align:right;}
.ptable .nm{color:#fff;font-weight:600;}
.ptable .muted{color:var(--muted);}
.ptable .sm{font-size:10.5px;color:var(--soft);}
.postbl .pos{min-width:210px;}
.postbl .pbar{vertical-align:middle;}
.postbl .pctag{margin-left:9px;font-size:11px;color:var(--cyan2);}
.schip{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;white-space:nowrap;}
.schip.below{color:#ffd0d0;background:rgba(255,77,79,.16);border:1px solid rgba(255,77,79,.4);}
.schip.within{color:#c6f4d8;background:rgba(67,212,119,.15);border:1px solid rgba(67,212,119,.4);}
.schip.above{color:#ffe6bd;background:rgba(247,185,85,.15);border:1px solid rgba(247,185,85,.4);}
.supprow{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;}
.supp{display:flex;flex-direction:column;gap:2px;border:1px dashed var(--hair);border-radius:10px;padding:10px 14px;background:rgba(141,177,206,.05);}
.sup-r{font-size:15px;font-weight:800;color:#fff;}
.sup-n{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--amber);}
.sup-w{font-family:'JetBrains Mono',monospace;font-size:9.5px;color:var(--soft);}
.foot{margin-top:22px;padding-top:14px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--soft);}
.foot .pills{display:flex;gap:8px;flex-wrap:wrap;}.foot .pill{border:1px solid var(--hair);border-radius:999px;padding:3px 9px;}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — Executive Comp Benchmarking</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _fail_closed(message) -> int:
    # quarantine any prior good report so nothing stale-but-valid-looking is left live. Best-effort: if the
    # output dir itself is unwritable we cannot rename/delete — say so on the one error line instead of
    # pretending it was quarantined.
    stuck = []
    for p in (REPORT, DIGEST, OUT / "PUBLISHED.json"):
        if not p.exists():
            continue
        try:
            p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            try:
                p.unlink()
            except OSError:
                stuck.append(p.name)
    note = f" (WARNING: could not quarantine {', '.join(stuck)} — output dir unwritable)" if stuck else ""
    print(f"FAIL CLOSED: {_one_line(message)}{note}", file=sys.stderr)
    return 1


def _stage(path: Path, text: str) -> Path:
    """Write text to path's .tmp sibling and return it. The caller os.replace()s the staged files in one
    final step, so a write error can't leave a live file half-written."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    return tmp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp executive-comp benchmarking (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Publishing a pay-positioning view requires a named committee approver "
              "(Compensation Committee).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        result = _load_benchmark()
        report = build_report(result)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"benchmarking data unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_json = (json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF,
                            "roles_benchmarked": report["roles"], "positions": report["n_positions"],
                            "below_target": report["n_below"]}, indent=2) + "\n")
    try:
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST, pub_path):     # clear any prior .stale quarantine for what we (re)write
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        # STAGE everything to .tmp first — a write error here aborts before ANY live file is touched.
        rep_tmp = _stage(REPORT, html_doc)
        dig_tmp = _stage(DIGEST, digest_doc)
        pub_tmp = _stage(pub_path, pub_json) if args.publish else None
        # COMMIT with os.replace: report + digest, then the approval marker LAST. A crash mid-commit can
        # only leave a fresh report/digest WITHOUT a PUBLISHED.json (reads as a draft) — never a published
        # marker without its report. A redrawn DRAFT removes any prior approval INSIDE this guarded block,
        # so a failure there degrades to one clean fail-closed line instead of an uncaught crash.
        os.replace(rep_tmp, REPORT)
        os.replace(dig_tmp, DIGEST)
        if args.publish:
            os.replace(pub_tmp, pub_path)
        else:
            pub_path.unlink(missing_ok=True)
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    print(f"{COMPANY} Executive Comp — Benchmarking ({AS_OF})")
    print(f"  {report['n_positions']} positions across {len(report['roles'])} roles vs "
          f"{report['n_peers']} peers | {report['n_below']} below target | "
          f"{HERO_ROLE} TDC at ~{ch.ordinal(round(report['hero']['percentile']))} pctile")
    if report["suppressed"]:
        print(f"  suppressed (thin peer disclosure): {', '.join(s['role'] for s in report['suppressed'])}")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nBenchmarking approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. The Compensation Committee sets pay. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
