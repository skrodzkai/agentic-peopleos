#!/usr/bin/env python3
"""Merit / comp-cycle planning board agent — the annual-cycle deliverable a VP of Total Rewards takes into
the planning committee, rendered.

A dark board dashboard over the company-wide compensation cycle: the merit-increase budget allocated through a
performance x compa-ratio matrix, the bonus pool (target x attainment), promotion increases, and equity
refreshers -- which are emitted as append-valid rows in the equity-ledger schema (written to
output/equity_refresh_grants.sample.csv). They are FY2026 grants, so they carry into the NEXT period's board
equity metrics (burn/SBC/overhang) rather than the current close. Every number comes from
foundation/compute/merit_comp.py -- the
agent renders and governs; it does no math and it authorizes no pay.

IMPORTANT (on the dashboard and here): the merit matrix, bonus targets, company attainment, refresh grid, and
the merit budget are ILLUSTRATIVE placeholders -- a real cycle calibrates them to the plan.

    python3 run.py                                  # writes the draft dashboard + digest (nothing sent)
    python3 run.py --publish --approved-by "Compensation Committee Chair"
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from foundation import evidence_portfolio as portfolio_ev  # noqa: E402
from foundation.compute import merit_comp as MC        # noqa: E402
from foundation.compute import equity_spend as E        # noqa: E402  (the equity-ledger grant schema)
from foundation.render import charts as ch             # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
REFRESH_CSV = OUT / "equity_refresh_grants.sample.csv"   # the appendable grant-ledger delta (the real handoff)
COMPANY = "Acme Corp"
AS_OF = "FY2026 planning"
PERIOD = "FY2026 comp cycle · synthetic company-wide workforce"
SCOPE = "publish.merit_comp_planning"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")
_RATINGS = ("outstanding", "exceeds", "meets", "below")
_QUARTILES = (("q1", "<0.90"), ("q2", "0.90–1.0"), ("q3", "1.0–1.1"), ("q4", ">1.10"))
_LEVELS = ("L3", "L4", "L5", "L6", "L7")


class ReportError(RuntimeError):
    """Raised when the comp-cycle view cannot be produced (fail closed)."""


def _e(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _md(v):
    s = str(v)
    for ch_ in "\\`*_[]()~|<>":
        s = s.replace(ch_, "\\" + ch_)
    return s


def _one_line(t, limit=300):
    return " ".join(str(t).split())[:limit]


def _m(v):        # $ millions
    return f"${v / 1e6:,.1f}M"


def _plain_finite(*xs):
    return all(type(x) in (int, float) and math.isfinite(x) for x in xs)


def _is_count(*xs):
    # a real, non-negative integer count. `type(x) is int` (not isinstance) rejects bool — isinstance(True,
    # int) is True in Python, so a corrupt True/False reaching a headcount/grant/promotion field must fail.
    return all(type(x) is int and x >= 0 for x in xs)


_ACME = REPO / "foundation" / "data" / "acme"


def _group_split(grants):
    """Recompute the by-participant-group split straight from the grant rows (the engine's by_group must equal
    this — a check that a group can't be silently re-labeled while the totals still reconcile)."""
    out = {}
    for g in grants:
        b = out.setdefault(g["participant_group"], {"grants": 0, "shares": 0})
        b["grants"] += 1
        b["shares"] += g["shares_granted"]
    return out


def _grants_are_append_valid(grants):
    """The REAL 'append-valid' guarantee, not just a structural check: append the emitted grant rows to a copy
    of the live equity ledger and construct EquityPlan over it. This runs the equity arm's FULL validation
    surface (plan_id existence, emp existence, grant_date within the plan window + after any term, vesting
    coherence, PSU rules, grant-id uniqueness, finite fair value) — so a row the ledger would reject can never
    be rendered as 'append-valid'. Returns True iff the augmented ledger validates."""
    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp()) / "acme"
        shutil.copytree(_ACME, tmp)
        with open(tmp / "equity_grants.csv", "a", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(E._GRANT_COLS), lineterminator="\n")
            w.writerows(grants)
        before = len(E.EquityPlan(_ACME).grants)
        after = E.EquityPlan(tmp)                      # re-validates the whole ledger incl. the appended rows
        return len(after.grants) == before + len(grants)
    except (E.EquityDataError, OSError, ValueError, KeyError):
        return False
    finally:
        if tmp is not None:
            shutil.rmtree(tmp.parent, ignore_errors=True)


# ---------------------------------------------------------------- build + validate
def build_report(result):
    """Validate the engine output (fail closed) and shape it for rendering. No math is done here."""
    r = result
    m = r["merit"]
    er = r["equity_refresh"]
    checks = [
        _plain_finite(r["payroll"], m["budget"], m["spend"], m["spend_pct"], m["budget_pct"], m["headroom"]),
        r["eligible_headcount"] > 0 and r["payroll"] > 0,
        # the merit pool + spend are non-negative dollars, and the budget is a sane fraction of payroll (a
        # negative pool/spend or an absurd budget % would render as garbage $/% in the headline). NB: headroom
        # is intentionally NOT floored at 0 — it is negative on the OVER-BUDGET path (guarded by within_budget).
        m["budget"] >= 0 and m["spend"] >= 0 and 0.0 <= m["budget_pct"] <= 100.0,
        isinstance(m["within_budget"], bool) and m["within_budget"],           # the plan must fit the budget
        m["spend"] <= m["budget"] + 1.0 and abs((m["budget"] - m["spend"]) - m["headroom"]) < 2.0,  # $ roundings
        0.0 <= m["spend_pct"] <= m["budget_pct"] + 0.01,
        _plain_finite(r["bonus_pool"], r["promo_spend"], r["equity_refresh"]["total_value"]),
        r["bonus_pool"] >= 0 and r["promo_spend"] >= 0 and r["equity_refresh"]["total_value"] >= 0,
        # every rendered COUNT is a real non-negative int — a NaN/inf/negative/bool would otherwise reach the
        # committee headline, the KPI band, the equity-handoff tile, or the digest as garbage
        _is_count(r["promotions"], r["equity_refresh"]["grant_count"], r["equity_refresh"]["total_shares"]),
        # counts drawn from the population can't exceed it (can't promote / exceed band-max for more people
        # than exist)
        r["promotions"] <= r["eligible_headcount"] and r["over_max_after_merit"] <= r["eligible_headcount"],
        _plain_finite(r["avg_compa_ratio"], r["avg_new_compa_ratio"]),
        r["avg_compa_ratio"] > 0 and r["avg_new_compa_ratio"] > 0,              # a compa-ratio (base/mid) is positive
        r["avg_new_compa_ratio"] >= r["avg_compa_ratio"],                       # merit moves toward market
        # exact rating set AND every rating carries all four quartiles (guards the KeyErrors below)
        set(r["matrix"]) == set(_RATINGS)
        and all(set(r["matrix"][rt]) == {q for q, _ in _QUARTILES} for rt in r["matrix"]),
        all(_plain_finite(r["matrix"][rt][q]) and 0.0 <= r["matrix"][rt][q] < 0.5
            for rt in r["matrix"] for q, _ in _QUARTILES),
        # matrix discipline: within a rating, merit % is monotone non-increasing as compa-ratio rises
        all(r["matrix"][rt]["q1"] >= r["matrix"][rt]["q2"] >= r["matrix"][rt]["q3"] >= r["matrix"][rt]["q4"]
            for rt in r["matrix"] if set(r["matrix"][rt]) == {q for q, _ in _QUARTILES}),
        # matrix discipline across ratings: at each compa-quartile, a higher rating never pays LESS than a lower one
        all(r["matrix"]["outstanding"][q] >= r["matrix"]["exceeds"][q]
            >= r["matrix"]["meets"][q] >= r["matrix"]["below"][q] for q, _ in _QUARTILES)
        if set(r["matrix"]) == set(_RATINGS) else False,
        _is_count(r["over_max_after_merit"]),
        # the by-rating aggregates drive the spend histogram + allocation strip: the key-set must be a
        # non-empty subset of the known ratings (an empty by_rating would render a blank strip silently), every
        # amount must be finite AND non-negative before it reaches the chart geometry (a NaN -> nan SVG
        # height/y; a negative -> "$-1.5M"), and the per-rating headcounts must be real ints that reconcile to
        # the eligible population
        bool(r["by_rating"]) and set(r["by_rating"]) <= set(_RATINGS),
        all(_plain_finite(r["by_rating"][rt][f]) and r["by_rating"][rt][f] >= 0 for rt in r["by_rating"]
            for f in ("merit_amount", "bonus_amount", "actual_base")),
        all(_is_count(r["by_rating"][rt]["n"]) for rt in r["by_rating"])
        and sum(r["by_rating"][rt]["n"] for rt in r["by_rating"]) == r["eligible_headcount"],
        # the on-leave disclosure must be a real split that reconciles with the eligible headcount (honest
        # labeling): both counts non-negative ints, neither exceeds eligible, and they sum to it
        _is_count(r["on_leave_count"], r["active_count"], r["eligible_headcount"])
        and r["active_count"] <= r["eligible_headcount"]
        and r["active_count"] + r["on_leave_count"] == r["eligible_headcount"],
        # CROSS-FIELD RECONCILIATION — the charts must not contradict the headline: the by-rating spend
        # totals reconcile to the headline merit spend + bonus pool (within $ rounding), and the promotion
        # count is consistent with the promotion spend (both zero or both positive)
        abs(sum(r["by_rating"][rt]["merit_amount"] for rt in r["by_rating"]) - m["spend"]) < 2.0,
        abs(sum(r["by_rating"][rt]["bonus_amount"] for rt in r["by_rating"]) - r["bonus_pool"]) < 2.0,
        (r["promotions"] == 0) == (r["promo_spend"] == 0),
        # the equity-refresher aggregates reconcile to the emitted grant rows (the appendable artifact): the
        # rows exist, count + share totals match, and the participant-group split sums back to the totals
        isinstance(er.get("grants"), list) and len(er["grants"]) == er["grant_count"]
        and sum(g["shares_granted"] for g in er["grants"]) == er["total_shares"],
        _plain_finite(sum(g["shares_granted"] for g in er["grants"]))
        and sum(gb["grants"] for gb in er["by_group"].values()) == er["grant_count"]
        and sum(gb["shares"] for gb in er["by_group"].values()) == er["total_shares"],
        # EVERY emitted grant row must be genuinely append-valid against the equity ledger, not just aggregate-
        # consistent: exact schema/column order, a MERIT participant group (ceo/section16/management/staff —
        # NOT 'director', which the group split doesn't render and no employee-refresher should carry), a known
        # award/grant type, and a positive integer share count (a bad row must not render as "append-valid")
        all(tuple(g.keys()) == E._GRANT_COLS and g["participant_group"] in MC._LEDGER_GROUPS
            and g["award_type"] in E._AWARDS and g["grant_type"] in E._GRANT_TYPES
            and type(g["shares_granted"]) is int and g["shares_granted"] > 0 for g in er["grants"]),
        # no duplicate grant ids among the emitted rows (they must not collide with each other or the ledger)
        len({g["grant_id"] for g in er["grants"]}) == len(er["grants"]),
        # the by_group split must be RECOMPUTED from the grant rows, not merely sum-consistent — otherwise CEO
        # grants could be moved to 'staff' while the totals still reconcile
        er["by_group"] == _group_split(er["grants"]),
    ]
    if not all(checks):
        raise ReportError(f"merit-comp result failed validation (check #{checks.index(False)})")
    # after the cheap structural checks pass, prove the emitted rows are REALLY append-valid against the live
    # equity ledger (catches a corrupted plan_id / emp_id / grant_date / vesting / fair value the structure
    # check can't see). Run last so the fast field checks short-circuit first.
    if not _grants_are_append_valid(er["grants"]):
        raise ReportError("emitted equity-refresher rows are not append-valid against the equity ledger")
    verdict = ("ON-BUDGET — the cycle fits the merit pool with headroom" if m["headroom"] >= 0
               else "OVER-BUDGET — reduce allocations before committee sign-off")
    return {"r": r, "m": m, "verdict": verdict}


# ---------------------------------------------------------------- render
_GRP_LABEL = (("ceo", "CEO"), ("section16", "Sec 16"), ("management", "mgmt"), ("staff", "staff"))


def _grp_split(by_group):
    # the refreshers by preserved equity-ledger group, most-senior first (executives stay executives)
    parts = [f"{by_group[g]['grants']:,} {lab}" for g, lab in _GRP_LABEL if g in by_group and by_group[g]["grants"]]
    return " · ".join(parts)


def _kpi(label, value, sub, color=None):
    vc = f" style='color:{color}'" if color else ""
    return (f"<div class='kpi'><div class='k-l'>{_e(label)}</div>"
            f"<div class='k-v mono'{vc}>{_e(value)}</div><div class='k-s'>{_e(sub)}</div></div>")


def _merit_cell(pct):
    # green heat for the biggest increases, fading to muted for 0
    if pct <= 0:
        return "#0a1f2c", "#4a6072"
    t = min(1.0, pct / 0.07)
    return f"rgba(67,212,119,{0.12 + 0.5 * t:.2f})", "#dbe7f0"


def render_html(report):
    r, m = report["r"], report["m"]
    body = []
    body.append(f"<header class='top'><div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                f"<div class='sub'>Total Rewards · Comp Cycle</div></div>"
                f"<div class='ttl'><h1>Merit &amp; Comp-Cycle Plan — Committee Review</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                f"<span class='status'>Draft · awaiting committee approval</span></header>")
    vc = ch.GREEN if m["headroom"] >= 0 else ch.AMBER
    body.append(f"<section class='headline' style='border-color:{vc}'>"
                f"<div class='hl-tag'>Cycle headline</div>"
                f"<p>The merit pool of <b>{_m(m['budget'])}</b> (<b>{m['budget_pct']:.1f}%</b> of a "
                f"<b>{_m(r['payroll'])}</b> eligible payroll) funds a <b>{m['spend_pct']:.2f}%</b> average "
                f"increase across <b>{r['eligible_headcount']:,}</b> employees "
                f"(<b>{r['active_count']:,}</b> active + <b>{r['on_leave_count']:,}</b> on protected leave, "
                f"who stay merit-eligible), leaving <b>{_m(m['headroom'])}</b> "
                f"of headroom. Alongside: a <b>{_m(r['bonus_pool'])}</b> bonus pool, <b>{r['promotions']}</b> "
                f"promotions, and <b>{_m(r['equity_refresh']['total_value'])}</b> of FY2026 equity refreshers "
                f"emitted as append-valid grant-ledger rows. <span class='vd' style='color:{vc}'>{_e(report['verdict'])}</span></p></section>")
    # KPI band
    body.append("<section class='kpis'>"
                + _kpi("Merit spend", f"{m['spend_pct']:.2f}%", f"pool {m['budget_pct']:.1f}% · {_m(m['headroom'])} headroom",
                       ch.GREEN if m["within_budget"] else ch.AMBER)
                + _kpi("Bonus pool", _m(r["bonus_pool"]), "target × attainment × rating")
                + _kpi("Promotions", f"{r['promotions']}", f"{_m(r['promo_spend'])} in promo increases")
                + _kpi("Equity refreshers", _m(r["equity_refresh"]["total_value"]),
                       f"{r['equity_refresh']['grant_count']:,} grants → ledger")
                + _kpi("Compa-ratio", f"{r['avg_compa_ratio']:.2f} → {r['avg_new_compa_ratio']:.2f}",
                       "average, moved toward market")
                + "</section>")
    # the merit matrix — the centerpiece
    head = "".join(f"<th>{_e(lab)}</th>" for _, lab in _QUARTILES)
    rows = ""
    for rt in _RATINGS:
        cells = ""
        for q, _ in _QUARTILES:
            pct = r["matrix"][rt][q]
            bg, fg = _merit_cell(pct)
            cells += f"<td style='background:{bg};color:{fg}'>{pct * 100:.1f}%</td>"
        rows += f"<tr><th class='rl'>{_e(rt)}</th>{cells}</tr>"
    body.append("<section class='tile'><h3>Merit matrix — increase % by rating × compa-ratio</h3>"
                "<div class='t-sub'>Higher performers who sit below market get the largest increases; a low "
                "performer already above market gets 0%. Illustrative cells — a real cycle calibrates them.</div>"
                f"<table class='mtx'><tr><th></th>{head}</tr>{rows}</table>"
                "<div class='mx-cap'>columns = compa-ratio band (base ÷ range-mid)</div></section>")
    # merit spend by rating
    order = [rt for rt in ("outstanding", "exceeds", "meets", "below") if rt in r["by_rating"]]
    hist = ch.histogram([round(r["by_rating"][rt]["merit_amount"] / 1e6, 2) for rt in order], list(order))
    body.append("<section class='tile'><h3>Where the merit budget goes — spend by rating</h3>"
                "<div class='t-sub'>Differentiation: the pool concentrates on stronger performers, not a "
                "flat across-the-board increase.</div>" + hist
                + "<div class='alloc'>"
                + "".join(f"<span class='ag'>{_e(rt)}: {_m(r['by_rating'][rt]['merit_amount'])} · "
                          f"{r['by_rating'][rt]['n']:,} ppl</span>" for rt in order)
                + "</div></section>")
    # guardrail + equity handoff
    body.append("<section class='tile wide'><h3>Guardrails &amp; the equity handoff</h3>"
                "<div class='guards'>"
                f"<div class='g'><div class='g-v mono' style='color:{ch.GREEN if m['within_budget'] else ch.RED}'>"
                f"{'PASS' if m['within_budget'] else 'OVER'}</div><div class='g-l'>Budget conformance — "
                f"{_m(m['spend'])} of {_m(m['budget'])}</div></div>"
                f"<div class='g'><div class='g-v mono' style='color:{ch.AMBER if r['over_max_after_merit'] else ch.GREEN}'>"
                f"{r['over_max_after_merit']}</div><div class='g-l'>above band-max after merit — flag for committee "
                f"review/capping</div></div>"
                f"<div class='g'><div class='g-v mono' style='color:{ch.CYAN}'>"
                f"{r['equity_refresh']['total_shares']:,}</div><div class='g-l'>refresher shares as append-valid "
                f"rows in the equity-ledger schema ({_grp_split(r['equity_refresh']['by_group'])}) — FY2026 grants, "
                f"so they carry into the <b>next</b> period's board burn/SBC/overhang</div></div>"
                "</div></section>")
    body.append("<footer class='foot'>Built by the <b>merit-comp-planning</b> agent · it renders the cycle plan; "
                "the <b>Compensation Committee</b> approves budgets, increases, and promotions. The merit matrix, "
                "bonus targets, attainment, refresh grid, and budget are <b>illustrative</b> placeholders. "
                "Synthetic company-wide workforce; presentation + governance only — the agent authorizes no pay.</footer>")
    return _page("".join(body))


def render_digest(report):
    r, m = report["r"], report["m"]
    return "\n".join([
        f"# {COMPANY} — Merit & Comp-Cycle Plan (committee digest, {AS_OF})", "",
        f"**{_md(report['verdict'])}**", "",
        f"- **Eligible:** {r['eligible_headcount']:,} employees "
        f"({r['active_count']:,} active + {r['on_leave_count']:,} on protected leave, still merit-eligible — "
        "matches the People Analytics headcount definition)",
        f"- **Merit:** {m['spend_pct']:.2f}% avg increase · {_m(m['spend'])} of a {_m(m['budget'])} pool "
        f"({m['budget_pct']:.1f}% of {_m(r['payroll'])} payroll) · {_m(m['headroom'])} headroom",
        f"- **Bonus pool:** {_m(r['bonus_pool'])} (target × attainment × individual factor)",
        f"- **Promotions:** {r['promotions']} · {_m(r['promo_spend'])} in promotion increases",
        f"- **Equity refreshers:** {_m(r['equity_refresh']['total_value'])} across "
        f"{r['equity_refresh']['grant_count']:,} grants ({r['equity_refresh']['total_shares']:,} shares; "
        f"{_grp_split(r['equity_refresh']['by_group'])}) — emitted as append-valid FY2026 grant-ledger rows "
        "(`output/equity_refresh_grants.sample.csv`) that carry into the next period's board equity metrics",
        f"- **Compa-ratio:** {r['avg_compa_ratio']:.2f} → {r['avg_new_compa_ratio']:.2f} (moved toward market); "
        f"{r['over_max_after_merit']} above band-max after merit (flagged)",
        "", "_Synthetic company-wide workforce. The merit matrix, bonus targets, attainment, refresh grid, and "
        "budget are illustrative placeholders. Presentation + governance only — the agent authorizes no pay; "
        "the Compensation Committee approves the cycle. Draft._"])


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
.k-v{font-size:19px;font-weight:700;margin:2px 0}.k-s{color:#8db1ce;font-size:11px}
.tile{background:linear-gradient(180deg,#0f2a3e,#0a1f2c);border:1px solid rgba(141,177,206,.16);border-radius:12px;padding:16px;margin:14px 0}
.tile h3{margin:0 0 2px;font-size:14px}.t-sub{color:#8db1ce;font-size:12px;margin-bottom:10px}
.mtx{border-collapse:collapse;width:100%;font-size:13px;text-align:center}
.mtx th{color:#8db1ce;font-weight:600;font-size:12px;padding:6px}.mtx td{padding:9px 6px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-weight:700;border:1px solid #06131d}
.mtx .rl{text-align:right;color:#cfe0ee;padding-right:12px}.mx-cap{color:#8db1ce;font-size:11px;margin-top:6px}
.alloc{margin-top:10px;display:flex;flex-wrap:wrap;gap:8px}.ag{background:#08283a;border:1px solid #14364a;border-radius:4px;padding:3px 8px;font-size:11px;color:#b9d0e0}
.guards{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.g{background:#08283a;border:1px solid #14364a;border-radius:8px;padding:14px;text-align:center}
.g-v{font-size:24px;font-weight:800;margin-bottom:4px}.g-l{color:#8db1ce;font-size:11px}
.foot{color:#8db1ce;font-size:11px;border-top:1px solid #14364a;margin-top:20px;padding-top:12px}
@media(max-width:820px){.kpis{grid-template-columns:1fr 1fr}.guards{grid-template-columns:1fr}}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — Merit & Comp-Cycle Plan</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _stale_published():
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
    # stale EVERY committed artifact — incl. the PNG snapshot — so a refused run never leaves a live dashboard
    for p in portfolio_ev.managed_outputs(REPORT, DIGEST) + (
            REFRESH_CSV, OUT / "report.sample.png", OUT / "PUBLISHED.json"):
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


def _grants_csv(grants):
    """The equity-refresher grant rows serialized in the equity ledger's EXACT column order (E._GRANT_COLS) —
    an append-valid, deterministic delta. It is a self-describing CSV WITH a header row; to append to the
    ledger, concatenate its DATA rows (skip the header line), as the engine handshake test does."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(E._GRANT_COLS), lineterminator="\n")
    w.writeheader()
    w.writerows(grants)                       # rows are already sorted by emp_id in the engine (deterministic)
    return buf.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Acme Corp merit / comp-cycle planning dashboard (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw = args.approved_by or ""
    approver = raw.strip()
    if args.publish and (any(ord(c) < 32 for c in raw) or not APPROVER_RE.fullmatch(approver)):
        _stale_published()
        print("PUBLISH GATE: refused. Distribution requires a named committee approver.\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2
    try:
        report = build_report(MC.compute())
        html_doc, digest_doc = render_html(report), render_digest(report)
        html_doc, digest_doc, report_evidence, digest_evidence = portfolio_ev.prepare_pair(
            "merit-comp-planning", report, html_doc, digest_doc, REPO)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"merit-comp view unavailable: {exc}")

    pub = OUT / "PUBLISHED.json"
    pub.unlink(missing_ok=True)
    try:
        OUT.mkdir(exist_ok=True)
        for p in portfolio_ev.managed_outputs(REPORT, DIGEST) + (REFRESH_CSV, pub):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        portfolio_ev.write_sidecars(REPORT, DIGEST, report_evidence, digest_evidence)
        _atomic_write(REFRESH_CSV, _grants_csv(report["r"]["equity_refresh"]["grants"]))
        if args.publish:
            _atomic_write(pub, json.dumps({"approved_by": approver, "marker_type": "local_publish_marker", "registry_backed": False, "scope": SCOPE, "as_of": AS_OF,
                                           "verdict": report["verdict"]}, indent=2) + "\n")
    except OSError as exc:
        return _fail_closed(f"could not write output: {exc}")

    m = report["m"]
    print(f"{COMPANY} Merit & Comp-Cycle Plan ({AS_OF})")
    print(f"  merit {m['spend_pct']:.2f}% of {_m(report['r']['payroll'])} (pool {m['budget_pct']:.1f}%) · "
          f"bonus {_m(report['r']['bonus_pool'])} · {report['r']['promotions']} promos · {report['verdict'].split(' —')[0]}")
    print("  wrote report.sample.html, day1-digest.sample.md, and equity_refresh_grants.sample.csv")
    print("\nDRAFT only. The Compensation Committee approves the cycle. Nothing was sent."
          if not args.publish else f"\nPublished locally by {approver} — a local PUBLISHED.json marker "
          "(named-approver acknowledgment, not a registry-backed approval); no external send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
