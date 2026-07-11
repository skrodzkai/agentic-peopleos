#!/usr/bin/env python3
"""Acme Corp — Executive Compensation Peer Group Builder (Compensation Committee view).

The first agent of the Executive Compensation arm. It composes a dark, board-ready dashboard that
builds a defensible executive-comp peer group in the two steps a committee actually uses:

  1. SCREEN (the gate) — a hard, transparent size + industry screen (0.5–2.0× of the subject on
     revenue and market cap each 0.5–2.0×, membership in the documented software/SaaS peer group;
     headcount is a soft fit factor, not a gate). Membership is decided here and is
     defensible on one line.
  2. FIT-RANK (the order) — within that group, peers are ranked by a pure revenue-weighted
     size-closeness score. The score ORDERS the group into a recommended core + a substitution
     watchlist; it never decides membership.

Like every arm agent it is PRESENTATION + GOVERNANCE ONLY: it runs no screening or ranking math
(every PASS/FAIL and fit score comes from foundation/compute/peers.py), draws with the deterministic
SVG toolkit (foundation/render/charts.py), is transparent about every exclusion, carries the
committee's target-percentile policy forward to the benchmarking arm, fails closed, and stops at a
human approval gate — because finalizing the peer group is a committee decision, not the model's.

    python3 run.py                                              # draft only
    python3 run.py --publish                                    # refused: needs a named committee approver
    python3 run.py --publish --approved-by "Compensation Committee Chair"

Standard library only; deterministic; offline. The candidate PEERS are real public companies with
as-disclosed public financials (a dated, illustrative snapshot; provenance in governance/real-peer-data.md);
only the subject (Acme) is synthetic.
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

from foundation import evidence_portfolio as portfolio_ev  # noqa: E402
from foundation.render import charts as ch                 # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "FY2026"
PERIOD = "FY2026 proxy season · real public peers · illustrative snapshot"
AGENT = "executive-comp-peer-builder"
SCOPE = "publish.exec_comp_peer_group"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")
CORE_N = 12          # the recommended core peer group; in-band peers beyond it become the watchlist

# The committee's target-percentile policy as RANGES (disclosed practice is a band centered near the
# median, not a single point), carried forward to the benchmarking arm AFTER the peer group is approved.
# ILLUSTRATIVE committee policy (a governance input), not a market dataset — the screen never recommends
# pay; benchmarking begins only once a human approves the peer group.
TARGET_PERCENTILES = [
    ("Base salary", "P45–55"),
    ("Target STI / bonus", "P50–60"),
    ("Total target cash", "P50–60"),
    ("LTI / equity (target)", "P50–65"),
    ("Total direct comp", "P50–65"),
]


class ReportError(RuntimeError):
    """Raised when the peer-group view cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_universe():
    from foundation.compute.peers import PeerUniverse
    return PeerUniverse()


def _e(v):
    import html
    return html.escape(str(v))


def _money(usd) -> str:
    """Compact, board-style money: $1.2B / $663M / $50M."""
    usd = float(usd)
    if usd >= 1_000_000_000:
        return f"${usd / 1_000_000_000:.1f}B"
    return f"${round(usd / 1_000_000)}M"


def _mult(co, subj) -> str:
    return f"{co / subj:.1f}×"


def _quantile(sorted_vals, q):
    """Linear-interpolated quantile of a pre-sorted list (presentation stat, not a screen decision)."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(pos)
    if lo + 1 >= n:
        return float(sorted_vals[-1])
    return sorted_vals[lo] + (sorted_vals[lo + 1] - sorted_vals[lo]) * (pos - lo)


# ---------------------------------------------------------------- compute (no screening/ranking math here)
def build_report(universe):
    res = universe.screen()                              # the gate AND the fit-rank are entirely peers.py
    subj = res["subject"]
    peers = res["peers"]                                 # already fit-ranked, best size match first
    if not peers:
        raise ReportError("the screen returned no peers — a peer group view must not ship empty")

    sub_ind = subj["gics_subindustry"]
    # the industry gate is GROUP membership (software/SaaS), which spans several GICS sub-industries — count
    # by the actual gics check the screener recorded, not an exact sub-industry match
    same_industry = [r for r in res["results"] if r["checks"].get("gics", False)]
    n_universe = res["n_candidates"]
    n_same = len(same_industry)
    n_peers = res["n_peers"]
    excl_industry = n_universe - n_same                  # filtered out by the industry-group screen
    excl_size = n_same - n_peers                          # in the software/SaaS group, outside the size band

    core = peers[:CORE_N]
    watchlist = peers[CORE_N:]                            # in-band alternates for committee substitution

    peer_revs = sorted(p["company"]["revenue_usd"] for p in peers)
    peer_mcaps = sorted(p["company"]["market_cap_usd"] for p in peers)
    median_rev = _quantile(peer_revs, 0.5)
    below = sum(1 for v in peer_revs if v < subj["revenue_usd"])
    subj_pctile = round(100 * below / n_peers)

    # Exclusions: EVERY non-peer, each with its failing criterion — in-group size-outs first (highest
    # pass_count), then any out-of-group names last. Rendering all of them keeps the "every exclusion on
    # the record" claim honest.
    near = [r for r in res["results"] if not r["is_peer"]]
    near.sort(key=lambda r: (-r["pass_count"], not r["checks"].get("gics", False),
                             abs(r["company"]["revenue_usd"] - subj["revenue_usd"])))

    return {
        "res": res, "subject": subj, "peers": peers, "core": core, "watchlist": watchlist,
        "sub_industry": sub_ind, "n_universe": n_universe, "n_same": n_same, "n_peers": n_peers,
        "excl_industry": excl_industry, "excl_size": excl_size,
        "peer_revs": peer_revs, "peer_mcaps": peer_mcaps, "median_rev": median_rev,
        "subj_pctile": subj_pctile, "near_misses": near,   # ALL in-group size-outs (the claim is "every exclusion")
        "narrative": _narrative(subj, sub_ind, n_universe, n_peers, peers[0], median_rev, subj_pctile, excl_size),
    }


def _narrative(subj, sub_ind, n_universe, n_peers, top, median_rev, pctile, excl_size):
    tc = top["company"]
    n_core = min(CORE_N, n_peers)
    n_watch = n_peers - n_core
    return (f"Screened <b>{n_universe} public companies</b> to <b>{n_peers} in-band candidates</b> — "
            f"in the <b>software/SaaS peer group</b> (a documented set of GICS sub-industries; {_e(COMPANY)} "
            f"is <b>{_e(sub_ind)}</b>) and within <b>0.5–2.0×</b> of {_e(COMPANY)} on "
            f"<b>revenue and market cap</b> — then ranked by a revenue-weighted size fit into a "
            f"<b>{n_core}-company recommended core peer group</b> + a <b>{n_watch}-company watchlist</b> "
            f"(headcount, a soft factor, shapes the rank, not membership). "
            f"Closest match: <span class='up'><b>{_e(tc['company_name'])}</b> (fit {top['fit']:.0f})</span>. "
            f"{_e(COMPANY)}'s <b>{_money(subj['revenue_usd'])}</b> revenue sits near the <b>{ch.ordinal(pctile)} percentile</b> "
            f"of the in-band group (median <b>{_money(median_rev)}</b>). "
            f"<span class='warn'>The fit score orders the group; the screen — not the score — decides membership.</span>")


# ---------------------------------------------------------------- presentation (charts + layout)
def _tile(title, sub, chart, extra="", scope="Universe"):
    return (f"<div class='tile'><div class='t-head'><div><h3>{_e(title)}</h3>"
            f"<div class='t-sub'>{_e(sub)}</div></div><span class='t-scope'>{_e(scope)}</span></div>"
            f"<div class='chart'>{chart}</div>{extra}</div>")


def _legend(items):
    return "<div class='legend'>" + "".join(
        f"<span><i style='background:{_e(c)}'></i>{_e(l)}</span>" for l, c in items) + "</div>"


def _crit_chips(criteria):
    """Hard-gate chips (the criteria that decide membership) + a muted chip for the soft fit factor."""
    gics = {"group": "Software/SaaS peer group", "subindustry": "GICS sub-industry",
            "sector": "GICS sector"}.get(criteria["gics"], "Industry group")
    chips = []
    if criteria.get("revenue_mult"):
        lo, hi = criteria["revenue_mult"]
        chips.append((f"Revenue {lo:g}–{hi:g}×", "size"))
    if criteria.get("market_cap_mult"):
        mlo, mhi = criteria["market_cap_mult"]
        chips.append((f"Market cap {mlo:g}–{mhi:g}×", "size"))
    if criteria.get("employees_mult"):     # only if headcount is configured as a HARD gate
        elo, ehi = criteria["employees_mult"]
        chips.append((f"Headcount {elo:g}–{ehi:g}×", "size"))
    if criteria.get("gics") in ("sector", "subindustry"):
        chips.append((f"Same {gics}", "ind"))
    out = "".join(f"<span class='cchip {k}'>{_e(t)}</span>" for t, k in chips)
    if not criteria.get("employees_mult"):  # headcount is the soft factor — show it, label it as soft
        out += "<span class='cchip soft'>+ headcount · soft (ranks, doesn't gate)</span>"
    return "<div class='crit'>" + out + "</div>"


def _bucketize(values, n=5):
    lo, hi = min(values), max(values)
    width = ((hi - lo) or 1) / n
    counts = [0] * n
    for v in values:
        counts[min(int((v - lo) / width), n - 1)] += 1
    labels = [f"${round((lo + i * width) / 1_000_000)}–{round((lo + (i + 1) * width) / 1_000_000)}M"
              for i in range(n)]
    return counts, labels


def _peer_rows(rows, start_rank):
    out = []
    for i, p in enumerate(rows, start=start_rank):
        c = p["company"]
        fit = p["fit"]
        out.append(
            f"<tr><td class='num mono'>{i}</td><td class='tk mono'>{_e(c['ticker'])}</td>"
            f"<td class='nm'>{_e(c['company_name'])}</td>"
            f"<td class='mono r'>{_e(_money(c['revenue_usd']))}</td>"
            f"<td class='mono r'>{_e(_money(c['market_cap_usd']))}</td>"
            f"<td class='mono r'>{c['employees']}</td>"
            f"<td class='si'>{_e(c['gics_subindustry'])}</td>"
            f"<td class='fit'><div class='fitwrap'><span class='mono'>{fit:.0f}</span>"
            f"<span class='fitbar'><span style='width:{max(2, min(100, fit)):.0f}%'></span></span></div></td></tr>")
    return "".join(out)


def _peer_table(rows, start_rank=1):
    return (
        "<table class='ptable'><thead><tr>"
        "<th>#</th><th>Ticker</th><th>Company</th><th class='r'>Revenue</th><th class='r'>Mkt cap</th>"
        "<th class='r'>Empl</th><th>Sub-industry</th>"
        "<th title='revenue-weighted size closeness, 100 = identical size'>Size fit</th>"
        "</tr></thead><tbody>" + _peer_rows(rows, start_rank) + "</tbody></table>")


def _excl_table(report):
    subj = report["subject"]

    def mark(co, field, passed):   # hard-gate cell: pass/fail coloring
        return f"<span class='chk {'p' if passed else 'f'}'>{_mult(co[field], subj[field])}</span>"

    rows = []
    for r in report["near_misses"]:
        c = r["company"]
        rows.append(
            f"<tr><td class='tk mono'>{_e(c['ticker'])}</td><td class='nm'>{_e(c['company_name'])}</td>"
            f"<td class='mono r'>{_e(_money(c['revenue_usd']))}</td>"
            f"<td>{mark(c, 'revenue_usd', r['checks']['revenue'])}</td>"
            f"<td>{mark(c, 'market_cap_usd', r['checks']['market_cap'])}</td>"
            f"<td class='si'>{_e(c['gics_subindustry'])} "
            f"<span class='chk {'p' if r['checks'].get('gics') else 'f'}'>{'✓' if r['checks'].get('gics') else '✗'}</span></td>"
            f"<td class='soft mono'>{_mult(c['employees'], subj['employees'])}</td></tr>")
    if not rows:
        return "<div class='t-sub'>No exclusions — every screened company is a peer.</div>"
    return (
        "<table class='ptable excl'><thead><tr>"
        "<th>Ticker</th><th>Company</th><th class='r'>Revenue</th>"
        "<th>Rev ×</th><th>Cap ×</th><th>Industry</th>"
        "<th title='soft factor — ranks, does not gate'>Head × (soft)</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>")


def render_html(report):
    subj = report["subject"]
    n_peers, n_universe = report["n_peers"], report["n_universe"]
    pctile = report["subj_pctile"]
    rev_m = [v / 1_000_000 for v in report["peer_revs"]]
    acme_m = subj["revenue_usd"] / 1_000_000
    lo_m, hi_m = min(rev_m + [acme_m]), max(rev_m + [acme_m])
    q1, med, q3 = (_quantile(report["peer_revs"], q) / 1_000_000 for q in (0.25, 0.5, 0.75))

    body = []
    body.append("<header class='topbar'>"
                f"<div class='brandwrap'>{MARK_SVG}"
                "<div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                "<div class='brand-sub'>Executive Compensation</div></div></div>"
                "<div class='title'><h1>Peer Group Builder — Compensation Committee</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                "<div class='spacer'></div>"
                "<span class='status'>Draft · awaiting committee approval</span></header>")

    body.append("<section class='insight'>"
                "<svg class='glyph' viewBox='0 0 24 24'><path d='M12 2 L13.7 8.3 L20 10 L13.7 11.7 L12 18 "
                "L10.3 11.7 L4 10 L10.3 8.3 Z' fill='#1ba7ff'/><circle cx='18.5' cy='4.5' r='1.6' fill='#f7b955'/>"
                "<circle cx='5' cy='17' r='1.2' fill='#7c8cff'/></svg>"
                "<div><div class='tag'>Generated insight · screen of the public-company universe</div>"
                f"<p>{report['narrative']}</p></div></section>")

    facts = [("Revenue", _money(subj["revenue_usd"])), ("Market cap", _money(subj["market_cap_usd"])),
             ("Headcount", str(subj["employees"])), ("Industry", subj["gics_subindustry"])]
    factrow = "".join(f"<div class='sf'><span class='sf-v mono'>{_e(v)}</span>"
                      f"<span class='sf-l'>{_e(l)}</span></div>" for l, v in facts)
    body.append("<section class='beacon'><div class='head'>"
                f"<div><div class='label'>Subject company · {_e(subj['ticker'])}</div>"
                f"<div class='hero'><span class='v mono'>{_e(COMPANY)}</span>"
                f"<span class='pct'>~{ch.ordinal(pctile)} percentile of the {n_peers} in-band candidates</span></div></div>"
                f"<div class='subjfacts'>{factrow}</div></div>"
                "<div class='chart'>"
                # no `target=` here: the median is already a labeled tick, and an amber "target" marker
                # would read as a comp/pay target on a revenue-position strip (it isn't one)
                + ch.percentile_strip(acme_m, lo_m, hi_m,
                                      [(q1, f"25th · ${round(q1)}M"), (med, f"median · ${round(med)}M"),
                                       (q3, f"75th · ${round(q3)}M")],
                                      you_label=COMPANY, unit_prefix="$", unit_suffix="M")
                + "<div class='cap'>Revenue position within the screened peer group (real public peers; illustrative snapshot).</div></div></section>")

    grid = []
    funnel = ch.waterfall([("Universe", n_universe, "total"),
                           ("− Other industry", -report["excl_industry"], "sub"),
                           ("− Outside size", -report["excl_size"], "sub"),
                           ("In-band", n_peers, "total")])
    grid.append("<div class='col-6'>" + _tile(
        "Screen criteria & funnel", "Broad universe → industry → size band → peer group",
        funnel, _crit_chips(report["res"]["criteria"])
        + _legend([("Universe / group", ch.CYAN), ("Excluded", ch.RED)])) + "</div>")

    counts, labels = _bucketize(report["peer_revs"])
    lo, hi = min(report["peer_revs"]), max(report["peer_revs"])
    width = ((hi - lo) or 1) / 5
    acme_idx = min(int((subj["revenue_usd"] - lo) / width), 4) if lo <= subj["revenue_usd"] <= hi else None
    hl = {acme_idx: ch.CYAN} if acme_idx is not None else None
    dist = ch.histogram(counts, labels, highlight=hl)
    spread = (f"<div class='statrow'>"
              f"<div class='stat'><span class='s-v mono'>{_money(lo)}</span><span class='s-l'>smallest peer</span></div>"
              f"<div class='stat'><span class='s-v mono'>{_money(report['median_rev'])}</span><span class='s-l'>median peer</span></div>"
              f"<div class='stat'><span class='s-v mono'>{_money(hi)}</span><span class='s-l'>largest peer</span></div>"
              f"<div class='stat'><span class='s-v mono' style='color:var(--cyan2)'>{_money(subj['revenue_usd'])}</span>"
              f"<span class='s-l'>{_e(COMPANY)}</span></div></div>")
    grid.append("<div class='col-6'>" + _tile(
        "Peer size distribution", "Peers by revenue band · subject's band highlighted", dist, spread,
        scope="Peer group") + "</div>")
    body.append("<section class='grid'>" + "".join(grid) + "</section>")

    # core peer group (the recommendation) — fit-ranked
    body.append("<section class='tile wide'><div class='t-head'><div>"
                f"<h3>Recommended core peer group · {len(report['core'])} companies</h3>"
                "<div class='t-sub'>All clear the hard screen (revenue · market cap · software/SaaS group); "
                "ordered by revenue-weighted size fit (headcount soft)</div>"
                "</div><span class='t-scope'>Recommend-only</span></div>"
                + _peer_table(report["core"], start_rank=1) + "</section>")

    # watchlist — in-band alternates for substitution
    if report["watchlist"]:
        body.append("<section class='tile wide'><div class='t-head'><div>"
                    f"<h3>Watchlist · {len(report['watchlist'])} in-band alternates</h3>"
                    "<div class='t-sub'>Also pass every screen criterion — a defensible bench the committee can "
                    "substitute into the core group</div>"
                    "</div><span class='t-scope'>Bench</span></div>"
                    + _peer_table(report["watchlist"], start_rank=len(report["core"]) + 1) + "</section>")

    # defensible exclusions — right business, wrong size
    body.append("<section class='tile wide'><div class='t-head'><div>"
                f"<h3>Defensible exclusions · every screened company on record · all {len(report['near_misses'])}</h3>"
                "<div class='t-sub'>Each non-peer with its failing criterion — outside the software/SaaS group "
                "(industry ✗) or outside the hard size gate (revenue or market cap); "
                "each multiple is vs the subject; <span class='chk p'>blue</span> passed, "
                "<span class='chk f'>red</span> failed. Headcount shown for context (it ranks, it doesn't gate).</div>"
                "</div><span class='t-scope'>Transparency</span></div>"
                + _excl_table(report) + "</section>")

    # target-percentile policy carried forward to the benchmarking arm
    pol = "".join(f"<div class='pp'><span class='pp-v mono'>{_e(p)}</span><span class='pp-l'>{_e(el)}</span></div>"
                  for el, p in TARGET_PERCENTILES)
    body.append("<section class='leadrow'><div class='ld-h'>Target percentile policy →<br>benchmarking</div>"
                f"{pol}<div class='pp-note'>Committee-set targets, carried forward once the peer group is "
                "approved. The screen never recommends pay.</div></section>")

    body.append("<footer class='foot'>"
                f"<div>Built by the <b>{AGENT}</b> agent · the screen <b>recommends</b> a peer group and a "
                "fit-ranked order; the <b>Compensation Committee</b> approves the final list. The agent runs no "
                "screening or ranking math — every PASS/FAIL and fit score is computed by the shared screener.</div>"
                "<div class='pills'>"
                "<span class='pill'>Real peers · synthetic subject</span>"
                f"<span class='pill'>{n_peers} of {n_universe} screened</span>"
                "<span class='pill'>Hard gate + size-fit rank</span>"
                "<span class='pill'>Committee approves</span></div></footer>")

    return _page("".join(body))


def render_digest(report):
    subj = report["subject"]
    lo, hi = min(report["peer_revs"]), max(report["peer_revs"])
    mlo, mhi = min(report["peer_mcaps"]), max(report["peer_mcaps"])
    top = report["peers"][0]["company"]
    lines = [
        f"# {COMPANY} — Executive Comp Peer Group (Compensation Committee) digest",
        f"_{PERIOD} · draft for committee review_", "",
        f"- Screened **{report['n_universe']} public companies** → **{report['n_peers']} in-band candidates**: "
        f"in the **software/SaaS peer group** (documented GICS sub-industries; {COMPANY} is "
        f"**{report['sub_industry']}**), within **0.5–2.0×** on **revenue and market cap** "
        f"(headcount is a soft fit factor, not a gate); then fit-ranked into a "
        f"**{len(report['core'])}-company recommended core peer group** + a **{len(report['watchlist'])}-company "
        f"watchlist**.",
        f"- {COMPANY}: **{_money(subj['revenue_usd'])}** revenue, **{_money(subj['market_cap_usd'])}** market cap, "
        f"**{subj['employees']}** employees — at the **~{ch.ordinal(report['subj_pctile'])} percentile** of the group "
        f"(median peer **{_money(report['median_rev'])}**).",
        f"- Recommended core: **{len(report['core'])}** companies (closest match **{top['company_name']}**, "
        f"fit **{report['peers'][0]['fit']:.0f}**); watchlist of **{len(report['watchlist'])}** in-band alternates.",
        f"- Group revenue spans **{_money(lo)}–{_money(hi)}**; market cap **{_money(mlo)}–{_money(mhi)}**.",
        f"- Excluded: **{report['excl_size']}** in-group on size alone, **{report['excl_industry']}** as "
        f"outside the software/SaaS group — every exclusion on the record with its failing criterion.",
        f"- Target percentiles carried forward: {', '.join(f'{el} {p}' for el, p in TARGET_PERCENTILES)} "
        "(committee policy; applied only after the peer group is approved).",
        "",
        "_The screen RECOMMENDS; the Compensation Committee approves the final peer group (human-in-the-loop)._",
        "_Real public-company peers (as-disclosed public financials, an illustrative snapshot — see governance/real-peer-data.md); the subject (Acme) is synthetic._",
        "_Publish gate: a named committee approver must sign off before this group is used for benchmarking._",
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
.beacon{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px 22px 14px;margin-bottom:16px;box-shadow:0 14px 40px rgba(0,0,0,.5);}
.beacon .head{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:16px;}
.beacon .label{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);}
.beacon .hero{display:flex;align-items:baseline;gap:12px;margin-top:3px;flex-wrap:wrap;}
.beacon .hero .v{font-size:34px;font-weight:800;letter-spacing:-.02em;line-height:1;color:#fff;}
.beacon .hero .pct{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12px;color:var(--cyan2);background:rgba(27,167,255,.14);border:1px solid var(--line);padding:3px 9px;border-radius:999px;}
.subjfacts{display:flex;gap:22px;flex-wrap:wrap;}
.sf{display:flex;flex-direction:column;text-align:right;}
.sf-v{font-size:17px;font-weight:800;color:#fff;}
.sf-l{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);margin-top:2px;}
.cap{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:6px;}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;}
.tile{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px 14px;display:flex;flex-direction:column;}
.tile.wide{margin-top:16px;}
.col-6{grid-column:span 6;}
.t-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:2px;}
.t-head h3{margin:0;font-size:14.5px;color:#fff;font-weight:700;}
.t-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:3px;}
.t-scope{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);border:1px solid var(--hair);border-radius:999px;padding:3px 9px;white-space:nowrap;}
.chart{margin-top:10px;}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--muted);}
.legend span{display:inline-flex;align-items:center;gap:6px;}.legend i{width:10px;height:10px;border-radius:3px;}
.crit{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;}
.cchip{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;font-weight:700;padding:4px 9px;border-radius:6px;border:1px solid var(--hair);}
.cchip.size{color:var(--cyan2);background:rgba(27,167,255,.12);border-color:var(--line);}
.cchip.ind{color:var(--indigo);background:rgba(124,140,255,.12);border-color:rgba(124,140,255,.4);}
.cchip.soft{color:var(--soft);background:transparent;border-style:dashed;}
.statrow{display:flex;gap:20px;flex-wrap:wrap;margin-top:12px;padding-top:12px;border-top:1px solid var(--hair);}
.stat .s-v{font-size:17px;font-weight:800;color:#fff;}.stat .s-l{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);display:block;margin-top:2px;}
.ptable{width:100%;border-collapse:collapse;margin-top:12px;font-size:12.5px;}
.ptable th{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);font-weight:700;}
.ptable td{padding:7px 10px;border-bottom:1px solid var(--hair);color:var(--text);}
.ptable tbody tr:hover{background:rgba(27,167,255,.05);}
.ptable .r,.ptable th.r{text-align:right;}
.ptable .num{color:var(--soft);}
.ptable .tk{color:var(--cyan2);font-weight:700;}
.ptable .nm{color:#fff;font-weight:600;}
.ptable .si{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10.5px;color:var(--muted);}
.ptable .soft{color:var(--soft);opacity:.85;}
.fitwrap{display:flex;align-items:center;gap:8px;}
.fitbar{display:inline-block;width:70px;height:7px;background:rgba(141,177,206,.16);border-radius:4px;overflow:hidden;}
.fitbar>span{display:block;height:7px;background:linear-gradient(90deg,#1ba7ff,#48c7ff);border-radius:4px;}
.chk{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;font-weight:700;padding:1px 5px;border-radius:5px;}
.chk.p{color:var(--cyan2);background:rgba(27,167,255,.14);}
.chk.f{color:var(--red);background:rgba(255,77,79,.16);}
.leadrow{display:flex;align-items:center;gap:22px;flex-wrap:wrap;margin-top:16px;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:14px 18px;}
.ld-h{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--cyan2);line-height:1.4;}
.pp{display:flex;flex-direction:column;}
.pp-v{font-size:20px;font-weight:800;color:#fff;}
.pp-l{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;text-transform:uppercase;color:var(--soft);margin-top:2px;}
.pp-note{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9.5px;color:var(--soft);max-width:240px;margin-left:auto;}
.foot{margin-top:22px;padding-top:14px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10.5px;color:var(--soft);}
.foot .pills{display:flex;gap:8px;flex-wrap:wrap;}.foot .pill{border:1px solid var(--hair);border-radius:999px;padding:3px 9px;}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — Executive Comp Peer Group</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _fail_closed(message) -> int:
    for p in portfolio_ev.managed_outputs(REPORT, DIGEST) + (OUT / "PUBLISHED.json",):
        if not p.exists():
            continue
        try:
            p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            try:
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
    ap = argparse.ArgumentParser(description="Acme Corp executive-comp peer-group builder (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Finalizing a peer group requires a named committee approver "
              "(Compensation Committee).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        universe = _load_universe()
        report = build_report(universe)
        html_doc, digest_doc = render_html(report), render_digest(report)
        html_doc, digest_doc, report_evidence, digest_evidence = portfolio_ev.prepare_pair(
            AGENT, report, html_doc, digest_doc, REPO)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"peer universe unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)   # remove any stale approval BEFORE writing — a draft or a
    #                                  # failed run must never inherit a prior run's "published" flag
    try:
        OUT.mkdir(exist_ok=True)
        for p in portfolio_ev.managed_outputs(REPORT, DIGEST) + (pub_path,):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        portfolio_ev.write_sidecars(REPORT, DIGEST, report_evidence, digest_evidence)
        if args.publish:
            _atomic_write(pub_path,
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF,
                                      "peer_count": report["n_peers"], "core_count": len(report["core"])},
                                     indent=2) + "\n")
        elif pub_path.exists():
            pub_path.unlink()   # a redrawn DRAFT invalidates any prior approval record
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    print(f"{COMPANY} Executive Comp — Peer Group Builder ({AS_OF})")
    print(f"  {report['n_peers']} peers of {report['n_universe']} screened "
          f"(core {len(report['core'])} + watchlist {len(report['watchlist'])}) | "
          f"{COMPANY} at ~{ch.ordinal(report['subj_pctile'])} pctile | median peer {_money(report['median_rev'])}")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nPeer group approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. The Compensation Committee must approve the final peer group. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
