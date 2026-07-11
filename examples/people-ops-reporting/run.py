#!/usr/bin/env python3
"""Acme Corp — People Ops Service Desk reporting agent (Agentic PeopleOS Analytics arm).

Presentation + governance over the shared compute engine. The dashboard leads with the People Ops
SERVICE-DESK metrics (case volume, SLA attainment, time-to-resolution, reopen/FCR/CSAT, backlog) —
all computed by `foundation/compute/engine.py`. The broader ISO 30414 areas routed here (Health &
Safety, Compliance & Ethics, Learning & Development, self-service deflection) are shown HONESTLY in a
per-domain coverage section — their source tables aren't modeled in the synthetic foundation yet, so
they are listed, never estimated.

    python3 run.py
    python3 run.py --publish --approved-by "People Ops Lead"

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
AGENT = "people-ops-reporting"
SCOPE = "publish.people_ops_report"
APPROVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}$")

# Service-desk metrics lead the dashboard; the rest are routed here but not yet instrumented.
SERVICE_IDS = ["case_volume", "sla_attainment", "time_to_resolution", "reopen_rate",
               "first_contact_resolution", "case_csat", "open_case_backlog", "self_service_deflection"]
OTHER_IDS = ["recordable_incident_rate", "lost_time_injury_rate", "absence_rate",
             "grievance_rate", "disciplinary_action_rate", "ethics_hotline_cases",
             "training_hours_per_fte", "training_completion_rate", "critical_skill_coverage"]
METRIC_IDS = SERVICE_IDS + OTHER_IDS

DOMAIN_TITLES = {"people_ops": "People Ops", "health_safety": "Health & Safety",
                 "compliance_ethics": "Compliance & Ethics", "learning_development": "Learning & Development"}


class ReportError(RuntimeError):
    """Raised when the report cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_engine():
    from foundation.compute.engine import MetricEngine
    return MetricEngine()


def _registry():
    from core.metrics import MetricRegistry
    return MetricRegistry.load()


def build_report(engine):
    reg = _registry()
    results = {}
    for mid in METRIC_IDS:
        r = engine.compute(mid)
        if r["status"] not in ("ok", "data_pending"):
            raise ReportError(f"metric '{mid}' returned unexpected status '{r['status']}'")
        results[mid] = r

    # Pending grouped by registry domain (honest, organized coverage).
    pending_by_domain = {}
    for mid in METRIC_IDS:
        if results[mid]["status"] == "data_pending":
            dom = reg.get(mid)["domain"]
            pending_by_domain.setdefault(dom, []).append(
                {"name": results[mid]["name"], "needs": results[mid]["needs"]})

    def ok(mid):
        return results[mid] if results[mid]["status"] == "ok" else None

    cv, sla, ttr = ok("case_volume"), ok("sla_attainment"), ok("time_to_resolution")
    ro, fcr, csat, bk = ok("reopen_rate"), ok("first_contact_resolution"), ok("case_csat"), ok("open_case_backlog")

    cards = []
    if cv:
        cards.append({"value": cv["value"], "label": f"Cases · {cv['extras']['period_days']}d"})
    if sla:
        cards.append({"value": f"{sla['value']}%", "label": "SLA attainment",
                      "tone": "good" if sla["value"] >= 85 else ("bad" if sla["value"] < 70 else "warn")})
    if ttr:
        cards.append({"value": ttr["extras"]["p50"], "label": f"TTR p50 h (p90 {ttr['extras']['p90']})"})
    if bk:
        cards.append({"value": bk["value"], "label": f"Open backlog ({bk['extras']['breached_open']} breached)",
                      "tone": "warn" if bk["extras"]["breached_open"] else "neutral"})
    if fcr:
        cards.append({"value": f"{fcr['value']}%", "label": "First-contact resolution"})
    if csat:
        cards.append({"value": f"{csat['value']}%", "label": "CSAT (4–5)", "tone": "good"})
    if ro:
        cards.append({"value": f"{ro['value']}%", "label": "Reopen rate",
                      "tone": "warn" if ro["value"] >= 10 else "neutral"})

    return {"results": results, "pending_by_domain": pending_by_domain, "cards": cards,
            "ok_ids": [m for m in METRIC_IDS if results[m]["status"] == "ok"],
            "pending_count": sum(len(v) for v in pending_by_domain.values()),
            "narrative": _narrative(sla, bk, ttr)}


def _narrative(sla, bk, ttr):
    parts = []
    if sla:
        parts.append(f"SLA attainment is {sla['value']}% "
                     f"({sla['extras']['open_past_sla']} cases open past SLA).")
    if ttr:
        parts.append(f"Resolution time p50 {ttr['extras']['p50']}h / p90 {ttr['extras']['p90']}h (wall-clock).")
    if bk and bk["extras"].get("by_age"):
        old = bk["extras"]["by_age"].get("15d+", 0)
        parts.append(f"Backlog is aging — {old} open cases older than 15 days — the queue to clear.")
    return " ".join(parts) if parts else "People Ops service snapshot."


# ---------- rendering ----------

def render_html(report):
    r = report["results"]
    body = [dash.brand_header(),
            dash.title_block("People Operations", "People Ops Service Desk",
                             f"{COMPANY} · as of {AS_OF} · synthetic data"),
            dash.narrator(report["narrative"]),
            dash.kpi_cards(report["cards"])]

    if r["open_case_backlog"]["status"] == "ok":
        ages = r["open_case_backlog"]["extras"]["by_age"]
        mx = max(ages.values(), default=1)
        body.append(dash.section("Open-case backlog by age"))
        body.append(dash.bars([{"label": k, "value": v, "max": mx,
                                "color": "#ff4d4f" if k == "15d+" else "#1ba7ff"} for k, v in ages.items()]))

    if r["case_volume"]["status"] == "ok":
        cat = r["case_volume"]["extras"]["by_category"]
        mx = max(cat.values(), default=1)
        body.append(dash.section("Case volume by category"))
        body.append(dash.bars([{"label": k, "value": v, "max": mx} for k, v in cat.items()]))

    # Per-domain honest coverage (People Ops self-service + H&S + Compliance + L&D)
    for dom in ["people_ops", "health_safety", "compliance_ethics", "learning_development"]:
        items = report["pending_by_domain"].get(dom)
        if items:
            body.append(dash.data_pending_block(
                items, title=f"Coverage — {DOMAIN_TITLES[dom]} (not yet instrumented)"))

    body.append(dash.metric_definitions(_registry(), report["ok_ids"]))
    body.append(dash.governance_footer(AGENT))
    return dash.page(f"{COMPANY} — People Ops Service Desk", "".join(body))


def render_digest(report):
    r = report["results"]
    lines = [f"# {COMPANY} — People Ops Service Desk digest", f"_As of {AS_OF} · draft for review_", "",
             f"- {report['narrative']}",
             f"- **{r['case_volume']['value']}** cases ({r['case_volume']['extras']['per_100_fte']}/100 FTE); "
             f"SLA **{r['sla_attainment']['value']}%**; reopen **{r['reopen_rate']['value']}%**; "
             f"CSAT **{r['case_csat']['value']}%**."]
    # Name the pending domains dynamically from what the engine actually reported (so the sentence
    # can never drift from the coverage section above — incl. People Ops self-service deflection).
    names = [DOMAIN_TITLES.get(d, d) for d in sorted(report["pending_by_domain"])]
    if not names:
        lines.append("- Coverage: every routed People Ops metric is instrumented (nothing data_pending).")
    else:
        if len(names) > 1:
            domains_phrase = ", ".join(names[:-1]) + (", and " if len(names) > 2 else " and ") + names[-1]
        else:
            domains_phrase = names[0]
        lines.append(f"- Coverage: {report['pending_count']} metrics across {domains_phrase} "
                     f"are defined but not yet instrumented (shown honestly).")
    lines += ["", "_Numbers computed by the shared metric engine and cited from metrics.registry.json._",
              "", "_Publish gate: a human (People Ops) must approve before this is distributed._"]
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
    ap = argparse.ArgumentParser(description="Acme Corp People Ops Service Desk reporting agent (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver) or not APPROVER_RE.fullmatch(approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (People Ops).\n"
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
    except Exception as exc:
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
    print(f"{COMPANY} People Ops Service Desk — as of {AS_OF}")
    print(f"  computed: {len(report['ok_ids'])} metrics | data_pending: {report['pending_count']} | "
          f"SLA: {r['sla_attainment']['value']}%")
    print("  wrote report.sample.html and day1-digest.sample.md")

    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (People Ops) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
