#!/usr/bin/env python3
"""Acme Corp — Retention Risk, Committee View (Agentic PeopleOS · governed glass-box ML).

The marquee of the retention arm: one dark, executive dashboard that renders the PUBLISHED, pinned
glass-box hazard model (foundation/compute/retention.py) as a segment-first planning instrument for a
Compensation / People committee. Like every arm agent it is PRESENTATION + GOVERNANCE ONLY — it does no
model fitting and computes no source metric: every statistic comes from the engine (the model is
reconstructed from the committed manifest via model_from_manifest, so this renders the exact published
artifact without re-fitting), and the agent only formats those statistics and the plain display ratios
between them (e.g. a lift or a below-vs-above band ratio). It draws with the deterministic SVG toolkit
(foundation/render/charts.py), it fails closed, and it stops at a human publish gate.

The design thesis: every dashboard *claims* to be trustworthy; this one *performs* it. Every metric is
shown next to its no-skill baseline, the model's disagreements with observed history are plotted and
flagged red (never averaged away), the planted decoy features and the realism guard get their own
readout, and fairness is a visibly UNCHECKED checklist. The honesty rails are the visual system.

    python3 run.py                                              # draft only
    python3 run.py --publish                                    # refused: needs a valid approver
    python3 run.py --publish --approved-by "People Analytics Lead"

Standard library only; deterministic; offline. Segment-first — no individual is ever named or scored on
this surface; the model card (governance/retention-risk-model-card.md) is the binding contract.
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

from foundation.render import charts as ch                 # noqa: E402
from foundation.compute import retention as R              # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "committee-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "panel month 36"
PERIOD = "Acme Corp · panel month 36 · out-of-time test m30–35 · synthetic data"
AGENT = "retention-risk"
SCOPE = "publish.retention_risk"
MODEL_CARD = "governance/retention-risk-model-card.md"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

HORIZON = 6                # planning horizon (months) used across the page
SURV_MAX_H = 12            # survival staircase length
MEDIAN_H = 18             # horizon over which a median time-to-exit is sought (else 'not reached')

# Presentation-only display labels (NOT values — the numbers all come from the engine).
DRIVER_LABELS = {
    "unvested_equity_pct_comp": "Unvested equity, % of comp",
    "comp_ratio": "Paid at/above band midpoint",
    "mths_since_last_raise": "Months since last raise",
    "mths_since_promo": "Months since last promotion",
    "mgr_team_attrition_ttm": "Manager's team attrition (12m)",
    "equity_moneyness": "Equity deep in the money",
    "stuck_in_level_flag": "Stuck in level",
    "engagement_slope_3p": "Engagement trending up",
    "team_departures_90d": "Team departures (last 90d)",
    "high_perf_unrecognized": "High performer, unrecognized",
    "days_to_next_vest": "Long wait to next vest",
    "perf_rating_delta_4q": "Performance rating improving",
}
# Lowercase mid-sentence glosses + short chip names — the DISPLAY text; the coefficient VALUES are always
# read live from the model (report["coef_ranked"]), never hardcoded, so a re-fit can't leave the copy stale.
DRIVER_PHRASE = {
    "unvested_equity_pct_comp": "unvested equity", "comp_ratio": "at-or-above-mid pay",
    "mths_since_last_raise": "stale raises", "mths_since_promo": "stale promotions",
    "mgr_team_attrition_ttm": "manager-team churn", "equity_moneyness": "in-the-money equity",
    "stuck_in_level_flag": "being stuck in level", "engagement_slope_3p": "rising engagement",
    "team_departures_90d": "recent team departures", "high_perf_unrecognized": "unrecognized high performers",
    "days_to_next_vest": "a long wait to vest", "perf_rating_delta_4q": "improving performance",
}
LEVER_CHIP = {
    "comp_ratio": "comp_ratio", "mths_since_last_raise": "mths_since_raise",
    "unvested_equity_pct_comp": "unvested_equity", "mgr_team_attrition_ttm": "mgr_team_attrition",
    "team_departures_90d": "team_departures_90d", "mths_since_promo": "mths_since_promo",
    "stuck_in_level_flag": "stuck_in_level", "high_perf_unrecognized": "high_perf_unrecognized",
}
COMP_LABELS = {"below": "Pay below band", "within": "Pay within band", "above": "Pay above band"}
TENURE_LABELS = {"<1y": "Tenure <1y", "1-2y": "Tenure 1–2y", "2-3y": "Tenure 2–3y",
                 "3-5y": "Tenure 3–5y", "5y+": "Tenure 5y+"}
DIM_LABELS = {"function": "Function", "level": "Level", "region_band": "Region",
              "manager_span_band": "Manager span", "comp_position_band": "Comp position",
              "tenure_band": "Tenure"}
LEDGER_DIMS = ["function", "level", "region_band", "manager_span_band", "comp_position_band", "tenure_band"]
N_DRIVERS = 12             # glass-box drivers shown (top by |coef|)


class ReportError(RuntimeError):
    """Raised when the committee view cannot be produced (fail closed)."""


# ---------------------------------------------------------------- small formatters (presentation only)
def _pct(p, dp=1):
    return f"{p * 100:.{dp}f}%"


def _pp(x, dp=1):
    return f"{x * 100:+.{dp}f}pp"


def _mult(x, dp=1):
    return f"{x:.{dp}f}×"


def _e(v):
    import html
    return html.escape(str(v))


_MD_ESCAPE = set("\\`*_[]()<>")             # markdown-structural chars: links, emphasis, code, inline HTML


def _md(v):
    """Escape a dynamic value for Markdown PROSE — neutralizes injected links/emphasis/code/inline-HTML so a
    segment label can never smuggle active markup into the digest (the digest is .md, not HTML — html.escape is
    the wrong layer there). Also breaks GFM BARE-URL autolinks (`http(s)://…`, `www.…`) with a zero-width
    space so a hostile label can't become a clickable link even without explicit `[]()` syntax."""
    s = "".join("\\" + ch if ch in _MD_ESCAPE else ch for ch in str(v))
    return s.replace("://", ":\u200b//").replace("www.", "www\u200b.")


def _one_line(text, limit=300):
    return " ".join(str(text).split())[:limit]


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _seg_label(dim, value):
    if dim == "comp_position_band":
        return COMP_LABELS.get(value, value)
    if dim == "tenure_band":
        return TENURE_LABELS.get(value, value)
    return str(value)


def _short(dim, value):
    if dim == "comp_position_band":
        return {"below": "below band", "within": "within band", "above": "above band"}.get(value, value)
    if dim == "tenure_band":
        return f"{value} tenure"
    if dim == "manager_span_band":
        return f"span {value}"
    return str(value)


def _rendered(segs):
    return [s for s in segs if not s.get("suppressed")]


def _by_model_desc(segs):
    """Rendered segments worst-first (by model bottom-up risk); suppressed ones appended."""
    rend = sorted(_rendered(segs), key=lambda s: -s["bottom_up_6mo"])
    return rend + [s for s in segs if s.get("suppressed")]


# ---------------------------------------------------------------- compute (no math here — engine only)
def build_report():
    rows = R.load_panel()
    design = R.build_design(rows)
    slices = R.temporal_slices(design)
    model, calibration, bands = R.model_from_manifest()        # the PUBLISHED artifact, no re-fit

    metrics = R.evaluate(model, calibration, design, slices)
    R.realism_guard(model, metrics)                            # fail closed on an implausibly-perfect model
    for k in ("roc_auc", "pr_auc", "brier", "horizon_concordance", "base_rate"):
        if not _finite(metrics[k]):
            raise ReportError(f"headline metric '{k}' is not a finite number — the view must not ship")
    pk = metrics["precision_at_k"]
    if pk["status"] != "ok" or not _finite(pk["precision"]):
        raise ReportError("precision@k has an insufficient denominator — the committee view needs a top-decile precision")

    company = R.company_risk(rows, model, calibration, design, slices, horizon=HORIZON)
    tiers = R.tier_counts(model, calibration, design, slices)
    survival = R.company_survival(model, calibration, design, slices, max_h=SURV_MAX_H, median_h=MEDIAN_H)
    segments = R.segment_risk(rows, model, calibration, design, slices)
    recon = R.reconciliation_summary(segments)

    comp = {s["value"]: s for s in _rendered(segments["comp_position_band"])}
    for need in ("below", "within", "above"):
        if need not in comp:
            raise ReportError(f"comp-position segment '{need}' is missing/suppressed — required for the beacon and levers")
    tenure_rend = _rendered(segments["tenure_band"])
    if not tenure_rend:
        raise ReportError("no rendered tenure segments — required for the concentration story")
    top_tenure = max(tenure_rend, key=lambda s: s["bottom_up_6mo"])
    ratio_below_above = comp["below"]["bottom_up_6mo"] / comp["above"]["bottom_up_6mo"]

    all_rendered = [(dim, s) for dim in segments for s in _rendered(segments[dim])]
    if not all_rendered:
        raise ReportError("no rendered segments at all — the panel is empty")
    worst_dim, worst_seg = max(all_rendered, key=lambda ds: abs(ds[1]["reconciliation_gap"]))
    lo_dim, lo_seg = min(all_rendered, key=lambda ds: ds[1]["bottom_up_6mo"])
    hi_dim, hi_seg = max(all_rendered, key=lambda ds: ds[1]["bottom_up_6mo"])

    coef_ranked = sorted(model["coef"].items(), key=lambda kv: -abs(kv[1]))
    decoy_ranks = sorted(i + 1 for i, (n, _) in enumerate(coef_ranked) if n in R.DECOY_FEATURES)
    if any(rank <= 3 for rank in decoy_ranks):
        raise ReportError("a planted decoy feature ranks in the top-3 by |coef| — leakage/realism tripwire")
    # The static committee framing (narrative, driver statrow, comp levers) assumes equity + comp-ratio are the
    # top-2 protective forces and a staleness/manager feature leads the push side. The chip VALUES are always read
    # live from coef_ranked, but this guard fails the build closed if a re-fit reshuffles the ranks, so the framing
    # can never silently go false.
    _neg = [n for n, v in coef_ranked if v < 0]
    _pos = [n for n, v in coef_ranked if v > 0]
    if set(_neg[:2]) != {"unvested_equity_pct_comp", "comp_ratio"}:
        raise ReportError("driver ranks shifted: the top-2 protective forces are no longer unvested equity + "
                          "comp-ratio — the committee copy assumes this (re-fit review required)")
    if not _pos or _pos[0] not in {"mths_since_last_raise", "mths_since_promo", "mgr_team_attrition_ttm"}:
        raise ReportError("driver ranks shifted: the strongest push factor is no longer a staleness/manager "
                          "feature — the committee copy assumes this (re-fit review required)")
    drivers = coef_ranked[:N_DRIVERS]
    for name, _ in drivers:
        if name not in DRIVER_LABELS:
            raise ReportError(f"driver '{name}' has no committee label — refusing to render an unlabeled coefficient")

    # diagnostics on the PUBLISHED model (no re-fit): decile calibration + robust coefficient intervals
    reliability = R.reliability_curve(model, calibration, design, slices)
    coef_ci = R.coefficient_intervals(model, design, slices)
    ci_by_feature = {c["feature"]: c for c in coef_ci["coefficients"]}
    # every decoy's stability interval must span 0 (it's noise) — fail the build if a decoy reads significant
    for c in coef_ci["coefficients"]:
        if c["is_decoy"] and c["excludes_zero"]:
            raise ReportError(f"a planted decoy ({c['feature']}) has a coefficient interval excluding 0 — "
                              "the stability diagnostic contradicts the leakage tripwire")

    lift = pk["precision"] / metrics["base_rate"]
    n_flagged = pk["n_flagged"]
    n_exits = round(pk["precision"] * n_flagged)
    slice_sizes = {"train": len(slices["train"]), "calibration": len(slices["calibration"]),
                   "test": len(slices["test"])}

    report = {
        "metrics": metrics, "company": company, "tiers": tiers, "survival": survival,
        "segments": segments, "recon": recon, "bands": bands, "intercept": model["intercept"],
        "coef_ranked": coef_ranked, "decoy_ranks": decoy_ranks, "drivers": drivers,
        "comp": comp, "top_tenure": top_tenure, "ratio_below_above": ratio_below_above,
        "worst": {"dim": worst_dim, "seg": worst_seg}, "lo": {"dim": lo_dim, "seg": lo_seg},
        "hi": {"dim": hi_dim, "seg": hi_seg}, "lift": lift, "n_flagged": n_flagged, "n_exits": n_exits,
        "slice_sizes": slice_sizes, "reliability": reliability, "coef_ci": coef_ci,
        "ci_by_feature": ci_by_feature,
    }
    report["narrative"] = _narrative(report)
    return report


def _narrative(d):
    c, below, toptt = d["company"], d["comp"]["below"], d["top_tenure"]
    direction = "high" if c["gap"] >= 0 else "low"
    prot = [DRIVER_PHRASE[n] for n, v in d["coef_ranked"] if v < 0 and n in DRIVER_PHRASE][:2]
    push = [DRIVER_PHRASE[n] for n, v in d["coef_ranked"] if v > 0 and n in DRIVER_PHRASE][:2]
    return (f"Voluntary-exit risk over the next {HORIZON} months is <b>{_pct(c['bottom_up'])}</b> by the model "
            f"vs <b>{_pct(c['top_down'])}</b> observed <span class='warn'>(model reads {_pp(c['gap'])} {direction})</span>. "
            f"Pressure concentrates where <b>pay sits below band</b> ({_pct(below['bottom_up_6mo'])}, "
            f"<b>{_mult(d['ratio_below_above'])}</b> the above-band rate) and in "
            f"<b>{_e(_seg_label('tenure_band', toptt['value']))}</b> ({_pct(toptt['bottom_up_6mo'])}). "
            f"<span class='up'>The strongest protective forces are {prot[0]} and {prot[1]}</span>; "
            f"{push[0]} and {push[1]} are the strongest push factors. "
            f"<b>{d['recon']['n_flagged']} of {d['recon']['n_segments']} segments</b> disagree with observed history "
            f"beyond {_pct(R.RECONCILE_GAP, 0)} — flagged below, not averaged away.")


# ---------------------------------------------------------------- presentation (charts + layout)
def _tile(title, sub, chart, extra="", cls="", scope="Segment-level"):
    return (f"<div class='tile {cls}'><div class='t-head'><div><h3>{_e(title)}</h3>"
            f"<div class='t-sub'>{_e(sub)}</div></div><span class='t-scope'>{_e(scope)}</span></div>"
            f"<div class='chart'>{chart}</div>{extra}</div>")


def _legend(items):
    return "<div class='legend'>" + "".join(
        f"<span><i style='background:{c}'></i>{_e(l)}</span>" for l, c in items) + "</div>"


def _statrow(stats):
    cells = "".join(
        f"<div class='stat'><span class='s-v mono'{(' style=' + chr(39) + 'color:' + col + chr(39)) if col else ''}>"
        f"{_e(v)}</span><span class='s-l'>{_e(l)}</span></div>"
        for v, l, col in stats)
    return f"<div class='statrow'>{cells}</div>"


def _kpi_card(label, value, chip, kind, ctx):
    # No history series exists for a single trained artifact — every card is honestly 'trend n/a', never a
    # faked line (the house pattern).
    return (f"<div class='kpi'><div class='k-top'><div class='k-label'>{_e(label)}</div>"
            f"<div class='k-spark'><span class='k-na'>trend n/a</span></div></div>"
            f"<div class='k-val mono'>{_e(value)}</div>"
            f"<div class='k-foot'><span class='chip {kind}'>{_e(chip)}</span>"
            f"<span class='k-ctx'>{_e(ctx)}</span></div></div>")


def _seg_forest(report, dims, label_w=150):
    """Segment-mode forest: solid point = model bottom-up, ghost = observed KM, red row = gap-flagged."""
    rows = []
    for dim in dims:
        for s in _by_model_desc(report["segments"][dim]):
            if s.get("suppressed"):
                continue
            rows.append({"group": _seg_label(dim, s["value"]),
                         "adj": round(s["bottom_up_6mo"] * 100, 1),
                         "raw": round(s["top_down_6mo"] * 100, 1),
                         "flag": s["gap_flagged"], "sub": _pp(s["reconciliation_gap"])})
    return ch.forest_plot(rows, 0, 22, unit="%", zero_label=None, ghost_label="obs",
                          color_mode="flag", tick_step=2, label_w=label_w,
                          value_fmt=lambda v: f"{v:.1f}%")


def render_html(report):
    m, c = report["metrics"], report["company"]
    tiers, surv, recon = report["tiers"], report["survival"], report["segments"] and report["recon"]
    comp = report["comp"]
    body = []

    # header
    body.append("<header class='topbar'>"
                f"<div class='brandwrap'>{MARK_SVG}"
                "<div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                "<div class='brand-sub'>Retention Risk</div></div></div>"
                "<div class='title'><h1>Workforce Planning — Retention Risk · Committee View</h1>"
                f"<div class='meta'>{_e(PERIOD)} · <a href='../../../{MODEL_CARD}'>model card</a></div></div>"
                "<div class='spacer'></div>"
                "<span class='status'>Draft · awaiting publish approval</span></header>")

    # P0 — guardrail strip (the reading contract, BEFORE any number)
    chips = [
        "SEGMENT-FIRST · no per-employee score surfaced, no names, ever",
        "PLANNING SIGNAL · never adverse action, never a manager leaderboard",
        "SYNTHETIC DATA · demonstrates mechanics + governance, not real-world accuracy",
        "FAIRNESS: NOT YET VALIDATED · scaffolding only — see panel below",
        "VOLUNTARY EXITS ONLY · involuntary & retirement censored, never predicted",
    ]
    body.append("<section class='rails'><span class='rl'>Read me first — what this is and isn't</span>"
                + "".join(f"<span class='chip2'>{_e(t)}</span>" for t in chips)
                + "<div class='rails-cap'>Enforced in the compute layer (suppression floor, excluded "
                  "attributes, fail-closed), not just stated here — see the model card.</div></section>")

    # P1 — insight ribbon (deterministic narrator)
    body.append("<section class='insight'>"
                "<svg class='glyph' viewBox='0 0 24 24'><path d='M12 2 L13.7 8.3 L20 10 L13.7 11.7 L12 18 "
                "L10.3 11.7 L4 10 L10.3 8.3 Z' fill='#1ba7ff'/><circle cx='18.5' cy='4.5' r='1.6' fill='#f7b955'/>"
                "<circle cx='5' cy='17' r='1.2' fill='#7c8cff'/></svg>"
                "<div><div class='tag'>Generated insight · engine read of the out-of-time test window</div>"
                f"<p>{report['narrative']}</p></div></section>")

    # P2 — beacon: the spread instrument (company aggregate on its own segment spread)
    max_model_pct = report["hi"]["seg"]["bottom_up_6mo"] * 100
    hi_axis = max(22, int(math.ceil(max_model_pct)) + 2)
    picks, seen = [], set()
    for dim, s in ([(report["lo"]["dim"], report["lo"]["seg"])]
                   + [("comp_position_band", comp[k]) for k in ("above", "within", "below")]
                   + [(report["hi"]["dim"], report["hi"]["seg"])]):
        key = (dim, s["value"])
        if key in seen:
            continue
        seen.add(key)
        picks.append((round(s["bottom_up_6mo"] * 100, 1),
                      f"{_short(dim, s['value'])} · {_pct(s['bottom_up_6mo'])}"))
    picks.sort(key=lambda t: t[0])
    beacon_strip = ch.percentile_strip(round(c["top_down"] * 100, 1), 0, hi_axis, picks,
                                       target=None, you_label="Company (obs)",
                                       unit_prefix="", unit_suffix="%")
    body.append("<section class='beacon'><div class='head'>"
                "<div><div class='label'>6-mo voluntary-exit outlook — the spread is the story</div>"
                f"<div class='hero'><span class='v mono'>≈{_pct(c['top_down'])}</span>"
                f"<span class='pct'>model ≈{_pct(c['bottom_up'])} · gap {_pp(c['gap'])}</span></div></div>"
                "<div class='sub'>Leading with the observed rate; the model's disagreement is worn as a badge. "
                f"Range = lowest→highest rendered segment (n ≥ {R.SEG_MIN_N} each). Test window m30–35, "
                f"{m['n_test_employees']:,} employees.</div></div>"
                f"<div class='chart'>{beacon_strip}</div></section>")

    # P3 — KPI row (5 cards, each metric beside its baseline; guard threshold printed on card 3)
    kpis = [
        _kpi_card(f"{HORIZON}-mo exit risk (model)", _pct(c["bottom_up"]),
                  f"{_pp(c['gap'])} vs observed", "warn", f"observed {_pct(c['top_down'])} · test m30–35"),
        _kpi_card("Base voluntary hazard", f"{_pct(m['base_rate'])}/mo", "test window", "flat",
                  f"{m['n_test_rows']:,} person-months · {m['n_test_employees']:,} emp"),
        _kpi_card("Rank quality (ROC-AUC)", f"{m['roc_auc']:.3f}",
                  f"guard fails >{R.REALISM_AUC_MAX:g}", "up", "out-of-time test · imbalanced"),
        _kpi_card("Top-decile lift", _mult(report["lift"]),
                  f"{report['n_exits']} / {report['n_flagged']} flagged", "flat",
                  f"vs {_pct(m['base_rate'])} base rate"),
        _kpi_card("Segments", str(recon["n_segments"]),
                  f"{recon['n_flagged']} gap-flagged", "warn",
                  f"{recon['n_suppressed']} suppressed · floor n≥{R.SEG_MIN_N}"),
    ]
    body.append("<section class='kpis'>" + "".join(kpis) + "</section>")

    grid = []

    # P4 — risk by comp position & tenure (model vs observed)
    seg_legend = _legend([("model (6-mo, calibrated)", ch.CYAN), ("observed (KM, same window)", ch.SOFT),
                          ("disagreement > 2pp", ch.RED)])
    p4_stats = _statrow([(_mult(report["ratio_below_above"]), "below-vs-above band", None),
                         (_pp(comp["below"]["reconciliation_gap"]), "model-high on below-band", ch.RED),
                         (_pct(report["top_tenure"]["bottom_up_6mo"]), f"at {_seg_label('tenure_band', report['top_tenure']['value'])}", None)])
    grid.append("<div class='col-6'>" + _tile(
        "Risk by comp position & tenure", "Model vs observed 6-mo voluntary exit · worst-first",
        _seg_forest(report, ["comp_position_band", "tenure_band"]),
        seg_legend + p4_stats
        + "<div class='foot-note'>Where pay sits in band is the sharpest split the model and observed history "
          "AGREE on — a compensation lever, routed to the comp arms below.</div>") + "</div>")

    # P5 — risk by function & level (the governance showcase)
    w = report["worst"]["seg"]
    p5_stats = _statrow([(_pct(report["hi"]["seg"]["bottom_up_6mo"]),
                          f"peak: {_seg_label(report['hi']['dim'], report['hi']['seg']['value'])} "
                          f"(n={report['hi']['seg']['n_employees']})", None),
                         (_pp(w["reconciliation_gap"]),
                          f"worst disagreement: {_seg_label(report['worst']['dim'], w['value'])}", ch.RED)])
    grid.append("<div class='col-6'>" + _tile(
        "Risk by function & level", "Model vs observed · red = disagreement surfaced, never averaged",
        _seg_forest(report, ["function", "level"]),
        seg_legend + p5_stats
        + f"<div class='foot-note'>Red rows are where model and history disagree beyond {_pct(R.RECONCILE_GAP, 0)}. "
          f"{_e(_seg_label(report['worst']['dim'], w['value']))} is the cautionary tale: the model reads "
          f"{_pct(w['bottom_up_6mo'])} where history shows {_pct(w['top_down_6mo'])}. Trust the agreeing rows, "
          f"interrogate the red ones — that is what a glass-box is for.</div>") + "</div>")

    # P6 — segment ledger (the audit table, all rendered segments)
    grid.append("<div class='col-12'>" + _ledger(report) + "</div>")

    # P7 — glass-box drivers (the second marquee)
    grid.append("<div class='col-7'>" + _drivers_panel(report) + "</div>")

    # P8 — trust panel
    grid.append("<div class='col-5'>" + _trust_panel(report) + "</div>")

    # P8b — reliability diagram (does the calibrated probability match reality?)
    grid.append("<div class='col-12'>" + _reliability_panel(report) + "</div>")

    # P9 — survival outlook
    grid.append("<div class='col-6'>" + _survival_panel(report) + "</div>")

    # P10 — support tiers
    grid.append("<div class='col-6'>" + _tiers_panel(report) + "</div>")

    # P11 — planning levers
    grid.append("<div class='col-7'>" + _levers_panel(report) + "</div>")

    # P12 — fairness (unchecked checklist)
    grid.append("<div class='col-5'>" + _fairness_panel() + "</div>")

    body.append("<section class='grid'>" + "".join(grid) + "</section>")

    # footer
    body.append("<footer class='foot'>"
                f"<div>Composed by the <b>{AGENT}</b> agent · every number is engine-computed from "
                "<b>foundation/compute/retention.py</b> and reproduced in CI · a human owns what is published.</div>"
                "<div class='pills'>"
                "<span class='pill'>Synthetic data</span>"
                "<span class='pill'>Voluntary exits only</span>"
                "<span class='pill'>Out-of-time eval m30–35</span>"
                f"<span class='pill'>Suppression floor n≥{R.SEG_MIN_N}</span>"
                "<span class='pill'>Fairness: scaffolding only</span>"
                "<span class='pill'>Model manifest: retention_model_manifest.json</span>"
                "<span class='pill'>Realism-guarded</span></div></footer>")
    return _page("".join(body))


def _ledger(report):
    head = ("<tr><th>Dimension</th><th>Segment</th><th class='num'>Employees</th>"
            f"<th class='num'>Model {HORIZON}-mo</th><th class='num'>Observed {HORIZON}-mo</th>"
            "<th class='num'>Gap (pp)</th><th>Flag</th></tr>")
    trs = []
    for dim in LEDGER_DIMS:
        for s in _by_model_desc(report["segments"][dim]):
            if s.get("suppressed"):
                trs.append(f"<tr><td>{_e(DIM_LABELS[dim])}</td><td>{_e(_seg_label(dim, s['value']))}</td>"
                           f"<td class='mono'>{_e(s['size_band'])}</td><td class='mono'>—</td>"
                           "<td class='mono'>—</td><td class='mono'>—</td>"
                           "<td><span class='flagchip' style='color:var(--soft);background:rgba(141,177,206,.12)'>SUPPRESSED</span></td></tr>")
                continue
            gap = s["reconciliation_gap"]
            gcls = "gap-hi" if gap > R.RECONCILE_GAP else "gap-lo" if gap < -R.RECONCILE_GAP else ""
            flag = "<span class='flagchip'>GAP</span>" if s["gap_flagged"] else ""
            trs.append(f"<tr><td>{_e(DIM_LABELS[dim])}</td><td>{_e(_seg_label(dim, s['value']))}</td>"
                       f"<td class='mono'>{s['n_employees']:,}</td>"
                       f"<td class='mono'>{_e(_pct(s['bottom_up_6mo']))}</td>"
                       f"<td class='mono'>{_e(_pct(s['top_down_6mo']))}</td>"
                       f"<td class='mono {gcls}'>{_e(_pp(gap))}</td><td>{flag}</td></tr>")
    foot = (f"Suppressed this run: {report['recon']['n_suppressed']} segments · privacy floor "
            f"n ≥ {R.SEG_MIN_N} employees (raisable, never lowerable) · a suppressed segment shows a coarse "
            "size band only — no estimate.")
    cap = (f"The full reconciliation ledger — every rendered segment, both estimates, and the gap. "
           f"Max |gap| {_pct(report['recon']['max_abs_gap'])} "
           f"({_seg_label(report['worst']['dim'], report['worst']['seg']['value'])}). "
           f"{report['recon']['n_flagged']} of {report['recon']['n_segments']} flagged.")
    return (f"<div class='tile'><div class='t-head'><div><h3>Segment ledger — the audit surface</h3>"
            f"<div class='t-sub'>All rendered segments across 6 dimensions · region is broad-only, never country</div></div>"
            "<span class='t-scope'>Reconciliation</span></div>"
            f"<table class='ledger'>{head}{''.join(trs)}</table>"
            f"<div class='ledger-foot'>{_e(foot)}</div>"
            f"<div class='foot-note'>{_e(cap)}</div></div>")


def _drivers_panel(report):
    # each driver carries its 95% robust (sandwich) coefficient interval as a whisker — so the chart shows
    # not just the point estimate but how tightly the data pins it (a wide bar crossing 0 = not distinguishable
    # from no effect). The axis widens to hold the widest interval so no whisker renders off-canvas.
    cif = report["ci_by_feature"]
    rows = []
    for n, v in report["drivers"]:
        e = cif[n]
        rows.append({"group": DRIVER_LABELS[n], "adj": round(v, 2),
                     "ci_lo": round(e["ci_lo"], 2), "ci_hi": round(e["ci_hi"], 2)})
    lo = min([r["ci_lo"] for r in rows] + [-0.8])
    hi = max([r["ci_hi"] for r in rows] + [0.6])
    axis_lo = math.floor(lo * 5) / 5.0                          # snap to the 0.2 tick grid, with headroom
    axis_hi = math.ceil(hi * 5) / 5.0
    forest = ch.forest_plot(rows, axis_lo, axis_hi, unit="", zero_label="no effect (0)", color_mode="direction",
                            tick_step=0.2, label_w=210, value_fmt=lambda v: f"{v:+.2f}")
    heads = ("<div class='drv-heads'><span class='prot'>← Protects (keeps people)</span>"
             "<span class='risk'>Raises risk (pushes people out) →</span></div>")
    stats = _statrow([("equity + fair pay", "lead the protectors", ch.GREEN),
                      ("raise/promo drought", "top push factor", ch.RED),
                      ("manager team churn", "a leading driver", ch.RED)])
    cap = (f"Exact additive log-odds per feature (contributions + intercept {report['intercept']:.2f} = the score "
           "— no approximation, nothing hidden), each with a whisker showing its <b>95% robust (sandwich) "
           "stability interval — evaluated at the published pinned coefficients, no re-fit; synthetic-validation "
           "only</b>. <b>Associational, not causal</b>: this linear model's decomposition, not 'the reason' "
           "anyone leaves. The top of this chart is the comp arms' home turf — equity vesting and band position.")
    note = (f"3 planted decoy (pure-noise) features rank #{report['decoy_ranks'][0]}, #{report['decoy_ranks'][1]}, "
            f"#{report['decoy_ranks'][2]} of {len(report['coef_ranked'])} — and every decoy's interval "
            "<b>spans 0</b> (indistinguishable from no effect), the leakage tripwire confirmed twice over.")
    return (f"<div class='tile'><div class='t-head'><div><h3>Why people leave — the glass-box drivers</h3>"
            "<div class='t-sub'>Top-12 coefficients · additive log-odds per +1 SD · 95% stability whiskers (pinned coeffs, no re-fit) · protective (green) vs risk (red)</div></div>"
            "<span class='t-scope'>Model internals</span></div>"
            f"<div class='chart'>{heads}{forest}</div>{stats}"
            f"<div class='foot-note'>{cap}</div><div class='foot-note'>{note}</div></div>")


def _reliability_panel(report):
    """Decile reliability diagram: predicted vs observed voluntary-exit rate per equal-count bin, on the
    calibration diagonal, with the Expected Calibration Error. The single sharpest 'is it calibrated?' chart."""
    rel = report["reliability"]
    bins = rel["bins"]
    w, h = 560, 300
    mL, mR, mT, mB = 52, 16, 20, 44
    pw, ph = w - mL - mR, h - mT - mB
    hi = max([b["mean_pred"] for b in bins] + [b["obs_freq"] for b in bins]) * 1.08 or 1.0
    X = lambda v: mL + (v / hi) * pw
    Y = lambda v: mT + ph - (v / hi) * ph
    b = [f"<line x1='{mL}' y1='{Y(0):.1f}' x2='{X(hi):.1f}' y2='{Y(hi):.1f}' stroke='{ch.SOFT}' "
         f"stroke-dasharray='4 4' opacity='.7'/>",
         f"<text x='{X(hi):.1f}' y='{Y(hi)-6:.1f}' text-anchor='end' font-family=\"{ch.MONO}\" "
         f"font-size='9' fill='{ch.SOFT}'>perfect calibration</text>"]
    for t in range(5):
        gv = hi * t / 4
        b.append(f"<line x1='{mL}' y1='{Y(gv):.1f}' x2='{X(hi):.1f}' y2='{Y(gv):.1f}' stroke='{ch.GRID}'/>")
        b.append(f"<text x='{mL-6}' y='{Y(gv)+3:.1f}' text-anchor='end' font-family=\"{ch.MONO}\" "
                 f"font-size='8.5' fill='{ch.SOFT}'>{gv*100:.0f}%</text>")
        b.append(f"<text x='{X(gv):.1f}' y='{h-16:.1f}' text-anchor='middle' font-family=\"{ch.MONO}\" "
                 f"font-size='8.5' fill='{ch.SOFT}'>{gv*100:.0f}%</text>")
    pts = "".join(f"<circle cx='{X(bn['mean_pred']):.1f}' cy='{Y(bn['obs_freq']):.1f}' r='4' "
                  f"fill='{ch.RED if abs(bn['gap'])>0.01 else ch.CYAN2}' stroke='{ch.BG}' stroke-width='1'/>"
                  for bn in bins)
    b.append(f"<text x='{(mL+X(hi))/2:.1f}' y='{h-4}' text-anchor='middle' font-family=\"{ch.MONO}\" "
             f"font-size='9' fill='{ch.MUTED}'>predicted monthly exit probability →</text>")
    b.append(pts)
    svg = ch._svg(w, h, "".join(b)).replace("<svg ", "<svg data-chart='reliability' ", 1)
    return (f"<div class='tile'><div class='t-head'><div><h3>Is it calibrated? — reliability by decile</h3>"
            f"<div class='t-sub'>Predicted vs observed exit rate per equal-count bin · on the diagonal = honest</div></div>"
            "<span class='t-scope'>Calibration</span></div>"
            f"<div class='chart'>{svg}</div>"
            f"<div class='foot-note'>Expected Calibration Error <b>{rel['ece']*100:.2f}%</b> across {rel['n_bins']} "
            f"deciles of {rel['n']:,} test person-months (base rate {rel['base_rate']*100:.2f}%/mo). Points on the "
            "dashed diagonal mean the calibrated probability matches reality; a red point is a bin that misses by "
            "&gt;1pp. Computed on the out-of-time test slice from the published model — no re-fit.</div></div>")


def _trust_panel(report):
    m = report["metrics"]
    base = m["base_rate"]
    brier_noskill = base * (1 - base)
    rows = [
        ("PR-AUC", f"{m['pr_auc']:.3f}", f"{base:.3f}", f"{_mult(m['pr_auc'] / base)} baseline"),
        ("Precision@top-decile", _pct(m['precision_at_k']['precision']), _pct(base),
         f"{_mult(report['lift'])} — {report['n_exits']} of {report['n_flagged']} exit"),
        ("ROC-AUC", f"{m['roc_auc']:.3f}", "0.500", "strong ranking"),
        ("Brier", f"{m['brier']:.4f}", f"{brier_noskill:.4f}", "≈ base-rate forecast"),
        ("Survival concordance", f"{m['horizon_concordance']:.3f}", "0.500", "orders time-to-exit"),
    ]
    tbl = "<table class='mini'><tr><th>metric</th><th>model</th><th>no-skill</th><th>read</th></tr>" + "".join(
        f"<tr><td>{_e(a)}</td><td class='mono'>{_e(b)}</td><td class='mono'>{_e(cc)}</td>"
        f"<td class='mono good'>{_e(d)}</td></tr>" for a, b, cc, d in rows) + "</table>"
    lead = ("<p class='foot-note'>On a "
            f"{_pct(base)}-per-month event, honest numbers look small. A no-skill model scores PR-AUC {base:.3f}; "
            "'accuracy' would read ~98% for a model that predicts nobody ever leaves. So we grade on lift over "
            "no-skill — and print the baseline beside every number. The value is in <b>ranking</b>: the top decile "
            f"of person-months catches {_mult(report['lift'])} its share of real exits.</p>")
    guard = ("<div class='guardrow'><span>ROC "
             f"{m['roc_auc']:.3f}</span><span class='ok'>OK · fails &gt;{R.REALISM_AUC_MAX:g}</span></div>"
             f"<div class='guardrow'><span>PR-AUC {m['pr_auc']:.3f}</span>"
             f"<span class='ok'>OK · fails &gt;{R.REALISM_PR_AUC_MAX:g}</span></div>"
             f"<div class='guardrow'><span>precision@k {_pct(m['precision_at_k']['precision'])}</span>"
             "<span class='ok'>OK · fails if perfect</span></div>"
             f"<div class='guardrow'><span>decoy in top-3</span><span class='ok'>none · best decoy #{report['decoy_ranks'][0]}</span></div>")
    sz = report["slice_sizes"]
    total = sum(sz.values())
    split = ("<div class='split'>"
             f"<div style='flex:{sz['train']};background:var(--cyan)'>TRAIN ≤m23 · {sz['train']:,}</div>"
             f"<div style='flex:{sz['calibration']};background:var(--indigo)'>CAL m24–29 · {sz['calibration']:,}</div>"
             f"<div style='flex:{sz['test']};background:var(--green)'>TEST m30–35 · {sz['test']:,}</div></div>")
    return (f"<div class='tile'><div class='t-head'><div><h3>Trust panel — honest metrics, on purpose</h3>"
            "<div class='t-sub'>Every number beside its no-skill baseline · the realism guard · the out-of-time split</div></div>"
            "<span class='t-scope'>Evaluation</span></div>"
            f"{tbl}{lead}"
            "<div class='foot-note' style='margin-top:10px'><b>Realism guard — 'too good fails the build'</b></div>"
            f"{guard}"
            f"<div class='foot-note' style='margin-top:10px'>Everything scored here is from the <b>future</b> "
            "relative to training — a genuine forward test, Platt-calibrated on the middle window; band "
            "thresholds are set on calibration, so no test information leaks into them.</div>"
            f"{split}</div>")


def _survival_panel(report):
    surv = report["survival"]
    bins = [round(s * 100, 1) for s in surv["survival"]]
    labels = [f"M{i + 1}" for i in range(len(bins))]
    hi_bin = HORIZON - 1
    chart = ch.histogram(bins, labels, highlight={hi_bin: ch.CYAN})
    c = report["company"]
    p6 = surv["p_exit"][HORIZON - 1]
    p12 = surv["p_exit"][min(11, len(surv["p_exit"]) - 1)]
    med = surv["median_months"]
    med_txt = f"{med} mo" if med is not None else f">{surv['median_horizon']} mo"
    stats = _statrow([(f"{_pct(c['bottom_up'])} / {_pct(c['top_down'])}", f"P(exit ≤ {HORIZON}mo) model / obs", None),
                      (_pct(p12), "P(exit ≤ 12mo) model", None),
                      (med_txt, "median time-to-exit", None)])
    return _tile("Survival outlook — the honest clock",
                 f"Company survival S(t), months 1–{SURV_MAX_H} · the {HORIZON}-mo bin is the planning horizon",
                 chart,
                 stats + f"<div class='foot-note'>Company-level survival under the calibrated model; risk compounds "
                         f"monthly from a {_pct(report['metrics']['base_rate'])} base hazard. Months beyond the "
                         "observed test window are projected forward at the current hazard (a what-if, not a forecast).</div>",
                 scope="Company")


def _tiers_panel(report):
    t = report["tiers"]
    counts, thr = t["counts"], t["thresholds"]
    bins = [counts["low"], counts["elevated"], counts["high"]]
    labels = ["Low", "Elevated", "High"]
    chart = ch.histogram(bins, labels, highlight={0: ch.GREEN, 1: ch.AMBER, 2: ch.RED})
    stats = _statrow([(f"{_pct(thr['elevated'], 2)}/mo", "elevated ≥", ch.AMBER),
                      (f"{_pct(thr['high'], 2)}/mo", "high ≥", ch.RED),
                      (f"{t['n_rows']:,}", "person-months", None)])
    cap = ("Tiers size the <b>support</b> effort — how many segments' worth of people get proactive stay-interviews, "
           "comp review, or manager attention. Counted in person-months, never listed as people, and a tier is "
           "<b>never an adverse-action label</b>. Thresholds set at the calibration 85th/97th percentiles; "
           "test-slice shares shown — drift is visible, not hidden.")
    return _tile("Support tiers — where help goes first",
                 "Test-slice person-months per tier · thresholds from the calibration slice", chart,
                 stats + f"<div class='foot-note'>{cap}</div>", scope="Support sizing")


def _levers_panel(report):
    comp = report["comp"]
    below, tenure = comp["below"], report["top_tenure"]
    coef = dict(report["coef_ranked"])                          # live coefficients — chip VALUES are never hardcoded

    def chip(name):
        v = coef[name]
        return (f"{LEVER_CHIP[name]} {v:+.2f}", "prot" if v < 0 else "risk")

    cards = [
        ("Comp review — routed, never automatic",
         [chip("comp_ratio"), chip("mths_since_last_raise"), chip("unvested_equity_pct_comp")],
         f"below-band ({_pct(below['bottom_up_6mo'])}) · {_seg_label('tenure_band', tenure['value'])} ({_pct(tenure['bottom_up_6mo'])})",
         "Raises a <span class='gate'>comp review suggested</span> flag into the comp-governance process "
         "(the Exec-Comp / benchmarking arms) — a human runs the review; this signal never changes pay directly."),
        ("Manager support",
         [chip("mgr_team_attrition_ttm"), chip("team_departures_90d")],
         "segments where team-churn features dominate the additive explanation",
         "HRBP coaching + backfill planning — with the manager's HRBP, <span class='gate'>never a manager-facing "
         "risk score</span>."),
        ("Career pathing & recognition",
         [chip("mths_since_promo"), chip("stuck_in_level_flag"), chip("high_perf_unrecognized")],
         "L3 and stuck-in-level populations",
         "Promotion-readiness and recognition review in the <span class='gate'>next talent cycle</span>."),
    ]
    html = []
    for name, drivers, targets, route in cards:
        chips = "".join(f"<span class='d {k}'>{_e(t)}</span>" for t, k in drivers)
        html.append(f"<div class='lever'><h4>{_e(name)}</h4><div class='lv-drv'>{chips}</div>"
                    f"<div class='lv-tgt'>Targets: {_e(targets)}</div><div class='lv-route'>{route}</div></div>")
    cap = ("The model recommends <b>context for a planning conversation</b>; a named human owns every consequential "
           "decision, and any action is loggable to the decision ledger (hashes/IDs/disposition — never raw features).")
    return (f"<div class='tile'><div class='t-head'><div><h3>What a committee does with this — planning levers</h3>"
            "<div class='t-sub'>Every lever ends in a human gate · recommendations, never actions</div></div>"
            "<span class='t-scope'>Planning</span></div>"
            f"<div class='levers'>{''.join(html)}</div><div class='foot-note'>{cap}</div></div>")


def _fairness_panel():
    checklist = (
        "<span class='hdr'>SHIPPED (scaffolding)</span>\n"
        "<span class='done'> [x] protected attributes excluded from model inputs</span>\n"
        "<span class='done'> [x] neutral audit stratum retained (never a feature)</span>\n"
        "<span class='done'> [x] small-n suppression, floor n ≥ 30 (raisable only)</span>\n"
        "<span class='done'> [x] two-way segment reconciliation, gaps surfaced</span>\n"
        "<span class='hdr'>NOT BUILT (the fairness-audit increment)</span>\n"
        "<span class='todo'> [ ] group calibration curves</span>\n"
        "<span class='todo'> [ ] FPR / FNR &amp; equal-opportunity gaps</span>\n"
        "<span class='todo'> [ ] subgroup precision@k with confidence intervals</span>\n"
        "<span class='todo'> [ ] documented remediation playbook</span>")
    cap = ("Until the audit increment exists, treat any group-level read as unvalidated. Four-fifths-rule screening "
           "alone is considered too thin here. Publishing an unchecked checklist is the point — a governed system "
           "shows its unfinished edges.")
    return (f"<div class='tile fair'><div class='t-head'><div><h3>Fairness — NOT YET VALIDATED</h3>"
            "<div class='t-sub'>scaffolding shipped · audit not built — do not rely on this build for a fairness determination</div></div>"
            "<span class='t-scope'>Governance</span></div>"
            f"<div class='checklist'>{checklist}</div><div class='foot-note'>{cap}</div></div>")


# ---------------------------------------------------------------- digest
def render_digest(report):
    c, m, recon = report["company"], report["metrics"], report["recon"]
    comp, w = report["comp"], report["worst"]["seg"]
    base = m["base_rate"]
    top3 = sorted(((dim, s) for dim in report["segments"] for s in _rendered(report["segments"][dim])),
                  key=lambda ds: -ds[1]["bottom_up_6mo"])[:3]
    prot = [(DRIVER_LABELS[n], v) for n, v in report["coef_ranked"] if v < 0 and n in DRIVER_LABELS][:3]
    risk = [(DRIVER_LABELS[n], v) for n, v in report["coef_ranked"] if v > 0 and n in DRIVER_LABELS][:3]
    lines = [
        f"# {COMPANY} — Retention Risk (Committee View) digest",
        f"_{PERIOD} · draft for review_", "",
        f"- **Headline:** 6-mo voluntary-exit risk **{_pct(c['top_down'])}** observed / **{_pct(c['bottom_up'])}** "
        f"model ({_pp(c['gap'])}); below-band **{_mult(report['ratio_below_above'])}** the above-band rate; "
        f"**{_md(_seg_label('tenure_band', report['top_tenure']['value']))}** runs {_pct(report['top_tenure']['bottom_up_6mo'])}.",
        "- **Where:** top-3 risk segments — "
        + "; ".join(f"{DIM_LABELS[dim]} {_md(s['value'])} {_pct(s['bottom_up_6mo'])} model / "
                    f"{_pct(s['top_down_6mo'])} obs" for dim, s in top3)
        + f". {recon['n_flagged']} of {recon['n_segments']} segments flagged; "
        f"{_md(_seg_label(report['worst']['dim'], w['value']))} {_pp(w['reconciliation_gap'])} — trust the agreeing rows, "
        "interrogate the red ones.",
        "- **Why:** protective — "
        + ", ".join(f"{lab} ({v:+.2f})" for lab, v in prot)
        + "; risk — " + ", ".join(f"{lab} ({v:+.2f})" for lab, v in risk)
        + ". The top of the driver chart is equity vesting and band position — retention is a comp lever.",
        f"- **Trust:** ROC **{m['roc_auc']:.3f}** · PR-AUC **{m['pr_auc']:.3f}** vs no-skill **{base:.3f}** · "
        f"top-decile lift **{_mult(report['lift'])}** ({report['n_exits']}/{report['n_flagged']}) · out-of-time "
        f"split · realism guard passed (decoys #{report['decoy_ranks'][0]}/#{report['decoy_ranks'][1]}/#{report['decoy_ranks'][2]}).",
        "- **Do (planning only):** point the next comp cycle at flagged below-band/senior-tenure segments (human-gated); "
        "manager support where team churn drives risk (human-gated); career pathing for stuck-in-level (human-gated).",
        "", "```",
        "SEGMENT-FIRST · no per-employee score surfaced, ever",
        "PLANNING SIGNAL · never adverse action, never a manager leaderboard",
        "SYNTHETIC DATA · mechanics + governance, not real-world accuracy",
        "FAIRNESS: NOT YET VALIDATED · scaffolding only",
        "VOLUNTARY EXITS ONLY · involuntary & retirement censored",
        "```", "",
        "_Draft. A named human (People Analytics) must approve before distribution. Nothing was sent._",
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
a{color:var(--cyan2);text-decoration:none;}a:hover{text-decoration:underline;}
.topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;border-bottom:2px solid var(--cyan);padding-bottom:16px;}
.brandwrap{display:flex;align-items:center;gap:12px;min-width:0;}
.logomark{height:42px;width:auto;flex:0 0 auto;display:block;}
.brand{font-weight:800;font-size:18px;color:#fff;}.brand .os{color:var(--cyan);}
.brand-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--soft);margin-top:2px;}
.title h1{margin:0;font-size:20px;color:#fff;font-weight:800;letter-spacing:-.01em;}
.title .meta{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;color:var(--muted);margin-top:3px;}
.spacer{flex:1;}
.status{border:1px solid rgba(247,185,85,.5);color:var(--amber);background:rgba(247,185,85,.12);font-size:11px;font-weight:800;padding:5px 12px;border-radius:999px;text-transform:uppercase;}
.rails{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:16px 0 4px;padding:12px 14px;border:1px solid rgba(247,185,85,.32);background:rgba(247,185,85,.05);border-radius:12px;}
.rails .rl{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.13em;text-transform:uppercase;color:var(--amber);font-weight:800;margin-right:4px;}
.rails .chip2{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--muted);border:1px solid rgba(247,185,85,.4);border-radius:999px;padding:4px 10px;}
.rails-cap{flex-basis:100%;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:4px;}
.insight{display:flex;gap:13px;align-items:flex-start;margin:14px 0;background:linear-gradient(98deg,rgba(27,167,255,.12),rgba(27,167,255,.03) 65%,transparent);
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
.beacon .sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;color:var(--soft);text-align:right;max-width:330px;}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:16px;}
.kpi{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:14px 15px;display:flex;flex-direction:column;gap:9px;}
.kpi .k-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
.kpi .k-label{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);line-height:1.3;}
.kpi .k-spark{flex:0 0 auto;width:84px;text-align:right;}
.k-na{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:8.5px;color:var(--soft);opacity:.8;}
.kpi .k-val{font-size:24px;font-weight:800;letter-spacing:-.02em;line-height:1;color:#fff;}
.kpi .k-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.chip{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;font-weight:700;padding:2px 7px;border-radius:6px;white-space:nowrap;}
.chip.up{color:var(--green);background:rgba(67,212,119,.14);}.chip.down{color:var(--red);background:rgba(255,77,79,.14);}
.chip.flat{color:var(--cyan2);background:rgba(27,167,255,.14);}
.chip.warn{color:var(--amber);background:rgba(247,185,85,.14);}.chip.ok{color:var(--green);background:rgba(67,212,119,.14);}
.k-ctx{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;}
.tile{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:16px 18px 14px;display:flex;flex-direction:column;}
.col-6{grid-column:span 6;}.col-7{grid-column:span 7;}.col-5{grid-column:span 5;}.col-12{grid-column:span 12;}
.t-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:2px;}
.t-head h3{margin:0;font-size:14.5px;color:#fff;font-weight:700;}
.t-sub{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:3px;}
.t-scope{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);border:1px solid var(--hair);border-radius:999px;padding:3px 9px;white-space:nowrap;}
.chart{margin-top:10px;}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--muted);}
.legend span{display:inline-flex;align-items:center;gap:6px;}.legend i{width:10px;height:10px;border-radius:3px;}
.statrow{display:flex;gap:20px;flex-wrap:wrap;margin-top:12px;padding-top:12px;border-top:1px solid var(--hair);}
.stat .s-v{font-size:17px;font-weight:800;color:#fff;}.stat .s-l{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);display:block;margin-top:2px;}
.foot-note{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:9px;line-height:1.5;}
.foot-note b{color:var(--muted);}
.drv-heads{display:flex;justify-content:space-between;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px;}
.drv-heads .prot{color:var(--green);}.drv-heads .risk{color:var(--red);}
.ledger{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;}
.ledger th{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);}
.ledger th.num{text-align:right;}
.ledger td{padding:6px 10px;border-bottom:1px solid var(--hair);color:var(--text);}
.ledger td.mono{text-align:right;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;}
.ledger tr:hover{background:rgba(27,167,255,.04);}
.ledger .gap-hi{color:var(--red);font-weight:700;}.ledger .gap-lo{color:var(--amber);font-weight:700;}
.ledger .flagchip{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;font-weight:800;color:var(--red);background:rgba(255,77,79,.14);border-radius:5px;padding:1px 6px;}
.ledger-foot{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);margin-top:8px;}
.mini{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:6px;}
.mini th{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;}
.mini th:first-child{text-align:left;}
.mini td{padding:5px 8px;border-bottom:1px solid var(--hair);text-align:right;}
.mini td:first-child{text-align:left;color:var(--muted);}
.mini .good{color:var(--green);}
.guardrow{display:flex;justify-content:space-between;gap:8px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10.5px;padding:5px 0;border-bottom:1px solid var(--hair);}
.guardrow .ok{color:var(--green);}
.split{display:flex;gap:3px;margin-top:12px;height:34px;border-radius:7px;overflow:hidden;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;}
.split div{display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:#04121c;padding:0 6px;text-align:center;line-height:1.1;}
.levers{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:8px;}
.lever{background:var(--panel);border:1px solid var(--hair);border-radius:10px;padding:12px 13px;display:flex;flex-direction:column;gap:8px;}
.lever h4{margin:0;font-size:12.5px;color:#fff;line-height:1.25;}
.lever .lv-drv{display:flex;flex-wrap:wrap;gap:5px;}
.lever .d{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:9px;padding:2px 6px;border-radius:5px;}
.lever .d.prot{color:var(--green);background:rgba(67,212,119,.12);}.lever .d.risk{color:var(--red);background:rgba(255,77,79,.12);}
.lever .lv-tgt{font-size:11px;color:var(--muted);}
.lever .lv-route{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10px;color:var(--soft);border-top:1px solid var(--hair);padding-top:7px;line-height:1.5;}
.lever .gate{color:var(--amber);font-weight:700;}
.fair{border-left:3px solid var(--amber);}
.checklist{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;line-height:1.7;white-space:pre;margin-top:8px;}
.checklist .done{color:var(--green);}.checklist .todo{color:var(--amber);}.checklist .hdr{color:var(--soft);letter-spacing:.05em;}
.foot{margin-top:22px;padding-top:14px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:10.5px;color:var(--soft);}
.foot .pills{display:flex;gap:8px;flex-wrap:wrap;}.foot .pill{border:1px solid var(--hair);border-radius:999px;padding:3px 9px;}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — Retention Risk (Committee View)</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
def _fail_closed(message):
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


def _atomic_write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Acme Corp retention-risk committee view (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(ch_) < 32 for ch_ in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        # a refused publish must not leave a prior 'approved' marker standing — invalidate it before returning
        (OUT / "PUBLISHED.json").unlink(missing_ok=True)
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (People Analytics).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        report = build_report()
        html_doc, digest_doc = render_html(report), render_digest(report)
        # PUBLISHING is a distribution act: require the committed manifest to REPRODUCE (a fresh re-fit within
        # tolerance) before we write the approval, so a report whose model no longer matches its published
        # weights can never be blessed. (A plain draft skips this — the reproducibility gate is CI's job there.)
        if args.publish and not R.check_reproducible():
            raise ReportError("model manifest does not reproduce within tolerance — refusing to publish")
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:                                    # any engine/data failure fails closed, never a half-draft
        return _fail_closed(f"retention engine unavailable: {exc}")

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
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF}, indent=2) + "\n")
        elif pub_path.exists():
            pub_path.unlink()
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    c, recon = report["company"], report["recon"]
    print(f"{COMPANY} Retention Risk — Committee View ({AS_OF})")
    print(f"  6-mo exit risk model {_pct(c['bottom_up'])} / observed {_pct(c['top_down'])} | "
          f"{recon['n_segments']} segments, {recon['n_flagged']} gap-flagged, {recon['n_suppressed']} suppressed")
    print("  wrote report.sample.html and committee-digest.sample.md")
    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (People Analytics) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
