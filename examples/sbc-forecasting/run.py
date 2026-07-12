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

from core import evidence as ev                            # noqa: E402
from foundation.compute import sbc_forecast as SBC        # noqa: E402
from foundation.render import dashboard as dash           # noqa: E402
from foundation.render import charts as ch                # noqa: E402
from foundation.render import evidence as er               # noqa: E402

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

    # the dashboard SAYS the backlog "reconciles to the equity-spend arm" — so verify that AT RUNTIME, not
    # just in a test: import the equity-spend engine, compute its unamortized-SBC backlog over the committed
    # data, and fail closed on any drift. (The committed dashboard renders the default dataset, which both
    # engines read; the engine's own reconciliation test exercises custom data dirs directly.)
    try:
        from foundation.compute import equity_spend as _E
        es_backlog = _E.compute()["unamortized_sbc"]
    except Exception as exc:
        raise ReportError(f"could not cross-check the equity-spend backlog at build time: {exc}")
    if _c(es_backlog) != _c(li["backlog_unrecognized_usd"]):
        raise ReportError(f"backlog does not reconcile to the equity-spend arm at runtime "
                          f"(sbc {_c(li['backlog_unrecognized_usd'])} != equity-spend {_c(es_backlog)} cents)")

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
        {"value": _m(li["backlog_unrecognized_usd"]), "label": "Locked-in SBC backlog",
         "claim_id": "claim.sbc.backlog"},
        {"value": f"{li['wavg_remaining_years']:.1f} yr", "label": "Wtd-avg remaining vesting",
         "claim_id": "claim.sbc.remaining-vesting"},
        {"value": _m(sched[0]["gross_expense"]), "label": f"FY{sched[0]['fy']} locked-in expense",
         "claim_id": "claim.sbc.first-year-locked"},
        {"value": f"FY{li['runoff_complete_fy']}" if li["runoff_complete_fy"] else "beyond horizon",
         "label": "Locked-in runoff complete", "claim_id": "claim.sbc.runoff-complete"},
        {"value": f"{result['context']['backlog_pct_market_cap']:.1f}%", "label": "Backlog · % market cap",
         "claim_id": "claim.sbc.backlog-market-cap"},
        {"value": _m(tf[0]["total"]), "label": f"FY{tf[0]['fy']} total forecast",
         "claim_id": "claim.sbc.first-year-total"},
    ]
    return {"r": result, "li": li, "tf": tf, "cards": cards, "narrative": _narrative(result)}


def _narrative(result):
    li = result["li"] if "li" in result else result["locked_in"]
    sched = li["schedule"]
    tf = result["total_forecast"]
    a = result["assumptions"]
    parts = [
        f"{_m(li['backlog_unrecognized_usd'])} of SBC expense is already locked in from grants made — it "
        f"rolls off over {li['wavg_remaining_years']:.1f} yr, from {_m(sched[0]['gross_expense'])} in "
        f"FY{sched[0]['fy']} down to {_m(sched[-1]['gross_expense'])} by FY{sched[-1]['fy']} as those grants "
        f"finish vesting (the final non-zero runoff is FY{li['runoff_complete_fy']}). This runoff is not a "
        f"choice; it ties to the equity-spend backlog.",
        f"Holding grants at the trailing-12-month run-rate ({_m(a['new_grant_run_rate_usd'])}/yr, illustrative) "
        f"and haircutting for a {a['forfeiture_rate_annual_pct']:.0f}% estimated forfeiture rate, total SBC "
        f"lands near {_m(tf[0]['total'])} in FY{tf[0]['fy']} rising toward {_m(tf[-1]['total'])} by "
        f"FY{tf[-1]['fy']} as the new-grant layer ramps.",
    ]
    return " ".join(parts)


# ---------- machine-readable evidence ----------

def _evidence_paths():
    """Sidecars follow the active artifact paths, including evals' throwaway output directories."""
    return REPORT.with_suffix(".evidence.json"), DIGEST.with_suffix(".evidence.json")


def build_evidence(report, artifact_id, artifact_type, semantic_payload):
    """Build the complete claim graph shared by the HTML and digest artifacts."""
    result, li, tf = report["r"], report["li"], report["tf"]
    sched, assumptions = li["schedule"], result["assumptions"]
    builder = ev.EvidenceBuilder(
        artifact_id=artifact_id,
        agent_id="agent.sbc-forecasting",
        title="Acme Corp SBC Expense Forecast",
        artifact_type=artifact_type,
        as_of=result["as_of"],
        period="FY%d–FY%d" % (tf[0]["fy"], tf[-1]["fy"]),
        semantic_payload=semantic_payload,
    )

    data_dir = REPO / "foundation" / "data" / "acme"
    source_specs = (
        ("source.sbc.equity-grants", "equity_grants.csv", "Synthetic equity grant ledger"),
        ("source.sbc.workers", "workers.csv", "Synthetic worker status and term dates"),
        ("source.sbc.directors", "directors.csv", "Synthetic director roster"),
        ("source.sbc.equity-plans", "equity_plans.csv", "Synthetic equity-plan terms"),
        ("source.sbc.shares", "shares_outstanding.csv", "Synthetic shares and market context"),
        ("source.sbc.financials", "financials.csv", "Synthetic quarterly revenue"),
    )
    for source_id, filename, label in source_specs:
        builder.repo_source(data_dir / filename, REPO, source_id, label, "dataset",
                            "generator-seed-30414", result["as_of"], "synthetic")
    builder.repo_source(REPO / "foundation" / "compute" / "sbc_forecast.py", REPO,
                        "source.sbc.policy", "SBC forecast methodology and assumption policy", "model",
                        "sbc-forecast-v1", result["as_of"], "public")

    builder.transformation(
        "transform.sbc.locked-in.v1", "Locked-in SBC runoff", "v1",
        "foundation.compute.sbc_forecast.compute",
        "Straight-line remaining grant-date fair value by fiscal year with service-condition treatment")
    builder.transformation(
        "transform.sbc.total-forecast.v1", "Total go-forward SBC forecast", "v1",
        "foundation.compute.sbc_forecast.compute",
        "Forfeiture-adjusted locked-in runoff plus the explicitly illustrative new-grant overlay")
    builder.transformation(
        "transform.sbc.context.v1", "SBC context ratio", "v1",
        "foundation.compute.sbc_forecast.compute",
        "Compare forecast or backlog with the committed market-cap and trailing-revenue context")

    builder.assumption("assumption.sbc.full-vesting", "Continued service for gross runoff", True,
                       "boolean", "v1", "illustrative", ["source.sbc.policy"])
    builder.assumption("assumption.sbc.forfeiture-rate", "Estimated annual forfeiture rate",
                       assumptions["forfeiture_rate_annual_pct"], "percent", "v1", "illustrative",
                       ["source.sbc.policy"])
    builder.assumption("assumption.sbc.new-grant-run-rate", "Annual new-grant run rate",
                       assumptions["new_grant_run_rate_usd"], "USD/year", "v1", "illustrative",
                       ["source.sbc.equity-grants", "source.sbc.policy"])
    builder.assumption("assumption.sbc.new-grant-vesting", "New-grant vesting period",
                       assumptions["new_grant_vest_months"], "months", "v1", "illustrative",
                       ["source.sbc.policy"])
    builder.assumption("assumption.sbc.flat-revenue", "Revenue basis",
                       assumptions["revenue_basis"], "text", "v1", "illustrative",
                       ["source.sbc.financials", "source.sbc.policy"])

    builder.check("check.sbc.backlog-reconciliation", "Backlog reconciliation", "passed",
                  "examples.sbc-forecasting.run.build_report",
                  "Displayed gross runoff ties exactly to the backlog and cross-checks the equity-spend arm",
                  ["source.sbc.equity-grants", "source.sbc.workers", "source.sbc.directors",
                   "source.sbc.equity-plans", "source.sbc.policy"])
    builder.check("check.sbc.schedule-integrity", "Runoff schedule integrity", "passed",
                  "examples.sbc-forecasting.run.build_report",
                  "Every displayed amount is finite; cumulative expense is monotonic and equals the running sum",
                  ["source.sbc.equity-grants", "source.sbc.policy"])
    builder.check("check.sbc.total-decomposition", "Forecast decomposition", "passed",
                  "examples.sbc-forecasting.run.build_report",
                  "Every total equals forfeiture-adjusted locked-in expense plus the new-grant overlay",
                  ["source.sbc.equity-grants", "source.sbc.financials", "source.sbc.policy"])
    builder.check("check.sbc.source-schema", "Source schema and referential integrity", "passed",
                  "foundation.compute.sbc_forecast._load",
                  "Headers, dates, recipients, plans, awards, period spines, and economics validate fail closed",
                  ["source.sbc.equity-grants", "source.sbc.workers", "source.sbc.directors",
                   "source.sbc.equity-plans", "source.sbc.shares", "source.sbc.financials",
                   "source.sbc.policy"])

    builder.caveat("caveat.sbc.full-vesting", "warning",
                   "Gross locked-in runoff assumes continued service until the separate forfeiture overlay")
    builder.caveat("caveat.sbc.forfeiture", "warning",
                   "The future forfeiture rate is illustrative rather than company guidance")
    builder.caveat("caveat.sbc.new-grants", "warning",
                   "The new-grant run rate and attribution pattern are illustrative rather than company guidance")
    builder.caveat("caveat.sbc.revenue", "warning",
                   "The percentage-of-revenue context holds trailing revenue flat")

    locked_sources = ["source.sbc.equity-grants", "source.sbc.workers", "source.sbc.directors",
                      "source.sbc.equity-plans"]
    all_sources = locked_sources + ["source.sbc.shares", "source.sbc.financials"]
    locked_checks = ["check.sbc.backlog-reconciliation", "check.sbc.schedule-integrity",
                     "check.sbc.source-schema"]
    total_checks = locked_checks + ["check.sbc.total-decomposition"]

    builder.claim(
        "claim.sbc.backlog", "Unrecognized SBC backlog is %s." % _m(li["backlog_unrecognized_usd"]),
        li["backlog_unrecognized_usd"], _m(li["backlog_unrecognized_usd"]), "USD", "as of fiscal close",
        result["as_of"], locked_sources, "transform.sbc.locked-in.v1", locked_checks,
        status="caveated", assumption_ids=["assumption.sbc.full-vesting"],
        caveat_ids=["caveat.sbc.full-vesting"])
    builder.claim(
        "claim.sbc.remaining-vesting", "Weighted-average remaining vesting is %.1f years." %
        li["wavg_remaining_years"], li["wavg_remaining_years"], "%.1f yr" % li["wavg_remaining_years"],
        "years", "as of fiscal close", result["as_of"], locked_sources,
        "transform.sbc.locked-in.v1", locked_checks, status="caveated",
        assumption_ids=["assumption.sbc.full-vesting"], caveat_ids=["caveat.sbc.full-vesting"])
    builder.claim(
        "claim.sbc.first-year-locked", "FY%d locked-in gross expense is %s." %
        (sched[0]["fy"], _m(sched[0]["gross_expense"])), sched[0]["gross_expense"],
        _m(sched[0]["gross_expense"]), "USD", "FY%d" % sched[0]["fy"], result["as_of"],
        locked_sources, "transform.sbc.locked-in.v1", locked_checks, status="caveated",
        assumption_ids=["assumption.sbc.full-vesting"], caveat_ids=["caveat.sbc.full-vesting"])
    builder.claim(
        "claim.sbc.runoff-complete", "Locked-in runoff completes in FY%d." % li["runoff_complete_fy"],
        li["runoff_complete_fy"], "FY%d" % li["runoff_complete_fy"], "fiscal_year", "forecast horizon",
        result["as_of"], locked_sources, "transform.sbc.locked-in.v1", locked_checks,
        status="caveated", assumption_ids=["assumption.sbc.full-vesting"],
        caveat_ids=["caveat.sbc.full-vesting"])
    builder.claim(
        "claim.sbc.first-year-total", "FY%d total SBC forecast is %s." %
        (tf[0]["fy"], _m(tf[0]["total"])), tf[0]["total"], _m(tf[0]["total"]), "USD",
        "FY%d" % tf[0]["fy"], result["as_of"], all_sources, "transform.sbc.total-forecast.v1",
        total_checks, status="caveated",
        assumption_ids=["assumption.sbc.forfeiture-rate", "assumption.sbc.new-grant-run-rate",
                        "assumption.sbc.new-grant-vesting"],
        caveat_ids=["caveat.sbc.forfeiture", "caveat.sbc.new-grants"])
    if artifact_type == "dashboard":
        builder.claim(
            "claim.sbc.backlog-market-cap", "Unrecognized SBC backlog is %.1f%% of market capitalization." %
            result["context"]["backlog_pct_market_cap"], result["context"]["backlog_pct_market_cap"],
            "%.1f%%" % result["context"]["backlog_pct_market_cap"], "percent", "as of fiscal close",
            result["as_of"], all_sources, "transform.sbc.context.v1", total_checks,
            status="caveated", assumption_ids=["assumption.sbc.full-vesting"],
            caveat_ids=["caveat.sbc.full-vesting"])
        builder.claim(
            "claim.sbc.first-year-revenue-pct", "FY%d total SBC equals %.1f%% of trailing revenue." %
            (tf[0]["fy"], tf[0]["pct_ttm_revenue"]), tf[0]["pct_ttm_revenue"],
            "%.1f%%" % tf[0]["pct_ttm_revenue"], "percent", "FY%d" % tf[0]["fy"], result["as_of"],
            all_sources, "transform.sbc.context.v1", total_checks, status="caveated",
            assumption_ids=["assumption.sbc.forfeiture-rate", "assumption.sbc.new-grant-run-rate",
                            "assumption.sbc.new-grant-vesting", "assumption.sbc.flat-revenue"],
            caveat_ids=["caveat.sbc.forfeiture", "caveat.sbc.new-grants", "caveat.sbc.revenue"])

    for row in sched:
        builder.claim(
            "claim.sbc.runoff.fy%d" % row["fy"],
            "FY%d gross runoff is %s, forfeiture-adjusted runoff is %s, and cumulative gross is %s." %
            (row["fy"], _m(row["gross_expense"]), _m(row["forfeiture_adj_expense"]),
             _m(row["cumulative_gross"])), row["gross_expense"], _m(row["gross_expense"]), "USD",
            "FY%d" % row["fy"], result["as_of"], locked_sources, "transform.sbc.locked-in.v1",
            locked_checks, material=False, status="caveated",
            assumption_ids=["assumption.sbc.full-vesting", "assumption.sbc.forfeiture-rate"],
            caveat_ids=["caveat.sbc.full-vesting", "caveat.sbc.forfeiture"])
    for row in tf:
        builder.claim(
            "claim.sbc.total.fy%d" % row["fy"],
            "FY%d locked-in expense is %s, new-grant expense is %s, total SBC is %s, and the revenue ratio is %.1f%%." %
            (row["fy"], _m(row["locked_in"]), _m(row["new_grants"]), _m(row["total"]),
             row["pct_ttm_revenue"]), row["total"], _m(row["total"]), "USD", "FY%d" % row["fy"],
            result["as_of"], all_sources, "transform.sbc.total-forecast.v1", total_checks,
            material=False, status="caveated",
            assumption_ids=["assumption.sbc.forfeiture-rate", "assumption.sbc.new-grant-run-rate",
                            "assumption.sbc.new-grant-vesting", "assumption.sbc.flat-revenue"],
            caveat_ids=["caveat.sbc.forfeiture", "caveat.sbc.new-grants", "caveat.sbc.revenue"])
    return builder.build()


# ---------- rendering ----------

def render_html(report, evidence_manifest=None):
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
    runoff_ids = ["claim.sbc.runoff.fy%d" % s["fy"] for s in sched]
    body.append(er.scope(dash.bars([
        {"label": f"FY{s['fy']}",
         "value": round(s["gross_expense"] / 1e6, 1),
         "max": mx / 1e6, "color": "var(--cyan)"} for s in sched if s["gross_expense"] > 0]),
                         runoff_ids, "Open evidence for the locked-in runoff series"))
    body.append(er.scope(dash.data_table(
        ["Fiscal year", "Gross expense", "Forfeiture-adj (illus.)", "Cumulative"],
        [[f"FY{s['fy']}", _m(s["gross_expense"]), _m(s["forfeiture_adj_expense"]),
          _m(s["cumulative_gross"])] for s in sched], center_from=1), runoff_ids,
                         "Open evidence for the locked-in runoff table"))
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
    body.append(er.scope(ch.dual_axis_line(labels, totals, pcts,
                                           left_fmt=lambda v: f"${v}M", right_fmt=lambda v: f"{v}%",
                                           uid="sbc_total"),
                         ["claim.sbc.total.fy%d" % t["fy"] for t in tf],
                         "Open the evidence behind the forecast series"))
    total_rows = []
    for i, t in enumerate(tf):
        total_value = (er.value(_m(t["total"]), "claim.sbc.first-year-total")
                       if i == 0 else _m(t["total"]))
        pct_value = (er.value(f"{t['pct_ttm_revenue']:.1f}%", "claim.sbc.first-year-revenue-pct")
                     if i == 0 else f"{t['pct_ttm_revenue']:.1f}%")
        total_rows.append([
            f"FY{t['fy']}", _m(t["locked_in"]), _m(t["new_grants"]), total_value, pct_value,
        ])
    body.append(er.scope(dash.data_table(
        ["Fiscal year", "Locked-in (forf-adj)", "New grants (illus.)", "Total SBC", "% of TTM rev"],
        total_rows, center_from=1), ["claim.sbc.total.fy%d" % t["fy"] for t in tf],
                         "Open evidence for the total forecast table"))
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
                + dash.chip("Locked-in gross: pure amortization (assumes full vesting)", "ok")
                + dash.chip("Reconciles to equity-spend backlog", "ok")
                + dash.chip("Forfeiture rate: illustrative", "warn")
                + dash.chip("New-grant run-rate: illustrative", "warn")
                + dash.chip("Not financial guidance", "bad") + "</div>")
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                + dash._esc(result["disclaimer"]) + "</div>")
    body.append(dash.governance_footer(AGENT))
    page = dash.page(f"{COMPANY} — SBC Expense Forecast", "".join(body))
    return er.decorate_page(page, evidence_manifest) if evidence_manifest is not None else page


def render_digest(report, evidence_manifest=None):
    result, li, tf = report["r"], report["li"], report["tf"]
    sched = li["schedule"]
    a = result["assumptions"]
    narrative = er.markdown_refs(f"- {report['narrative']}", [
        er.reference(_m(li["backlog_unrecognized_usd"]), "claim.sbc.backlog"),
        er.reference(f"{li['wavg_remaining_years']:.1f} yr", "claim.sbc.remaining-vesting"),
        er.reference(_m(sched[0]["gross_expense"]), "claim.sbc.first-year-locked"),
        er.reference(f"FY{li['runoff_complete_fy']}", "claim.sbc.runoff-complete",
                     f"final non-zero runoff is FY{li['runoff_complete_fy']}"),
        er.reference(_m(tf[0]["total"]), "claim.sbc.first-year-total"),
    ])
    runoff = er.markdown_refs(
        f"- Locked-in runoff: {' -> '.join(_m(s['gross_expense']) for s in sched if s['gross_expense'] > 0)} "
        f"(FY{sched[0]['fy']} onward), completing FY{li['runoff_complete_fy']}. Reconciles to the "
        f"{_m(li['backlog_unrecognized_usd'])} equity-spend backlog.",
        [er.reference(_m(s["gross_expense"]), "claim.sbc.runoff.fy%d" % s["fy"])
         for s in sched if s["gross_expense"] > 0])
    total = er.markdown_refs(
        f"- Total forecast (locked-in + illustrative {_m(a['new_grant_run_rate_usd'])}/yr run-rate, "
        f"{a['forfeiture_rate_annual_pct']:.0f}% forfeiture): {' -> '.join(_m(t['total']) for t in tf)}.",
        [er.reference(_m(t["total"]), "claim.sbc.total.fy%d" % t["fy"],
                      (_m(t["total"]) + ".") if t is tf[-1] else "") for t in tf])
    lines = [f"# {COMPANY} — SBC forecast digest", f"_As of fiscal close {result['as_of']} · draft for review_",
             "", narrative, runoff, total]
    lines += ["", "_Locked-in gross runoff is pure amortization of grants already made — it assumes continued "
              "service (full vesting) until the separate forfeiture overlay is applied; the forfeiture rate, "
              "new-grant run-rate, and flat revenue are illustrative. Not financial guidance._",
              "", "_Publish gate: a human (Finance / Total Rewards) must approve before distribution._"]
    return "\n".join(lines) + "\n"


# ---------- fail-closed + entrypoint ----------

def _fail_closed(message) -> int:
    for p in (REPORT, DIGEST) + _evidence_paths():
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
    tmp.write_bytes(text.encode("utf-8"))
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
        report_manifest = build_evidence(report, "artifact.sbc-forecasting.report", "dashboard", report)
        digest_manifest = build_evidence(report, "artifact.sbc-forecasting.digest", "digest", report)
        html_doc = render_html(report, report_manifest)
        digest_doc = render_digest(report, digest_manifest)
        render_violations = er.coverage_violations(html_doc, report_manifest)
        render_violations += er.coverage_violations(digest_doc, digest_manifest)
        if render_violations:
            raise ReportError("evidence render coverage failed: %s" % render_violations[0])
    except (ReportError, SBC.SBCDataError) as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"SBC-forecast engine unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)
    report_evidence, digest_evidence = _evidence_paths()
    try:
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST, report_evidence, digest_evidence):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        _atomic_write(report_evidence, ev.format_manifest(report_manifest))
        _atomic_write(digest_evidence, ev.format_manifest(digest_manifest))
        if args.publish:
            _atomic_write(pub_path,
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": result["as_of"]},
                                     indent=2) + "\n")
    except OSError as exc:
        for p in (REPORT, DIGEST, report_evidence, digest_evidence, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    li = result["locked_in"]
    print(f"{COMPANY} SBC forecast — as of {result['as_of']}")
    print(f"  locked-in backlog {_m(li['backlog_unrecognized_usd'])} | FY{li['schedule'][0]['fy']} "
          f"{_m(li['schedule'][0]['gross_expense'])} -> runoff complete FY{li['runoff_complete_fy']}")
    print("  wrote report.sample.html, day1-digest.sample.md, and evidence sidecars")
    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (Finance / Total Rewards) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
