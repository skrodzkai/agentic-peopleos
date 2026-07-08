#!/usr/bin/env python3
"""Acme Corp — Pay-Equity & EU Pay Transparency agent (Agentic PeopleOS Executive-Comp arm).

Presentation + governance over foundation/compute/pay_equity.py. It renders the two numbers a Total-Rewards
leader must be able to defend to the board and, from 2026-27, to regulators under the EU Pay Transparency
Directive: the RAW pay gap (what you must publish) and the ADJUSTED, like-for-like residual (what an equal-pay
audit actually investigates), the latter with a confidence interval. It then runs the Directive's 5%
joint-pay-assessment screen per category of workers. The agent does no math and recommends no pay change; it
reports and governs.

IMPORTANT (on the dashboard and here): protected-class groups are PSEUDONYMISED in the synthetic data (A / B,
grp1-3) — the tool reports gaps between groups and never asserts which real class a label denotes. "Category
of workers" is job level, a stand-in for the Directive's equal-work grouping. Pay is base only. A surviving
adjusted gap is a flag for a privileged equal-pay review, not a legal finding.

    python3 run.py                                       # writes the draft dashboard + digest (nothing sent)
    python3 run.py --publish --approved-by "Chief People Officer"

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

from foundation.compute import pay_equity as PE          # noqa: E402
from foundation.render import dashboard as dash           # noqa: E402
from foundation.render import charts as ch                # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "31 Jan 2026"
AGENT = "pay-equity"
SCOPE = "publish.pay_equity_report"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")


class ReportError(RuntimeError):
    """Raised when the pay-equity view cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _finite(*vals):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals)


# ---------- build (validate the engine output; the agent trusts nothing silently) ----------

def build_report(result):
    for k in ("company", "as_of", "population", "headline", "dimensions", "pay_measure", "disclaimer"):
        if k not in result:
            raise ReportError(f"engine result missing '{k}'")
    n = result["population"]["n_analyzed"]
    if not (isinstance(n, int) and n > 0):
        raise ReportError("engine reported a non-positive analyzed population")

    gender = next((d for d in result["dimensions"] if d["key"] == "gender_group"), None)
    if gender is None or "eu_pay_transparency" not in gender:
        raise ReportError("engine result is missing the primary gender lens with its EU screen")

    # every group's counts must partition the population; every RENDERED number must be finite (a NaN/Inf must
    # fail closed, never reach the page); every CI must bracket its point estimate.
    for d in result["dimensions"]:
        if sum(g["n"] for g in d["unadjusted"]["groups"]) != n:
            raise ReportError(f"{d['key']}: group counts do not partition the analyzed population")
        for g in d["unadjusted"]["groups"]:
            if not _finite(g["mean_gap_pct"], g["median_gap_pct"], g["mean_hourly"], g["median_hourly"]):
                raise ReportError(f"{d['key']}: non-finite raw-gap statistics for {g['group']}")
        for g in d["adjusted"]["groups"]:
            if not _finite(g["adjusted_gap_pct"], g["ci_lo_pct"], g["ci_hi_pct"], g["se"]):
                raise ReportError(f"{d['key']}: non-finite adjusted-gap statistics for {g['group']}")
            if not (g["ci_lo_pct"] <= g["adjusted_gap_pct"] <= g["ci_hi_pct"]):
                raise ReportError(f"{d['key']}: adjusted CI does not bracket the point estimate for {g['group']}")
        if not (0.0 <= d["adjusted"]["r2"] <= 1.0):
            raise ReportError(f"{d['key']}: R^2 out of range")

    eu = gender["eu_pay_transparency"]
    # every assessable category's rendered gaps must be finite, and its flag consistent with the >=5% trigger
    for c in eu["categories"]:
        if not c.get("assessable"):
            continue
        if not _finite(c["mean_gap_pct"], c["median_gap_pct"]):
            raise ReportError(f"EU category {c['category']}: non-finite gap statistics")
        if c["exceeds_threshold"] != (c["mean_gap_pct"] >= eu["threshold_pct"]):
            raise ReportError(f"EU category {c['category']}: flag inconsistent with its mean gap vs threshold")
    if eu["joint_assessment_required"] != (eu["n_flagged"] > 0):
        raise ReportError("EU joint-assessment flag inconsistent with the flagged-category count")

    h = result["headline"]
    if not _finite(h["unadjusted_median_gap_pct"], h["unadjusted_mean_gap_pct"], h["adjusted_gap_pct"]):
        raise ReportError("non-finite headline gap statistics")
    cards = [
        {"value": f"{h['unadjusted_median_gap_pct']:.1f}%", "label": "Raw median gap · gender",
         "tone": "warn" if h["unadjusted_median_gap_pct"] >= 5 else "neutral"},
        {"value": f"{h['unadjusted_mean_gap_pct']:.1f}%", "label": "Raw mean gap · gender"},
        {"value": f"{h['adjusted_gap_pct']:+.1f}%", "label": "Adjusted (like-for-like)",
         "tone": "bad" if h["adjusted_significant"] else "good"},
        {"value": f"{eu['n_flagged']}", "label": f"EU categories >5% (of {eu['n_categories']})",
         "tone": "bad" if eu["n_flagged"] else "good"},
        {"value": "Indicated" if eu["joint_assessment_required"] else "None",
         "label": "EU joint assessment (screen)", "tone": "bad" if eu["joint_assessment_required"] else "good"},
        {"value": f"{n:,}", "label": "Employees analyzed"},
    ]
    return {"r": result, "gender": gender, "eu": eu, "cards": cards, "narrative": _narrative(result, gender, eu)}


def _narrative(result, gender, eu):
    h = result["headline"]
    ref, foc = h["reference_group"], h["focus_group"]
    sig = "statistically significant" if h["adjusted_significant"] else "not statistically distinguishable from zero"
    parts = [
        f"The raw median gender pay gap is {h['unadjusted_median_gap_pct']:.1f}% (group {foc} vs {ref}); once "
        f"job level, family, country, tenure, rating and management are held equal it falls to "
        f"{h['adjusted_gap_pct']:+.1f}% — {sig}. Most of the raw gap is workforce COMPOSITION, not unequal pay "
        f"for equal work.",
    ]
    if eu["joint_assessment_required"]:
        flagged = [c["category"] for c in eu["categories"] if c.get("exceeds_threshold")]
        parts.append(f"But the EU Pay Transparency 5% screen fires in {eu['n_flagged']} category "
                     f"({', '.join(flagged)}): a joint pay assessment is owed there unless the gap is justified "
                     f"by objective, gender-neutral factors within six months.")
    else:
        parts.append("No category crosses the EU 5% joint-assessment trigger on the mean.")
    return " ".join(parts)


# ---------- rendering ----------

def _gap_axis(rows):
    """A symmetric-ish %-gap axis wide enough for every point, CI end, and raw ghost, snapped to the 2-tick
    grid so the forest plot's integer ticks always land."""
    vals = [0.0]
    for d in rows:
        vals += [d["adj"], d.get("raw", d["adj"]), d.get("ci_lo", d["adj"]), d.get("ci_hi", d["adj"])]
    lo = min(vals) - 1.0
    hi = max(vals) + 1.0
    return math.floor(lo / 2) * 2, math.ceil(hi / 2) * 2


def _forest_rows(dim):
    unadj = {g["group"]: g for g in dim["unadjusted"]["groups"]}
    rows = []
    for g in dim["adjusted"]["groups"]:
        # ghost = raw MEAN gap vs the same (highest-mean) reference the adjusted coefficient is measured
        # against, so point and ghost are apples-to-apples on one reference (and non-negative). The median gap
        # is reported separately (headline KPI + EU screen); it can rank groups differently than the mean.
        raw = round(unadj[g["group"]]["mean_gap_pct"], 1)
        rows.append({"group": f"Group {g['group']}", "adj": round(g["adjusted_gap_pct"], 1),
                     "ci_lo": g["ci_lo_pct"], "ci_hi": g["ci_hi_pct"], "raw": raw,
                     "sub": ("gap ≠ 0" if g["significant"] else "n.s.")})
    return rows


def render_html(report):
    result, gender, eu = report["r"], report["gender"], report["eu"]
    body = [
        dash.brand_header(),
        dash.title_block("Pay Equity · EU Pay Transparency",
                         "Pay-Equity Assessment",
                         f"{COMPANY} · as of {AS_OF} · {result['pay_measure']} · synthetic, pseudonymised"),
        dash.narrator(report["narrative"]),
        dash.kpi_cards(report["cards"]),
    ]

    # 1) gender: raw vs like-for-like, on one axis
    grows = _forest_rows(gender)
    glo, ghi = _gap_axis(grows)
    body.append(dash.section("Gender pay gap — raw (ghost) vs like-for-like adjusted (point + 95% CI)"))
    body.append(ch.forest_plot(grows, lo=glo, hi=ghi, zero_label="parity (0%)",
                               ghost_label="raw", color_mode="significance",
                               value_fmt=lambda v: f"{v:+.1f}%"))
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                "The <b>ghost</b> marker is the raw mean gap; the <b>point</b> is the "
                "regression-adjusted residual with its 95% confidence interval. An interval that crosses "
                "parity means the like-for-like gap is not statistically distinguishable from zero. "
                f"Adjusted model: n={gender['adjusted']['n']}, R&sup2;={gender['adjusted']['r2']:.2f}, controls "
                "for level · family · country · tenure · rating · management.</div>")

    # 2) EU 5% joint-assessment screen, per category of workers (job level)
    body.append(dash.section(f"EU Pay Transparency — {eu['threshold_pct']:.0f}% joint-assessment screen "
                             "by category of workers (job level)"))
    trows = []
    for c in eu["categories"]:
        if not c.get("assessable"):
            trows.append([c["category"], c["n"], "—", "—", "—", "—", "n/a"])
            continue
        status = ("⚑ assessment" if c["exceeds_threshold"] else ("median watch" if c.get("median_watch") else "ok"))
        # 2 decimals near a legal 5% threshold so a 4.98% never displays as "5.0%" next to an "ok" status
        trows.append([c["category"], c["n"], f"{c['advantaged_group']}▸{c['disadvantaged_group']}",
                      f"{c['mean_gap_pct']:.2f}%", f"{c['median_gap_pct']:.2f}%",
                      f"{eu['threshold_pct']:.0f}%", status])
    body.append(dash.data_table(["Level", "N", "Adv▸Disadv", "Mean gap", "Median gap", "Threshold", "Status"],
                                trows, center_from=1))
    trigger = ", ".join(c["category"] for c in eu["categories"] if c.get("exceeds_threshold")) or "none"
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                f"Article 10 flags a category for a <b>joint pay assessment</b> when its <b>mean</b> gap "
                f"reaches 5% (\"at least 5%\") and is not justified by objective, gender-neutral factors within "
                f"six months. Screen-flagged here: <b>{dash._esc(trigger)}</b>. The median gap is shown too "
                f"(Article 9 mandates both); a category clean on the mean but ≥5% on the median is a watch, not "
                f"a flag. Gaps are shown <b>before</b> objective-factor justification — this is a screen flag, "
                f"not a legal determination.</div>")

    # 3) ethnicity — same machinery, a voluntary lens
    erows = _forest_rows(next(d for d in result["dimensions"] if d["key"] == "ethnicity_group"))
    if erows:
        elo, ehi = _gap_axis(erows)
        body.append(dash.section("Additional equity lens — ethnicity group (raw vs adjusted)"))
        body.append(ch.forest_plot(erows, lo=elo, hi=ehi, zero_label="parity (0%)",
                                   ghost_label="raw", color_mode="significance",
                                   value_fmt=lambda v: f"{v:+.1f}%"))

    # honesty + governance
    body.append(dash.section("What this is — and is not"))
    body.append("<div style='display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px'>"
                + dash.chip("Illustrative", "warn") + dash.chip("Synthetic, pseudonymised data", "flat")
                + dash.chip("Base pay only", "flat") + dash.chip("Observable controls only", "flat")
                + dash.chip("Not legal advice", "bad") + "</div>")
    body.append("<div style='font-size:11.5px;color:var(--soft);line-height:1.55;margin:8px 0 2px'>"
                + dash._esc(result['disclaimer']) + "</div>")
    body.append(dash.governance_footer(AGENT))
    return dash.page(f"{COMPANY} — Pay-Equity Assessment", "".join(body))


def render_digest(report):
    result, gender, eu = report["r"], report["gender"], report["eu"]
    h = result["headline"]
    lines = [f"# {COMPANY} — Pay-equity digest", f"_As of {AS_OF} · draft for review_", "",
             f"- {report['narrative']}",
             f"- Raw gender gap: median **{h['unadjusted_median_gap_pct']:.1f}%**, mean "
             f"**{h['unadjusted_mean_gap_pct']:.1f}%** (group {h['focus_group']} vs {h['reference_group']}). "
             f"Adjusted (like-for-like): **{h['adjusted_gap_pct']:+.1f}%** "
             f"({'significant' if h['adjusted_significant'] else 'not significant'}, "
             f"R²={gender['adjusted']['r2']:.2f}, n={gender['adjusted']['n']})."]
    if eu["joint_assessment_required"]:
        flagged = ", ".join(c["category"] for c in eu["categories"] if c.get("exceeds_threshold"))
        lines.append(f"- **EU 5% trigger fires** in {eu['n_flagged']} category ({flagged}) — a joint pay "
                     f"assessment is owed unless objectively justified within six months.")
    else:
        lines.append("- No EU 5% category trigger on the mean.")
    lines += ["", "_Numbers computed by foundation/compute/pay_equity.py on synthetic, pseudonymised data; "
              "base pay only; observable controls only. Illustrative — not legal advice._",
              "", "_Publish gate: a human (People/Total Rewards) must approve before distribution._"]
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
    ap = argparse.ArgumentParser(description="Acme Corp pay-equity reporting agent (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (People/Total Rewards).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        result = PE.compute()
        report = build_report(result)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except (ReportError, PE.PayEquityDataError) as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"pay-equity engine unavailable: {exc}")

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
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    h = result["headline"]
    print(f"{COMPANY} pay-equity assessment — as of {AS_OF}")
    print(f"  analyzed {result['population']['n_analyzed']:,} employees | raw median gender gap "
          f"{h['unadjusted_median_gap_pct']:.1f}% -> adjusted {h['adjusted_gap_pct']:+.1f}% "
          f"({'sig' if h['adjusted_significant'] else 'n.s.'}) | EU flagged {report['eu']['n_flagged']}")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (People/Total Rewards) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
