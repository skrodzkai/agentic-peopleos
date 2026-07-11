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
       digest=True, metric_id=None):
    return ClaimSpec(
        key=key,
        statement="%s: %s." % (label, display),
        value=value,
        display=str(display),
        unit=unit,
        html_anchor=str(display if html_anchor is None else html_anchor),
        digest_anchor=str(display if digest_anchor is None else digest_anchor),
        digest=digest,
        metric_id=metric_id,
    )


def _metric(report, metric_id, key=None, display=None, html_anchor=None,
            digest_anchor=None, digest=True):
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
              digest=digest, metric_id=metric_id)


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
    direction = "grew" if net >= 0 else "shrank"
    return [
        _metric(report, "headcount"),
        _metric(report, "fte"),
        _metric(report, "net_headcount_growth", digest_anchor="%s by %d" % (direction, abs(net))),
        _metric(report, "span_of_control"),
        _metric(report, "span_outlier_rate"),
    ]


def _attrition(report):
    claims = [_metric(report, metric_id) for metric_id in (
        "voluntary_attrition", "regrettable_attrition", "total_turnover_rate",
        "involuntary_turnover_rate", "twelve_month_retention", "new_hire_attrition")]
    claims[-1] = _metric(report, "new_hire_attrition", digest=False)
    return claims


def _people_ops(report):
    ttr = report["results"]["time_to_resolution"]
    return [
        _metric(report, "case_volume"),
        _metric(report, "sla_attainment"),
        _metric(report, "time_to_resolution", display="%sh" % ttr["extras"]["p50"],
                html_anchor=str(ttr["extras"]["p50"])),
        _metric(report, "open_case_backlog", digest=False),
        _metric(report, "reopen_rate"),
        _metric(report, "case_csat"),
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
        _metric(report, "net_headcount_growth"),
        _metric(report, "voluntary_attrition"),
        _metric(report, "compa_ratio"),
        _metric(report, "out_of_band_rate"),
    ]


def _comp_reporting(report):
    k = report["kpis"]
    return [
        _c("population", "Employees analyzed", k["population"], str(k["population"]), "employees"),
        _c("average-compa", "Average compa-ratio", k["avg_compa"], str(k["avg_compa"]), "ratio"),
        _c("out-of-band", "Employees outside their salary band", k["out_of_band"],
           str(k["out_of_band"]), "employees"),
        _c("unexcepted", "Out-of-band employees without a documented exception", k["unexcepted_oob"],
           str(k["unexcepted_oob"]), "employees"),
        _c("exceptions", "Documented compensation exceptions", k["exceptions"], str(k["exceptions"]),
           "employees", digest=False),
    ]


def _ta(report):
    k = report["kpis"]
    labels = {
        "total_open": ("Open requisitions", "requisitions"),
        "at_risk": ("At-risk requisitions", "requisitions"),
        "avg_days_open": ("Average requisition age", "days"),
        "median_pipeline": ("Median active pipeline", "candidates"),
        "on_hold": ("Requisitions on hold", "requisitions"),
    }
    return [_c(key.replace("_", "-"), labels[key][0], k[key], str(k[key]), labels[key][1])
            for key in ("total_open", "at_risk", "avg_days_open", "median_pipeline", "on_hold")]


def _retention(report):
    c, m, recon = report["company"], report["metrics"], report["recon"]
    pct = lambda value: "%.1f%%" % (value * 100)
    mult = lambda value: "%.1f×" % value
    return [
        _c("observed-risk", "Observed six-month voluntary-exit risk", c["top_down"], pct(c["top_down"]),
           "percent", html_anchor="≈" + pct(c["top_down"])),
        _c("model-risk", "Model six-month voluntary-exit risk", c["bottom_up"], pct(c["bottom_up"]),
           "percent", html_anchor="≈" + pct(c["bottom_up"])),
        _c("roc-auc", "Out-of-time ROC-AUC", m["roc_auc"], "%.3f" % m["roc_auc"], "auc"),
        _c("top-decile-lift", "Top-decile lift versus the base rate", report["lift"],
           mult(report["lift"]), "multiple"),
        _c("segment-count", "Rendered and suppressed segment ledger size", recon["n_segments"],
           str(recon["n_segments"]), "segments"),
        _c("below-above-ratio", "Below-band risk relative to above-band risk", report["ratio_below_above"],
           mult(report["ratio_below_above"]), "multiple"),
    ]


def _peer_builder(report):
    subject = report["subject"]
    top = report["peers"][0]
    return [
        _c("universe", "Public companies screened", report["n_universe"], str(report["n_universe"]),
           "companies", html_anchor="%d public companies" % report["n_universe"]),
        _c("in-band", "In-band peer candidates", report["n_peers"], str(report["n_peers"]),
           "companies", html_anchor="%d in-band candidates" % report["n_peers"]),
        _c("core", "Recommended core peer group size", len(report["core"]), str(len(report["core"])),
           "companies", html_anchor="%d-company recommended core peer group" % len(report["core"])),
        _c("watchlist", "In-band alternate watchlist size", len(report["watchlist"]),
           str(len(report["watchlist"])), "companies",
           html_anchor="%d-company watchlist" % len(report["watchlist"])),
        _c("subject-revenue", "Synthetic subject revenue", subject["revenue_usd"],
           _compact_money(subject["revenue_usd"]), "USD"),
        _c("subject-percentile", "Synthetic subject revenue percentile in the in-band group",
           report["subj_pctile"], "%dth" % report["subj_pctile"], "percentile",
           html_anchor="%dth percentile" % report["subj_pctile"]),
        _c("closest-fit", "Closest peer size-fit score", top["fit"], "%.0f" % top["fit"], "score",
           html_anchor="fit %.0f" % top["fit"]),
    ]


def _rtsr(report):
    perf, val, issuer = report["performance"], report["valuation"], report["issuer_row"]
    return [
        _c("issuer-tsr", "Issuer total shareholder return", issuer["tsr"]["return_pct"],
           "%.2f%%" % issuer["tsr"]["return_pct"], "percent", digest=False),
        _c("issuer-percentile", "Issuer relative-TSR percentile", perf["issuer_percentile"],
           "%.2f%%" % perf["issuer_percentile"], "percentile"),
        _c("payout", "Indicated PSU payout", perf["payout_percent"],
           "%.2f%%" % perf["payout_percent"], "percent-of-target"),
        _c("fair-value", "Monte Carlo fair value per target share", val["fair_value_per_target_share"],
           "$%.2f" % val["fair_value_per_target_share"], "USD/share"),
        _c("fair-value-ratio", "Monte Carlo fair value relative to spot", val["fair_value_ratio_to_spot"],
           "%.2fx" % val["fair_value_ratio_to_spot"], "multiple"),
    ]


def _iss(report):
    m, cg = report["measures"], report["comparison_group"]
    return [
        _c("concern", "Anticipated ISS quantitative concern", report["concern"], report["concern"],
           "category", html_anchor=report["concern"] + " concern"),
        _c("mom", "Multiple of median CEO pay", m["mom"]["value"], "%.2f×" % m["mom"]["value"],
           "multiple"),
        _c("rda", "Relative Degree of Alignment", m["rda"]["value"], "%.0f" % m["rda"]["value"],
           "points"),
        _c("pta", "Pay-TSR Alignment", m["pta"]["value"], "%.0f%%" % m["pta"]["value"],
           "percent"),
        _c("comparison-group", "ISS-derived comparison group size", cg["n_group"], str(cg["n_group"]),
           "companies"),
    ]


def _equity_spend(report):
    r, gp = report["r"], report["gp"]
    return [
        _c("sbc-revenue", "Trailing SBC as a percentage of revenue", r["sbc_pct_revenue"]["ttm_pct"],
           "%.1f%%" % r["sbc_pct_revenue"]["ttm_pct"], "percent"),
        _c("vabr", "Three-year value-adjusted burn rate", r["vabr_3yr_pct"],
           "%.2f%%" % r["vabr_3yr_pct"], "percent"),
        _c("benchmark-cap", "Illustrative burn-rate benchmark cap", gp["benchmark_cap_pct"],
           "%.2f%%" % gp["benchmark_cap_pct"], "percent"),
        _c("overhang", "Equity overhang", r["overhang_pct"], "%.1f%%" % r["overhang_pct"], "percent"),
        _c("dilution", "Outstanding-award dilution", r["dilution_pct"],
           "%.1f%%" % r["dilution_pct"], "percent"),
        _c("pool-longevity", "Estimated equity-pool longevity", r["pool_longevity_years"],
           "%.1f yrs" % r["pool_longevity_years"], "years"),
        _c("sbc-backlog", "Locked-in unamortized SBC backlog", r["unamortized_sbc"],
           _money1(r["unamortized_sbc"]), "USD"),
    ]


def _pay_equity(report):
    h, eu = report["r"]["headline"], report["eu"]
    indicated = "Indicated" if eu["potential_joint_assessment"] else "None"
    return [
        _c("raw-median", "Raw median gender pay gap", h["unadjusted_median_gap_pct"],
           "%.1f%%" % h["unadjusted_median_gap_pct"], "percent"),
        _c("raw-mean", "Raw mean gender pay gap", h["unadjusted_mean_gap_pct"],
           "%.1f%%" % h["unadjusted_mean_gap_pct"], "percent"),
        _c("adjusted", "Like-for-like adjusted gender pay gap", h["adjusted_gap_pct"],
           "%+.1f%%" % h["adjusted_gap_pct"], "percent"),
        _c("eu-flagged", "EU pay-transparency categories at or above the five-percent screen",
           eu["n_flagged"], str(eu["n_flagged"]), "categories",
           html_anchor="%d category" % eu["n_flagged"]),
        _c("joint-assessment", "Potential EU joint-pay-assessment screen", indicated, indicated, "screen",
           digest_anchor=("potential joint-pay-assessment obligation" if eu["potential_joint_assessment"]
                          else "No EU 5% category trigger")),
        _c("population", "Employees analyzed", report["r"]["population"]["n_analyzed"],
           format(report["r"]["population"]["n_analyzed"], ","), "employees", digest=False),
    ]


def _glass_lewis(report):
    gl, iss, syn = report["gl"], report["iss"], report["syn"]
    return [
        _c("gl-concern", "Glass Lewis reconstructed concern", gl["concern"], gl["concern"], "category"),
        _c("gl-composite", "Glass Lewis five-test composite", gl["composite_score"],
           "%.0f/100" % gl["composite_score"], "score"),
        _c("iss-concern", "ISS reconstructed concern", iss["concern"], iss["concern"], "category"),
        _c("verdict", "Cross-advisor reconciliation verdict", syn["verdict"], syn["verdict"], "category"),
        _c("support-band", "Directional say-on-pay support band", report["band"][0],
           "%.0f–%.0f%%" % report["band"], "percent-range"),
        _c("peer-count", "Glass Lewis peer group size", gl["peer_group"]["n"],
           str(gl["peer_group"]["n"]), "companies"),
    ]


def _pvp(report):
    rows = report["table"]["rows"]
    first, last = rows[0], rows[-1]
    ratio = last["peo_cap"] / last["peo_sct_total"] if last["peo_sct_total"] else 0.0
    verdict = "ALIGNED" if report["alignment"]["aligned"] else "DIVERGENT"
    return [
        _c("peo-cap", "Latest-year PEO Compensation Actually Paid", last["peo_cap"],
           _money1(last["peo_cap"]), "USD"),
        _c("peo-sct", "Latest-year PEO Summary Compensation Table total", last["peo_sct_total"],
           _money1(last["peo_sct_total"]), "USD", digest=False),
        _c("cap-sct-ratio", "Latest-year PEO CAP-to-SCT ratio", ratio, "%.2fx" % ratio, "multiple",
           digest=False),
        _c("cap-start", "First covered-year PEO CAP", first["peo_cap"], _money1(first["peo_cap"]), "USD"),
        _c("tsr-end", "Latest company TSR index value", last["company_tsr_value"],
           "$%.0f" % last["company_tsr_value"], "index"),
        _c("alignment", "Pay-versus-performance directional read", verdict, verdict, "category",
           digest_anchor=("aligned" if report["alignment"]["aligned"] else "divergent")),
    ]


def _merit(report):
    r, merit = report["r"], report["m"]
    return [
        _c("eligible", "Merit-eligible employees", r["eligible_headcount"],
           format(r["eligible_headcount"], ","), "employees"),
        _c("merit-spend", "Average merit increase", merit["spend_pct"],
           "%.2f%%" % merit["spend_pct"], "percent"),
        _c("merit-budget", "Merit budget", merit["budget"], _money1(merit["budget"]), "USD"),
        _c("headroom", "Merit budget headroom", merit["headroom"], _money1(merit["headroom"]), "USD"),
        _c("bonus", "Bonus pool", r["bonus_pool"], _money1(r["bonus_pool"]), "USD"),
        _c("promotions", "Planned promotions", r["promotions"], str(r["promotions"]), "employees"),
        _c("refreshers", "Equity refresher value", r["equity_refresh"]["total_value"],
           _money1(r["equity_refresh"]["total_value"]), "USD"),
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
        ("foundation/data/acme/iss_universe.csv", "foundation/data/acme/peer_universe.csv"), _iss,
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
        (WORKERS, COMP_BANDS), _pay_equity, "Raw and regression-adjusted pay-gap analysis",
        "Synthetic, pseudonymised base-pay analysis; EU screen is readiness guidance, not a legal determination."),
    "glass-lewis-screen": AgentSpec(
        "Acme Corp ISS vs Glass Lewis Say-on-Pay War Room", "2026-07-02", "2026 say-on-pay season",
        ("foundation/data/acme/exec_pay_tsr.csv", "foundation/data/acme/iss_universe.csv",
         "foundation/data/acme/peer_universe.csv"), _glass_lewis,
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


# Every generated dashboard/digest pair in the public portfolio.  The verifier uses
# this explicit inventory so deleting or omitting a sidecar is a CI failure.
PORTFOLIO_OUTPUTS = (
    ("attrition-reporting", "day1-digest.sample.md"),
    ("comp-reporting", "day1-digest.sample.md"),
    ("equity-spend", "day1-digest.sample.md"),
    ("executive-comp-benchmarking", "day1-digest.sample.md"),
    ("executive-comp-peer-builder", "day1-digest.sample.md"),
    ("glass-lewis-screen", "day1-digest.sample.md"),
    ("headcount-reporting", "day1-digest.sample.md"),
    ("iss-pay-screen", "day1-digest.sample.md"),
    ("merit-comp-planning", "day1-digest.sample.md"),
    ("operating-review", "day1-digest.sample.md"),
    ("pay-equity", "day1-digest.sample.md"),
    ("pay-versus-performance", "day1-digest.sample.md"),
    ("people-intelligence", "day1-digest.sample.md"),
    ("people-ops-reporting", "day1-digest.sample.md"),
    ("retention-risk", "committee-digest.sample.md"),
    ("rtsr-psu-valuation", "day1-digest.sample.md"),
    ("sbc-forecasting", "day1-digest.sample.md"),
    ("ta-reporting", "day1-digest.sample.md"),
)


_TOKEN_RE = re.compile(r"<[^>]*>|[^<]+")
_TAG_RE = re.compile(r"^</?\s*([a-zA-Z0-9:-]+)")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_NO_BUTTON = {"a", "button", "script", "style", "svg", "textarea", "title"}


def _annotate_html(html_doc, claims):
    """Insert one evidence trigger at each declared text anchor, preserving all other bytes."""
    remaining = list(claims)
    stack = []
    out = []
    for token in _TOKEN_RE.findall(html_doc):
        if token.startswith("<"):
            out.append(token)
            match = _TAG_RE.match(token)
            if not match or token.startswith(("<!--", "<!", "<?")):
                continue
            tag = match.group(1).lower()
            if token.startswith("</"):
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index] == tag:
                        del stack[index:]
                        break
            elif not token.rstrip().endswith("/>") and tag not in _VOID:
                stack.append(tag)
            continue
        if not remaining or set(stack) & _NO_BUTTON:
            out.append(token)
            continue
        text = token
        for claim in list(remaining):
            anchor = claim.html_anchor or claim.display
            encoded = html.escape(anchor)
            if encoded not in text:
                continue
            claim_id = claim.key
            text = text.replace(encoded, er.trigger(anchor, claim_id), 1)
            remaining.remove(claim)
        out.append(text)
    if remaining:
        raise PortfolioEvidenceError("HTML is missing declared evidence anchor for %s (%r)" %
                                     (remaining[0].key, remaining[0].html_anchor or remaining[0].display))
    return "".join(out)


def _annotate_digest(digest_doc, claims):
    lines = digest_doc.splitlines(keepends=True)
    for claim in claims:
        anchor = claim.digest_anchor or claim.display
        found = False
        for index, line in enumerate(lines):
            if anchor not in line:
                continue
            ending = "\n" if line.endswith("\n") else ""
            body = line[:-1] if ending else line
            lines[index] = body + "<!-- evidence:%s -->" % claim.key + ending
            found = True
            break
        if not found:
            raise PortfolioEvidenceError("digest is missing declared evidence anchor for %s (%r)" %
                                         (claim.key, anchor))
    return "".join(lines)


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
                  "The agent completed its schema, finite-value, reconciliation, and domain-specific output guards before evidence generation")
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
    return [ClaimSpec("claim.%s.%s" % (agent_id, claim.key), claim.statement, claim.value,
                      claim.display, claim.unit, claim.html_anchor, claim.digest_anchor,
                      claim.digest, claim.metric_id) for claim in claims]


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


def portfolio_artifacts(repo):
    paths = []
    for agent_id, digest_name in PORTFOLIO_OUTPUTS:
        output = Path(repo) / "examples" / agent_id / "output"
        paths.extend((output / "report.sample.html", output / digest_name))
    return paths
