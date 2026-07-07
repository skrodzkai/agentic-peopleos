#!/usr/bin/env python3
"""Glass Lewis pay screen + ISS-vs-GL "advisor war room" — the committee deliverable, rendered.

Two proxy advisors score the SAME executive-pay facts through DIFFERENT lenses, and a board needs to know
where they agree, where they diverge, and what to do about it before the say-on-pay vote. This dark committee
dashboard renders:
- the illustrative Glass Lewis CURRENT (2026) model — a five-test 0-100 SCORECARD mapping to a CONCERN LEVEL
  (Negligible / Low / Medium / High / Severe),
- alongside the illustrative ISS concern level (Low/Medium/High) from the ISS engine in this repo,
- the reconciliation: an agree/diverge verdict, WHY they diverge, the committee considerations, and a directional
  say-on-pay support band.

Every number comes from foundation/compute/glass_lewis_screen.py — the agent renders and governs; it does no
scoring and makes no vote prediction.

IMPORTANT (on the dashboard and here): the Glass Lewis model — the test weights, score bands, and peer rules —
and the ISS side are ILLUSTRATIVE reconstructions, NOT the advisors' output and not affiliated with either
firm, built only from PUBLIC methodology. Glass Lewis RETIRED its old A-F letter grade with the 2026 model;
this renders the current concern-level scorecard.

    python3 run.py                                  # writes the draft dashboard + digest (nothing sent)
    python3 run.py --publish --approved-by "Compensation Committee Chair"
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from foundation.compute import glass_lewis_screen as G   # noqa: E402
from foundation.render import charts as ch                # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "2025 proxy"
PERIOD = "2025 say-on-pay · synthetic universe"
SCOPE = "publish.glass_lewis_screen"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

# concern-level colors (both advisors use a lower-is-better concern scale)
_CONCERN_C = {"Negligible": ch.GREEN, "Low": ch.GREEN, "Medium": ch.AMBER, "High": ch.RED, "Severe": ch.RED}
_TESTBAND_C = {"Negligible": ch.GREEN, "Low": ch.GREEN, "Moderate": ch.AMBER, "High": ch.RED, "Severe": ch.RED}
_CONCERN_ORD = {"Negligible": 0, "Low": 0, "Medium": 1, "High": 2, "Severe": 2, "": 3}
# the committee-considerations lookup follows deterministically from the reconciliation verdict (a presentation lookup, not new math)
_VERDICT = {
    "CLEAN SWEEP": ("both proxy advisors clear the program — typically monitoring, no rebuttal.", ch.GREEN),
    "ISS-ONLY FLAG": ("the ISS lens is the one flagging — Glass Lewis's broader scorecard reads lower-concern; "
                      "reviewing ISS's rationale and TSR-linked disclosure are common considerations here.", ch.AMBER),
    "GL-ONLY FLAG": ("the Glass Lewis lens is the one flagging — ISS reads the program as aligned; the GL "
                     "scorecard tests are the common area to review here.", ch.AMBER),
    "DUAL WATCH": ("both advisors caution — typically proactive engagement plus disclosure enhancements ahead "
                   "of the vote.", ch.AMBER),
    "TWO-FRONT FIGHT": ("both advisors adverse — a say-on-pay campaign is common at this posture; typically "
                        "escalated engagement plus committee-responsiveness disclosure.", ch.RED),
}


class ReportError(RuntimeError):
    """Raised when the two-advisor view cannot be produced (fail closed)."""


def _e(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _md(v):
    """Neutralize Markdown-active characters in interpolated free-text for the digest (HTML-escaping alone
    doesn't stop **bold** / [link](url) / `code` from being interpreted)."""
    s = str(v)
    for ch_ in "\\`*_[]()~|<>":
        s = s.replace(ch_, "\\" + ch_)
    return s


def _one_line(t, limit=300):
    return " ".join(str(t).split())[:limit]


def _ord(n):
    """English ordinal for a percentile: 1st / 2nd / 3rd / 21st / 93rd / 11th (not the naive '93th')."""
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _fin(*xs):
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def _plain_finite(*xs):
    """Stricter than _fin for interpolated values: a PLAIN int/float (no exotic subclass with a hostile
    __format__) that is finite (no inf/NaN from an engine edge)."""
    return all(type(x) in (int, float) and math.isfinite(x) for x in xs)


# ---------------------------------------------------------------- build + validate
def build_report(result):
    """Validate the engine output (fail closed) and shape it for rendering. No scoring is done here."""
    r = result
    gl, iss, syn = r["gl"], r["iss"], r["synthesis"]
    lo, hi = syn["say_on_pay_support_band_pct"]
    c = syn["contrast"]
    cf = gl["counterfactuals"]
    rda = iss["measures"]["rda"]
    pcts = [gl["pay_pctile"], gl["neo_pay_pctile"], gl["sti_pctile"], gl["tsr_pctile"], gl["fin_pctile"]]
    checks = [
        r["subject"] == iss["subject"]["ticker"] == gl["subject"],      # both advisors scored one issuer
        gl["concern"] in ("Negligible", "Low", "Medium", "High", "Severe"),
        iss["concern"] in ("Low", "Medium", "High"),
        syn["verdict"] in _VERDICT,
        _plain_finite(gl["composite_score"], gl["quant_score"]),
        0.0 <= gl["composite_score"] <= 100.0,
        gl["concern"] == G.concern_for_score(gl["composite_score"]),    # concern matches the composite band
        len(gl["tests"]) == 5 and abs(sum(t["weight"] for t in gl["tests"]) - 1.0) < 1e-9,
        all(_plain_finite(t["score"]) and 0.0 <= t["score"] <= 100.0 and t["band"] for t in gl["tests"]),
        _plain_finite(*pcts) and all(0.0 <= p <= 100.0 for p in pcts),
        _plain_finite(gl["cap_excess_vs_median"]) and gl["cap_excess_vs_median"] >= 0.0,  # rendered as "N.NN×"
        _plain_finite(cf["tsr_only_score"], cf["financials_only_score"]),
        cf["tsr_only_score"] <= gl["composite_score"] <= cf["financials_only_score"] + 1e-9,  # divergence shape
        _plain_finite(qp := gl["qualitative"]["penalty"]) and qp >= 0.0,
        # say-on-pay responsiveness factor: bounded prior-support % + engagement threshold, known responsiveness
        # vocab, and a below_threshold flag that is CONSISTENT with the numbers it is rendered against (an
        # inconsistent flag would render a false, self-contradicting sentence)
        _plain_finite(sop := gl["say_on_pay"]["prior_support_pct"]) and 0.0 <= sop <= 100.0,
        _plain_finite(sopthr := gl["say_on_pay"]["engage_threshold_pct"]) and 0.0 <= sopthr <= 100.0,
        gl["say_on_pay"]["responsiveness"] in ("robust", "limited", "none", "n/a"),
        isinstance(gl["say_on_pay"]["below_threshold"], bool)
        and gl["say_on_pay"]["below_threshold"] == (sop < sopthr),
        _plain_finite(lo, hi) and 0.0 <= lo < hi <= 100.0,              # SOP band is an ordered range (bool-safe)
        "NOT a vote forecast" in syn["band_basis"],
        isinstance(syn["agree"], bool),
        # rendered contrast values are finite, in range, and EQUAL their source fields
        _plain_finite(c["iss_pay_pctile"], c["iss_tsr_pctile"], c["gl_pay_pctile"], c["gl_fin_pctile"],
                      c["gl_tsr_pctile"], c["gl_composite"]),
        c["gl_pay_pctile"] == gl["pay_pctile"] and c["gl_fin_pctile"] == gl["fin_pctile"]
        and c["gl_tsr_pctile"] == gl["tsr_pctile"] and c["gl_composite"] == gl["composite_score"],
        c["iss_pay_pctile"] == rda["pay_pctile"] and c["iss_tsr_pctile"] == rda["tsr_pctile"],
        # the ISS MOM/RDA/PTA values are rendered in the war-room card — they too must be plain + finite
        _plain_finite(iss["measures"]["mom"]["value"], rda["value"], iss["measures"]["pta"]["value"]),
    ]
    if not all(checks):
        raise ReportError(f"glass-lewis result failed validation (check #{checks.index(False)})")
    action, color = _VERDICT[syn["verdict"]]
    return {"r": r, "gl": gl, "iss": iss, "syn": syn, "action": action, "color": color, "band": (lo, hi)}


# ---------------------------------------------------------------- render
def _kpi(label, value, sub, color=None):
    vc = f" style='color:{color}'" if color else ""
    return (f"<div class='kpi'><div class='k-l'>{_e(label)}</div>"
            f"<div class='k-v mono'{vc}>{_e(value)}</div><div class='k-s'>{_e(sub)}</div></div>")


def _advisor_card(name, lens, badge, badge_color, rows):
    body = "".join(f"<div class='mrow'><span class='ml'>{_e(k)}</span>"
                   f"<span class='mv mono'>{_e(v)}</span></div>" for k, v in rows)
    return (f"<div class='adv' style='border-top:3px solid {badge_color}'>"
            f"<div class='adv-h'><span class='adv-n'>{_e(name)}</span>"
            f"<span class='adv-b' style='background:{badge_color}'>{_e(badge)}</span></div>"
            f"<div class='adv-lens'>{_e(lens)}</div>{body}</div>")


def _test_row(t):
    col = _TESTBAND_C.get(t["band"], ch.AMBER)
    w = max(1.5, min(100.0, t["score"]))
    return (f"<div class='trow'><div class='t-top'><span class='t-lab'>{_e(t['label'])}</span>"
            f"<span class='t-sc mono' style='color:{col}'>{t['score']:.0f} · {_e(t['band'])}</span></div>"
            f"<div class='t-bar'><div class='t-fill' style='width:{w:.1f}%;background:{col}'></div></div>"
            f"<div class='t-wt'>weight {t['weight']*100:.0f}%</div></div>")


def _lens_strip(pay_pctile, perf_pctile, pay_label, uid):
    return ch.percentile_strip(pay_pctile, 0, 100, ticks=[(0, "0"), (50, "median"), (100, "100th")],
                               target=perf_pctile, you_label=pay_label, unit_prefix="", ordinal=True, uid=uid)


def render_html(report):
    gl, iss, syn = report["gl"], report["iss"], report["syn"]
    c = syn["contrast"]
    cf = gl["counterfactuals"]
    vcolor = report["color"]
    gl_c = _CONCERN_C[gl["concern"]]
    iss_c = _CONCERN_C[iss["concern"]]
    body = []
    body.append(f"<header class='top'><div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                f"<div class='sub'>Executive Compensation · Proxy Advisors</div></div>"
                f"<div class='ttl'><h1>ISS vs Glass Lewis — Say-on-Pay War Room</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                f"<span class='status'>Draft · awaiting committee review</span></header>")
    agree_txt = "agree" if syn["agree"] else "diverge"
    body.append(f"<section class='headline' style='border-color:{vcolor}'>"
                f"<div class='hl-tag'>Reconciliation verdict · illustrative reconstructions, not advisor output</div>"
                f"<p>The two proxy-advisor reconstructions <b>{agree_txt}</b>: Glass Lewis's 5-test scorecard "
                f"reads <b style='color:{gl_c}'>{_e(gl['concern'])}</b> concern "
                f"(<span class='mono'>{gl['composite_score']:.0f}/100</span>) while ISS reads "
                f"<b style='color:{iss_c}'>{_e(iss['concern'])}</b> concern — "
                f"<span class='vd' style='color:{vcolor}'>{_e(syn['verdict'])}</span>. "
                f"<b>Committee considerations:</b> {_e(report['action'])}</p></section>")
    # KPI band
    body.append("<section class='kpis'>"
                + _kpi("Glass Lewis concern", gl["concern"], f"composite {gl['composite_score']:.0f}/100 · 5-test scorecard", gl_c)
                + _kpi("ISS concern", iss["concern"], f"MOM {iss['measures']['mom']['value']:.2f}× · "
                       f"RDA {iss['measures']['rda']['value']:+.0f} · PTA {iss['measures']['pta']['value']:+.1f}", iss_c)
                + _kpi("Verdict", syn["verdict"], "agree" if syn["agree"] else "advisors diverge", vcolor)
                + _kpi("Say-on-pay support", f"{report['band'][0]:.0f}–{report['band'][1]:.0f}%",
                       "directional band · not a forecast", ch.CYAN)
                + _kpi("GL peer group", f"{gl['peer_group']['n']}", "cap-banded co-citation network", ch.CYAN)
                + "</section>")
    # the two lenses
    body.append("<section class='tile'><h3>The two lenses on the same pay</h3>"
                "<div class='t-sub'>Both advisors are illustrative reconstructions scoring identical synthetic "
                "facts. ISS is CEO-only and TSR-centric; Glass Lewis's current model is a broad five-test "
                "scorecard that weighs pay against financial performance as well as TSR.</div>"
                "<div class='war'>"
                + _advisor_card("ISS", "CEO-only pay vs 5-yr relative TSR (MOM / RDA / PTA cascade)",
                                iss["concern"] + " concern", iss_c,
                                [("CEO pay percentile", _ord(c['iss_pay_pctile'])),
                                 ("5-yr TSR percentile", _ord(c['iss_tsr_pctile'])),
                                 ("Multiple of median (MOM)", f"{iss['measures']['mom']['value']:.2f}×"),
                                 ("Rel. degree of alignment", f"{iss['measures']['rda']['value']:+.0f}"),
                                 ("Pay-TSR trend align (PTA)", f"{iss['measures']['pta']['value']:+.1f}")])
                + _advisor_card("Glass Lewis", "5-test scorecard: granted CEO/NEO pay vs TSR AND vs financials",
                                gl["concern"] + f" · {gl['composite_score']:.0f}", gl_c,
                                [("CEO granted pay percentile", _ord(gl['pay_pctile'])),
                                 ("NEO-team pay percentile", _ord(gl['neo_pay_pctile'])),
                                 ("Financial-perf percentile", _ord(gl['fin_pctile'])),
                                 ("5-yr TSR percentile", _ord(gl['tsr_pctile'])),
                                 ("CAP vs peer-median ratio", f"{gl['cap_excess_vs_median']:.2f}×")])
                + "</div>"
                f"<div class='why'>Why they diverge: {_e(syn['divergence_driver'])}.</div></section>")
    # the 5-test scorecard
    tests = "".join(_test_row(t) for t in gl["tests"])
    qual = gl["qualitative"]
    sop = gl["say_on_pay"]
    if sop["below_threshold"]:
        sop_line = (f"Say-on-pay responsiveness (a <b>recommendation-level</b> factor, separate from the P4P "
                    f"score above): prior support {sop['prior_support_pct']:.1f}% fell below the "
                    f"~{sop['engage_threshold_pct']:.0f}% engagement threshold — board responsiveness "
                    f"<b>{_e(sop['responsiveness'])}</b>, recommendation concern <b>{_e(sop['recommendation_concern'])}</b>.")
        sop_color = _CONCERN_C.get("Medium", "#f7b955")
    else:
        sop_line = (f"Say-on-pay responsiveness (a recommendation-level factor, separate from the P4P score): "
                    f"prior support <b>{sop['prior_support_pct']:.1f}%</b> — above the "
                    f"~{sop['engage_threshold_pct']:.0f}% engagement threshold, no responsiveness concern.")
        sop_color = "#43d477"
    qnote = (f"Qualitative downward modifier: −{qual['penalty']:.0f} ({'; '.join(qual['flags']) if qual['flags'] else 'no flags'})."
             f" {qual['note']}.")
    body.append("<section class='tile'><h3>Glass Lewis scorecard — the five quantitative tests</h3>"
                "<div class='t-sub'>Each test scores 0–100 (higher = better alignment). The weighted composite "
                "maps to the concern level. STI is measured as payout <b>relative to target</b>, not raw dollars. "
                "Illustrative simplifications: the test <b>weights</b> are undisclosed by Glass Lewis; GL "
                "publishes test-specific rating ranges, but we intentionally apply <b>one uniform illustrative "
                "band</b> across tests; the CAP-vs-TSR test is benchmarked to the <b>synthetic 15-name peer "
                "median</b> (GL uses broader market-cap peer context); and financial-growth tests use a 3-yr "
                "window here (GL's is 5-yr).</div>"
                f"<div class='tests'>{tests}</div>"
                f"<div class='why' style='border-left:3px solid {sop_color};padding-left:9px'>{sop_line}</div>"
                f"<div class='why'>Composite {gl['composite_score']:.0f}/100 → {_e(gl['concern'])} concern. {_e(qnote)}</div>"
                "</section>")
    # counterfactual: pure-TSR vs financials-only
    body.append("<section class='tile'><h3>Why Glass Lewis lands lower than ISS — the scorecard's two poles</h3>"
                "<div class='t-sub'>These are non-additive counterfactual reads. A pure pay-vs-TSR view (ISS's "
                "focus) and a financials-only view bracket the real composite — the scorecard blends them.</div>"
                "<div class='cfs'>"
                f"<div class='cf'><div class='cf-h'>If scored on pay-vs-TSR only (ISS's lens)</div>"
                f"<div class='cf-v mono' style='color:{_CONCERN_C[cf['tsr_only_concern']]}'>{cf['tsr_only_score']:.0f} → {_e(cf['tsr_only_concern'])}</div>"
                f"<div class='cf-s'>the CEO's grant outran a weak stock</div></div>"
                f"<div class='cf mid'><div class='cf-h'>Actual 5-test composite</div>"
                f"<div class='cf-v mono' style='color:{gl_c}'>{gl['composite_score']:.0f} → {_e(gl['concern'])}</div>"
                f"<div class='cf-s'>the blended board read</div></div>"
                f"<div class='cf'><div class='cf-h'>If scored on financials only</div>"
                f"<div class='cf-v mono' style='color:{_CONCERN_C[cf['financials_only_concern']]}'>{cf['financials_only_score']:.0f} → {_e(cf['financials_only_concern'])}</div>"
                f"<div class='cf-s'>lean NEO team + solid financials</div></div>"
                "</div>"
                "<div class='ls-h'>ISS lens — CEO pay vs relative TSR (needle = pay, dashed = performance)</div>"
                + _lens_strip(c["iss_pay_pctile"], c["iss_tsr_pctile"], "CEO pay", "iss")
                + "<div class='ls-h'>Glass Lewis — CEO pay vs financial performance (the aligned axis ISS ignores)</div>"
                + _lens_strip(gl["pay_pctile"], gl["fin_pctile"], "CEO pay", "glfin")
                + "</section>")
    body.append("<footer class='foot'>Built by the <b>glass-lewis-screen</b> agent · it renders the two-advisor "
                "view; the <b>Compensation Committee</b> owns the say-on-pay response. The Glass Lewis (current "
                "2026 scorecard; the legacy A–F grade is retired) and ISS models here are <b>illustrative "
                "reconstructions</b> — NOT Glass Lewis or ISS output, not affiliated with either firm, built "
                "from public methodology — over a <b>synthetic</b> universe. The support band is a directional "
                "estimate, not a vote forecast.</footer>")
    return _page("".join(body))


def render_digest(report):
    gl, iss, syn = report["gl"], report["iss"], report["syn"]
    c = syn["contrast"]
    cf = gl["counterfactuals"]
    return "\n".join([
        f"# {COMPANY} — ISS vs Glass Lewis (say-on-pay digest, {AS_OF})", "",
        f"**{syn['verdict']}** — committee considerations: {report['action']}", "",
        f"- **Glass Lewis (2026 scorecard):** **{gl['concern']}** concern (composite {gl['composite_score']:.0f}/100 "
        f"across 5 tests; CEO pay {_ord(gl['pay_pctile'])} vs financials {_ord(gl['fin_pctile'])} vs TSR {_ord(gl['tsr_pctile'])})",
        f"- **ISS:** **{iss['concern']}** concern (CEO pay {_ord(c['iss_pay_pctile'])} vs 5-yr TSR "
        f"{_ord(c['iss_tsr_pctile'])}; MOM {iss['measures']['mom']['value']:.2f}×, "
        f"RDA {iss['measures']['rda']['value']:+.0f}, PTA {iss['measures']['pta']['value']:+.1f})",
        f"- **Why they {'agree' if syn['agree'] else 'diverge'}:** {_md(syn['divergence_driver'])} — a pay-vs-TSR-only "
        f"read scores {cf['tsr_only_score']:.0f} ({cf['tsr_only_concern']}); financials-only {cf['financials_only_score']:.0f} "
        f"({cf['financials_only_concern']}); the blended composite is {gl['composite_score']:.0f} ({gl['concern']})",
        f"- **Say-on-pay support band:** {report['band'][0]:.0f}–{report['band'][1]:.0f}% "
        "(directional practitioner range for this posture — **not a vote forecast**)",
        (f"- **Say-on-pay responsiveness:** prior support {gl['say_on_pay']['prior_support_pct']:.1f}% "
         + ("above the ~{:.0f}% engagement threshold — no responsiveness concern".format(
             gl['say_on_pay']['engage_threshold_pct'])
            if not gl['say_on_pay']['below_threshold']
            else "below the ~{:.0f}% threshold; board responsiveness {} — a recommendation-level concern, "
                 "separate from the P4P composite".format(
             gl['say_on_pay']['engage_threshold_pct'],
             _md(gl['say_on_pay']['responsiveness'])))),
        f"- **GL peer group:** {gl['peer_group']['n']} names (cap-banded co-citation network)",
        "", "_Synthetic universe. Glass Lewis (current 2026 concern-level scorecard; the legacy A–F grade is "
        "retired) and ISS are illustrative reconstructions — NOT the advisors' output, not affiliated with "
        "either firm. Draft; the Compensation Committee owns the response._"])


_STYLE = """
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),#06131d;background-repeat:no-repeat;color:#dbe7f0;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:1180px;margin:0 auto;padding:26px}.mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
.top{display:flex;align-items:center;gap:18px;border-bottom:1px solid #14364a;padding-bottom:14px;margin-bottom:6px}
.brand{font-weight:800;font-size:18px;letter-spacing:.3px}.os{color:#1ba7ff}.sub{color:#8db1ce;font-size:11px;text-transform:uppercase;letter-spacing:1px}
.ttl{flex:1}.ttl h1{margin:0;font-size:20px;font-weight:800;letter-spacing:-.01em}.meta{color:#8db1ce;font-size:12px;margin-top:2px}
.status{background:rgba(247,185,85,.13);color:#f7b955;border:1px solid rgba(247,185,85,.45);border-radius:999px;padding:5px 12px;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
.headline{background:linear-gradient(180deg,#0f2a3e,#0a1f2c);border-left:4px solid #43d477;border-radius:12px;padding:14px 18px;margin:16px 0}
.hl-tag{color:#8db1ce;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}.headline p{margin:0}.vd{font-weight:800}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:16px 0}
.kpi{background:linear-gradient(180deg,#0f2a3e,#0a1f2c);border:1px solid rgba(141,177,206,.16);border-radius:12px;padding:12px}.k-l{color:#8db1ce;font-size:11px;min-height:28px}
.k-v{font-size:20px;font-weight:700;margin:2px 0}.k-s{color:#8db1ce;font-size:11px}
.tile{background:linear-gradient(180deg,#0f2a3e,#0a1f2c);border:1px solid rgba(141,177,206,.16);border-radius:12px;padding:16px;margin:14px 0}
.tile h3{margin:0 0 2px;font-size:14px}.t-sub{color:#8db1ce;font-size:12px;margin-bottom:10px}
.war{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.adv{background:#08283a;border:1px solid #14364a;border-radius:8px;padding:14px}
.adv-h{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
.adv-n{font-weight:700;font-size:15px}.adv-b{color:#06131d;font-weight:800;font-size:11px;border-radius:5px;padding:2px 9px}
.adv-lens{color:#8db1ce;font-size:11px;margin-bottom:10px;min-height:30px}
.mrow{display:flex;justify-content:space-between;border-top:1px solid rgba(141,177,206,.16);padding:5px 0;font-size:12px}
.ml{color:#8db1ce}.mv{font-weight:700}
.why{color:#b9d0e0;font-size:12px;margin-top:12px;background:#08283a;border-left:3px solid #1ba7ff;border-radius:4px;padding:8px 11px}
.tests{display:flex;flex-direction:column;gap:11px}
.trow{}.t-top{display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px}.t-lab{color:#cfe0ee}.t-sc{font-weight:700}
.t-bar{height:9px;background:#08283a;border:1px solid #14364a;border-radius:5px;overflow:hidden}.t-fill{height:100%}
.t-wt{color:#8db1ce;font-size:10px;margin-top:2px}
.cfs{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:6px}
.cf{background:#08283a;border:1px solid #14364a;border-radius:8px;padding:12px;text-align:center}.cf.mid{border-color:#1ba7ff}
.cf-h{color:#8db1ce;font-size:11px;min-height:30px}.cf-v{font-size:19px;font-weight:700;margin:4px 0}.cf-s{color:#8db1ce;font-size:11px}
.ls-h{color:#b9d0e0;font-size:12px;font-weight:600;margin:12px 0 -6px}
.foot{color:#8db1ce;font-size:11px;border-top:1px solid #14364a;margin-top:20px;padding-top:12px}
@media(max-width:820px){.kpis{grid-template-columns:1fr 1fr}.war,.cfs{grid-template-columns:1fr}}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — ISS vs Glass Lewis</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _stale_published():
    """Rename a prior PUBLISHED.json to .stale (a refused/failed run must not leave an approval marker live)."""
    pub = OUT / "PUBLISHED.json"
    if pub.exists():
        try:
            pub.rename(pub.with_name("PUBLISHED.json.stale"))
        except OSError:
            try:
                pub.unlink()
            except OSError:
                pass


def _fail_closed(message):
    for p in (REPORT, DIGEST, OUT / "PUBLISHED.json"):
        if p.exists():
            try:
                p.rename(p.with_name(p.name + ".stale"))
            except OSError:
                try:
                    p.unlink()
                except OSError:
                    pass
    print(f"FAIL CLOSED: {_one_line(message)}", file=sys.stderr)
    return 1


def _atomic_write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Acme Corp ISS-vs-Glass-Lewis say-on-pay war room (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw = args.approved_by or ""
    approver = raw.strip()
    if args.publish and (any(ord(ch_) < 32 for ch_ in raw) or not APPROVER_RE.fullmatch(approver)):
        _stale_published()      # a refused publish must not leave a prior approval marker standing (SPEC)
        print("PUBLISH GATE: refused. Distribution requires a named committee approver.\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2
    try:
        report = build_report(G.compute())
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"glass-lewis view unavailable: {exc}")

    pub = OUT / "PUBLISHED.json"
    pub.unlink(missing_ok=True)
    try:
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST, pub):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        if args.publish:
            _atomic_write(pub, json.dumps({"approved_by": approver, "marker_type": "local_publish_marker", "registry_backed": False, "scope": SCOPE, "as_of": AS_OF,
                                           "verdict": report["syn"]["verdict"]}, indent=2) + "\n")
    except OSError as exc:
        return _fail_closed(f"could not write output: {exc}")

    gl, iss, syn = report["gl"], report["iss"], report["syn"]
    print(f"{COMPANY} ISS vs Glass Lewis — Say-on-Pay War Room ({AS_OF})")
    print(f"  GL {gl['concern']} ({gl['composite_score']:.0f}/100) · ISS {iss['concern']} · {syn['verdict']} · "
          f"support band {report['band'][0]:.0f}-{report['band'][1]:.0f}%")
    print("  wrote report.sample.html and day1-digest.sample.md")
    print("\nDRAFT only. The Compensation Committee owns the say-on-pay response. Nothing was sent."
          if not args.publish else f"\nApproved by {approver}. Recorded locally (no external send).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
