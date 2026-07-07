#!/usr/bin/env python3
"""Acme Corp — Relative TSR PSU Tracker + Monte Carlo Valuation.

An Executive Compensation arm example: tracks an rTSR PSU against a synthetic software index and
estimates an illustrative terminal-price fair value with a deterministic Monte Carlo model.

The sample mirrors a common public software-company rTSR structure, but all issuer, peer, price, and
assumption data is synthetic. This is decision-support math, not accounting/legal/investment advice.

    python3 run.py
    python3 run.py --publish
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

from foundation.compute.rtsr import PayoutCurve, evaluate_performance, monte_carlo_valuation  # noqa: E402
from foundation.render import charts as ch  # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
DATA = HERE / "data"
AGENT = "rtsr-psu-valuation"
SCOPE = "publish.rtsr_psu_valuation"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")


class ReportError(RuntimeError):
    """Raised when the rTSR report cannot be produced."""


def _e(v):
    import html
    return html.escape(str(v))


def _pct(v):
    return f"{float(v):.2f}%"


def _money(v):
    return f"${float(v):,.2f}"


def _read_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _curve(plan):
    pts = [(p["percentile"], p["payout_percent"]) for p in plan["payout_curve"]]
    return PayoutCurve(pts)


def _expanded_correlations(assumptions):
    tickers = [t.upper() for t in assumptions["tickers"]]
    corr = assumptions.get("correlations")
    if corr:
        return corr
    compact = assumptions.get("correlation", {})
    diag = float(compact.get("diagonal", 1.0))
    off = float(compact.get("off_diagonal", 0.0))
    return {a: {b: (diag if a == b else off) for b in tickers} for a in tickers}


def load_inputs():
    plan = _read_json("plan_terms.sample.json")
    companies = _read_json("companies.sample.json")
    prices = _read_json("prices.sample.json")
    assumptions = _read_json("valuation_assumptions.sample.json")
    payout_history = _read_json("payout_history.sample.json")
    assumptions = dict(assumptions)
    assumptions["correlations"] = _expanded_correlations(assumptions)
    return {
        "plan": plan,
        "companies": companies,
        "prices": prices,
        "assumptions": assumptions,
        "payout_history": payout_history,
    }


def _validate_inputs(inputs):
    plan = inputs["plan"]
    if plan.get("issuer") != inputs["assumptions"].get("issuer"):
        raise ReportError("plan issuer and valuation issuer differ")
    company_tickers = {c["ticker"].upper() for c in inputs["companies"]}
    if plan["issuer"].upper() not in company_tickers:
        raise ReportError("issuer missing from company roster")
    missing = company_tickers - set(inputs["prices"])
    if missing:
        raise ReportError(f"missing price observations for: {', '.join(sorted(missing))}")
    if any(str(c.get("role", "")).lower() == "issuer" and c.get("ticker") != plan.get("issuer")
           for c in inputs["companies"]):
        raise ReportError("sample inputs must keep exactly one synthetic issuer")
    history = inputs.get("payout_history", [])
    if not isinstance(history, list) or len(history) < 2:
        raise ReportError("payout history requires at least two synthetic snapshots")
    for row in history:
        if not isinstance(row, dict):
            raise ReportError("payout history rows must be objects")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(row.get("date", ""))):
            raise ReportError("payout history dates must use YYYY-MM-DD")
        try:
            v = float(row["payout_percent"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReportError("payout history payout_percent must be numeric") from exc
        if not math.isfinite(v) or v < 0 or v > 200:
            raise ReportError("payout history payout_percent must be between 0 and 200")


def build_report(inputs):
    _validate_inputs(inputs)
    plan = inputs["plan"]
    curve = _curve(plan)
    performance = evaluate_performance(inputs["companies"], inputs["prices"], curve,
                                       averaging_days=int(plan["averaging_days"]))
    valuation = monte_carlo_valuation(inputs["assumptions"], curve)
    issuer_row = next(r for r in performance["ranked"] if r["ticker"] == plan["issuer"])
    return {
        "plan": plan,
        "performance": performance,
        "valuation": valuation,
        "issuer_row": issuer_row,
        "assumptions": inputs["assumptions"],
        "payout_history": inputs["payout_history"],
        "narrative": _narrative(plan, performance, valuation),
    }


def _narrative(plan, perf, val):
    return (
        f"{_e(plan['company_name'])} ranks at the <b>{_pct(perf['issuer_percentile'])}</b> percentile "
        f"of the synthetic software index, producing an indicated payout of "
        f"<b>{_pct(perf['payout_percent'])}</b> of target. The deterministic Monte Carlo model "
        f"estimates an illustrative terminal-price fair value of "
        f"<b>{_money(val['fair_value_per_target_share'])}</b> "
        f"per target share, or <b>{val['fair_value_ratio_to_spot']:.2f}x</b> spot."
    )


def _kpi(label, value, sub, klass=""):
    return (f"<div class='kpi {klass}'><div class='k-label'>{_e(label)}</div>"
            f"<div class='k-val mono'>{_e(value)}</div><div class='k-sub'>{_e(sub)}</div></div>")


def _tile(title, sub, body, scope="Plan"):
    return (f"<section class='tile'><div class='t-head'><div><h3>{_e(title)}</h3>"
            f"<div class='t-sub'>{_e(sub)}</div></div><span>{_e(scope)}</span></div>{body}</section>")


def _payout_curve_svg(curve):
    rows = curve.points
    w, h = 520, 210
    x0, x1, y0, y1 = 48, 492, 170, 24
    sx = lambda p: x0 + (p / 100.0) * (x1 - x0)
    sy = lambda v: y0 - (v / 220.0) * (y0 - y1)
    pts = [(0, 0.0)] + list(rows) + [(100, rows[-1][1])]
    path = "M" + " L".join(f"{sx(p):.1f} {sy(v):.1f}" for p, v in pts)
    body = [
        f"<line x1='{x0}' y1='{y0}' x2='{x1}' y2='{y0}' stroke='{ch.GRID}'/>",
        f"<line x1='{x0}' y1='{y1}' x2='{x0}' y2='{y0}' stroke='{ch.GRID}'/>",
        f"<path d='{path}' fill='none' stroke='{ch.CYAN}' stroke-width='3' stroke-linecap='round'/>",
    ]
    for p, v in rows:
        body.append(f"<circle cx='{sx(p):.1f}' cy='{sy(v):.1f}' r='5' fill='{ch.CYAN2}'/>")
        body.append(f"<text x='{sx(p):.1f}' y='{sy(v)-10:.1f}' text-anchor='middle' "
                    f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' fill='{ch.INK}'>{ch.ordinal(p)}</text>")
        body.append(f"<text x='{sx(p):.1f}' y='{sy(v)+20:.1f}' text-anchor='middle' "
                    f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' fill='{ch.MUTED}'>{v:g}%</text>")
    for p in (0, 25, 55, 75, 100):
        body.append(f"<text x='{sx(p):.1f}' y='{h-10}' text-anchor='middle' "
                    f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='9' fill='{ch.SOFT}'>{p}</text>")
    return ch._svg(w, h, "".join(body))


def _payout_history_svg(history, target=100.0):
    rows = [{"date": str(r["date"]), "payout": float(r["payout_percent"])} for r in history]
    w, h = 620, 250
    x0, x1, y0, y1 = 56, 588, 190, 24
    y_max = 220.0

    def sx(i):
        return x0 if len(rows) == 1 else x0 + i * (x1 - x0) / (len(rows) - 1)

    def sy(v):
        return y0 - (max(0.0, min(y_max, v)) / y_max) * (y0 - y1)

    line = "M" + " L".join(f"{sx(i):.1f} {sy(r['payout']):.1f}" for i, r in enumerate(rows))
    area = f"{line} L{x1:.1f} {y0:.1f} L{x0:.1f} {y0:.1f} Z"
    target_y = sy(target)
    body = [
        f"<line x1='{x0}' y1='{y0}' x2='{x1}' y2='{y0}' stroke='{ch.GRID}'/>",
        f"<line x1='{x0}' y1='{y1}' x2='{x0}' y2='{y0}' stroke='{ch.GRID}'/>",
        f"<line data-target-line='100' x1='{x0}' y1='{target_y:.1f}' x2='{x1}' y2='{target_y:.1f}' "
        f"stroke='{ch.AMBER}' stroke-width='2' stroke-dasharray='5 5' opacity='.9'/>",
        f"<text x='{x1}' y='{target_y - 7:.1f}' text-anchor='end' "
        f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' font-weight='700' "
        f"fill='{ch.AMBER}'>Target 100%</text>",
        f"<path d='{area}' fill='{ch.CYAN}' opacity='.10'/>",
        f"<path d='{line}' fill='none' stroke='{ch.CYAN2}' stroke-width='3' stroke-linejoin='round' "
        "stroke-linecap='round'/>",
    ]
    for pct in (0, 50, 100, 150, 200):
        y = sy(pct)
        body.append(f"<line x1='{x0 - 4}' y1='{y:.1f}' x2='{x1}' y2='{y:.1f}' stroke='{ch.GRID}'/>")
        body.append(f"<text x='{x0 - 10}' y='{y + 3:.1f}' text-anchor='end' "
                    f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='9' fill='{ch.SOFT}'>{pct}%</text>")
    label_idx = {0, len(rows) - 1, len(rows) // 2}
    for i, r in enumerate(rows):
        x, y = sx(i), sy(r["payout"])
        if i == len(rows) - 1:
            body.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='6' fill='#fff' stroke='{ch.CYAN}' stroke-width='3'/>")
            body.append(f"<text x='{x:.1f}' y='{y - 12:.1f}' text-anchor='middle' "
                        f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' font-weight='700' "
                        f"fill='{ch.INK}'>{r['payout']:.2f}%</text>")
        elif r["payout"] == 0:
            body.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='{ch.RED}'/>")
        if i in label_idx:
            body.append(f"<text x='{x:.1f}' y='{h - 16}' text-anchor='middle' "
                        f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='9' fill='{ch.SOFT}'>{_e(r['date'][5:])}</text>")
    svg = ch._svg(w, h, "".join(body))
    return svg.replace("<svg ", "<svg data-chart='payout-history' ", 1)


def _peer_tsr_distribution_svg(perf):
    rows = perf["ranked"]
    vals = [float(r["tsr"]["return_pct"]) for r in rows]
    w, h = 700, 300
    x0, x1, y0, y1 = 42, 680, 218, 28
    low = min(-100.0, min(vals))
    high = max(200.0, max(vals))

    def sy(v):
        return y0 - ((v - low) / (high - low)) * (y0 - y1)

    zero = sy(0.0)
    gap = 5
    bw = max(8, (x1 - x0 - gap * (len(rows) - 1)) / len(rows))
    body = [
        f"<line data-zero-line='true' x1='{x0}' y1='{zero:.1f}' x2='{x1}' y2='{zero:.1f}' "
        f"stroke='{ch.INK}' stroke-width='1.4' opacity='.55'/>",
        f"<text x='{x1}' y='{zero - 7:.1f}' text-anchor='end' "
        f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' fill='{ch.SOFT}'>0% TSR</text>",
    ]
    for pct in (-100, -50, 0, 50, 100, 150, 200):
        y = sy(pct)
        body.append(f"<line x1='{x0}' y1='{y:.1f}' x2='{x1}' y2='{y:.1f}' stroke='{ch.GRID}'/>")
        body.append(f"<text x='{x0 - 8}' y='{y + 3:.1f}' text-anchor='end' "
                    f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='9' fill='{ch.SOFT}'>{pct}%</text>")
    for i, r in enumerate(rows):
        val = float(r["tsr"]["return_pct"])
        x = x0 + i * (bw + gap)
        y = sy(max(val, 0))
        y_neg = sy(min(val, 0))
        height = abs(y_neg - y)
        is_issuer = r["role"] == "issuer"
        klass = "bar issuer" if is_issuer else "bar peer"
        fill = ch.CYAN2 if is_issuer else (ch.GREEN if val >= 0 else ch.RED)
        opacity = ".98" if is_issuer else ".72"
        body.append(f"<rect class='{klass}' x='{x:.1f}' y='{min(y, y_neg):.1f}' width='{bw:.1f}' "
                    f"height='{max(1, height):.1f}' rx='2' fill='{fill}' opacity='{opacity}'/>")
        if is_issuer:
            body.append(f"<text x='{x + bw / 2:.1f}' y='{min(y, y_neg) - 7:.1f}' text-anchor='middle' "
                        f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' font-weight='800' "
                        f"fill='{ch.CYAN2}'>Issuer highlighted</text>")
        if i % 2 == 0 or is_issuer:
            body.append(f"<text x='{x + bw / 2:.1f}' y='{h - 38}' transform='rotate(-45 {x + bw / 2:.1f} {h - 38})' "
                        f"text-anchor='end' font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='9' fill='{ch.SOFT}'>{_e(r['ticker'])}</text>")
    body.append(f"<text x='{(x0 + x1) / 2:.1f}' y='{h - 8}' text-anchor='middle' "
                f"font-family=\"ui-monospace,'SF Mono',Menlo,Consolas,monospace\" font-size='10' fill='{ch.MUTED}'>Synthetic software index TSRs</text>")
    svg = ch._svg(w, h, "".join(body))
    return svg.replace("<svg ", "<svg data-chart='peer-tsr-distribution' ", 1)


def _rank_table(perf):
    rows = []
    for r in perf["ranked"]:
        cls = "issuer" if r["role"] == "issuer" else ""
        rows.append(
            f"<tr class='{cls}'><td class='mono'>{r['rank']}</td><td class='mono'>{_e(r['ticker'])}</td>"
            f"<td>{_e(r['name'])}</td><td class='mono r'>{_pct(r['tsr']['return_pct'])}</td>"
            f"<td class='mono r'>{_pct(r['percentile'])}</td></tr>"
        )
    return ("<table><thead><tr><th>Rank</th><th>Ticker</th><th>Company</th>"
            "<th class='r'>TSR</th><th class='r'>Percentile</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _distribution_table(val):
    ci = val["fair_value_ci95"]
    rows = [
        ("Payout P10/P50/P90", f"{val['payout_distribution']['p10']:.2f}% / "
         f"{val['payout_distribution']['p50']:.2f}% / {val['payout_distribution']['p90']:.2f}%"),
        ("Percentile P10/P50/P90", f"{val['percentile_distribution']['p10']:.2f} / "
         f"{val['percentile_distribution']['p50']:.2f} / {val['percentile_distribution']['p90']:.2f}"),
        ("Issuer terminal price P10/P50/P90", f"${val['issuer_terminal_price_distribution']['p10']:.2f} / "
         f"${val['issuer_terminal_price_distribution']['p50']:.2f} / "
         f"${val['issuer_terminal_price_distribution']['p90']:.2f}"),
        ("Monte Carlo SE / 95% MC CI", f"${val['fair_value_standard_error']:.2f} / "
         f"${ci['low']:.2f} to ${ci['high']:.2f}"),
    ]
    return "<div class='dist'>" + "".join(
        f"<div><span>{_e(k)}</span><b class='mono'>{_e(v)}</b></div>" for k, v in rows
    ) + "</div>"


def render_html(report):
    plan, perf, val, assump = report["plan"], report["performance"], report["valuation"], report["assumptions"]
    issuer = report["issuer_row"]
    curve = PayoutCurve([(p["percentile"], p["payout_percent"]) for p in plan["payout_curve"]])
    bins = [
        val["payout_distribution"]["p10"],
        val["payout_distribution"]["p25"],
        val["payout_distribution"]["p50"],
        val["payout_distribution"]["p75"],
        val["payout_distribution"]["p90"],
    ]
    body = []
    body.append(
        "<header><div><div class='brand'>Agentic People<span>OS</span></div>"
        "<h1>Relative TSR PSU — Performance Tracker + Monte Carlo</h1>"
        f"<p>{_e(plan['company_name'])} · synthetic executive compensation example · "
        f"{_e(plan['performance_period']['start'])} to {_e(plan['performance_period']['end'])}</p></div>"
        "<div class='status'>Draft · Demo review required</div></header>"
    )
    body.append(f"<section class='insight'><div class='tag'>Committee readout</div><p>{report['narrative']}</p></section>")
    body.append("<section class='kpis'>" + "".join([
        _kpi("Issuer TSR", _pct(issuer["tsr"]["return_pct"]),
             f"30-day start avg {issuer['tsr']['start_avg']:.2f} / end avg {issuer['tsr']['end_avg']:.2f}"),
        _kpi("rTSR percentile", _pct(perf["issuer_percentile"]), "ranked with included index constituents"),
        _kpi("Indicated payout", _pct(perf["payout_percent"]), "linear interpolation; cap at 200%", "hot"),
        _kpi("Monte Carlo FV", _money(val["fair_value_per_target_share"]),
             f"{val['fair_value_ratio_to_spot']:.2f}x spot · Monte Carlo SE ${val['fair_value_standard_error']:.2f}"),
    ]) + "</section>")
    body.append("<main>")
    body.append(_tile("Payout history", "Daily payout snapshots vs Target 100%",
                      _payout_history_svg(report["payout_history"]), "Tracking"))
    body.append(_tile("Payout curve", "25th=50%, 55th=100%, 75th+=200%",
                      _payout_curve_svg(curve), "Public-style terms"))
    body.append(_tile("Percentile instrument", "Issuer position on the performance curve",
                      ch.percentile_strip(perf["issuer_percentile"], 0, 100,
                                          [(25, "25th / 50%"), (55, "55th / 100%"),
                                           (75, "75th / 200%")],
                                          target=55, you_label="Issuer",
                                          unit_prefix="", unit_suffix="th", uid="rtsr_pct"),
                      "Tracking"))
    body.append(_tile("Peer TSR distribution", "Included synthetic index constituents; Issuer highlighted",
                      _peer_tsr_distribution_svg(perf), "Index intersection"))
    body.append(_tile("Ranked TSR table", "Included peer set only; start/end index members",
                      _rank_table(perf), "Index intersection"))
    body.append(_tile("Monte Carlo distribution", "Illustrative stock-settled terminal-price estimate",
                      ch.histogram([round(x) for x in bins],
                                   ["P10", "P25", "P50", "P75", "P90"],
                                   highlight={2: ch.CYAN2}) + _distribution_table(val),
                      "Valuation"))
    body.append("</main>")
    body.append("<section class='method'><h2>Methodology and controls</h2>"
                f"<p>{_e(plan['methodology_note'])}</p>"
                "<ul>"
                f"<li>Comparator rule: {_e(plan['comparator_group'])}.</li>"
                f"<li>Excluded from ranking: {_e(', '.join(perf['excluded_peer_tickers']) or 'none')}.</li>"
                f"<li>Monte Carlo inputs: {assump['paths']:,} paths, seed {assump['seed']}, "
                f"{assump['performance_years']} years, risk-free rate {assump['risk_free_rate']:.2%}, "
                "constant synthetic volatilities and correlations.</li>"
                f"<li>Monte Carlo precision: SE {_money(val['fair_value_standard_error'])}; "
                f"95% MC CI {_money(val['fair_value_ci95']['low'])} to "
                f"{_money(val['fair_value_ci95']['high'])} per target share.</li>"
                "<li>Valuation caveat: the Monte Carlo path ranks total return using terminal prices; "
                "it does not model daily 30-day averaging-window paths, post-performance settlement "
                "timing, forfeitures, or award-specific accounting policy.</li>"
                "<li>Public-safety boundary: no real issuer, ticker, stock price, proxy, award, employee, "
                "or employer data is included.</li>"
                "<li>This is not accounting advice, legal advice, tax advice, investment advice, or an "
                "auditor-approved valuation opinion.</li>"
                "</ul></section>")
    return HTML.format(title="Relative TSR PSU", body="".join(body))


def render_digest(report):
    plan, perf, val = report["plan"], report["performance"], report["valuation"]
    return (
        f"# {plan['company_name']} — rTSR PSU draft digest\n\n"
        f"- **Performance period:** {plan['performance_period']['start']} to {plan['performance_period']['end']}\n"
        f"- **Comparator group:** S&P Software & Services-style synthetic index members at both start and end\n"
        f"- **Issuer percentile:** {perf['issuer_percentile']:.2f}%\n"
        f"- **Indicated payout:** {perf['payout_percent']:.2f}% of target\n"
        f"- **Monte Carlo fair value:** ${val['fair_value_per_target_share']:.2f} per target share "
        f"({val['fair_value_ratio_to_spot']:.2f}x spot; SE ${val['fair_value_standard_error']:.2f}; "
        f"95% MC CI ${val['fair_value_ci95']['low']:.2f} to ${val['fair_value_ci95']['high']:.2f})\n\n"
        "A demo named-reviewer label is required before publication. This synthetic example is not "
        "accounting advice, legal advice, investment advice, or an auditor-approved valuation.\n"
    )


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _managed_outputs():
    return (REPORT, DIGEST, OUT / "PUBLISHED.json")


def _mark_stale_outputs():
    marked = False
    for path in _managed_outputs():
        if path.exists():
            stale = path.with_name(path.name + ".stale")
            if stale.exists():
                stale.unlink()
            path.rename(stale)
            marked = True
    return marked


def _clear_stale_outputs():
    for path in _managed_outputs():
        stale = path.with_name(path.name + ".stale")
        if stale.exists():
            stale.unlink()


def _fail_closed(exc):
    suffix = " (prior output marked .stale)" if _mark_stale_outputs() else ""
    print(f"FAIL CLOSED: {_e(exc)}{suffix}", file=sys.stderr)
    return 1


def _publish_record(approved_by, report):
    return {
        "agent": AGENT,
        "scope": SCOPE,
        "control_type": "demo_named_reviewer_gate",
        "approved_by": approved_by,
        "issuer": report["plan"]["issuer"],
        "payout_percent": report["performance"]["payout_percent"],
        "fair_value_per_target_share": report["valuation"]["fair_value_per_target_share"],
        "disclaimer": "synthetic decision-support output; not accounting/legal/investment advice",
    }


def _display_path(path):
    try:
        return str(path.relative_to(HERE))
    except ValueError:
        return str(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--approved-by")
    args = parser.parse_args(argv)

    if args.publish and (not args.approved_by or not APPROVER_RE.match(args.approved_by)):
        print("REFUSED: demo publish requires --approved-by with a valid named reviewer", file=sys.stderr)
        return 2

    try:
        report = build_report(load_inputs())
        html = render_html(report)
        digest = render_digest(report)
    except Exception as exc:
        return _fail_closed(exc)

    try:
        _clear_stale_outputs()
        _atomic_write(REPORT, html)
        _atomic_write(DIGEST, digest)
        pub = OUT / "PUBLISHED.json"
        if args.publish:
            _atomic_write(pub, json.dumps(_publish_record(args.approved_by, report), indent=2) + "\n")
        elif pub.exists():
            pub.unlink()
    except Exception as exc:
        return _fail_closed(exc)
    print(f"WROTE {_display_path(REPORT)}")
    print(f"WROTE {_display_path(DIGEST)}")
    if args.publish:
        print(f"PUBLISHED with demo reviewer label: {args.approved_by}")
    return 0


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><title>{title}</title>
<style>
:root {{ color-scheme: dark; --bg:#06131d; --panel:#0a1f2c; --panel2:#0f2a3e; --line:rgba(141,177,206,.16);
  --ink:#eef7ff; --muted:#8db1ce; --soft:#8296ab; --cyan:#1ba7ff; --cyan2:#48c7ff;
  --green:#43d477; --red:#ff4d4f; --amber:#f7b955; --indigo:#7c8cff; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),var(--bg) color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
.mono {{ font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace; }}
header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px; padding:30px 34px 18px;
  border-bottom:1px solid var(--line); background:linear-gradient(180deg,#0f2a3e,#06131d); }}
.brand {{ color:var(--cyan); font-size:13px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }}
.brand span {{ color:#fff; }} h1 {{ margin:8px 0 8px; font-size:30px; line-height:1.05; letter-spacing:0; }}
p {{ margin:0; color:var(--muted); }} .status {{ border:1px solid var(--amber); color:var(--amber);
  padding:8px 10px; border-radius:6px; font-size:12px; font-weight:800; text-transform:uppercase; }}
.insight {{ margin:24px 34px; padding:18px 20px; border:1px solid rgba(27,167,255,.35);
  background:#0a1f2c; border-radius:8px; }} .insight p {{ color:var(--ink); font-size:16px; line-height:1.45; }}
.tag {{ color:var(--cyan2); font-size:12px; font-weight:800; text-transform:uppercase; margin-bottom:8px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:0 34px 18px; }}
.kpi {{ min-height:120px; padding:16px; border:1px solid var(--line); border-radius:8px; background:var(--panel); }}
.kpi.hot {{ border-color:rgba(247,185,85,.5); }} .k-label {{ color:var(--muted); font-size:12px; font-weight:800; text-transform:uppercase; }}
.k-val {{ font-size:29px; margin:12px 0 8px; }} .k-sub {{ color:var(--soft); font-size:12px; line-height:1.35; }}
main {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:0 34px 22px; }}
.tile {{ padding:16px; border:1px solid var(--line); border-radius:8px; background:var(--panel); overflow:hidden; }}
.t-head {{ display:flex; justify-content:space-between; gap:18px; margin-bottom:14px; }}
h3 {{ margin:0; font-size:16px; }} .t-sub {{ color:var(--muted); margin-top:4px; font-size:12px; }}
.t-head span {{ color:var(--soft); font-size:11px; font-weight:800; text-transform:uppercase; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }} th {{ text-align:left; color:var(--muted);
  border-bottom:1px solid var(--line); padding:7px 6px; }} td {{ padding:7px 6px; border-bottom:1px solid rgba(141,177,206,.1); }}
.r {{ text-align:right; }} tr.issuer td {{ color:var(--cyan2); font-weight:800; background:rgba(27,167,255,.08); }}
.dist {{ display:grid; gap:8px; margin-top:12px; }} .dist div {{ display:flex; justify-content:space-between;
  gap:16px; padding:9px 10px; border:1px solid rgba(141,177,206,.12); border-radius:6px; }}
.dist span {{ color:var(--muted); }} .method {{ margin:0 34px 34px; padding:18px 20px; border:1px solid var(--line);
  border-radius:8px; background:#071a26; }} h2 {{ margin:0 0 10px; font-size:17px; }}
li {{ margin:8px 0; color:var(--muted); line-height:1.45; }}
@media (max-width: 900px) {{ header,.kpis,main {{ display:block; }} .kpi,.tile {{ margin-bottom:12px; }} }}
</style></head><body>{body}</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
