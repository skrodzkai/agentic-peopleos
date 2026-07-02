#!/usr/bin/env python3
"""Acme Corp — monthly People Operating Review composer (Agentic PeopleOS Analytics arm).

The cross-domain showpiece. It composes headline KPIs from every domain via the shared compute
engine (it does no math and re-implements no agent), adds a consolidated "what needs attention" and
an honest instrumentation-coverage map, and — because this is the consequential, executive-facing
artifact — it ships behind the FULL role-scoped, ledger-backed approval gate (not the lightweight
named-approver gate the leaf agents use):

    python3 run.py                                               # draft only
    python3 run.py --publish --approved-by hr.business-partner   # entitled human → recorded + published
    python3 run.py --publish --approved-by obs.engineering       # NOT entitled → denied + escalation (refused)

The approval is recorded in a hash-chained ledger, re-verified against the approval registry
(entitlement + channel ACL + point-in-time registry version). Standard library only; deterministic.
"""
from __future__ import annotations

import argparse
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
LEDGER = OUT / "decision.sample.events.jsonl"
COMPANY = "Acme Corp"
AS_OF = "Jan 2026"
AGENT = "operating-review"
SCOPE = "publish.operating_review"
CHANNEL = "people-analytics"
CASE = "OPS-REVIEW-2026-01"
# Fixed timestamps so the decision ledger is deterministic (no wall-clock).
T = ["2026-01-31T16:00:00Z", "2026-01-31T16:05:00Z", "2026-01-31T16:06:00Z"]

# Curated executive view (NOT registry-routed — operating-review has no downstream metrics).
WORKFORCE = ["headcount", "fte", "net_headcount_growth", "span_of_control", "contingent_workforce_ratio"]
ATTRITION = ["voluntary_attrition", "regrettable_attrition", "total_turnover_rate", "twelve_month_retention"]
PEOPLE_OPS = ["case_volume", "sla_attainment", "time_to_resolution", "open_case_backlog"]
STRIPS = [("Workforce", WORKFORCE), ("Attrition & Retention", ATTRITION), ("People Ops", PEOPLE_OPS)]
OPERATING_REVIEW_METRICS = WORKFORCE + ATTRITION + PEOPLE_OPS + ["leadership_diversity"]

DOMAIN_TITLES = {"talent_acquisition": "Talent Acquisition", "headcount": "Headcount & Workforce",
                 "total_rewards": "Total Rewards", "attrition": "Attrition & Retention",
                 "performance": "Performance & Talent", "people_ops": "People Operations",
                 "health_safety": "Health & Safety", "compliance_ethics": "Compliance & Ethics",
                 "learning_development": "Learning & Development", "succession": "Succession",
                 "diversity": "Diversity", "business_linkage": "Business Linkage"}


class ReportError(RuntimeError):
    """Raised when the review cannot be produced (fail closed)."""


def _one_line(text, limit=300) -> str:
    return " ".join(str(text).split())[:limit]


def _load_engine():
    from foundation.compute.engine import MetricEngine
    return MetricEngine()


def _registry():
    from core.metrics import MetricRegistry
    return MetricRegistry.load()


def _fmt(res):
    """Format an engine value by its unit for a KPI card."""
    v, unit = res["value"], res.get("unit")
    if unit == "percent":
        return f"{v}%"
    if unit == "hours":
        return f"{res['extras'].get('p50', v)}h"
    if unit == "count" and res["metric_id"] == "net_headcount_growth":
        return f"{'+' if v >= 0 else ''}{v}"
    return str(v)


def build_report(engine):
    reg = _registry()
    results = {}
    for mid in OPERATING_REVIEW_METRICS:
        r = engine.compute(mid)
        if r["status"] != "ok":
            raise ReportError(f"operating-review headline metric '{mid}' is not computable "
                              f"(status '{r['status']}') — the review must not ship with a missing headline")
        results[mid] = r

    # Instrumentation coverage map: ok vs total per domain across the WHOLE registry.
    coverage = {}
    for dom in sorted({m["domain"] for m in reg.all()}):
        ids = [m["id"] for m in reg.by_domain(dom)]
        okc = sum(1 for mid in ids if engine.compute(mid)["status"] == "ok")
        coverage[dom] = (okc, len(ids))

    return {"results": results, "coverage": coverage, "ok_ids": list(OPERATING_REVIEW_METRICS),
            "narrative": _narrative(engine, results)}


def _narrative(engine, r):
    seg = engine.segment("voluntary_attrition", "level")
    hot = max(seg.items(), key=lambda kv: kv[1]) if seg else None
    parts = [f"Headcount {r['headcount']['value']} (net "
             f"{'+' if r['net_headcount_growth']['value'] >= 0 else ''}{r['net_headcount_growth']['value']} / 12mo)."]
    parts.append(f"Voluntary attrition {r['voluntary_attrition']['value']}%"
                 + (f", highest in {hot[0]} ({hot[1]}%)" if hot else "") + ".")
    parts.append(f"People Ops SLA {r['sla_attainment']['value']}% with "
                 f"{r['open_case_backlog']['extras']['breached_open']} cases past SLA.")
    return " ".join(parts)


# ---------- rendering ----------

def render_html(report):
    r = report["results"]
    body = [dash.brand_header(),
            dash.title_block("People Operating Review", "Monthly People Operating Review",
                             f"{COMPANY} · as of {AS_OF} · synthetic data"),
            dash.narrator(report["narrative"], label="Executive summary — what needs attention")]
    for title, ids in STRIPS:
        body.append(dash.section(title))
        body.append(dash.kpi_cards([{"value": _fmt(r[mid]), "label": r[mid]["name"]} for mid in ids]))
    # Leadership diversity (dict value)
    lead = r["leadership_diversity"]["value"]
    body.append(dash.section("Leadership diversity (people-managers)"))
    body.append(dash.kpi_cards([{"value": f"{v}%", "label": f"Group {g}"} for g, v in sorted(lead.items())]))
    # Coverage map
    body.append(dash.section("Instrumentation coverage (measured vs defined)"))
    rows = [[DOMAIN_TITLES.get(d, d), f"{ok}/{tot}", "●" * ok + "○" * (tot - ok)]
            for d, (ok, tot) in report["coverage"].items()]
    body.append(dash.data_table(["Domain", "Instrumented", "Coverage"], rows))
    body.append(dash.metric_definitions(_registry(), report["ok_ids"]))
    body.append(dash.governance_footer(AGENT))
    return dash.page(f"{COMPANY} — People Operating Review", "".join(body))


def render_digest(report):
    r = report["results"]
    instrumented = sum(ok for ok, _ in report["coverage"].values())
    total = sum(tot for _, tot in report["coverage"].values())
    lines = [f"# {COMPANY} — People Operating Review digest", f"_As of {AS_OF} · draft for review_", "",
             f"- {report['narrative']}",
             f"- Instrumentation coverage: **{instrumented}/{total}** registry metrics computed across "
             f"{len(report['coverage'])} domains (measured vs defined; the rest are honestly data_pending).",
             "", "_Composed from the shared metric engine; metrics cited from metrics.registry.json._",
             "", "_Publish gate: role-scoped, ledger-backed approval (scope publish.operating_review)._"]
    return "\n".join(lines) + "\n"


# ---------- FULL role-scoped, ledger-backed publish gate ----------

def publish_decision(approver_id, commit_fn):
    """Record the publish decision in a hash-chained ledger, adjudicated + re-verified by the
    approval registry. The gated ``action: published`` event is appended ONLY after ``commit_fn()``
    has actually written the report — so the ledger can never claim a publish that did not happen.
    An unrecognized approver id is still recorded (recommendation + escalation), never silently
    dropped. Returns (decision, published, violations); ``commit_fn`` may raise OSError on write
    failure, leaving a truthful recommendation+approval (no action) trail.
    """
    from core.event_log import EventLog, validate_log
    from core.approval_registry import ApprovalRegistry, ACME
    reg = ApprovalRegistry(ACME)

    def actor(aid):
        a = dict(reg.actors[aid]); a["id"] = aid
        return a

    OUT.mkdir(exist_ok=True)
    if LEDGER.exists():
        LEDGER.unlink()
    log = EventLog(LEDGER)
    rec = log.append({"ts": T[0], "actor": actor("agent.coordinator"), "channel": CHANNEL,
                      "type": "recommendation", "case_ref": CASE, "correlation_id": CASE,
                      "requires_approval": True, "scope": SCOPE,
                      "payload": {"ask": "publish the monthly People Operating Review"}})

    known = approver_id in reg.actors
    appr = None
    if known:
        entitled, _r = reg.can_approve(approver_id, SCOPE)
        member, _m = reg.can_react(approver_id, CHANNEL)
        decision = "approved" if (entitled and member) else "denied"
        appr = log.append({"ts": T[1], "actor": actor(approver_id), "channel": CHANNEL, "type": "approval",
                           "case_ref": CASE, "correlation_id": CASE, "scope": SCOPE,
                           "causation_id": rec["event_id"],
                           "approval": {"decision": decision, "entitled": entitled, "by": approver_id,
                                        "scope": SCOPE, "reason": "entitled channel member"
                                        if decision == "approved" else "not entitled for this scope",
                                        "registry_version": reg.version()},
                           "payload": {}})
    else:
        decision = "unknown_actor"

    if decision == "approved":
        # Side effect FIRST. If the write fails, the action event is never appended — the ledger
        # stays truthful (an approval with no published action), and the OSError propagates.
        commit_fn()
        log.append({"ts": T[2], "actor": actor("agent.coordinator"), "channel": CHANNEL, "type": "action",
                    "case_ref": CASE, "correlation_id": CASE, "gated": True, "scope": SCOPE,
                    "causation_id": appr["event_id"], "payload": {"published": True}})
    else:
        reason = ("approver id not recognized — held for an authorized approver" if not known
                  else "no entitled approval — held for an authorized approver")
        log.append({"ts": T[2], "actor": actor("agent.coordinator"), "channel": CHANNEL, "type": "escalation",
                    "case_ref": CASE, "correlation_id": CASE, "causation_id": rec["event_id"],
                    "payload": {"reason": reason}})
    return decision, decision == "approved", validate_log(LEDGER, registry=reg)


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
    ap = argparse.ArgumentParser(description="Acme Corp People Operating Review composer (example).")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None, help="approver ACTOR ID (e.g. hr.business-partner)")
    args = ap.parse_args(argv)

    # Validate the RAW approver id BEFORE any normalization: reject control characters
    # (newline/tab/CR) and require a full actor-id charset match (re.fullmatch — no trailing-newline
    # bypass that would let "hr.business-partner\n" satisfy an entitled id).
    approver = args.approved_by or ""
    if args.publish and (not approver or any(ord(c) < 32 for c in approver)
                         or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid approver ACTOR ID "
              "(e.g. hr.business-partner).\n  Re-run with:  --publish --approved-by hr.business-partner",
              file=sys.stderr)
        return 2

    try:
        engine = _load_engine()
        report = build_report(engine)
        html_doc, digest_doc = render_html(report), render_digest(report)
    except ReportError as exc:
        return _fail_closed(str(exc))
    except Exception as exc:
        return _fail_closed(f"compute engine unavailable: {exc}")

    def _commit():
        """Write the report + digest atomically. Called by the publish gate AFTER an entitled
        approval but BEFORE the ledger records the published action, so the ledger never lies."""
        OUT.mkdir(exist_ok=True)
        for p in (REPORT, DIGEST):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        try:
            _atomic_write(REPORT, html_doc)
            _atomic_write(DIGEST, digest_doc)
        except OSError:
            for p in (REPORT, DIGEST):
                try:
                    p.with_name(p.name + ".tmp").unlink()
                except OSError:
                    pass
            raise

    # Role-scoped, ledger-backed gate. The published action is recorded only after _commit() succeeds.
    decision = None
    if args.publish:
        try:
            decision, published, violations = publish_decision(approver, _commit)
        except OSError as exc:
            return _fail_closed(f"could not write output: {exc}")
        except Exception as exc:
            return _fail_closed(f"approval ledger error: {exc}")
        if violations:
            return _fail_closed(f"approval ledger failed verification: {violations[0]}")
        if not published:
            why = "unknown approver id" if decision == "unknown_actor" else "approver not entitled for this scope"
            print(f"PUBLISH GATE: refused — {why}. The attempt was recorded as an escalation in the ledger; "
                  f"nothing was distributed.", file=sys.stderr)
            return 2
    else:
        try:
            _commit()
        except OSError as exc:
            return _fail_closed(f"could not write output: {exc}")

    instrumented = sum(ok for ok, _ in report["coverage"].values())
    total = sum(tot for _, tot in report["coverage"].values())
    print(f"{COMPANY} People Operating Review — as of {AS_OF}")
    print(f"  headline metrics: {len(report['ok_ids'])} | instrumentation coverage: {instrumented}/{total}")
    print("  wrote report.sample.html and day1-digest.sample.md")
    if args.publish:
        print(f"\nPublished — approved by {approver} (entitled). Decision recorded in "
              f"decision.sample.events.jsonl (ledger-verified).")
    else:
        print("\nDRAFT only. Publishing requires a role-scoped, ledger-backed approval. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
