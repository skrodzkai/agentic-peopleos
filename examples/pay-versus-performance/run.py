#!/usr/bin/env python3
"""Acme Corp — Pay-versus-Performance / Compensation Actually Paid (SEC Item 402(v)).

An Executive Compensation arm example: reconstructs the mandatory Pay-versus-Performance disclosure —
the five-year table of Compensation Actually Paid (CAP) versus Total Shareholder Return, peer TSR, net
income, and a company-selected measure — from a synthetic executive equity-award book and one committed
subject stock-price path. The centerpiece is the SCT-Total-to-CAP reconciliation bridge.

All issuer, award, price, and financial data is synthetic. This is an illustrative reconstruction of the
402(v) methodology, not accounting/legal/investment advice or an auditor-approved ASC 718 valuation.

    python3 run.py
    python3 run.py --publish --approved-by "Compensation Committee Chair"
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

from foundation.compute.pvp import (  # noqa: E402
    PayVersusPerformance, pvp_table, relationship_series, cap_for_neo_year, alignment)
from foundation.render import charts as ch  # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
DATA = HERE / "data"
AGENT = "pay-versus-performance"
SCOPE = "publish.pay_versus_performance"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")


class ReportError(RuntimeError):
    """Raised when the Pay-versus-Performance report cannot be produced."""


def _e(v):
    import html
    return html.escape(str(v))


def _m(v):
    return f"${float(v) / 1e6:,.2f}M"


def _mm(v):
    return f"${float(v) / 1e6:,.1f}M"


def _idx(v):
    return f"${float(v):,.0f}"


def load_inputs():
    awards = json.loads((DATA / "awards.sample.json").read_text(encoding="utf-8"))
    financials = json.loads((DATA / "pvp_financials.sample.json").read_text(encoding="utf-8"))
    return {"awards": awards, "financials": financials}


def build_report(inputs):
    pvp = PayVersusPerformance(inputs["awards"], inputs["financials"])
    table = pvp_table(pvp)
    series = relationship_series(table)
    align = alignment(table)
    last_fy = pvp.fiscal_years[-1]
    peo_bridge = cap_for_neo_year(pvp, pvp.peo, last_fy)
    # reconciliation self-check: the itemized bridge MUST tie to the reported CAP to the cent
    bridged = round(sum(v for _l, v, k in peo_bridge["bridge"] if k != "total"), 2) + peo_bridge["bridge"][0][1]
    if abs(bridged - peo_bridge["cap"]) > 0.01:
        raise ReportError("CAP reconciliation bridge does not tie to reported CAP")
    return {
        "company_name": pvp.company_name,
        "peo_role": pvp.peo["role"],
        "table": table,
        "series": series,
        "alignment": align,
        "last_fy": last_fy,
        "peo_bridge": peo_bridge,
        "n_nonpeo": len(pvp.non_peo),
        "narrative": _narrative(pvp, table, align),
    }


def _narrative(pvp, table, align):
    rows = table["rows"]
    first, last = rows[0], rows[-1]
    ratio = last["peo_cap"] / last["peo_sct_total"] if last["peo_sct_total"] else 0.0
    verdict = ("moves <b>with</b>" if align["aligned"] else "<b>diverges from</b>")
    return (
        f"{_e(pvp.company_name)}'s <b>Compensation Actually Paid</b> to the {_e(pvp.peo['role'])} "
        f"{verdict} company performance across the disclosure window: PEO CAP went from "
        f"<b>{_mm(first['peo_cap'])}</b> in FY{first['fy']} to <b>{_mm(last['peo_cap'])}</b> in "
        f"FY{last['fy']} as a fixed $100 invested at the FY{pvp.base_date.year} close grew to "
        f"<b>{_idx(last['company_tsr_value'])}</b>. FY{last['fy']} CAP is <b>{ratio:.2f}x</b> the "
        f"{_mm(last['peo_sct_total'])} Summary Compensation Table total — the difference is the year's "
        f"fair-value change on unvested equity, exactly what Item 402(v) exists to surface."
    )


# --------------------------------------------------------------------------- rendering helpers
def _kpi(label, value, sub, klass=""):
    return (f"<div class='kpi {klass}'><div class='k-label'>{_e(label)}</div>"
            f"<div class='k-val mono'>{_e(value)}</div><div class='k-sub'>{_e(sub)}</div></div>")


def _tile(title, sub, body, scope="402(v)", wide=False):
    cls = "tile wide" if wide else "tile"
    return (f"<section class='{cls}'><div class='t-head'><div><h3>{_e(title)}</h3>"
            f"<div class='t-sub'>{_e(sub)}</div></div><span>{_e(scope)}</span></div>{body}</section>")


# compact axis labels for the waterfall (the engine's descriptive labels overflow an 8-bar axis)
_BRIDGE_SHORT = {
    "SCT total": "SCT total", "less SCT equity FV": "− Grant FV", "+ YE FV new grants": "+ New YE",
    "+ Δ FV prior unvested": "+ Δ Held", "+ vest FV new grants": "+ Vest new",
    "+ Δ to vest, prior": "+ Δ→Vest", "less forfeited FV": "− Forfeit", "+ dividends": "+ Divs",
    "pension adj": "Pension", "CAP": "CAP",
}


def _bridge_svg(bridge):
    # Draw in $millions with compact labels; drop equity terms that round to $0.0M for THIS year so the
    # chart stays legible. The full itemized reconciliation (every 402(v) line, incl. zeros) is shown in
    # the table beside it, and CAP is computed independently by the engine — so nothing is hidden.
    steps = []
    for label, usd, kind in bridge:
        m = round(usd / 1e6, 1)
        if kind != "total" and m == 0.0:
            continue
        steps.append((_BRIDGE_SHORT.get(label, label), m, kind))
    svg = ch.waterfall(steps)
    return svg.replace("<svg ", "<svg data-chart='cap-bridge' ", 1)


def _bridge_table(bridge):
    rows = []
    for label, usd, kind in bridge:
        cls = " class='mono b'" if kind == "total" else " class='mono'"
        rows.append(f"<div><span>{_e(label)}</span><b{cls}>{_m(usd)}</b></div>")
    return "<div class='dist bridge'>" + "".join(rows) + "</div>"


def _pvp_table_html(table):
    head = ("<tr><th>Fiscal year</th><th class='r'>PEO SCT total</th><th class='r'>PEO CAP</th>"
            "<th class='r'>Avg non-PEO SCT</th><th class='r'>Avg non-PEO CAP</th>"
            "<th class='r'>TSR $100</th><th class='r'>Peer TSR $100</th>"
            "<th class='r'>Net income</th><th class='r'>" + _e(table["csm_label"]) + "</th></tr>")
    body = []
    for r in table["rows"]:
        body.append(
            f"<tr><td class='mono'>FY{r['fy']}</td>"
            f"<td class='mono r'>{_m(r['peo_sct_total'])}</td>"
            f"<td class='mono r hot'>{_m(r['peo_cap'])}</td>"
            f"<td class='mono r'>{_m(r['avg_nonpeo_sct_total'])}</td>"
            f"<td class='mono r'>{_m(r['avg_nonpeo_cap'])}</td>"
            f"<td class='mono r'>{_idx(r['company_tsr_value'])}</td>"
            f"<td class='mono r'>{_idx(r['peer_tsr_value'])}</td>"
            f"<td class='mono r'>{_m(r['net_income_usd'])}</td>"
            f"<td class='mono r'>{_m(r['csm_usd'])}</td></tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def _relationship_svg(series, right_key, right_label, right_fmt, chart_id):
    labels = [f"FY{y}" for y in series["years"]]
    left = [round(v / 1e6) for v in series["peo_cap"]]                 # PEO CAP in $M
    right = [round(v) for v in series[right_key]]
    svg = ch.dual_axis_line(labels, left, right,
                            left_fmt=lambda v: f"${v}M",
                            right_fmt=right_fmt)
    return svg.replace("<svg ", f"<svg data-chart='{chart_id}' ", 1)


def render_html(report):
    table, series, align = report["table"], report["series"], report["alignment"]
    rows = table["rows"]
    last = rows[0] if len(rows) == 1 else rows[-1]
    first = rows[0]
    ratio = last["peo_cap"] / last["peo_sct_total"] if last["peo_sct_total"] else 0.0
    verdict = "ALIGNED" if align["aligned"] else "DIVERGENT"
    body = []
    body.append(
        "<header><div><div class='brand'>Agentic People<span>OS</span></div>"
        "<h1>Pay versus Performance — Compensation Actually Paid</h1>"
        f"<p>{_e(report['company_name'])} · SEC Item 402(v) illustrative reconstruction · "
        f"FY{first['fy']}–FY{last['fy']} · synthetic executive compensation example</p></div>"
        "<div class='status'>Draft · Demo review required</div></header>"
    )
    body.append(f"<section class='insight'><div class='tag'>Committee readout</div><p>{report['narrative']}</p></section>")
    body.append("<section class='kpis'>" + "".join([
        _kpi(f"PEO CAP (FY{last['fy']})", _mm(last["peo_cap"]),
             f"vs SCT total {_mm(last['peo_sct_total'])}"),
        _kpi(f"CAP-to-SCT (FY{last['fy']})", f"{ratio:.2f}x",
             "equity revaluation vs reported pay"),
        _kpi("PEO CAP trajectory", f"{_mm(first['peo_cap'])} → {_mm(last['peo_cap'])}",
             f"as TSR $100 → {_idx(last['company_tsr_value'])}"),
        _kpi("Pay-for-performance", verdict,
             "PEO CAP direction vs company TSR", "hot"),
    ]) + "</section>")
    body.append("<main>")
    body.append(_tile(f"CAP reconciliation — {report['peo_role']}, FY{report['last_fy']}",
                      "Summary Compensation Table total → Compensation Actually Paid, in $millions",
                      "<div class='bridge-wrap'><div class='bridge-chart'>"
                      + _bridge_svg(report["peo_bridge"]["bridge"])
                      + "</div><div class='bridge-side'><div class='bridge-h'>Every 402(v)(2)(iii) line</div>"
                      + _bridge_table(report["peo_bridge"]["bridge"]) + "</div></div>",
                      "402(v)(2)(iii)", wide=True))
    body.append(_tile("Pay-versus-Performance table",
                      f"Five covered years · PEO + average of {report['n_nonpeo']} non-PEO NEOs · "
                      "TSR indexed to a fixed $100",
                      _pvp_table_html(table), "402(v)(1)", wide=True))
    body.append(_tile("CAP vs company TSR", "PEO CAP ($M, filled) against the $100 TSR index (dashed)",
                      _relationship_svg(series, "company_tsr_value", "TSR",
                                        lambda v: f"${v}", "cap-vs-tsr"), "Relationship"))
    body.append(_tile("CAP vs net income", "PEO CAP ($M, filled) against net income ($M, dashed)",
                      _relationship_svg(series, "net_income_usd", "Net income",
                                        lambda v: f"${round(v / 1e6)}M", "cap-vs-ni"), "Relationship"))
    body.append(_tile(f"CAP vs {series['csm_label']}",
                      f"PEO CAP ($M, filled) against the company-selected measure ($M, dashed)",
                      _relationship_svg(series, "csm_usd", series["csm_label"],
                                        lambda v: f"${round(v / 1e6)}M", "cap-vs-csm"), "Relationship"))
    body.append(_tile("Company-selected measure", "The financial measure the committee names most linked to CAP",
                      f"<div class='dist'><div><span>Measure</span><b class='mono'>{_e(series['csm_label'])}</b></div>"
                      f"<div><span>FY{first['fy']}</span><b class='mono'>{_m(first['csm_usd'])}</b></div>"
                      f"<div><span>FY{last['fy']}</span><b class='mono'>{_m(last['csm_usd'])}</b></div></div>",
                      "402(v)(6)"))
    body.append("</main>")
    body.append("<section class='method'><h2>Methodology and controls</h2>"
                "<ul>"
                "<li>CAP is reconciled from each NEO's Summary Compensation Table total by the equity "
                "fair-value roll-forward in Reg. S-K 402(v)(2)(iii): subtract the SCT grant-date fair "
                "value of stock and option awards, then add year-end fair value of current-year unvested "
                "awards, the change in fair value of prior-year unvested awards, the vesting-date value of "
                "awards that vested, the change in fair value to the vesting date for prior-year awards, "
                "and subtract the prior-year-end value of awards forfeited.</li>"
                "<li>Fair values are re-measured, not assumed: restricted stock at the share price, options "
                "by Black-Scholes, and relative-TSR market-condition PSUs by the same deterministic Monte "
                "Carlo estimator the rTSR PSU arm ships (remaining-period re-measurement).</li>"
                "<li>One committed synthetic stock-price path drives both the executives' equity fair values "
                "and the company Total Shareholder Return column, so the pay side and the performance side "
                "tie to a single price series.</li>"
                "<li>The reconciliation bridge is self-checked: the itemized steps must tie to the reported "
                "CAP to the cent, or the build fails closed.</li>"
                "<li>This is an illustrative reconstruction of the disclosure methodology on synthetic Acme "
                "data. Pension adjustments are not applicable to this issuer (no defined-benefit plan). A "
                "filer's award fair values are produced by its valuation provider under audited assumptions; "
                "the peer-group TSR, net income, and company-selected measure here are synthetic.</li>"
                "<li>Public-safety boundary: no real issuer, ticker, stock price, proxy, award, employee, or "
                "employer data is included. Synthetic tickers carry an obvious marker.</li>"
                "<li>This is decision-support math, not accounting, legal, tax, or investment advice, and not "
                "an auditor-approved ASC 718 valuation or the company's filed 402(v) disclosure.</li>"
                "</ul></section>")
    return HTML.format(title="Pay versus Performance", body="".join(body))


def render_digest(report):
    table, align = report["table"], report["alignment"]
    rows = table["rows"]
    first, last = rows[0], rows[-1]
    lines = [
        f"# {report['company_name']} — Pay-versus-Performance draft digest",
        "",
        f"- **Disclosure:** SEC Item 402(v) illustrative reconstruction, FY{first['fy']}–FY{last['fy']} "
        f"(synthetic data)",
        f"- **PEO CAP:** {_mm(first['peo_cap'])} (FY{first['fy']}) to {_mm(last['peo_cap'])} (FY{last['fy']})",
        f"- **Company TSR ($100 base):** {_idx(first['company_tsr_value'])} to {_idx(last['company_tsr_value'])}",
        f"- **Pay-for-performance read:** PEO CAP {align['cap_direction']}, company TSR "
        f"{align['tsr_direction']} — {'aligned' if align['aligned'] else 'divergent'} (a directional "
        "legibility signal, not a say-on-pay vote forecast)",
        f"- **Reconciliation:** every CAP figure ties from the SCT total through the 402(v)(2)(iii) equity "
        "roll-forward; the bridge is self-checked to the cent",
        "",
        "A demo named-reviewer label is required before publication. This synthetic example is not "
        "accounting, legal, or investment advice, and not an auditor-approved ASC 718 valuation.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- output plumbing (fail-closed)
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
    last = report["table"]["rows"][-1]
    return {
        "agent": AGENT,
        "scope": SCOPE,
        "control_type": "demo_named_reviewer_gate",
        "approved_by": approved_by,
        "company": report["company_name"],
        "last_fy": last["fy"],
        "peo_cap_last_fy": last["peo_cap"],
        "pay_for_performance": "aligned" if report["alignment"]["aligned"] else "divergent",
        "disclaimer": "synthetic 402(v) reconstruction; not accounting/legal/investment advice",
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
* {{ box-sizing:border-box; }} body {{ margin:0; background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
.mono {{ font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace; }}
header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px; padding:30px 34px 18px;
  border-bottom:1px solid var(--line); background:linear-gradient(180deg,#0f2a3e,#06131d); }}
.brand {{ color:var(--cyan); font-size:13px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }}
.brand span {{ color:#fff; }} h1 {{ margin:8px 0 8px; font-size:30px; line-height:1.05; letter-spacing:0; }}
p {{ margin:0; color:var(--muted); }} .status {{ border:1px solid var(--amber); color:var(--amber);
  padding:8px 10px; border-radius:6px; font-size:12px; font-weight:800; text-transform:uppercase; white-space:nowrap; }}
.insight {{ margin:24px 34px; padding:18px 20px; border:1px solid rgba(27,167,255,.35);
  background:#0a1f2c; border-radius:8px; }} .insight p {{ color:var(--ink); font-size:16px; line-height:1.45; }}
.tag {{ color:var(--cyan2); font-size:12px; font-weight:800; text-transform:uppercase; margin-bottom:8px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:0 34px 18px; }}
.kpi {{ min-height:120px; padding:16px; border:1px solid var(--line); border-radius:8px; background:var(--panel); }}
.kpi.hot {{ border-color:rgba(67,212,119,.5); }} .k-label {{ color:var(--muted); font-size:12px; font-weight:800; text-transform:uppercase; }}
.k-val {{ font-size:26px; margin:12px 0 8px; }} .k-sub {{ color:var(--soft); font-size:12px; line-height:1.35; }}
main {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:0 34px 22px; }}
.tile {{ padding:16px; border:1px solid var(--line); border-radius:8px; background:var(--panel); overflow:hidden; }}
.tile.wide {{ grid-column:1 / -1; }}
.t-head {{ display:flex; justify-content:space-between; gap:18px; margin-bottom:14px; }}
h3 {{ margin:0; font-size:16px; }} .t-sub {{ color:var(--muted); margin-top:4px; font-size:12px; }}
.t-head span {{ color:var(--soft); font-size:11px; font-weight:800; text-transform:uppercase; white-space:nowrap; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }} th {{ text-align:left; color:var(--muted);
  border-bottom:1px solid var(--line); padding:7px 6px; }} td {{ padding:7px 6px; border-bottom:1px solid rgba(141,177,206,.1); }}
.r {{ text-align:right; }} td.hot {{ color:var(--cyan2); font-weight:800; }}
.dist {{ display:grid; gap:8px; margin-top:4px; }} .dist div {{ display:flex; justify-content:space-between;
  gap:16px; padding:9px 10px; border:1px solid rgba(141,177,206,.12); border-radius:6px; }}
.dist span {{ color:var(--muted); }}
.bridge-wrap {{ display:grid; grid-template-columns:1.7fr 1fr; gap:18px; align-items:start; }}
.bridge-chart {{ min-width:0; }}
.bridge-h {{ color:var(--soft); font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:.06em; margin-bottom:8px; }}
.dist.bridge {{ gap:5px; }} .dist.bridge div {{ padding:6px 10px; font-size:12px; }}
.dist.bridge b.b {{ color:var(--cyan2); }}
@media (max-width: 900px) {{ .bridge-wrap {{ grid-template-columns:1fr; }} }}
.method {{ margin:0 34px 34px; padding:18px 20px; border:1px solid var(--line);
  border-radius:8px; background:#071a26; }} h2 {{ margin:0 0 10px; font-size:17px; }}
li {{ margin:8px 0; color:var(--muted); line-height:1.45; }}
@media (max-width: 900px) {{ header,.kpis,main {{ display:block; }} .kpi,.tile {{ margin-bottom:12px; }} }}
</style></head><body>{body}</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
