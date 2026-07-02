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

print(f"OK — {passed} handoff checks passed.")
