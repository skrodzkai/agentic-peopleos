#!/usr/bin/env python3
"""Acme Corp — ISS Pay-for-Performance Screen (Compensation Committee view).

The Executive Compensation arm's board-anticipation agent: a dark dashboard that shows how the ISS
quantitative pay-for-performance screen would likely read the subject — the overall concern level, the
three measures (MOM / RDA / PTA) against their bands, the ISS-derived comparison group, and the qualitative
factors a Medium/High concern puts in scope. This demo runs on a SEPARATE SYNTHETIC universe (issuers S0NN,
synthetic pay/TSR) from the peer-builder's real peer group, so no real company ever carries a fabricated
pay/TSR figure.

Like every arm agent it is PRESENTATION + GOVERNANCE ONLY: it runs no screening math (every value comes
from foundation/compute/iss_screen.py), fails closed, and stops at a human approval gate.

IMPORTANT, on the dashboard and here: this is an ILLUSTRATION of ISS's PUBLISHED methodology on SYNTHETIC
data. ISS publishes its quantitative concern threshold table and the WLS/aggregation mechanics in its
Pay-for-Performance Mechanics document; what it does NOT publish is the exact FPA threshold and the
qualitative-evaluation outcome, which still require ISS/consultant review. This is NOT ISS's actual output.

    python3 run.py                                              # draft only
    python3 run.py --publish --approved-by "Compensation Committee Chair"

Standard library only; deterministic; offline; synthetic.
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
PERIOD = "FY2026 proxy season · synthetic"
AGENT = "iss-pay-screen"
SCOPE = "publish.iss_pay_screen"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")
_CONCERN_COLOR = {"Low": ch.GREEN, "Medium": ch.AMBER, "High": ch.RED}


class ReportError(RuntimeError):
    """Raised when the ISS screen view cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_iss():
    from foundation.compute.iss_screen import ISSUniverse
    return ISSUniverse()


def _load_peers():
    from foundation.compute.peers import PeerUniverse
    return PeerUniverse()


def _e(v):
    import html
    return html.escape(str(v))


# ---------------------------------------------------------------- compute (no screening math here)
def build_report(iss_universe, peer_universe=None):
    res = iss_universe.screen()                           # the screen decision is entirely iss_screen.py
    cg = res["comparison_group"]
    iss_tickers = {m["ticker"] for m in cg["group"]}

    # overlap with the committee's OWN peer group (the peer-builder arm), if available — the "two peer
    # objects" point: the ISS-derived group is a different membership from the committee-selected group.
    committee = {"available": False, "core": [], "overlap": [], "n_core": 0}
    if peer_universe is not None:
        try:
            pr = peer_universe.screen()
            core = pr["peers"][:15]
            core_tk = {p["company"]["ticker"] for p in core}
            committee = {"available": True, "core": sorted(core_tk),
                         "overlap": sorted(iss_tickers & core_tk), "n_core": len(core_tk)}
        except Exception:
            committee = {"available": False, "core": [], "overlap": [], "n_core": 0}

    return {"res": res, "concern": res["concern"], "measures": res["measures"],
            "comparison_group": cg, "iss_tickers": sorted(iss_tickers), "committee": committee,
            "triggers": res["qualitative_triggers"], "narrative": _narrative(res, committee)}


def _narrative(res, committee):
    m = res["measures"]
    concern = res["concern"]
    drivers = [name.upper() for name, d in (("mom", m["mom"]), ("rda", m["rda"]), ("pta", m["pta"]))
               if d["band"] in ("Medium", "High")]
    driver_txt = (" — driven by " + ", ".join(drivers)) if drivers else ""
    lead = (f"On an illustrative model of the public ISS methodology, {_e(COMPANY)} screens at "
            f"<b class='c-{concern.lower()}'>{concern} concern</b>{driver_txt}.")
    body = (f" CEO pay is <b>{m['mom']['value']:.2f}× the peer median</b> (MOM) while sitting at the "
            f"<b>{ch.ordinal(m['rda']['pay_pctile'])} percentile</b> of pay vs the <b>{ch.ordinal(m['rda']['tsr_pctile'])}</b> "
            f"of 5-year TSR — an alignment gap of <b>{m['rda']['value']:.0f}</b> points (RDA).")
    if committee["available"]:
        body += (" This ISS demo runs on a <b>separate synthetic universe</b> from the peer-builder's real peer "
                 "group (disjoint by design, so no real company ever carries a fabricated pay/TSR figure).")
    tail = " <span class='warn'>The screen anticipates the board read; a human committee decides the response.</span>"
    return lead + body + tail


# ---------------------------------------------------------------- presentation (gauges + layout)
def _gauge(value, axis_lo, axis_hi, medium, high, higher_worse, fmt):
    """A horizontal concern gauge: Low (green) / Medium (amber) / High (red) zones + a value needle."""
    w, h = 360, 50
    x0, x1, ty, bh = 10, 350, 26, 13

    def X(v):
        v = max(axis_lo, min(axis_hi, v))
        return x0 + (x1 - x0) * (v - axis_lo) / (axis_hi - axis_lo)

    if higher_worse:                  # MOM: green up to medium, amber to high, red beyond
        zones = [(axis_lo, medium, ch.GREEN), (medium, high, ch.AMBER), (high, axis_hi, ch.RED)]
    else:                             # RDA/PTA: red below high (more negative), amber to medium, green above
        zones = [(axis_lo, high, ch.RED), (high, medium, ch.AMBER), (medium, axis_hi, ch.GREEN)]
    b = []
    for z0, z1, col in zones:
        b.append(f"<rect x='{X(z0):.1f}' y='{ty - bh / 2:.1f}' width='{max(0, X(z1) - X(z0)):.1f}' "
                 f"height='{bh}' fill='{col}' opacity='.32'/>")
    for thr in (medium, high):        # threshold ticks
        b.append(f"<line x1='{X(thr):.1f}' y1='{ty - bh / 2 - 3:.1f}' x2='{X(thr):.1f}' "
                 f"y2='{ty + bh / 2 + 3:.1f}' stroke='{ch.SOFT}' stroke-width='1' stroke-dasharray='2 2'/>")
    xv = X(value)
    b.append(f"<line x1='{xv:.1f}' y1='{ty - bh / 2 - 5:.1f}' x2='{xv:.1f}' y2='{ty + bh / 2 + 5:.1f}' "
             f"stroke='#fff' stroke-width='2.5'/><circle cx='{xv:.1f}' cy='{ty}' r='4.2' fill='#fff'/>")
    b.append(f"<text x='{xv:.1f}' y='13' text-anchor='middle' font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" "
             f"font-size='10.5' font-weight='700' fill='#fff'>{_e(fmt)}</text>")
    return (f"<svg viewBox='0 0 {w} {h}' xmlns='http://www.w3.org/2000/svg' "
            f"style='width:100%;height:auto'>{''.join(b)}</svg>")


def _measure_card(name, sub, value_fmt, band, gauge):
    bc = _CONCERN_COLOR[band]
    return (f"<div class='meas'><div class='m-top'><div><div class='m-name'>{_e(name)}</div>"
            f"<div class='m-sub'>{_e(sub)}</div></div>"
            f"<span class='m-band' style='color:{bc};border-color:{bc}'>{_e(band)}</span></div>"
            f"<div class='m-gauge'>{gauge}</div></div>")


def render_html(report):
    m = report["measures"]
    concern = report["concern"]
    cg = report["comparison_group"]
    cc = _CONCERN_COLOR[concern]

    body = []
    body.append("<header class='topbar'>"
                f"<div class='brandwrap'>{MARK_SVG}"
                "<div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                "<div class='brand-sub'>Executive Compensation</div></div></div>"
                "<div class='title'><h1>ISS Pay-for-Performance Screen</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                "<div class='spacer'></div>"
                "<span class='status'>Draft · awaiting committee approval</span></header>")

    body.append("<section class='insight'>"
                "<svg class='glyph' viewBox='0 0 24 24'><path d='M12 2 L13.7 8.3 L20 10 L13.7 11.7 L12 18 "
                "L10.3 11.7 L4 10 L10.3 8.3 Z' fill='#1ba7ff'/><circle cx='18.5' cy='4.5' r='1.6' fill='#f7b955'/>"
                "<circle cx='5' cy='17' r='1.2' fill='#7c8cff'/></svg>"
                "<div><div class='tag'>Generated insight · illustrative model of the public ISS methodology</div>"
                f"<p>{report['narrative']}</p></div></section>")

    # concern beacon — the overall screen result, big, with the honest disclaimer
    drivers = [name.upper() for name, d in (("mom", m["mom"]), ("rda", m["rda"]), ("pta", m["pta"]))
               if d["band"] in ("Medium", "High")]
    body.append("<section class='beacon'><div class='head'>"
                "<div><div class='label'>Anticipated ISS quantitative concern</div>"
                f"<div class='hero'><span class='c-big' style='color:{cc}'>{_e(concern)}</span>"
                f"<span class='c-sub'>{'driven by ' + ', '.join(drivers) if drivers else 'no quantitative concern'}"
                "</span></div></div>"
                f"<div class='disc'>Illustrative model of ISS's <b>published</b> methodology on synthetic data — "
                "thresholds + WLS mechanics per ISS's Pay-for-Performance Mechanics doc; the exact FPA threshold "
                "+ qualitative outcome still need ISS/consultant review. Not ISS's actual output.</div></div></section>")

    # three measure gauges
    mom, rda, pta = m["mom"], m["rda"], m["pta"]
    gauges = "".join([
        _measure_card("MOM · Multiple of Median",
                      "CEO pay ÷ peer-median (50/50 blend of 1-yr & 3-yr)", f"{mom['value']:.2f}×",
                      mom["band"], _gauge(mom["value"], 1.0, 4.0, 2.33, 3.40, True, f"{mom['value']:.2f}×")),
        _measure_card("RDA · Relative Degree of Alignment",
                      "5-yr TSR percentile − pay percentile (lower = concern)", f"{rda['value']:.0f}",
                      rda["band"], _gauge(rda["value"], -80.0, 20.0, -54.0, -64.0, False, f"{rda['value']:.0f}")),
        _measure_card("PTA · Pay-TSR Alignment",
                      "5-yr indexed-TSR trend − pay trend (lower = concern)", f"{pta['value']:.0f}%",
                      pta["band"], _gauge(pta["value"], -60.0, 20.0, -30.0, -45.0, False, f"{pta['value']:.0f}%")),
    ])
    fpa = m["fpa"]
    fpa_row = (f"<div class='fpa'><span class='fpa-h'>FPA · Financial Performance Assessment</span>"
               f"<span class='fpa-v mono'>{fpa['value']:+.0f}</span>"
               f"<span class='fpa-n'>fin pctile {fpa['fin_pctile']:.0f} − pay pctile {fpa['pay_pctile']:.0f} · "
               f"{_e(fpa['note'])}</span></div>")
    body.append("<section class='measures'><div class='m-head'>Quantitative measures · "
                "<span class='leg'><i style='background:#43d477'></i>Low "
                "<i style='background:#f7b955'></i>Medium <i style='background:#ff4d4f'></i>High</span></div>"
                + gauges + fpa_row + "</section>")

    # comparison group + overlap with the committee's own peer group
    com = report["committee"]
    over = (f"<div class='stat'><span class='s-v mono'>{len(com['overlap'])}/{cg['n_group']}</span>"
            f"<span class='s-l'>overlap committee core</span></div>" if com["available"] else "")
    body.append("<section class='tile'><div class='t-head'><div>"
                "<h3>ISS-derived comparison group</h3>"
                "<div class='t-sub'>Self-peer graph + peer-of-peer walk, GICS + size screen — a DIFFERENT "
                "membership from the committee's own peer group</div></div>"
                "<span class='t-scope'>Illustrative</span></div>"
                "<div class='statrow'>"
                f"<div class='stat'><span class='s-v mono'>{cg['n_group']}</span><span class='s-l'>ISS peers</span></div>"
                f"<div class='stat'><span class='s-v mono'>{cg['n_first_degree']}</span><span class='s-l'>first-degree</span></div>"
                f"<div class='stat'><span class='s-v mono'>{cg['n_candidates']}</span><span class='s-l'>graph candidates</span></div>"
                + over + "</div>"
                "<div class='tickers'>" + "".join(f"<span class='tk'>{_e(t)}</span>" for t in report["iss_tickers"])
                + "</div></section>")

    # qualitative triggers — the second-stage ISS review factors a Medium/High puts in scope
    if report["triggers"]:
        items = "".join(f"<li>{_e(t)}</li>" for t in report["triggers"])
        body.append("<section class='tile wide'><div class='t-head'><div>"
                    f"<h3>Qualitative review in scope · {len(report['triggers'])} factors</h3>"
                    "<div class='t-sub'>A Medium/High quantitative screen triggers ISS's qualitative "
                    "review — the factors a committee should be ready to address</div></div>"
                    "<span class='t-scope'>Second stage</span></div>"
                    f"<ul class='triggers'>{items}</ul></section>")
    else:
        body.append("<section class='tile wide'><div class='t-head'><div>"
                    "<h3>Qualitative review</h3><div class='t-sub'>A Low quantitative screen does not "
                    "trigger ISS's qualitative review.</div></div><span class='t-scope'>Second stage</span>"
                    "</div></section>")

    body.append("<footer class='foot'>"
                f"<div>Built by the <b>{AGENT}</b> agent · an illustrative model of ISS's <b>public</b> "
                "methodology on synthetic data — <b>not</b> ISS's actual output. ISS publishes its threshold "
                "table + WLS mechanics; the exact FPA threshold + qualitative outcome need ISS/consultant review. "
                "The screen anticipates; a human decides.</div>"
                "<div class='pills'>"
                "<span class='pill'>Synthetic data</span>"
                "<span class='pill'>Public ISS methodology</span>"
                "<span class='pill'>ISS-published thresholds</span>"
                "<span class='pill'>Committee decides</span></div></footer>")

    return _page("".join(body))


def render_digest(report):
    m = report["measures"]
    com = report["committee"]
    lines = [
        f"# {COMPANY} — ISS Pay-for-Performance Screen (Compensation Committee) digest",
        f"_{PERIOD} · draft for committee review_", "",
        f"- **Anticipated concern: {report['concern']}** "
        + ("(driven by " + ", ".join(n.upper() for n, d in (("mom", m["mom"]), ("rda", m["rda"]),
           ("pta", m["pta"])) if d["band"] in ("Medium", "High")) + ")."
           if report["concern"] != "Low" else "(no quantitative concern)."),
        f"- MOM **{m['mom']['value']:.2f}×** the peer median ({m['mom']['band']}); "
        f"RDA **{m['rda']['value']:.0f}** ({m['rda']['band']}: pay {ch.ordinal(m['rda']['pay_pctile'])} pctile vs "
        f"TSR {ch.ordinal(m['rda']['tsr_pctile'])}); PTA **{m['pta']['value']:.0f}%** ({m['pta']['band']}).",
        f"- ISS-derived comparison group of **{report['comparison_group']['n_group']}** "
        + (f"({len(com['overlap'])} overlapping the committee's {com['n_core']}-company core)." if com["available"] else "names."),
        f"- Qualitative review in scope: **{len(report['triggers'])}** factors."
        if report["triggers"] else "- A Low screen does not trigger qualitative review.",
        "",
        "_Illustrative model of ISS's PUBLISHED methodology on synthetic data — NOT ISS's actual output. ISS",
        "publishes its threshold table + WLS mechanics (Pay-for-Performance Mechanics doc); the exact FPA",
        "threshold + the qualitative-evaluation outcome still require ISS/consultant review._",
        "_The screen anticipates the board read; a human committee decides the response. Publish requires a named approver._",
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
.wrap{max-width:1180px;margin:0 auto;}
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
.insight p{margin:0;font-size:14.5px;line-height:1.5;}
.insight .warn{color:var(--amber);font-weight:600;}
.c-low{color:var(--green);}.c-medium{color:var(--amber);}.c-high{color:var(--red);}
.beacon{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin-bottom:16px;box-shadow:0 14px 40px rgba(0,0,0,.5);}
.beacon .label{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);}
.beacon .hero{display:flex;align-items:baseline;gap:14px;margin-top:4px;flex-wrap:wrap;}
.beacon .c-big{font-size:42px;font-weight:800;letter-spacing:-.02em;line-height:1;}
.beacon .c-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12px;color:var(--muted);}
.beacon .disc{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);max-width:300px;text-align:right;line-height:1.5;}
.measures{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px;margin-bottom:16px;}
.m-head{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;}
.m-head .leg{display:inline-flex;align-items:center;gap:6px;}.m-head .leg i{width:9px;height:9px;border-radius:2px;display:inline-block;margin:0 2px 0 8px;}
.meas{padding:12px 0;border-top:1px solid var(--hair);}
.m-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
.m-name{font-size:13.5px;font-weight:700;color:#fff;}
.m-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:2px;}
.m-band{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;font-weight:800;padding:2px 9px;border-radius:999px;border:1px solid;text-transform:uppercase;white-space:nowrap;}
.m-gauge{margin-top:6px;}
.fpa{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:12px;margin-top:6px;border-top:1px solid var(--hair);}
.fpa-h{font-size:12.5px;font-weight:700;color:var(--cyan2);}
.fpa-v{font-size:16px;font-weight:800;color:#fff;}
.fpa-n{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);}
.tile{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px;margin-bottom:16px;}
.tile.wide{}
.t-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:2px;}
.t-head h3{margin:0;font-size:14.5px;color:#fff;font-weight:700;}
.t-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:3px;max-width:640px;}
.t-scope{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);border:1px solid var(--hair);border-radius:999px;padding:3px 9px;white-space:nowrap;}
.statrow{display:flex;gap:24px;flex-wrap:wrap;margin:12px 0;}
.stat .s-v{font-size:20px;font-weight:800;color:#fff;}.stat .s-l{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);display:block;margin-top:2px;}
.tickers{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}
.tickers .tk{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--cyan2);background:rgba(27,167,255,.10);border:1px solid var(--hair);border-radius:5px;padding:2px 7px;}
.triggers{margin:10px 0 0;padding-left:18px;display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;}
.triggers li{font-size:12.5px;color:var(--text);line-height:1.4;}
.foot{margin-top:8px;padding-top:14px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10.5px;color:var(--soft);}
.foot .pills{display:flex;gap:8px;flex-wrap:wrap;}.foot .pill{border:1px solid var(--hair);border-radius:999px;padding:3px 9px;}
@media(max-width:720px){.triggers{grid-template-columns:1fr;}}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — ISS Pay-for-Performance Screen</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _fail_closed(message) -> int:
    for p in (REPORT, DIGEST, OUT / "PUBLISHED.json"):
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
    ap = argparse.ArgumentParser(description="Acme Corp ISS pay-for-performance screen (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a named committee approver "
              "(Compensation Committee).\n  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        iss = _load_iss()
        try:
            peers = _load_peers()
        except Exception:
            peers = None                 # overlap is optional; the screen still stands alone
        report = build_report(iss, peers)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"ISS screen unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)   # remove any stale approval BEFORE writing — a draft or a
    #                                  # failed run must never inherit a prior run's "published" flag
    try:
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST, pub_path):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        if args.publish:
            _atomic_write(pub_path, json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF,
                                                "concern": report["concern"]}, indent=2) + "\n")
        elif pub_path.exists():
            pub_path.unlink()
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    print(f"{COMPANY} ISS Pay-for-Performance Screen ({AS_OF})")
    print(f"  anticipated concern: {report['concern']} | comparison group {report['comparison_group']['n_group']} | "
          f"{len(report['triggers'])} qualitative triggers")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nApproved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (Compensation Committee) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
