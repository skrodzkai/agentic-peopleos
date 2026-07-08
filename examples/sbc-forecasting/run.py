#!/usr/bin/env python3
"""Acme Corp — Stock-Based-Compensation (SBC) expense forecast agent (Agentic PeopleOS Executive-Comp arm).

Where the equity-spend arm answers "what did we spend, is the plan defensible?", this arm answers the
CFO/controller's forecasting question a Total-Rewards leader has to defend in the guidance conversation:
"how much SBC expense is already LOCKED IN for the next few years, and what will the run-rate be?" Most of the
near-term SBC line is not a choice — it is the amortization of grants already made, rolling off a fixed
schedule. This dashboard renders that runoff by fiscal year (with an illustrative forfeiture-rate haircut and
new-grant overlay), computed entirely by foundation/compute/sbc_forecast.py. The agent does no math and
guides nothing; it presents.

IMPORTANT (on the dashboard and here): the locked-in runoff is pure amortization of grants already made and
ties to the equity-spend backlog; the forfeiture rate, the new-grant run-rate, and the flat-revenue basis are
ILLUSTRATIVE assumptions, never guidance.

    python3 run.py                                       # writes the draft dashboard + digest (nothing sent)
    python3 run.py --publish --approved-by "Chief Financial Officer"

Standard library only; deterministic; offline.
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

from foundation.compute import sbc_forecast as SBC        # noqa: E402
from foundation.render import dashboard as dash           # noqa: E402
from foundation.render import charts as ch                # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AGENT = "sbc-forecasting"
SCOPE = "publish.sbc_forecast"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")


class ReportError(RuntimeError):
    """Raised when the SBC forecast cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _finite(*vals):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals)


def _m(v) -> str:
    """Compact USD, e.g. $85.9M / $1.20B."""
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


# ---------- build (validate the engine output) ----------

def build_report(result):
    for k in ("company", "as_of", "horizon_fys", "locked_in", "new_grant_overlay", "total_forecast",
              "assumptions", "context"):
        if k not in result:
            raise ReportError(f"engine result missing '{k}'")
    li = result["locked_in"]
    sched = li["schedule"]
    if not sched:
        raise ReportError("empty locked-in runoff schedule")

    # every rendered number finite
    if not _finite(li["backlog_unrecognized_usd"], li["wavg_remaining_years"], li["beyond_horizon_usd"]):
        raise ReportError("non-finite locked-in headline statistics")
    for s in sched:
        if not _finite(s["gross_expense"], s["forfeiture_adj_expense"], s["cumulative_gross"]):
            raise ReportError(f"FY{s['fy']}: non-finite runoff statistics")
        if s["gross_expense"] < 0:
            raise ReportError(f"FY{s['fy']}: negative gross expense")
        if s["forfeiture_adj_expense"] > s["gross_expense"] + 1e-6:
            raise ReportError(f"FY{s['fy']}: forfeiture-adjusted exceeds gross (haircut must not add expense)")

    # the runoff must reconcile EXACTLY (to the penny) — the dashboard claims the gross figures "sum exactly"
    # to the backlog. Compare in integer cents with zero tolerance, so any drift (even a cent) fails closed.
    def _c(x):
        return round(x * 100)
    gross_c = sum(_c(s["gross_expense"]) for s in sched)
    if gross_c + _c(li["beyond_horizon_usd"]) != _c(li["backlog_unrecognized_usd"]):
        raise ReportError("locked-in runoff does not reconcile to the backlog to the penny "
                          f"(gross {gross_c} + tail {_c(li['beyond_horizon_usd'])} != backlog "
                          f"{_c(li['backlog_unrecognized_usd'])} cents)")
    # and each cumulative must be the EXACT running sum of the displayed gross (not merely monotonic)
    run_c = 0
    for s in sched:
        run_c += _c(s["gross_expense"])
        if _c(s["cumulative_gross"]) != run_c:
            raise ReportError(f"FY{s['fy']}: cumulative is not the running sum of gross expense")

    tf = result["total_forecast"]
    for t in tf:
        if not _finite(t["locked_in"], t["new_grants"], t["total"], t["pct_ttm_revenue"]):
            raise ReportError(f"FY{t['fy']}: non-finite total-forecast statistics")
        if abs(t["total"] - (t["locked_in"] + t["new_grants"])) > 0.01:
            raise ReportError(f"FY{t['fy']}: total != locked-in + new-grant overlay")

    a = result["assumptions"]
    if not (0 < a["forfeiture_rate_annual_pct"] < 100) or a["new_grant_run_rate_usd"] <= 0:
        raise ReportError("illustrative assumptions out of range")

    cards = [
        {"value": _m(li["backlog_unrecognized_usd"]), "label": "Locked-in SBC backlog"},
        {"value": f"{li['wavg_remaining_years']:.1f} yr", "label": "Wtd-avg remaining vesting"},
        {"value": _m(sched[0]["gross_expense"]), "label": f"FY{sched[0]['fy']} locked-in expense"},
        {"value": f"FY{li['runoff_complete_fy']}" if li["runoff_complete_fy"] else "beyond horizon",
         "label": "Locked-in runoff complete"},
        {"value": f"{result['context']['backlog_pct_market_cap']:.1f}%", "label": "Backlog · % market cap"},
        {"value": _m(tf[0]["total"]), "label": f"FY{tf[0]['fy']} total forecast"},
    ]
    return {"r": result, "li": li, "tf": tf, "cards": cards, "narrative": _narrative(result)}


def _narrative(result):
    li = result["li"] if "li" in result else result["locked_in"]
    sched = li["schedule"]
    tf = result["total_forecast"]
    a = result["assumptions"]
    parts = [
        f"{_m(li['backlog_unrecognized_usd'])} of SBC expense is already locked in from grants made — it "
        f"rolls off over ~{li['wavg_remaining_years']:.1f} years, from {_m(sched[0]['gross_expense'])} in "
        f"FY{sched[0]['fy']} down to {_m(sched[-1]['gross_expense'])} by FY{sched[-1]['fy']} as those grants "
        f"finish vesting. This runoff is not a choice; it ties to the equity-spend backlog.",
        f"Holding grants at the trailing-12-month run-rate ({_m(a['new_grant_run_rate_usd'])}/yr, illustrative) "
        f"and haircutting for a {a['forfeiture_rate_annual_pct']:.0f}% estimated forfeiture rate, total SBC "
        f"lands near {_m(tf[0]['total'])} in FY{tf[0]['fy']} rising toward {_m(tf[-1]['total'])} by "
        f"FY{tf[-1]['fy']} as the new-grant layer ramps.",
    ]
    return " ".join(parts)


# ---------- rendering ----------

def render_html(report):
    result, li, tf = report["r"], report["li"], report["tf"]
    sched = li["schedule"]
    a = result["assumptions"]
    body = [
        dash.brand_header(),
        dash.title_block("SBC Expense Forecast",
                         "Stock-Based-Compensation Forecast",
                         f"{COMPANY} · as of fiscal close {result['as_of']} · synthetic"),
        dash.narrator(report["narrative"]),
        dash.kpi_cards(report["cards"]),
    ]

    # 1) locked-in runoff — the certain part
    body.append(dash.section("Locked-in SBC runoff — amortization of grants already made"))
    mx = max((s["gross_expense"] for s in sched), default=1) or 1
    body.append(dash.bars([{"label": f"FY{s['fy']}", "value": round(s["gross_expense"] / 1e6, 1), "max": mx / 1e6,
                            "color": "var(--cyan)"} for s in sched if s["gross_expense"] > 0]))
    body.append(dash.data_table(
        ["Fiscal year", "Gross expense", "Forfeiture-adj (illus.)", "Cumulative"],
        [[f"FY{s['fy']}", _m(s["gross_expense"]), _m(s["forfeiture_adj_expense"]), _m(s["cumulative_gross"])]
         for s in sched], center_from=1))
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                f"The gross runoff sums exactly to the {_m(li['backlog_unrecognized_usd'])} unamortized backlog "
                "(it just splits it by fiscal year); it is the same straight-line amortization the equity-spend "
                "arm reports. The forfeiture-adjusted column applies an <b>illustrative</b> "
                f"{a['forfeiture_rate_annual_pct']:.0f}%/yr estimated forfeiture rate — GAAP (ASU 2016-09) "
                "lets an issuer estimate forfeitures rather than wait for them.</div>")

    # 2) total go-forward forecast — locked-in + illustrative new grants
    body.append(dash.section("Total go-forward SBC forecast (locked-in + illustrative new grants)"))
    labels = [f"FY{t['fy']}" for t in tf]
    totals = [round(t["total"] / 1e6, 1) for t in tf]
    pcts = [t["pct_ttm_revenue"] for t in tf]
    body.append(ch.dual_axis_line(labels, totals, pcts,
                                  left_fmt=lambda v: f"${v}M", right_fmt=lambda v: f"{v}%", uid="sbc_total"))
    body.append(dash.data_table(
        ["Fiscal year", "Locked-in", "New grants (illus.)", "Total SBC", "% of TTM rev"],
        [[f"FY{t['fy']}", _m(t["locked_in"]), _m(t["new_grants"]), _m(t["total"]),
          f"{t['pct_ttm_revenue']:.1f}%" if t["pct_ttm_revenue"] is not None else "—"] for t in tf],
        center_from=1))
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                f"New-grant layer assumes the company keeps granting at its trailing-12-month run-rate "
                f"(<b>{_m(a['new_grant_run_rate_usd'])}/yr</b>), each vintage straight-line over "
                f"{a['new_grant_vest_months']} months; the % line holds revenue flat at the last "
                "trailing-twelve-months figure. <b>All three are illustrative assumptions, not guidance.</b> "
                "As the locked-in book rolls off, the new-grant layer carries the run-rate — the total is "
                "roughly flat, which is the real story for a guidance conversation.</div>")

    # honesty + governance
    body.append(dash.section("What this is — and is not"))
    body.append("<div style='display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px'>"
                + dash.chip("Locked-in runoff: assumption-free", "ok")
                + dash.chip("Reconciles to equity-spend backlog", "ok")
                + dash.chip("Forfeiture rate: illustrative", "warn")
                + dash.chip("New-grant run-rate: illustrative", "warn")
                + dash.chip("Not financial guidance", "bad") + "</div>")
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                + dash._esc(result["disclaimer"]) + "</div>")
    body.append(dash.governance_footer(AGENT))
    return dash.page(f"{COMPANY} — SBC Expense Forecast", "".join(body))


def render_digest(report):
    result, li, tf = report["r"], report["li"], report["tf"]
    sched = li["schedule"]
    a = result["assumptions"]
    lines = [f"# {COMPANY} — SBC forecast digest", f"_As of fiscal close {result['as_of']} · draft for review_",
             "", f"- {report['narrative']}",
             f"- Locked-in runoff: {' -> '.join(_m(s['gross_expense']) for s in sched if s['gross_expense'] > 0)} "
             f"(FY{sched[0]['fy']} onward), completing FY{li['runoff_complete_fy']}. Reconciles to the "
             f"{_m(li['backlog_unrecognized_usd'])} equity-spend backlog.",
             f"- Total forecast (locked-in + illustrative {_m(a['new_grant_run_rate_usd'])}/yr run-rate, "
             f"{a['forfeiture_rate_annual_pct']:.0f}% forfeiture): "
             f"{' -> '.join(_m(t['total']) for t in tf)}."]
    lines += ["", "_Locked-in runoff is assumption-free amortization; forfeiture rate, new-grant run-rate, and "
              "flat revenue are illustrative. Not financial guidance._",
              "", "_Publish gate: a human (Finance / Total Rewards) must approve before distribution._"]
    return "\n".join(lines) + "\n"


# ---------- fail-closed + entrypoint ----------

def _fail_closed(message) -> int:
    for p in (REPORT, DIGEST):
        try:
            if p.exists():
                p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            pass
    (OUT / "PUBLISHED.json").unlink(missing_ok=True)
    print(f"FAIL CLOSED: {_one_line(message)}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp SBC-forecast agent (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (Finance / Total Rewards).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        result = SBC.compute()
        report = build_report(result)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except (ReportError, SBC.SBCDataError) as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"SBC-forecast engine unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)
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
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": result["as_of"]},
                                     indent=2) + "\n")
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    li = result["locked_in"]
    print(f"{COMPANY} SBC forecast — as of {result['as_of']}")
    print(f"  locked-in backlog {_m(li['backlog_unrecognized_usd'])} | FY{li['schedule'][0]['fy']} "
          f"{_m(li['schedule'][0]['gross_expense'])} -> runoff complete FY{li['runoff_complete_fy']}")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (Finance / Total Rewards) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
