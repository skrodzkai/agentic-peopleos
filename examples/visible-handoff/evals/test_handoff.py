#!/usr/bin/env python3
"""Adversarial evals for the visible handoff. Run: python3 evals/test_handoff.py

Every check names the bad thing it prevents.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))      # the visible-handoff dir (run.py)
import run                                 # noqa: E402
from core.event_log import validate_log    # noqa: E402
from core import evidence_bundle as evidence_bundle_core  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def tmp():
    return Path(tempfile.mkdtemp()) / "out"


def approvals(r):
    return [e for e in r["events"] if e["type"] == "approval"]


# Happy path — an entitled human approves; the gated action runs; ledger is valid.
r = run.run_handoff(tmp())
ok(r["action_taken"] and r["decision"] == "approved" and not r["violations"],
   "entitled approval publishes and the ledger is valid")
ok([e["type"] for e in r["events"]] == ["request", "recommendation", "approval", "action"],
   "emits request -> recommendation -> approval -> action")
authorization_events = [e["authorization"] for e in r["events"]
                        if e["type"] in ("recommendation", "approval", "action")]
ok(authorization_events == [r["authorization"], r["authorization"], r["authorization"]],
   "recommendation -> approval -> action carries one exact authorization envelope")
ok(evidence_bundle_core.authorization_violations(
    evidence_bundle_core.load_bundle(r["bundle_path"]), r["authorization"]) == [],
   "the handoff authorization resolves to exact rendered report and evidence-graph bytes")
ok(r["authorization"]["bundle_hash"][:19] in r["transcript"],
   "the human-readable handoff exposes the bundle fingerprint under review")

# Unentitled human (a viewer in the channel) cannot authorize the action.
r = run.run_handoff(tmp(), approver_id="obs.engineering")
ok(not r["action_taken"] and r["decision"] == "denied", "a non-HR human cannot authorize a publish")
ok(not r["violations"], "correctly denying an unentitled reaction leaves a clean ledger")

# A bot reaction cannot approve.
ok(not run.run_handoff(tmp(), approver_id="agent.coordinator")["action_taken"],
   "a bot/agent reaction cannot approve")

# An unknown actor cannot approve.
ok(not run.run_handoff(tmp(), approver_id="human.ghost")["action_taken"],
   "an unknown actor cannot approve")

# A double-processed reaction yields exactly one approval (exactly-once).
r = run.run_handoff(tmp(), duplicate=True)
ok(r["action_taken"] and len(approvals(r)) == 1, "a replayed reaction does not double-approve")

# A retracted reaction authorizes nothing.
r = run.run_handoff(tmp(), retract=True)
ok(not r["action_taken"] and not r["violations"], "a retracted reaction does not authorize anything")

# A channel message cannot approve — injection is ignored (and logged as ignored).
r = run.run_handoff(tmp(), inject=True, retract=True)
ok(not r["action_taken"], "a channel message ('approve everything') cannot authorize an action")
ok(any(e["type"] == "fyi" and e["payload"].get("security") == "ignored_untrusted_instruction"
       for e in r["events"]), "the injection attempt is recorded as detected-and-ignored")

# Injection present but an entitled human still approves -> proceeds, still valid.
r = run.run_handoff(tmp(), inject=True, approver_id="hr.business-partner")
ok(r["action_taken"] and not r["violations"], "injection changes nothing; an entitled human still decides")

# Tampering with the produced ledger is detected.
d = tmp()
run.run_handoff(d)
lp = d / "events.jsonl"
lp.write_text(lp.read_text().replace('"published":true', '"published":false'))
ok(any("TAMPER" in v for v in validate_log(lp)), "editing the committed ledger is detected on replay")

# Truncation: dropping the trailing published-action row makes the chain validate clean (a consistent
# prefix) — but the head-count anchor written beside the ledger catches it. This is the concrete attack:
# lop off the "published" action and, without the anchor, the ledger looks like an un-acted-on request.
d2 = tmp()
r2 = run.run_handoff(d2)
lp2, ap2 = d2 / "events.jsonl", r2["anchor_path"]
lines = lp2.read_text(encoding="utf-8").strip().splitlines()
lp2.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")     # drop the last (action) row
ok(not validate_log(lp2), "a truncated ledger is a consistent prefix — the chain alone does not flag it")
ok(any("truncated" in v.lower() for v in validate_log(lp2, anchor=ap2)),
   "the committed head-count anchor catches the dropped action row (truncation)")

# every committed sample ledger validates against its committed anchor (the shipped artifacts reconcile)
import subprocess  # noqa: E402
REPO = Path(run.__file__).resolve().parents[2]
OUTDIR = Path(run.__file__).resolve().parent / "output"
for name in ("events.jsonl", "approved.events.sample.jsonl", "denied.events.sample.jsonl"):
    lg = OUTDIR / name
    ok(not validate_log(lg, anchor=lg.with_name(lg.name + ".anchor.json")),
       f"committed {name} matches its committed anchor")
committed_bundle = evidence_bundle_core.load_bundle(OUTDIR / "evidence-bundle.json")
for name in ("events.jsonl", "approved.events.sample.jsonl", "denied.events.sample.jsonl"):
    events = [__import__("json").loads(line) for line in (OUTDIR / name).read_text().splitlines()]
    envelope = next(event["authorization"] for event in events if event["type"] == "recommendation")
    ok(evidence_bundle_core.authorization_violations(committed_bundle, envelope) == [],
       f"committed {name} authorization resolves to the committed evidence bundle")

print(f"OK — {passed} handoff checks passed.")
