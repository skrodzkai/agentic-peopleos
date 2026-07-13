#!/usr/bin/env python3
"""Declarative Evidence Graph v1 adapter for the generated dashboard portfolio.

The two reference verticals (SBC forecasting and executive-comp benchmarking) own
domain-rich graphs in their agents.  This module migrates the rest of the portfolio
without copying a bespoke graph implementation into every ``run.py``: each dashboard
has an explicit consequential-claim catalog, source set, period, and transformation.

This is deliberately not an HTML-number scraper.  Claim values are read from the
validated report objects produced by each agent.  The adapter then finds the declared
human-readable anchor in the rendered artifact, adds the evidence trigger, and fails
closed if the claimed number is absent.  Digest references are attached to the exact
line that contains each declared claim.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from core import evidence as ev
from foundation.render import evidence as er


class PortfolioEvidenceError(ValueError):
    """Raised when a managed dashboard cannot produce honest evidence coverage."""


@dataclass(frozen=True)
class ClaimSpec:
    key: str
    statement: str
    value: object
    display: str
    unit: str
    html_anchor: str = ""
    digest_anchor: str = ""
    html_context: str = ""
    digest: bool = True
    metric_id: str = None


@dataclass(frozen=True)
class AgentSpec:
    title: str
    as_of: object
    period: object
    source_paths: tuple
    claim_factory: object
    transformation: str
    caveat: str


REGISTRY = "vault/90-people-analytics/metrics/metrics.registry.json"
WORKERS = "foundation/data/acme/workers.csv"
CASES = "foundation/data/acme/cases.csv"
FINANCIALS = "foundation/data/acme/financials.csv"
COMP_BANDS = "foundation/data/acme/comp_bands.csv"


def _resolve(value, report):
    return str(value(report) if callable(value) else value)


def _c(key, label, value, display, unit, html_anchor=None, digest_anchor=None,
       html_context=None, digest=True, metric_id=None):
    return ClaimSpec(
        key=key,
        statement="%s: %s." % (label, display),
        value=value,
        display=str(display),
        unit=unit,
        html_anchor=str(display if html_anchor is None else html_anchor),
        digest_anchor=str(display if digest_anchor is None else digest_anchor),
        html_context=str(label if html_context is None else html_context),
        digest=digest,
        metric_id=metric_id,
    )


def _metric(report, metric_id, key=None, display=None, html_anchor=None,
            digest_anchor=None, html_context=None, digest=True):
    result = report["results"][metric_id]
    value = result["value"]
    if display is None:
        if result.get("unit") == "percent":
            display = "%s%%" % value
        elif metric_id == "net_headcount_growth":
            display = "%+d" % value
        else:
            display = str(value)
    return _c(key or metric_id, result["name"], value, display,
              result.get("unit") or result.get("registry_unit") or "value",
              html_anchor=html_anchor, digest_anchor=digest_anchor,
              html_context=html_context, digest=digest, metric_id=metric_id)


def _money1(value):
    return "$%sM" % format(float(value) / 1e6, ",.1f")


def _money2(value):
    return "$%sM" % format(float(value) / 1e6, ",.2f")


def _compact_money(value):
    value = float(value)
    if value >= 1e9:
        return "$%.1fB" % (value / 1e9)
    return "$%.0fM" % (value / 1e6)


def _headcount(report):
    net = report["results"]["net_headcount_growth"]["value"]
    return [
        _metric(report, "headcount", digest_anchor="**%s** employees" %
                report["results"]["headcount"]["value"]),
        _metric(report, "fte", digest_anchor="**%s** FTE" % report["results"]["fte"]["value"]),
        _metric(report, "net_headcount_growth", digest_anchor="net change %+d" % net),
        _metric(report, "span_of_control", digest_anchor="Avg span **%s**" %
                report["results"]["span_of_control"]["value"]),
        _metric(report, "span_outlier_rate", digest_anchor="**%s%%** are span outliers" %
                report["results"]["span_outlier_rate"]["value"]),
    ]


def _attrition(report):
    result = report["results"]
    return [
        _metric(report, "voluntary_attrition", digest_anchor="voluntary **%s%%**" %
                result["voluntary_attrition"]["value"]),
        _metric(report, "regrettable_attrition"),
        _metric(report, "total_turnover_rate", digest_anchor="total **%s%%**" %
                result["total_turnover_rate"]["value"]),
        _metric(report, "involuntary_turnover_rate"),
        _metric(report, "twelve_month_retention", digest_anchor="retention **%s%%**" %
                result["twelve_month_retention"]["value"]),
        _metric(report, "new_hire_attrition", digest=False),
    ]


def _people_ops(report):
    ttr = report["results"]["time_to_resolution"]
    result = report["results"]
    return [
        _metric(report, "case_volume", digest_anchor="**%s** cases" % result["case_volume"]["value"]),
        _metric(report, "sla_attainment", digest_anchor="SLA **%s%%**" %
                result["sla_attainment"]["value"]),
        _metric(report, "time_to_resolution", display="%sh" % ttr["extras"]["p50"]),
        _metric(report, "open_case_backlog", digest=False),
        _metric(report, "reopen_rate", digest_anchor="reopen **%s%%**" %
                result["reopen_rate"]["value"]),
        _metric(report, "case_csat", digest_anchor="CSAT **%s%%**" % result["case_csat"]["value"]),
    ]


def _operating_review(report):
    r = report["results"]
    instrumented = sum(value[0] for value in report["coverage"].values())
    total = sum(value[1] for value in report["coverage"].values())
    claims = [
        _metric(report, "headcount"),
        _metric(report, "net_headcount_growth"),
        _metric(report, "voluntary_attrition"),
        _metric(report, "sla_attainment"),
        _metric(report, "open_case_backlog", digest=False),
        _c("breached-cases", "Open cases past SLA", r["open_case_backlog"]["extras"]["breached_open"],
           str(r["open_case_backlog"]["extras"]["breached_open"]), "cases"),
        _c("instrumentation", "Registry instrumentation coverage", instrumented,
           "%d/%d" % (instrumented, total), "metrics"),
    ]
    return claims


def _people_intelligence(report):
    r = report["results"]
    rpf = round(r["revenue_per_fte"]["value"] / 1000)
    return [
        _c("revenue-per-fte", "Revenue per FTE", r["revenue_per_fte"]["value"],
           "$%dK" % rpf, "USD/FTE", metric_id="revenue_per_fte"),
        _metric(report, "operating_leverage"),
        _metric(report, "headcount"),
        _metric(report, "net_headcount_growth", html_anchor="Headcount %d over 12 months" %
                r["net_headcount_growth"]["value"], html_context="Generated insight"),
        _metric(report, "voluntary_attrition"),
        _metric(report, "compa_ratio"),
        _metric(report, "out_of_band_rate", html_context="5% below · 2% above",
                digest_anchor="out-of-band pay **%s%%**" % r["out_of_band_rate"]["value"]),
    ]


def _comp_reporting(report):
    k = report["kpis"]
    return [
        _c("population", "Employees analyzed", k["population"], str(k["population"]), "employees",
           html_anchor="%d employees" % k["population"],
           digest_anchor="**%d** employees" % k["population"]),
        _c("average-compa", "Average compa-ratio", k["avg_compa"], str(k["avg_compa"]), "ratio",
           digest_anchor="avg compa-ratio **%s**" % k["avg_compa"]),
        _c("out-of-band", "Employees outside their salary band", k["out_of_band"],
           str(k["out_of_band"]), "employees",
           html_anchor="%d sit outside their band" % k["out_of_band"],
           digest_anchor="**%d** out of band" % k["out_of_band"]),
        _c("unexcepted", "Out-of-band employees without a documented exception", k["unexcepted_oob"],
           str(k["unexcepted_oob"]), "employees",
           html_anchor="%d of those have NO documented exception" % k["unexcepted_oob"],
           digest_anchor="**%d** without an exception" % k["unexcepted_oob"]),
        _c("exceptions", "Documented compensation exceptions", k["exceptions"], str(k["exceptions"]),
           "employees", html_context="Documented exceptions", digest=False),
    ]


def _ta(report):
    k = report["kpis"]
    labels = {
        "total_open": ("Open requisitions", "requisitions", "Open reqs"),
        "at_risk": ("At-risk requisitions", "requisitions", "At risk"),
        "avg_days_open": ("Average requisition age", "days", "Avg days open"),
        "median_pipeline": ("Median active pipeline", "candidates", "Median pipeline"),
        "on_hold": ("Requisitions on hold", "requisitions", "On hold"),
    }
    digest_context = {
        "total_open": "**%d** open reqs" % k["total_open"],
        "at_risk": "**%d** at risk" % k["at_risk"],
        "avg_days_open": "**%d** days open" % k["avg_days_open"],
        "median_pipeline": "median pipeline **%d**" % k["median_pipeline"],
        "on_hold": "%d on hold" % k["on_hold"],
    }
    return [_c(key.replace("_", "-"), labels[key][0], k[key], str(k[key]), labels[key][1],
               html_context=labels[key][2], digest_anchor=digest_context[key])
            for key in ("total_open", "at_risk", "avg_days_open", "median_pipeline", "on_hold")]


def _retention(report):
    c, m, recon = report["company"], report["metrics"], report["recon"]
    pct = lambda value: "%.1f%%" % (value * 100)
    mult = lambda value: "%.1f×" % value
    return [
        _c("observed-risk", "Observed six-month voluntary-exit risk", c["top_down"] * 100, pct(c["top_down"]),
           "percent", html_anchor="≈" + pct(c["top_down"]),
           digest_anchor="risk **%s** observed" % pct(c["top_down"])),
        _c("model-risk", "Model six-month voluntary-exit risk", c["bottom_up"] * 100, pct(c["bottom_up"]),
           "percent", html_anchor="≈" + pct(c["bottom_up"]),
           digest_anchor="/ **%s** model" % pct(c["bottom_up"])),
        _c("roc-auc", "Out-of-time ROC-AUC", m["roc_auc"], "%.3f" % m["roc_auc"], "auc",
           html_context="Rank quality (ROC-AUC)", digest_anchor="ROC **%.3f**" % m["roc_auc"]),
        _c("top-decile-lift", "Top-decile lift versus the base rate", report["lift"],
           mult(report["lift"]), "multiple", html_context="Top-decile lift",
           digest_anchor="top-decile lift **%s**" % mult(report["lift"])),
        _c("segment-count", "Rendered and suppressed segment ledger size", recon["n_segments"],
           str(recon["n_segments"]), "segments", html_context="Segments",
           digest_anchor="8 of %d segments flagged" % recon["n_segments"]),
        _c("below-above-ratio", "Below-band risk relative to above-band risk", report["ratio_below_above"],
           mult(report["ratio_below_above"]), "multiple", html_context="below-vs-above band",
           digest_anchor="below-band **%s**" % mult(report["ratio_below_above"])),
    ]


def _peer_builder(report):
    subject = report["subject"]
    top = report["peers"][0]
    return [
        _c("universe", "Public companies screened", report["n_universe"], str(report["n_universe"]),
           "companies", html_anchor="%d public companies" % report["n_universe"],
           digest_anchor="**%d public companies**" % report["n_universe"]),
        _c("in-band", "In-band peer candidates", report["n_peers"], str(report["n_peers"]),
           "companies", html_anchor="%d in-band candidates" % report["n_peers"],
           html_context="Generated insight",
           digest_anchor="**%d in-band candidates**" % report["n_peers"]),
        _c("core", "Recommended core peer group size", len(report["core"]), str(len(report["core"])),
           "companies", html_anchor="%d-company recommended core peer group" % len(report["core"]),
           digest_anchor="**%d-company recommended core peer group**" % len(report["core"])),
        _c("watchlist", "In-band alternate watchlist size", len(report["watchlist"]),
           str(len(report["watchlist"])), "companies",
           html_anchor="%d-company watchlist" % len(report["watchlist"]),
           digest_anchor="**%d-company watchlist**" % len(report["watchlist"])),
        _c("subject-revenue", "Synthetic subject revenue", subject["revenue_usd"],
           _compact_money(subject["revenue_usd"]), "USD",
           html_context="$852M Revenue $6.4B Market cap",
           digest_anchor="**%s** revenue" % _compact_money(subject["revenue_usd"])),
        _c("subject-percentile", "Synthetic subject revenue percentile in the in-band group",
           report["subj_pctile"], "%dth" % report["subj_pctile"], "percentile",
           html_anchor="~%dth percentile" % report["subj_pctile"],
           digest_anchor="**~%dth percentile**" % report["subj_pctile"]),
        _c("closest-fit", "Closest peer size-fit score", top["fit"], "%.0f" % top["fit"], "score",
           html_anchor="fit %.0f" % top["fit"], digest_anchor="fit **%.0f**" % top["fit"]),
    ]


def _rtsr(report):
    perf, val, issuer = report["performance"], report["valuation"], report["issuer_row"]
    return [
        _c("issuer-tsr", "Issuer total shareholder return", issuer["tsr"]["return_pct"],
           "%.2f%%" % issuer["tsr"]["return_pct"], "percent", html_context="Issuer TSR",
           digest=False),
        _c("issuer-percentile", "Issuer relative-TSR percentile", perf["issuer_percentile"],
           "%.2f%%" % perf["issuer_percentile"], "percentile", html_context="rTSR percentile",
           digest_anchor="**Issuer percentile:** %.2f%%" % perf["issuer_percentile"]),
        _c("payout", "Indicated PSU payout", perf["payout_percent"],
           "%.2f%%" % perf["payout_percent"], "percent-of-target",
           html_context="linear interpolation; cap at 200%",
           digest_anchor="**Indicated payout:** %.2f%%" % perf["payout_percent"]),
        _c("fair-value", "Monte Carlo fair value per target share", val["fair_value_per_target_share"],
           "$%.2f" % val["fair_value_per_target_share"], "USD/share",
           html_context="Monte Carlo FV",
           digest_anchor="**Monte Carlo fair value:** $%.2f" % val["fair_value_per_target_share"]),
        _c("fair-value-ratio", "Monte Carlo fair value relative to spot", val["fair_value_ratio_to_spot"],
           "%.2fx" % val["fair_value_ratio_to_spot"], "multiple",
           html_anchor="%.2fx spot" % val["fair_value_ratio_to_spot"], html_context="Monte Carlo FV",
           digest_anchor="%.2fx spot" % val["fair_value_ratio_to_spot"]),
    ]


def _iss(report):
    m, cg = report["measures"], report["comparison_group"]
    return [
        _c("concern", "Anticipated ISS quantitative concern", report["concern"], report["concern"],
           "category", html_anchor=report["concern"] + " concern",
           digest_anchor="Anticipated concern: " + report["concern"]),
        _c("mom", "Multiple of median CEO pay", m["mom"]["value"], "%.2f×" % m["mom"]["value"],
           "multiple", html_anchor="%.2f× the peer median" % m["mom"]["value"]),
        _c("rda", "Relative Degree of Alignment", m["rda"]["value"], "%.0f" % m["rda"]["value"],
           "points"),
        _c("pta", "Pay-TSR Alignment", m["pta"]["value"], "%.0f%%" % m["pta"]["value"],
           "percent"),
        _c("comparison-group", "ISS-derived comparison group size", cg["n_group"], str(cg["n_group"]),
           "companies", html_context="ISS peers"),
    ]


def _equity_spend(report):
    r, gp = report["r"], report["gp"]
    return [
        _c("sbc-revenue", "Trailing SBC as a percentage of revenue", r["sbc_pct_revenue"]["ttm_pct"],
           "%.1f%%" % r["sbc_pct_revenue"]["ttm_pct"], "percent",
           html_anchor="%.1f%% of revenue" % r["sbc_pct_revenue"]["ttm_pct"],
           digest_anchor="**SBC %% of revenue (TTM):** %.1f%%" % r["sbc_pct_revenue"]["ttm_pct"]),
        _c("vabr", "Three-year value-adjusted burn rate", r["vabr_3yr_pct"],
           "%.2f%%" % r["vabr_3yr_pct"], "percent", html_context="3-yr Value-Adjusted Burn",
           digest_anchor="**3-yr Value-Adjusted Burn (illustrative ISS-EPSC reconstruction):** %.2f%%" %
           r["vabr_3yr_pct"]),
        _c("benchmark-cap", "Illustrative burn-rate benchmark cap", gp["benchmark_cap_pct"],
           "%.2f%%" % gp["benchmark_cap_pct"], "percent",
           html_context="Board headline",
           digest_anchor="illustrative %.2f%% cap" % gp["benchmark_cap_pct"]),
        _c("overhang", "Equity overhang", r["overhang_pct"], "%.1f%%" % r["overhang_pct"], "percent",
           html_context="outstanding awards + pool / shares out",
           digest_anchor="**Overhang:** %.1f%%" % r["overhang_pct"]),
        _c("dilution", "Outstanding-award dilution", r["dilution_pct"],
           "%.1f%%" % r["dilution_pct"], "percent",
           digest_anchor="**Dilution:** %.1f%%" % r["dilution_pct"]),
        _c("pool-longevity", "Estimated equity-pool longevity", r["pool_longevity_years"],
           "%.1f yrs" % r["pool_longevity_years"], "years",
           digest_anchor="**Pool longevity:** %.1f yrs" % r["pool_longevity_years"]),
        _c("sbc-backlog", "Locked-in unamortized SBC backlog", r["unamortized_sbc"],
           _money1(r["unamortized_sbc"]), "USD",
           digest_anchor="**SBC backlog (locked in):** %s" % _money1(r["unamortized_sbc"])),
    ]


def _pay_equity(report):
    h, eu = report["r"]["headline"], report["eu"]
    indicated = "Indicated" if eu["potential_joint_assessment"] else "None"
    return [
        _c("raw-median", "Raw median gender pay gap", h["unadjusted_median_gap_pct"],
           "%.1f%%" % h["unadjusted_median_gap_pct"], "percent",
           digest_anchor="median **%.1f%%**" % h["unadjusted_median_gap_pct"]),
        _c("raw-mean", "Raw mean gender pay gap", h["unadjusted_mean_gap_pct"],
           "%.1f%%" % h["unadjusted_mean_gap_pct"], "percent",
           digest_anchor="mean **%.1f%%**" % h["unadjusted_mean_gap_pct"]),
        _c("adjusted", "Like-for-like adjusted gender pay gap", h["adjusted_gap_pct"],
           "%+.1f%%" % h["adjusted_gap_pct"], "percent",
           html_anchor="falls to %+.1f%%" % h["adjusted_gap_pct"],
           digest_anchor="**%+.1f%%** (not significant" % h["adjusted_gap_pct"]),
        _c("eu-flagged", "EU pay-transparency categories at or above the five-percent screen",
           eu["n_flagged"], str(eu["n_flagged"]), "categories",
           html_anchor="%d category" % eu["n_flagged"],
           digest_anchor="**EU 5%% screen flags** %d category" % eu["n_flagged"]),
        _c("joint-assessment", "Potential EU joint-pay-assessment screen", indicated, indicated, "screen",
           digest_anchor=("Indicated potential joint-pay-assessment obligation"
                          if eu["potential_joint_assessment"] else "None: no EU 5% category trigger")),
        _c("population", "Employees analyzed", report["r"]["population"]["n_analyzed"],
           format(report["r"]["population"]["n_analyzed"], ","), "employees", digest=False),
    ]


def _glass_lewis(report):
    gl, iss, syn = report["gl"], report["iss"], report["syn"]
    return [
        _c("gl-concern", "Glass Lewis reconstructed concern", gl["concern"], gl["concern"], "category",
           html_context="Glass Lewis concern", digest_anchor="**%s** concern" % gl["concern"]),
        _c("gl-composite", "Glass Lewis five-test composite", gl["composite_score"],
           "%.0f/100" % gl["composite_score"], "score", html_context="Glass Lewis concern",
           digest_anchor="composite %.0f/100" % gl["composite_score"]),
        _c("iss-concern", "ISS reconstructed concern", iss["concern"], iss["concern"], "category",
           html_context="ISS concern", digest_anchor="**%s** concern" % iss["concern"]),
        _c("verdict", "Cross-advisor reconciliation verdict", syn["verdict"], syn["verdict"], "category",
           html_context="Verdict", digest_anchor="**%s**" % syn["verdict"]),
        _c("support-band", "Directional say-on-pay support band", report["band"][0],
           "%.0f–%.0f%%" % report["band"], "percent-range",
           digest_anchor="**Say-on-pay support band:** %.0f–%.0f%%" % report["band"]),
        _c("peer-count", "Glass Lewis peer group size", gl["peer_group"]["n"],
           str(gl["peer_group"]["n"]), "companies", html_context="cap-banded co-citation network",
           digest_anchor="**GL peer group:** %d names" % gl["peer_group"]["n"]),
    ]


def _pvp(report):
    rows = report["table"]["rows"]
    first, last = rows[0], rows[-1]
    ratio = last["peo_cap"] / last["peo_sct_total"] if last["peo_sct_total"] else 0.0
    verdict = "ALIGNED" if report["alignment"]["aligned"] else "DIVERGENT"
    return [
        _c("peo-cap", "Latest-year PEO Compensation Actually Paid", last["peo_cap"],
           _money1(last["peo_cap"]), "USD", html_context="PEO CAP (FY%d)" % last["fy"],
           digest_anchor="to %s (FY%d)" % (_money1(last["peo_cap"]), last["fy"])),
        _c("peo-sct", "Latest-year PEO Summary Compensation Table total", last["peo_sct_total"],
           _money1(last["peo_sct_total"]), "USD", html_context="PEO CAP (FY%d)" % last["fy"],
           digest=False),
        _c("cap-sct-ratio", "Latest-year PEO CAP-to-SCT ratio", ratio, "%.2fx" % ratio, "multiple",
           html_context="CAP-to-SCT (FY%d)" % last["fy"], digest=False),
        _c("cap-start", "First covered-year PEO CAP", first["peo_cap"], _money1(first["peo_cap"]), "USD",
           html_anchor="%s → %s" % (_money1(first["peo_cap"]), _money1(last["peo_cap"])),
           digest_anchor="%s (FY%d)" % (_money1(first["peo_cap"]), first["fy"])),
        _c("tsr-end", "Latest company TSR index value", last["company_tsr_value"],
           "$%.0f" % last["company_tsr_value"], "index",
           html_anchor="as TSR $100 → $%.0f" % last["company_tsr_value"],
           digest_anchor="to $%.0f" % last["company_tsr_value"]),
        _c("alignment", "Pay-versus-performance directional read", verdict, verdict, "category",
           digest_anchor=("ALIGNED directional read" if report["alignment"]["aligned"]
                          else "DIVERGENT directional read")),
    ]


def _merit(report):
    r, merit = report["r"], report["m"]
    return [
        _c("eligible", "Merit-eligible employees", r["eligible_headcount"],
           format(r["eligible_headcount"], ","), "employees",
           digest_anchor="**Eligible:** %s employees" % format(r["eligible_headcount"], ",")),
        _c("merit-spend", "Average merit increase", merit["spend_pct"],
           "%.2f%%" % merit["spend_pct"], "percent",
           html_context="Merit spend", digest_anchor="**Merit:** %.2f%%" % merit["spend_pct"]),
        _c("merit-budget", "Merit budget", merit["budget"], _money1(merit["budget"]), "USD",
           digest_anchor="of a %s pool" % _money1(merit["budget"])),
        _c("headroom", "Merit budget headroom", merit["headroom"], _money1(merit["headroom"]), "USD",
           html_anchor="%s headroom" % _money1(merit["headroom"]),
           digest_anchor="%s headroom" % _money1(merit["headroom"])),
        _c("bonus", "Bonus pool", r["bonus_pool"], _money1(r["bonus_pool"]), "USD",
           html_context="target × attainment × rating",
           digest_anchor="**Bonus pool:** %s" % _money1(r["bonus_pool"])),
        _c("promotions", "Planned promotions", r["promotions"], str(r["promotions"]), "employees",
           html_context="in promo increases",
           digest_anchor="**Promotions:** %d" % r["promotions"]),
        _c("refreshers", "Equity refresher value", r["equity_refresh"]["total_value"],
           _money1(r["equity_refresh"]["total_value"]), "USD",
           html_context="grants → ledger",
           digest_anchor="**Equity refreshers:** %s" % _money1(r["equity_refresh"]["total_value"])),
    ]


SPECS = {
    "headcount-reporting": AgentSpec(
        "Acme Corp Workforce Report", "2026-01-31", "January 2026 workforce snapshot",
        (WORKERS, REGISTRY), _headcount, "Shared metric-engine workforce aggregation",
        "Synthetic workforce snapshot; production use requires governed HRIS extracts and access controls."),
    "attrition-reporting": AgentSpec(
        "Acme Corp Attrition Report", "2026-01-31", "Trailing twelve months through January 2026",
        (WORKERS, REGISTRY), _attrition, "Shared metric-engine attrition and cohort aggregation",
        "Synthetic workforce history; annualization and cohort maturity must be revalidated on production data."),
    "people-ops-reporting": AgentSpec(
        "Acme Corp People Ops Service Desk", "2026-01-31", "Ninety-day service window",
        (CASES, REGISTRY), _people_ops, "Shared metric-engine service-desk aggregation",
        "Synthetic case history; SLA clocks and category mappings are reference-mode definitions."),
    "operating-review": AgentSpec(
        "Acme Corp Monthly People Operating Review", "2026-01-31", "January 2026 operating review",
        (WORKERS, CASES, REGISTRY), _operating_review, "Cross-domain metric-engine operating review",
        "Synthetic operating snapshot; the approval ledger governs distribution, not the evidence sidecar alone."),
    "people-intelligence": AgentSpec(
        "Acme Corp People Intelligence Executive View", "2026-01-31", "Q4 FY2026 executive view",
        (WORKERS, FINANCIALS, COMP_BANDS, REGISTRY), _people_intelligence,
        "Cross-domain metric-engine executive aggregation",
        "Synthetic company data and illustrative SaaS benchmark anchors; not external market guidance."),
    "comp-reporting": AgentSpec(
        "Acme Corp Compensation Report", "2026-01-31", "January 2026 compensation snapshot",
        ("examples/comp-reporting/data/comp_snapshot.sample.csv", REGISTRY), _comp_reporting,
        "Compensation snapshot enrichment and band-position aggregation",
        "Synthetic employee-level inputs; flags require Total Rewards review and never authorize pay changes."),
    "ta-reporting": AgentSpec(
        "Acme Corp Talent Acquisition Report", lambda r: r["as_of"], lambda r: "TA snapshot as of " + r["as_of"],
        ("examples/ta-reporting/data/requisitions.sample.csv", REGISTRY), _ta,
        "Requisition aging, pipeline, and risk-flag aggregation",
        "Synthetic requisitions; thresholds are reference policy and flags require recruiter review."),
    "retention-risk": AgentSpec(
        "Acme Corp Retention Risk Committee View", "2026-01-31",
        "Panel month 36; out-of-time test months 30–35",
        ("foundation/data/acme/retention_panel.csv", "foundation/compute/manifests/retention_model_manifest.json",
         "governance/retention-risk-model-card.md"), _retention,
        "Published glass-box hazard model and segment reconciliation",
        "Synthetic, segment-first planning signal; fairness is not validated and individual adverse action is prohibited."),
    "executive-comp-peer-builder": AgentSpec(
        "Acme Corp Executive Compensation Peer Group", "2026-07-02", "FY2026 proxy season",
        ("foundation/data/acme/peer_universe.csv", "governance/real-peer-data.md"), _peer_builder,
        "Peer eligibility screen and size-fit ranking",
        "Real public-peer observations with a synthetic subject; the committee approves membership."),
    "rtsr-psu-valuation": AgentSpec(
        "Relative TSR PSU Performance and Valuation", lambda r: r["plan"]["performance_period"]["end"],
        lambda r: "%s to %s" % (r["plan"]["performance_period"]["start"],
                                 r["plan"]["performance_period"]["end"]),
        tuple("examples/rtsr-psu-valuation/data/%s" % name for name in (
            "companies.sample.json", "prices.sample.json", "plan_terms.sample.json",
            "valuation_assumptions.sample.json", "payout_history.sample.json")), _rtsr,
        "Relative-TSR performance evaluation and deterministic Monte Carlo valuation",
        "Synthetic valuation inputs and simplified path assumptions; not an auditor-approved ASC 718 valuation."),
    "iss-pay-screen": AgentSpec(
        "Acme Corp ISS Pay-for-Performance Screen", "2026-07-02", "FY2026 proxy season",
        ("foundation/data/acme/iss_universe.csv", "foundation/data/acme/exec_pay_tsr.csv",
         "foundation/data/acme/peer_universe.csv"), _iss,
        "Illustrative ISS pay-for-performance reconstruction",
        "Public-methodology reconstruction on synthetic issuer data; not ISS output or a voting recommendation."),
    "equity-spend": AgentSpec(
        "Acme Corp Company-Wide Equity Spend and Burn", "2025-12-31", "FY2025 company-wide equity plan",
        (WORKERS, "foundation/data/acme/equity_grants.csv", "foundation/data/acme/equity_plans.csv",
         "foundation/data/acme/directors.csv", FINANCIALS, "foundation/data/acme/shares_outstanding.csv",
         "foundation/data/acme/burn_benchmarks.csv"), _equity_spend,
        "Company-wide equity spend, burn, overhang, dilution, and pool forecast",
        "Synthetic plan data; benchmark cap and EPSC/Plan Cost reconstructions are illustrative."),
    "pay-equity": AgentSpec(
        "Acme Corp Pay-Equity Readiness Screen", "2026-01-31", "January 2026 pay-equity snapshot",
        (WORKERS,), _pay_equity, "Raw and regression-adjusted pay-gap analysis",
        "Synthetic, pseudonymised base-pay analysis; EU screen is readiness guidance, not a legal determination."),
    "glass-lewis-screen": AgentSpec(
        "Acme Corp ISS vs Glass Lewis Say-on-Pay War Room", "2026-07-02", "2026 say-on-pay season",
        ("foundation/data/acme/exec_pay_tsr.csv", "foundation/data/acme/iss_universe.csv",
         "foundation/data/acme/gl_financials.csv"), _glass_lewis,
        "Cross-advisor pay-for-performance reconstruction and reconciliation",
        "Illustrative ISS and Glass Lewis reconstructions; not advisor output and not a vote forecast."),
    "pay-versus-performance": AgentSpec(
        "Acme Corp Pay-versus-Performance", lambda r: "%d-12-31" % r["last_fy"],
        lambda r: "SEC Item 402(v) window through FY%d" % r["last_fy"],
        ("examples/pay-versus-performance/data/awards.sample.json",
         "examples/pay-versus-performance/data/pvp_financials.sample.json"), _pvp,
        "Item 402(v) CAP reconciliation and pay-performance relationship analysis",
        "Synthetic disclosure reconstruction; not filed disclosure, accounting advice, or an auditor valuation."),
    "merit-comp-planning": AgentSpec(
        "Acme Corp Merit and Compensation Cycle Plan", "2026-01-31", "FY2026 compensation cycle",
        (WORKERS, COMP_BANDS, "foundation/data/acme/equity_grants.csv",
         "foundation/data/acme/equity_plans.csv", "foundation/data/acme/directors.csv"), _merit,
        "Merit, bonus, promotion, and equity-refresher cycle allocation",
        "Synthetic workforce and illustrative matrix, budget, bonus, and refresher policies; committee approval required."),
}


# These two verticals own domain-specific evidence builders rather than the declarative adapter.
# Every other managed dashboard comes directly from SPECS.  The concrete artifact inventory is
# discovered and exactly reconciled below so adding, deleting, or renaming an output cannot silently
# shrink a hand-maintained list.
REFERENCE_VERTICALS = frozenset(("sbc-forecasting", "executive-comp-benchmarking"))


_TOKEN_RE = re.compile(r"<[^>]*>|[^<]+")
_TAG_RE = re.compile(r"^</?\s*([a-zA-Z0-9:-]+)")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_NO_BUTTON = {"a", "button", "script", "style", "svg", "textarea", "title"}


def _normal(value):
    return " ".join(html.unescape(str(value)).split()).casefold()


def _bounded_occurrences(text, needle):
    starts = []
    offset = 0
    while True:
        start = text.find(needle, offset)
        if start < 0:
            return starts
        end = start + len(needle)
        # Bound both sides regardless of the needle's first/last character. A signed
        # display such as ``-124`` must not attach inside an identifier like
        # ``team-124`` merely because the sign itself is punctuation.
        left_ok = start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_")
        right_ok = end == len(text) or not (text[end].isalnum() or text[end] == "_")
        if left_ok and right_ok:
            starts.append(start)
        offset = start + 1


def _annotate_html(html_doc, claims):
    """Plan unique text-node spans first, then insert exact display triggers once."""
    tokens = _TOKEN_RE.findall(html_doc)
    stack = []
    nodes = []
    token_ancestors = {}
    eligible = set()
    for token_index, token in enumerate(tokens):
        if token.startswith("<"):
            match = _TAG_RE.match(token)
            if not match or token.startswith(("<!--", "<!", "<?")):
                continue
            tag = match.group(1).lower()
            if token.startswith("</"):
                for index in range(len(stack) - 1, -1, -1):
                    if nodes[stack[index]]["tag"] == tag:
                        del stack[index:]
                        break
            elif not token.rstrip().endswith("/>") and tag not in _VOID:
                nodes.append({"tag": tag, "text": []})
                stack.append(len(nodes) - 1)
            continue
        token_ancestors[token_index] = tuple(stack)
        for node_id in stack:
            nodes[node_id]["text"].append(token)
        if not any(nodes[node_id]["tag"] in _NO_BUTTON for node_id in stack):
            eligible.add(token_index)

    plans = []
    for claim in claims:
        anchor = claim.html_anchor or claim.display
        if anchor.count(claim.display) != 1:
            raise PortfolioEvidenceError("HTML anchor for %s must contain display %r exactly once" %
                                         (claim.key, claim.display))
        encoded_anchor = html.escape(anchor, quote=False)
        encoded_display = html.escape(claim.display, quote=False)
        candidates = []
        for token_index in eligible:
            token = tokens[token_index]
            for start in _bounded_occurrences(token, encoded_anchor):
                display_start = start + encoded_anchor.index(encoded_display)
                context = _normal(claim.html_context)
                score = None
                if context:
                    for distance, node_id in enumerate(reversed(token_ancestors.get(token_index, ()))):
                        if context in _normal(" ".join(nodes[node_id]["text"])):
                            score = distance
                            break
                candidates.append((score, token_index, display_start,
                                   display_start + len(encoded_display)))
        if not candidates:
            raise PortfolioEvidenceError("HTML is missing declared evidence anchor for %s (%r)" %
                                         (claim.key, anchor))
        if len(candidates) > 1:
            contextual = [item for item in candidates if item[0] is not None]
            if contextual:
                best = min(item[0] for item in contextual)
                candidates = [item for item in contextual if item[0] == best]
        if len(candidates) != 1:
            raise PortfolioEvidenceError("HTML evidence anchor for %s is ambiguous (%d matches for %r); "
                                         "declare a more specific anchor/context" %
                                         (claim.key, len(candidates), anchor))
        _score, token_index, start, end = candidates[0]
        plans.append((token_index, start, end, claim))

    by_token = {}
    for token_index, start, end, claim in sorted(plans, key=lambda item: item[:3]):
        previous = by_token.setdefault(token_index, [])
        if previous and start < previous[-1][1]:
            raise PortfolioEvidenceError("HTML evidence spans overlap for %s and %s" %
                                         (previous[-1][2].key, claim.key))
        previous.append((start, end, claim))
    for token_index, token_plans in by_token.items():
        text = tokens[token_index]
        for start, end, claim in reversed(token_plans):
            text = text[:start] + er.trigger(claim.display, claim.key, claim.statement) + text[end:]
        tokens[token_index] = text
    return "".join(tokens)


def _annotate_digest(digest_doc, claims):
    try:
        return er.markdown_refs(digest_doc, [
            er.reference(claim.display, claim.key, claim.digest_anchor or claim.display)
            for claim in claims
        ])
    except er.EvidenceRenderError as exc:
        raise PortfolioEvidenceError(str(exc))


def _source_kind(path):
    name = Path(path).name.lower()
    if "registry" in name:
        return "registry"
    if "manifest" in name:
        return "model"
    if Path(path).suffix.lower() == ".md":
        return "policy"
    return "dataset"


def _source_label(path):
    return Path(path).stem.replace("_", " ").replace("-", " ").title()


def _build_manifest(agent_id, spec, report, claims, artifact_type, repo):
    as_of = _resolve(spec.as_of, report)
    period = _resolve(spec.period, report)
    semantic = [{"id": claim.key, "value": claim.value, "unit": claim.unit} for claim in claims]
    artifact_id = "artifact.%s.%s" % (agent_id, "report" if artifact_type == "dashboard" else "digest")
    builder = ev.EvidenceBuilder(artifact_id, "agent.%s" % agent_id, spec.title, artifact_type,
                                 as_of, period, semantic)
    source_ids = []
    for index, rel in enumerate(spec.source_paths, start=1):
        source_id = "source.%s.%02d" % (agent_id, index)
        classification = "public" if "peer_universe" in rel or "real-peer" in rel else "synthetic"
        builder.repo_source(Path(repo) / rel, repo, source_id, _source_label(rel), _source_kind(rel),
                            "reference-%s" % as_of, as_of, classification)
        source_ids.append(source_id)
    transform_id = "transform.%s.report.v1" % agent_id
    check_id = "check.%s.report-contract" % agent_id
    assumption_id = "assumption.%s.reference-mode" % agent_id
    caveat_id = "caveat.%s.reference-mode" % agent_id
    builder.transformation(transform_id, spec.transformation, "v1",
                           "examples.%s.run.build_report" % agent_id,
                           "Read validated source data, enforce the agent's fail-closed report contract, and produce the semantic report model")
    builder.check(check_id, "Agent report contract", "passed",
                  "examples.%s.run.build_report" % agent_id,
                  "The agent completed its schema, finite-value, reconciliation, and domain-specific output guards before evidence generation",
                  source_ids)
    builder.assumption(assumption_id, "Reference data mode", "synthetic", "mode", "v1", "observed",
                       source_ids=source_ids)
    builder.caveat(caveat_id, "warning", spec.caveat)
    for claim in claims:
        full_id = "claim.%s.%s" % (agent_id, claim.key)
        builder.claim(full_id, claim.statement, claim.value, claim.display, claim.unit, period, as_of,
                      source_ids, transform_id, [check_id], metric_id=claim.metric_id,
                      status="caveated", assumption_ids=[assumption_id], caveat_ids=[caveat_id])
    return builder.build()


def _with_full_ids(agent_id, claims):
    return [ClaimSpec(key="claim.%s.%s" % (agent_id, claim.key), statement=claim.statement,
                      value=claim.value, display=claim.display, unit=claim.unit,
                      html_anchor=claim.html_anchor, digest_anchor=claim.digest_anchor,
                      html_context=claim.html_context, digest=claim.digest,
                      metric_id=claim.metric_id) for claim in claims]


def prepare_pair(agent_id, report, html_doc, digest_doc, repo):
    """Return decorated artifacts plus their deterministic dashboard/digest manifests."""
    if agent_id not in SPECS:
        raise PortfolioEvidenceError("agent is not in the portfolio evidence catalog: %s" % agent_id)
    spec = SPECS[agent_id]
    claims = list(spec.claim_factory(report))
    if not (3 <= len(claims) <= 10):
        raise PortfolioEvidenceError("%s must declare 3–10 consequential claims" % agent_id)
    full_claims = _with_full_ids(agent_id, claims)
    digest_claims = [claim for claim in full_claims if claim.digest]
    if not digest_claims:
        raise PortfolioEvidenceError("%s digest declares no consequential claims" % agent_id)
    report_manifest = _build_manifest(agent_id, spec, report, claims, "dashboard", repo)
    digest_specs = [claim for claim in claims if claim.digest]
    digest_manifest = _build_manifest(agent_id, spec, report, digest_specs, "digest", repo)
    html_doc = _annotate_html(html_doc, full_claims)
    html_doc = er.decorate_page(html_doc, report_manifest)
    digest_doc = _annotate_digest(digest_doc, digest_claims)
    violations = er.coverage_violations(html_doc, report_manifest)
    violations += er.embedded_manifest_violations(html_doc, report_manifest)
    violations += er.coverage_violations(digest_doc, digest_manifest)
    if violations:
        raise PortfolioEvidenceError("%s evidence coverage failed: %s" % (agent_id, violations[0]))
    return html_doc, digest_doc, report_manifest, digest_manifest


def sidecar_path(artifact_path):
    artifact_path = Path(artifact_path)
    return artifact_path.with_name(artifact_path.stem + ".evidence.json")


def managed_outputs(report_path, digest_path):
    """Artifacts that must become stale together when a run fails closed."""
    return (Path(report_path), Path(digest_path), sidecar_path(report_path), sidecar_path(digest_path))


def write_sidecars(report_path, digest_path, report_manifest, digest_manifest):
    ev.write_manifest(sidecar_path(report_path), report_manifest)
    ev.write_manifest(sidecar_path(digest_path), digest_manifest)


def portfolio_inventory(repo):
    """Discover and exactly reconcile every governed dashboard/digest pair.

    SPECS plus the two domain-rich reference verticals define who must exist.  Disk discovery
    defines what actually exists.  Both sets must match, and every agent must expose exactly one
    report plus exactly one supported digest name.  This makes omissions and surprise outputs fail
    closed instead of letting an edited count or shortened tuple redefine coverage.
    """
    repo = Path(repo)
    expected = set(SPECS) | set(REFERENCE_VERTICALS)
    discovered = {
        path.parent.parent.name
        for path in (repo / "examples").glob("*/output/report.sample.html")
    }
    missing = sorted(expected - discovered)
    unexpected = sorted(discovered - expected)
    if missing or unexpected:
        parts = []
        if missing:
            parts.append("missing managed dashboards: %s" % ", ".join(missing))
        if unexpected:
            parts.append("uncatalogued dashboards: %s" % ", ".join(unexpected))
        raise PortfolioEvidenceError("; ".join(parts))

    inventory = []
    for agent_id in sorted(expected):
        output = repo / "examples" / agent_id / "output"
        reports = list(output.glob("report.sample.html"))
        digests = sorted(output.glob("*-digest.sample.md"))
        if len(reports) != 1 or len(digests) != 1:
            raise PortfolioEvidenceError(
                "%s must have exactly one report.sample.html and one *-digest.sample.md "
                "(found %d reports, %d digests)" %
                (agent_id, len(reports), len(digests)))
        inventory.append((agent_id, reports[0], digests[0]))
    return tuple(inventory)


def portfolio_artifacts(repo):
    paths = []
    for _agent_id, report_path, digest_path in portfolio_inventory(repo):
        paths.extend((report_path, digest_path))
    return tuple(paths)
