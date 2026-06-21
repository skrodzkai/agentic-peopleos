#!/usr/bin/env python3
"""Acme Corp — Attrition & Retention reporting agent (Agentic PeopleOS Analytics arm).

Presentation + governance over the shared compute engine. It reports annualized turnover (voluntary,
regrettable, total, involuntary), first-year/90-day attrition, 12-month retention, and segment
hotspots — all computed by `foundation/compute/engine.py`, cited from the registry, with the
annualization method stated plainly. The two un-instrumented mobility metrics are shown honestly.

    python run.py
    python run.py --publish --approved-by "People Analytics Lead"

Standard library only; deterministic; offline.
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

from foundation.render import dashboard as dash          # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "Jan 2026"
AGENT = "attrition-reporting"
SCOPE = "publish.attrition_report"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

METRIC_IDS = [
    "voluntary_attrition", "regrettable_attrition", "total_turnover_rate",
    "involuntary_turnover_rate", "new_hire_attrition", "early_attrition_90d",
    "twelve_month_retention", "internal_mobility_rate", "internal_fill_rate",
]


class ReportError(RuntimeError):
    """Raised when the report cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_engine():
    from foundation.compute.engine import MetricEngine
    return MetricEngine()


def build_report(engine):
    results = {}
    for mid in METRIC_IDS:
        r = engine.compute(mid)
        if r["status"] not in ("ok", "data_pending"):
            raise ReportError(f"metric '{mid}' returned unexpected status '{r['status']}'")
        results[mid] = r
    pending = [{"name": results[mid]["name"], "needs": results[mid]["needs"]}
               for mid in METRIC_IDS if results[mid]["status"] == "data_pending"]

    def val(mid):
        return results[mid]["value"] if results[mid]["status"] == "ok" else None

    # Segment hotspots (computed by the engine, not here).
    seg_level = engine.segment("voluntary_attrition", "level")
    seg_loc = engine.segment("voluntary_attrition", "location")
    hotspot = max(seg_level.items(), key=lambda kv: kv[1]) if seg_level else None

    cards = [
        {"value": f"{val('voluntary_attrition')}%", "label": "Voluntary attrition · ann."},
        {"value": f"{val('regrettable_attrition')}%", "label": "Regrettable · ann.",
         "tone": "warn" if (val('regrettable_attrition') or 0) >= 8 else "neutral"},
        {"value": f"{val('total_turnover_rate')}%", "label": "Total turnover · ann."},
        {"value": f"{val('involuntary_turnover_rate')}%", "label": "Involuntary · ann."},
        {"value": f"{val('twelve_month_retention')}%", "label": "12-mo retention", "tone": "good"},
        {"value": f"{val('new_hire_attrition')}%", "label": "New-hire attrition"},
    ]
    return {"results": results, "pending": pending, "cards": cards,
            "ok_ids": [m for m in METRIC_IDS if results[m]["status"] == "ok"],
            "seg_level": seg_level, "seg_loc": seg_loc, "hotspot": hotspot,
            "narrative": _narrative(results, hotspot)}


def _narrative(r, hotspot):
    v = lambda m: r[m]["value"]
    parts = [f"Voluntary attrition is {v('voluntary_attrition')}% annualized; "
             f"regrettable is {v('regrettable_attrition')}% "
             f"({r['regrettable_attrition']['extras']['regrettable_share_of_voluntary_pct']}% of voluntary)."]
    parts.append(f"Total turnover {v('total_turnover_rate')}% ({v('involuntary_turnover_rate')}% involuntary); "
                 f"12-month retention {v('twelve_month_retention')}%.")
    if hotspot:
        parts.append(f"Highest voluntary segment: {hotspot[0]} at {hotspot[1]}% — the retention focus.")
    return " ".join(parts)


# ---------- rendering ----------

def render_html(report):
    r = report["results"]
    body = [dash.brand_header(),
            dash.title_block("Attrition & Retention", "Attrition Report",
                             f"{COMPANY} · as of {AS_OF} · synthetic data"),
            dash.narrator(report["narrative"]),
            dash.kpi_cards(report["cards"]),
            ("<div style='font-size:11.5px;color:#8db1ce;margin:4px 0 0'>Annualization: simple "
             "&times;(12/months), average-headcount denominator (mean of monthly actives).</div>")]

    if report["seg_level"]:
        mx = max(report["seg_level"].values(), default=1)
        body.append(dash.section("Voluntary attrition by level"))
        body.append(dash.bars([{"label": k, "value": v, "max": mx,
                                "color": "#ff4d4f" if v == report["hotspot"][1] else "#1ba7ff"}
                               for k, v in report["seg_level"].items()]))
    if report["seg_loc"]:
        mx = max(report["seg_loc"].values(), default=1)
        body.append(dash.section("Voluntary attrition by location"))
        body.append(dash.bars([{"label": k, "value": v, "max": mx} for k, v in report["seg_loc"].items()]))

    # New-hire cohort detail
    if r["new_hire_attrition"]["status"] == "ok":
        nh, ea, ret = r["new_hire_attrition"], r["early_attrition_90d"], r["twelve_month_retention"]
        body.append(dash.section("New-hire cohort (matured ≥12 months)"))
        body.append(dash.data_table(
            ["Measure", "Value"],
            [["Cohort size", nh["extras"]["cohort"]],
             ["Left within 12 months", f"{nh['value']}% ({nh['extras']['left_within_12mo']})"],
             ["Left within 90 days", f"{ea['value']}% ({ea['extras']['left_within_90d']})"],
             ["12-month retention", f"{ret['value']}%"]]))

    body.append(dash.data_pending_block(report["pending"]))
    body.append(dash.metric_definitions(_registry(), report["ok_ids"]))
    body.append(dash.governance_footer(AGENT))
    return dash.page(f"{COMPANY} — Attrition Report", "".join(body))


def _registry():
    from core.metrics import MetricRegistry
    return MetricRegistry.load()


def render_digest(report):
    r = report["results"]
    lines = [f"# {COMPANY} — Attrition digest", f"_As of {AS_OF} · draft for review_", "",
             f"- {report['narrative']}",
             f"- Annualized (simple ×12/months, avg-headcount denominator): voluntary "
             f"**{r['voluntary_attrition']['value']}%**, total **{r['total_turnover_rate']['value']}%**, "
             f"retention **{r['twelve_month_retention']['value']}%**."]
    if report["hotspot"]:
        lines.append(f"- Watch: {report['hotspot'][0]} has the highest voluntary attrition "
                     f"({report['hotspot'][1]}%).")
    if report["pending"]:
        lines.append(f"- Coverage: {len(report['pending'])} mobility metrics defined but not yet "
                     f"instrumented (shown honestly, never estimated).")
    lines += ["", "_Numbers computed by the shared metric engine and cited from metrics.registry.json._",
              "", "_Publish gate: a human (People Analytics) must approve before this is distributed._"]
    return "\n".join(lines) + "\n"


# ---------- fail-closed + entrypoint ----------

def _fail_closed(message) -> int:
    for p in (REPORT, DIGEST):
        try:
            if p.exists():
                p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            pass
    print(f"FAIL CLOSED: {_one_line(message)}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp attrition reporting agent (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (People Analytics).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        engine = _load_engine()
        report = build_report(engine)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"compute engine unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    try:
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        if args.publish:  # approval record is part of the same all-or-nothing transaction
            _atomic_write(pub_path,
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": AS_OF}, indent=2) + "\n")
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            tmp = p.with_name(p.name + ".tmp")
            try:
                tmp.unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    r = report["results"]
    print(f"{COMPANY} attrition report — as of {AS_OF}")
    print(f"  computed: {len(report['ok_ids'])} metrics | data_pending: {len(report['pending'])} | "
          f"voluntary: {r['voluntary_attrition']['value']}%")
    print("  wrote report.sample.html and day1-digest.sample.md")

    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (People Analytics) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
