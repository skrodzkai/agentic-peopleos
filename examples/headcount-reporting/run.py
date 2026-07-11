#!/usr/bin/env python3
"""Acme Corp — Headcount & Workforce reporting agent (Agentic PeopleOS Analytics arm).

The first reporting agent built on the shared compute engine. It does NO metric math: it asks
`foundation/compute/engine.py` for each metric in its registry-declared set, renders the computable
ones into a dark operating dashboard, and lists the not-yet-instrumented ones HONESTLY in a coverage
section (never a fabricated number). It cites the metric registry, fails closed, and stops at a human
publish gate.

    python3 run.py                                          # draft only
    python3 run.py --publish                                # refused: needs a valid named approver
    python3 run.py --publish --approved-by "People Analytics Lead"

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

from foundation import evidence_portfolio as portfolio_ev  # noqa: E402
from foundation.render import dashboard as dash          # noqa: E402

OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "Jan 2026"
AGENT = "headcount-reporting"
SCOPE = "publish.headcount_report"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

# Every metric this agent is responsible for (registry downstream = headcount-reporting), in
# display order. The engine decides which are computable now vs data_pending — the agent never
# hardcodes a value.
METRIC_IDS = [
    "headcount", "fte", "net_headcount_growth", "span_of_control", "span_outlier_rate",
    "management_layers", "contingent_workforce_ratio", "representation_by_level",
    "leadership_diversity", "vacancy_rate", "headcount_plan_attainment",
    "succession_coverage", "successor_readiness", "adverse_impact_ratio",
]


class ReportError(RuntimeError):
    """Raised when the report cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_engine():
    """Load the shared compute engine; any failure (missing data / invalid registry) fails closed."""
    from foundation.compute.engine import MetricEngine
    return MetricEngine()


def build_report(engine):
    """Compute every declared metric via the engine and assemble the display model. No math here."""
    results = {}
    for mid in METRIC_IDS:
        r = engine.compute(mid)
        if r["status"] not in ("ok", "data_pending"):
            raise ReportError(f"metric '{mid}' returned unexpected status '{r['status']}'")
        results[mid] = r
    # Consistency invariant: the headcount KPI must equal the net-growth bridge ending (same
    # snapshot population). Disagreement means a data/engine drift — fail closed, never publish it.
    hcr, ngr = results["headcount"], results["net_headcount_growth"]
    if hcr["status"] == "ok" and ngr["status"] == "ok" and hcr["value"] != ngr["extras"]["ending"]:
        raise ReportError(f"headcount KPI ({hcr['value']}) disagrees with the bridge ending "
                          f"({ngr['extras']['ending']})")

    def ok(mid):
        return results[mid] if results[mid]["status"] == "ok" else None

    pending = [{"name": results[mid]["name"], "needs": results[mid]["needs"]}
               for mid in METRIC_IDS if results[mid]["status"] == "data_pending"]

    hc, fte, ng = ok("headcount"), ok("fte"), ok("net_headcount_growth")
    span, so, ml = ok("span_of_control"), ok("span_outlier_rate"), ok("management_layers")
    cont, rep, lead = ok("contingent_workforce_ratio"), ok("representation_by_level"), ok("leadership_diversity")

    cards = []
    if hc:
        cards.append({"value": hc["value"], "label": "Employees"})
    if fte:
        cards.append({"value": fte["value"], "label": "FTE"})
    if ng:
        sign = "+" if ng["value"] >= 0 else ""
        cards.append({"value": f"{sign}{ng['value']}", "label": "Net growth · 12mo",
                      "tone": "good" if ng["value"] >= 0 else "bad"})
    if span:
        cards.append({"value": span["extras"]["mean"], "label": "Avg span of control"})
    if so:
        cards.append({"value": f"{so['value']}%", "label": "Span outliers",
                      "tone": "warn" if so["value"] else "neutral"})
    if cont:
        cards.append({"value": f"{cont['value']}%", "label": "Contingent workforce"})

    return {"results": results, "pending": pending, "cards": cards,
            "ok_ids": [m for m in METRIC_IDS if results[m]["status"] == "ok"],
            "narrative": _narrative(hc, fte, ng, so, ml)}


def _narrative(hc, fte, ng, so, ml):
    parts = []
    if hc and fte:
        parts.append(f"{hc['value']} active employees ({fte['value']} FTE).")
    if ng:
        sign = "grew" if ng["value"] >= 0 else "shrank"
        e = ng["extras"]
        parts.append(f"Headcount {sign} by {abs(ng['value'])} over the trailing 12 months "
                     f"({e['hires']} hires, {e['total_exits']} exits).")
    if so and so["extras"].get("sub_scale_pct"):
        parts.append(f"{so['extras']['sub_scale_pct']}% of managers are sub-scale (<3 reports) — "
                     f"the clearest delayering signal.")
    elif ml:
        parts.append(f"The org runs {ml['extras']['max_depth']} management layers deep.")
    return " ".join(parts) if parts else "Workforce snapshot."


# ---------- rendering (all via the shared renderer) ----------

def render_html(report):
    r = report["results"]
    body = [dash.brand_header(),
            dash.title_block("Headcount & Workforce", "Workforce Report",
                             f"{COMPANY} · as of {AS_OF} · synthetic data"),
            dash.narrator(report["narrative"]),
            dash.kpi_cards(report["cards"])]

    # Management-layer distribution
    if r["management_layers"]["status"] == "ok":
        dist = r["management_layers"]["extras"]["distribution"]
        mx = max(dist.values(), default=1)
        body.append(dash.section("Management layers (employees per depth)"))
        body.append(dash.bars([{"label": f"Layer {k}", "value": v, "max": mx} for k, v in dist.items()]))

    # Span of control detail
    if r["span_of_control"]["status"] == "ok":
        s, o = r["span_of_control"]["extras"], r["span_outlier_rate"]["extras"]
        body.append(dash.section("Span of control"))
        body.append(dash.data_table(
            ["Measure", "Value"],
            [["People-managers", s["managers"]], ["Mean span", s["mean"]], ["Median span", s["median"]],
             ["Max span", s["max"]], ["Sub-scale (<3 reports)", f"{o['sub_scale_pct']}%"],
             ["Overloaded", f"{o['overloaded_pct']}%"]]))

    # Net-growth bridge (reconciles to ending - beginning)
    if r["net_headcount_growth"]["status"] == "ok":
        e = r["net_headcount_growth"]["extras"]
        body.append(dash.section("Headcount bridge · trailing 12 months"))
        body.append(dash.data_table(
            ["Component", "Count"],
            [["Beginning", e["beginning"]], ["+ Hires", e["hires"]],
             ["− Voluntary exits", e["voluntary_exits"]], ["− Involuntary exits", e["involuntary_exits"]],
             ["= Ending", e["ending"]]]))

    # Representation by level
    if r["representation_by_level"]["status"] == "ok":
        rep = r["representation_by_level"]["value"]
        groups = sorted({g for shares in rep.values() for g in shares})
        headers = ["Level"] + [f"Group {g} %" for g in groups]
        rows = [[lvl] + [f"{rep[lvl].get(g, 0)}%" for g in groups] for lvl in sorted(rep)]
        body.append(dash.section("Representation by level (synthetic groups)"))
        body.append(dash.data_table(headers, rows))

    # Leadership diversity
    if r["leadership_diversity"]["status"] == "ok":
        lead = r["leadership_diversity"]["value"]
        body.append(dash.section("Leadership diversity (people-managers)"))
        body.append(dash.kpi_cards([{"value": f"{v}%", "label": f"Group {g}"} for g, v in sorted(lead.items())]))

    body.append(dash.data_pending_block(report["pending"]))
    body.append(dash.metric_definitions(_registry(), report["ok_ids"]))
    body.append(dash.governance_footer(AGENT))
    return dash.page(f"{COMPANY} — Workforce Report", "".join(body))


def _registry():
    from core.metrics import MetricRegistry
    return MetricRegistry.load()


def render_digest(report):
    r = report["results"]
    lines = [f"# {COMPANY} — Workforce digest", f"_As of {AS_OF} · draft for review_", "",
             f"- {report['narrative']}"]
    if r["headcount"]["status"] == "ok":
        e = r["headcount"]["extras"]
        lines.append(f"- **{r['headcount']['value']}** employees ({e['active']} active, {e['on_leave']} on leave); "
                     f"**{r['fte']['value']}** FTE.")
    if r["span_of_control"]["status"] == "ok":
        lines.append(f"- Avg span **{r['span_of_control']['extras']['mean']}** across "
                     f"{r['span_of_control']['extras']['managers']} managers; "
                     f"**{r['span_outlier_rate']['value']}%** are span outliers.")
    if report["pending"]:
        lines.append(f"- Coverage: {len(report['pending'])} registry metrics are defined but not yet "
                     f"instrumented in the synthetic foundation (shown honestly, never estimated).")
    lines += ["", "_Numbers computed by the shared metric engine and cited from metrics.registry.json._",
              "", "_Publish gate: a human (People Analytics) must approve before this is distributed._"]
    return "\n".join(lines) + "\n"


# ---------- fail-closed + entrypoint ----------

def _fail_closed(message) -> int:
    for p in portfolio_ev.managed_outputs(REPORT, DIGEST):
        try:
            if p.exists():
                p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            pass
    (OUT / "PUBLISHED.json").unlink(missing_ok=True)   # a FAILED run must not leave a prior "published" flag
    print(f"FAIL CLOSED: {_one_line(message)}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp headcount reporting agent (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    # Validate the RAW approver: any control character (newline/tab/CR) is rejected outright, and the
    # name must fully match the charset (re.fullmatch — no trailing-newline bypass).
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
        html_doc, digest_doc, report_evidence, digest_evidence = portfolio_ev.prepare_pair(
            AGENT, report, html_doc, digest_doc, REPO)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:  # missing dataset, invalid registry, etc.
        return _fail_closed(f"compute engine unavailable: {exc}")

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)   # remove any stale approval BEFORE writing — a draft or a
    #                                  # failed run must never inherit a prior run's "published" flag
    try:
        OUT.mkdir(exist_ok=True)
        for p in portfolio_ev.managed_outputs(REPORT, DIGEST):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        portfolio_ev.write_sidecars(REPORT, DIGEST, report_evidence, digest_evidence)
        # The approval record is part of the same all-or-nothing transaction — if it can't be
        # written, the publish fails closed (no false "approved" with no record).
        if args.publish:
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

    k = report["results"]
    print(f"{COMPANY} workforce report — as of {AS_OF}")
    print(f"  computed: {len(report['ok_ids'])} metrics | data_pending: {len(report['pending'])} | "
          f"headcount: {k['headcount']['value']}")
    print("  wrote report.sample.html and day1-digest.sample.md")

    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (People Analytics) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
