#!/usr/bin/env python3
"""Evals for the messaging surface ACL. Run: python3 core/tests/test_messaging.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.approval_registry import ApprovalRegistry, ACME  # noqa: E402
from core.messaging import SimulatedChat            # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


reg = ApprovalRegistry(ACME)
chat = SimulatedChat("slack", registry=reg)


def actor(aid):
    a = dict(reg.actors[aid]); a["id"] = aid
    return a


# A channel member may post; the message renders in the transcript.
ref = chat.post("people-analytics", actor("agent.coordinator"), type="request", text="hi")
ok(ref and "hi" in chat.transcript("people-analytics"), "channel member may post")

# A non-member cannot post (surface enforces its own ACL, not just the orchestrator).
ghost = {"id": "human.ghost", "display": "Ghost", "kind": "human", "role": "unknown"}
try:
    chat.post("people-analytics", ghost, type="fyi", text="sneak")
    ok(False, "non-member post should raise")
except PermissionError:
    ok(True, "a non-member cannot post to the channel")

# A non-member cannot react either.
try:
    chat.react(ref, ghost, "✅")
    ok(False, "non-member react should raise")
except PermissionError:
    ok(True, "a non-member cannot react in the channel")

print(f"OK — {passed} messaging checks passed.")
