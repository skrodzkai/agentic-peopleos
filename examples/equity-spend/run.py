#!/usr/bin/env python3
"""Equity-Spend / Burn-Rate board agent — the VP-Total-Rewards board deliverable, rendered.

A dark board dashboard over the company-wide equity plan: SBC as % of revenue, gross/net burn and the current
ISS Equity-Plan-Scorecard Value-Adjusted Burn Rate vs an illustrative industry cap, overhang/dilution, pool
longevity (when the next shareholder share-request lands), the locked-in SBC backlog, and where the shares go
(exec vs management vs staff). Every number comes from foundation/compute/equity_spend.py — the agent renders
and governs; it does no math and it recommends no grants.

IMPORTANT (on the dashboard and here): benchmark caps, EPSC weights, and the SVT valuation are ILLUSTRATIVE —
representative of published software-industry practice, NOT ISS output. The plan-feature tests are scored
exactly from the plan facts.

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

from foundation.compute import equity_spend as E     # noqa: E402
from foundation.render import charts as ch            # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "FY2025"
PERIOD = "FY2025 · company-wide equity plan · synthetic"
SCOPE = "publish.equity_spend"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")


class ReportError(RuntimeError):
    """Raised when the equity-spend view cannot be produced (fail closed)."""


def _e(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _one_line(t, limit=300):
    return " ".join(str(t).split())[:limit]


def _m(v):        # $ millions
    return f"${v / 1e6:,.1f}M"


def _b(v):        # $ billions
    return f"${v / 1e9:,.2f}B"


def _fin(*xs):
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


# ---------------------------------------------------------------- build + validate
def build_report(result):
    """Validate the engine output (fail closed) and shape it for rendering. No math is done here."""
    r = result
    gp = r["epsc"]["grant_practices"]
    checks = [
        _fin(r["market_cap"], r["shares_outstanding"], r["price"]),
        abs(r["market_cap"] - r["shares_outstanding"] * r["price"]) < 1.0,
        _fin(r["vabr_3yr_pct"], r["overhang_pct"], r["pool_longevity_years"], r["unamortized_sbc"]),
        r["vabr_3yr_pct"] > 0 and 0 < r["overhang_pct"] < 100 and r["pool_longevity_years"] > 0,
        isinstance(gp["pass"], bool) and _fin(gp["vabr_3yr_pct"], gp["benchmark_cap_pct"]),
        abs(gp["vabr_3yr_pct"] - r["vabr_3yr_pct"]) < 0.011,     # the headline VABR matches the EPSC pillar
        0 <= r["epsc"]["features_passed"] <= r["epsc"]["features_total"] == 6,
        "illustrative" in gp["source_note"].lower(),             # defense-in-depth: benchmark must be illustrative
        r["fiscal_years"] == sorted(r["fiscal_years"]) and len(r["fiscal_years"]) >= 3,
        "ceo" in r["value_per_fte_by_group"],
    ]
    if not all(checks):
        raise ReportError(f"equity-spend result failed validation (check #{checks.index(False)})")

    # the honest board verdict — a presentation of engine facts, not a new computation
    feats_ok = r["epsc"]["features_passed"] == r["epsc"]["features_total"]
    verdict = ("DEFENSIBLE — file a refresh with confidence" if gp["pass"] and feats_ok
               else "WATCH — burn or plan features need attention before a share request")
    q = r["sbc_pct_revenue"]["quarterly"]
    changed = [("SBC % of revenue (TTM)", f"{r['sbc_pct_revenue']['ttm_pct']:.1f}%",
                q[-1]["pct"] - q[-5]["pct"] if len(q) >= 5 else 0.0, True),
               ("3-yr VABR vs cap", f"{r['vabr_3yr_pct']:.2f}% / {gp['benchmark_cap_pct']:.2f}%",
                gp["headroom_pct"], False),
               ("Pool longevity", f"{r['pool_longevity_years']:.1f} yrs", 0.0, False)]
    return {"r": r, "gp": gp, "verdict": verdict, "changed": changed,
            "refresh_year": r["fiscal_years"][-1] + int(math.ceil(r["pool_longevity_years"]))}


# ---------------------------------------------------------------- render
def _kpi(label, value, sub, series=None, good=None):
    spark = ch.sparkline(series, ch.CYAN if good is None else (ch.GREEN if good else ch.AMBER)) if series else ""
    return (f"<div class='kpi'><div class='k-l'>{_e(label)}</div>"
            f"<div class='k-v mono'>{_e(value)}</div><div class='k-spark'>{spark}</div>"
            f"<div class='k-s'>{_e(sub)}</div></div>")


def render_html(report):
    r, gp = report["r"], report["gp"]
    q = r["sbc_pct_revenue"]["quarterly"]
    fy_labels = [f"FY{b['fy']}" for b in r["burn"]]
    vabr_series = [b["vabr_pct"] for b in r["burn"]]
    body = []
    # header + headline banner
    body.append(f"<header class='top'><div><div class='brand'>Agentic People<span class='os'>OS</span></div>"
                f"<div class='sub'>Executive Compensation · Equity Spend</div></div>"
                f"<div class='ttl'><h1>Company-Wide Equity Spend &amp; Burn — Board Review</h1>"
                f"<div class='meta'>{_e(COMPANY)} · {_e(PERIOD)}</div></div>"
                f"<span class='status'>Draft · awaiting committee approval</span></header>")
    vc = ch.GREEN if gp["pass"] and r["epsc"]["features_passed"] == 6 else ch.AMBER
    body.append(f"<section class='headline' style='border-color:{vc}'>"
                f"<div class='hl-tag'>Board headline</div>"
                f"<p>Equity spend runs at <b>{r['sbc_pct_revenue']['ttm_pct']:.1f}% of revenue</b>; the 3-year "
                f"value-adjusted burn is <b>{r['vabr_3yr_pct']:.2f}%</b> against an illustrative "
                f"<b>{gp['benchmark_cap_pct']:.2f}%</b> industry cap "
                f"(<span style='color:{vc}'>{'passes +' + format(gp['headroom_pct'], '.2f') + 'pt' if gp['pass'] else 'over cap'}</span>), "
                f"and the approved pool funds <b>~{r['pool_longevity_years']:.1f} more years</b> of grants — a "
                f"shareholder share-request lands around the <b>{report['refresh_year']}</b> annual meeting. "
                f"Today's plan passes <b>{r['epsc']['features_passed']}/{r['epsc']['features_total']}</b> "
                f"scoreable EPSC feature tests. <span class='vd' style='color:{vc}'>{_e(report['verdict'])}</span></p>"
                "</section>")
    # KPI band
    sbc_series = [x["pct"] for x in q]
    body.append("<section class='kpis'>"
                + _kpi("SBC % of revenue (TTM)", f"{r['sbc_pct_revenue']['ttm_pct']:.1f}%",
                       "stock-based comp / revenue", sbc_series)
                + _kpi("3-yr Value-Adjusted Burn", f"{r['vabr_3yr_pct']:.2f}%",
                       f"cap {gp['benchmark_cap_pct']:.2f}% (illustrative)", vabr_series, gp["pass"])
                + _kpi("Overhang / dilution", f"{r['overhang_pct']:.1f}%", "outstanding + pool / shares out")
                + _kpi("Pool longevity", f"{r['pool_longevity_years']:.1f} yrs",
                       f"refresh ask ~{report['refresh_year']}")
                + _kpi("SBC backlog (locked-in)", _m(r["unamortized_sbc"]),
                       f"over {r['unamortized_sbc_years']:.1f} yrs, even at zero new grants")
                + "</section>")
    # spend trend — dual axis SBC $ vs SBC %
    q_labels = [x["period"][2:7] for x in q]
    body.append("<section class='tile'><h3>Equity-spend trend — SBC $ vs % of revenue</h3>"
                "<div class='t-sub'>The expense book matures then eases as the grant envelope steps down; the "
                "declining right axis is the maturation story.</div>"
                + ch.dual_axis_line(q_labels, [x["sbc"] / 1e6 for x in q], [x["pct"] for x in q],
                                    left_fmt=lambda v: f"${v:.0f}M", right_fmt=lambda v: f"{v:.0f}%", uid="sbc")
                + "</section>")
    # benchmark + EPSC readiness
    cap = gp["benchmark_cap_pct"]
    hi = max(4.0, cap * 1.5)
    strip = ch.percentile_strip(r["vabr_3yr_pct"], 0, hi,
                                ticks=[(0, "0%"), (cap, f"cap {cap:.2f}%"), (hi, f"{hi:.1f}%")], target=cap,
                                you_label="Acme 3-yr VABR", unit_prefix="", unit_suffix="%")
    feats = "".join(f"<div class='feat'><span class='tick' style='color:{ch.GREEN if f['pass'] else ch.RED}'>"
                    f"{'✓' if f['pass'] else '✗'}</span>{_e(f['test'])}</div>"
                    for f in r["epsc"]["plan_features"])
    body.append("<section class='tile wide'><h3>ISS Equity-Plan-Scorecard readiness</h3>"
                "<div class='t-sub'>If we filed a pool refresh today, would the plan pass proxy-advisor review? "
                "Plan Features are scored exactly from the plan; the burn cap and SVT are illustrative.</div>"
                "<div class='epsc'>"
                f"<div class='ep-col'><div class='ep-h'>Grant Practices — 3-yr burn vs cap</div>{strip}"
                f"<div class='ep-note'>VABR {r['vabr_3yr_pct']:.2f}% vs {cap:.2f}% cap · "
                f"{'PASS +' + format(gp['headroom_pct'], '.2f') + 'pt' if gp['pass'] else 'OVER by ' + format(-gp['headroom_pct'], '.2f') + 'pt'}</div></div>"
                f"<div class='ep-col'><div class='ep-h'>Plan Features — {r['epsc']['features_passed']}/6 pass</div>{feats}</div>"
                f"<div class='ep-col'><div class='ep-h'>Plan Cost (directional SVT)</div>"
                f"<div class='ep-svt mono'>{r['epsc']['plan_cost_svt_pct']:.1f}%</div>"
                f"<div class='ep-note'>value of outstanding + pool ÷ market cap · illustrative, not an ISS score</div></div>"
                "</div></section>")
    # burn table
    rows = "".join(f"<tr><td>FY{b['fy']}</td><td class='mono r'>{b['gross_pct']:.2f}%</td>"
                   f"<td class='mono r'>{b['net_pct']:.2f}%</td><td class='mono r hi'>{b['vabr_pct']:.2f}%</td>"
                   f"<td class='mono r mut'>{b['legacy_adjusted_pct']:.2f}%</td></tr>" for b in r["burn"])
    body.append("<section class='tile'><h3>Burn rate by fiscal year</h3>"
                "<table class='bt'><tr><th>FY</th><th class='r'>Gross</th><th class='r'>Net</th>"
                "<th class='r'>VABR (current ISS)</th><th class='r'>Legacy adj. (retired 2023)</th></tr>"
                + rows + "</table><div class='t-sub'>VABR is the current ISS convention; the legacy "
                "volatility-multiplier column is shown only because older board decks still quote it.</div></section>")
    # allocation — where the shares go
    vg = r["value_per_fte_by_group"]
    order = [g for g in ("ceo", "section16", "management", "staff", "director") if g in vg]
    labels = {"ceo": "CEO", "section16": "Other NEOs", "management": "Management", "staff": "Staff (broad-based)",
              "director": "Directors"}
    hist = ch.histogram([round(vg[g]["value"] / 1e6, 1) for g in order], [labels[g] for g in order])
    body.append("<section class='tile'><h3>Where the equity goes — grant value by group (latest FY)</h3>"
                "<div class='t-sub'>Company-wide: broad-based staff refreshers are the largest slice; executives "
                "are a minority of the plan's spend.</div>" + hist
                + "<div class='alloc'>"
                + "".join(f"<span class='ag'>{_e(labels[g])}: {_m(vg[g]['value'])} · "
                          f"{vg[g]['recipients']:,} ppl · {_m(vg[g]['per_fte'])}/ea</span>" for g in order)
                + "</div></section>")
    body.append("<footer class='foot'>Built by the <b>equity-spend</b> agent · it renders the board equity view; "
                "the <b>Compensation Committee</b> approves plan design and share requests. "
                "Benchmark caps, EPSC weights, and SVT are <b>illustrative</b> — representative of published "
                "software practice, not Glass Lewis or ISS output. Synthetic company-wide data.</footer>")
    return _page("".join(body))


def render_digest(report):
    r, gp = report["r"], report["gp"]
    return "\n".join([
        f"# {COMPANY} — Equity Spend & Burn (board digest, {AS_OF})", "",
        f"**{report['verdict']}**", "",
        f"- **SBC % of revenue (TTM):** {r['sbc_pct_revenue']['ttm_pct']:.1f}%",
        f"- **3-yr Value-Adjusted Burn (current ISS EPSC):** {r['vabr_3yr_pct']:.2f}% vs an illustrative "
        f"{gp['benchmark_cap_pct']:.2f}% cap — {'passes with ' + format(gp['headroom_pct'], '.2f') + 'pt headroom' if gp['pass'] else 'over the cap'}",
        f"- **Overhang / dilution:** {r['overhang_pct']:.1f}%",
        f"- **Pool longevity:** {r['pool_longevity_years']:.1f} yrs → a shareholder share-request around "
        f"the {report['refresh_year']} annual meeting",
        f"- **SBC backlog (locked in):** {_m(r['unamortized_sbc'])} over {r['unamortized_sbc_years']:.1f} yrs "
        "even at zero new grants",
        f"- **EPSC plan features:** {r['epsc']['features_passed']}/{r['epsc']['features_total']} scoreable tests pass",
        "", "_Company-wide synthetic equity plan. Benchmark caps / EPSC weights / SVT are illustrative — "
        "representative of published software practice, NOT ISS or Glass Lewis output. Draft; the Compensation "
        "Committee approves plan design and share requests._"])


_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#06131d;color:#dbe7f0;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:1180px;margin:0 auto;padding:26px}.mono{font-family:'JetBrains Mono','SF Mono',Menlo,monospace}
.top{display:flex;align-items:center;gap:18px;border-bottom:1px solid #14364a;padding-bottom:14px;margin-bottom:6px}
.brand{font-weight:800;font-size:17px;letter-spacing:.3px}.os{color:#1ba7ff}.sub{color:#6d8ba0;font-size:11px;text-transform:uppercase;letter-spacing:1px}
.ttl{flex:1}.ttl h1{margin:0;font-size:18px}.meta{color:#6d8ba0;font-size:12px;margin-top:2px}
.status{background:rgba(247,185,85,.13);color:#f7b955;border:1px solid rgba(247,185,85,.45);border-radius:5px;padding:4px 9px;font-size:11px;white-space:nowrap}
.headline{background:#0a1f2c;border-left:4px solid #43d477;border-radius:8px;padding:14px 18px;margin:16px 0}
.hl-tag{color:#6d8ba0;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}.headline p{margin:0}.vd{font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:16px 0}
.kpi{background:#0a1f2c;border:1px solid #14364a;border-radius:8px;padding:12px}.k-l{color:#6d8ba0;font-size:11px;min-height:28px}
.k-v{font-size:22px;font-weight:700;margin:2px 0}.k-spark{height:28px}.k-s{color:#8fb0c6;font-size:11px}
.tile{background:#0a1f2c;border:1px solid #14364a;border-radius:8px;padding:16px;margin:14px 0}.tile.wide{}
.tile h3{margin:0 0 2px;font-size:14px}.t-sub{color:#6d8ba0;font-size:12px;margin-bottom:10px}
.epsc{display:grid;grid-template-columns:1.3fr 1.1fr .8fr;gap:16px}.ep-h{font-weight:600;font-size:12px;margin-bottom:8px;color:#b9d0e0}
.ep-note{color:#6d8ba0;font-size:11px;margin-top:6px}.feat{font-size:12px;padding:2px 0}.tick{font-weight:800;margin-right:7px}
.ep-svt{font-size:26px;font-weight:700;color:#1ba7ff}
.bt{width:100%;border-collapse:collapse;font-size:13px}.bt th,.bt td{padding:6px 8px;border-bottom:1px solid #12303f;text-align:left}
.bt th.r,.bt td.r{text-align:right}.hi{color:#1ba7ff;font-weight:700}.mut{color:#6d8ba0}
.alloc{margin-top:10px;display:flex;flex-wrap:wrap;gap:8px}.ag{background:#08283a;border:1px solid #14364a;border-radius:4px;padding:3px 8px;font-size:11px;color:#b9d0e0}
.foot{color:#6d8ba0;font-size:11px;border-top:1px solid #14364a;margin-top:20px;padding-top:12px}
@media(max-width:820px){.kpis{grid-template-columns:1fr 1fr}.epsc{grid-template-columns:1fr}}
"""


def _page(body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(COMPANY)} — Equity Spend & Burn</title><style>{_STYLE}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


# ---------------------------------------------------------------- fail-closed + entrypoint
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
    ap = argparse.ArgumentParser(description="Acme Corp equity-spend board dashboard (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw = args.approved_by or ""
    approver = raw.strip()
    if args.publish and (any(ord(c) < 32 for c in raw) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a named committee approver.\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2
    try:
        report = build_report(E.compute())
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"equity-spend view unavailable: {exc}")

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
            _atomic_write(pub, json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF,
                                           "verdict": report["verdict"]}, indent=2) + "\n")
    except OSError as exc:
        return _fail_closed(f"could not write output: {exc}")

    print(f"{COMPANY} Equity Spend & Burn — Board Review ({AS_OF})")
    print(f"  SBC {report['r']['sbc_pct_revenue']['ttm_pct']:.1f}% rev · 3-yr VABR {report['r']['vabr_3yr_pct']:.2f}% "
          f"· longevity {report['r']['pool_longevity_years']:.1f}yr · {report['verdict']}")
    print("  wrote report.sample.html and day1-digest.sample.md")
    print("\nDRAFT only. The Compensation Committee approves plan design + share requests. Nothing was sent."
          if not args.publish else f"\nApproved by {approver}. Recorded locally (no external send).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
