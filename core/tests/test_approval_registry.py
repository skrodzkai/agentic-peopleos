#!/usr/bin/env python3
"""Evals for the approval registry. Run: python core/tests/test_approval_registry.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.approval_registry import ApprovalRegistry, ACME  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


reg = ApprovalRegistry(ACME)

# Pool coverage: any one of several HR approvers can approve — no single point of failure.
ok(reg.can_approve("hr.business-partner", "publish.ta_report")[0], "entitled HR approver can approve")
ok(reg.can_approve("hr.people-ops", "publish.ta_report")[0], "a different pool member can also approve")
ok(len(reg.entitled_pool("publish.ta_report")) >= 2, "approver pool has coverage (vacation/illness safe)")

# Rejections — the holes a hostile reviewer probes.
ok(not reg.can_approve("obs.engineering", "publish.ta_report")[0], "non-HR human cannot approve")
ok(not reg.can_approve("agent.ta-reporting", "publish.ta_report")[0], "an agent/bot cannot approve")
ok(not reg.can_approve("human.ghost", "publish.ta_report")[0], "unknown actor cannot approve")
ok(not reg.can_approve("hr.business-partner", "publish.unknown_scope")[0], "unknown decision scope is refused")

# Channel ACL — only members may post/react.
ok(reg.can_react("hr.business-partner", "people-analytics")[0], "channel member may react")
ok(not reg.can_react("human.ghost", "people-analytics")[0], "non-member cannot react")

# Reasons are human-readable (useful in the event log / audit).
ok("lacks role" in reg.can_approve("obs.engineering", "publish.ta_report")[1], "rejection gives a reason")

print(f"OK — {passed} approval-registry checks passed.")
