#!/usr/bin/env python3
"""Evals for the event ledger. Run: python3 core/tests/test_event_log.py

Each check answers 'what bad thing did this prevent?'
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import event_log  # noqa: E402
from core.event_log import EventLog, validate_log, LedgerError  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def actor(kind="agent", id="agent.x", role="reporter"):
    return {"id": id, "display": id, "kind": kind, "role": role}


def fresh():
    return Path(tempfile.mkdtemp()) / "events.jsonl"


# --- happy chain: request -> recommendation -> approval -> action -----------
p = fresh()
log = EventLog(p)
log.append({"ts": "2026-01-15T09:00:00Z", "actor": actor(id="agent.coordinator"),
            "channel": "people-analytics", "type": "request", "case_ref": "C1",
            "correlation_id": "C1", "payload": {"ask": "weekly report"}})
rec = log.append({"ts": "2026-01-15T09:01:00Z", "actor": actor(id="agent.ta-reporting"),
                  "channel": "people-analytics", "type": "recommendation", "case_ref": "C1",
                  "correlation_id": "C1", "requires_approval": True, "scope": "publish.ta_report",
                  "payload": {"report": "draft"}})
appr = log.append({"ts": "2026-01-15T09:05:00Z", "actor": actor("human", "hr.business-partner", "hr_approver"),
                   "channel": "people-analytics", "type": "approval", "case_ref": "C1",
                   "correlation_id": "C1", "scope": "publish.ta_report", "causation_id": rec["event_id"],
                   "idempotency_key": "react:dana:msg2:white_check_mark",
                   "approval": {"decision": "approved", "entitled": True, "scope": "publish.ta_report",
                                "by": "hr.business-partner"}, "payload": {}})
log.append({"ts": "2026-01-15T09:06:00Z", "actor": actor(id="agent.ta-reporting"),
            "channel": "people-analytics", "type": "action", "case_ref": "C1",
            "correlation_id": "C1", "gated": True, "scope": "publish.ta_report",
            "causation_id": appr["event_id"], "payload": {"published": True}})
ok(validate_log(p) == [], "valid chain passes")
ok(len(log.events()) == 4, "four events recorded")
ok(log.events()[0]["prev_hash"] == event_log.GENESIS, "first event links to GENESIS")

# --- idempotency: a re-processed reaction does not double-write -------------
before = len(log.events())
dup = log.append({"ts": "2026-01-15T09:05:00Z", "actor": actor("human", "hr.business-partner", "hr_approver"),
                  "channel": "people-analytics", "type": "approval", "case_ref": "C1",
                  "correlation_id": "C1", "idempotency_key": "react:dana:msg2:white_check_mark",
                  "approval": {"decision": "approved", "entitled": True, "by": "hr.business-partner"},
                  "payload": {}})
ok(len(log.events()) == before, "duplicate idempotency_key is a no-op (exactly-once)")
ok(validate_log(p) == [], "log still valid after idempotent re-append")

# --- tamper detection: editing a committed line is caught ------------------
p2 = fresh()
log2 = EventLog(p2)
log2.append({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi", "payload": {"v": 1}})
log2.append({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi", "payload": {"v": 2}})
raw = p2.read_text().replace('"v":1', '"v":999')
p2.write_text(raw)
ok(any("TAMPER" in v for v in validate_log(p2)), "content tampering is detected by the hash chain")

# --- gap / broken chain: dropping a line is caught -------------------------
p3 = fresh()
log3 = EventLog(p3)
for i in range(3):
    log3.append({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi", "payload": {"i": i}})
lines = p3.read_text().splitlines()
p3.write_text("\n".join([lines[0], lines[2]]) + "\n")  # drop the middle event
ok(validate_log(p3), "a removed event breaks the chain / sequence")

# --- decision laundering: an action with no entitled approval is caught -----
p4 = fresh()
log4 = EventLog(p4)
r = log4.append({"ts": "t", "actor": actor(), "channel": "c", "type": "recommendation",
                 "case_ref": "C9", "correlation_id": "C9", "requires_approval": True, "payload": {}})
log4.append({"ts": "t", "actor": actor(), "channel": "c", "type": "action", "case_ref": "C9",
             "correlation_id": "C9", "gated": True, "scope": "publish.ta_report", "payload": {"published": True}})
ok(any("laundered" in v for v in validate_log(p4)), "gated action without approval is flagged (no decision laundering)")

# --- non-entitled approval is recorded but never counts as authorization ----
p5 = fresh()
log5 = EventLog(p5)
rr = log5.append({"ts": "t", "actor": actor(), "channel": "c", "type": "recommendation",
                  "case_ref": "C5", "correlation_id": "C5", "requires_approval": True, "payload": {}})
log5.append({"ts": "t", "actor": actor("human", "obs.engineering", "viewer"), "channel": "c",
             "type": "approval", "case_ref": "C5", "correlation_id": "C5",
             "approval": {"decision": "approved", "entitled": False, "by": "obs.engineering"}, "payload": {}})
log5.append({"ts": "t", "actor": actor(), "channel": "c", "type": "action", "case_ref": "C5",
             "correlation_id": "C5", "gated": True, "scope": "publish.ta_report", "payload": {}})
v5 = validate_log(p5)
ok(any("non-entitled" in v for v in v5), "approval by a non-entitled actor is flagged")
ok(any("laundered" in v for v in v5), "an action riding a non-entitled approval is still laundered")

# --- fail closed on malformed input ---------------------------------------
p6 = fresh()
log6 = EventLog(p6)
try:
    log6.append({"actor": actor(), "channel": "c", "type": "fyi", "payload": {}})  # missing ts
    ok(False, "missing required field should raise")
except LedgerError:
    ok(True, "missing required field fails closed")
try:
    log6.append({"ts": "t", "actor": actor(), "channel": "c", "type": "nope", "payload": {}})
    ok(False, "bad type should raise")
except LedgerError:
    ok(True, "unknown event type fails closed")
# Writer is at least as strict as the replay validator (caught at append, not just on replay).
try:
    log6.append({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi", "payload": "oops"})
    ok(False, "non-object payload should raise at append")
except LedgerError:
    ok(True, "non-object payload fails closed at append")
try:
    log6.append({"ts": "t", "actor": actor("human", "hr.business-partner", "hr_approver"), "channel": "c",
                 "type": "approval", "approval": {"decision": "maybe", "entitled": "yes"}, "payload": {}})
    ok(False, "malformed approval should raise at append")
except LedgerError:
    ok(True, "malformed approval shape fails closed at append")
# Heuristic PII backstop: the ledger refuses a direct identifier in any field.
try:
    # notallowed.test is a reserved, non-allowlisted domain — flagged by the scanner, but not a
    # real address, so the public-safety scan over the repo stays clean.
    log6.append({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi",
                 "payload": {"note": "email jane.doe@notallowed.test"}})
    ok(False, "PII payload should raise at append")
except LedgerError:
    ok(True, "PII in a payload is refused at append (heuristic backstop)")

# === Adversarial regressions — each is an exploit the validator must catch ===
import json as _json  # noqa: E402
from core.approval_registry import ApprovalRegistry, ACME  # noqa: E402

reg = ApprovalRegistry(ACME)


def _ev(t, **kw):
    base = {"ts": "t", "actor": actor(), "channel": "people-analytics", "type": t,
            "case_ref": "X", "correlation_id": "X", "payload": {}}
    base.update(kw)
    return base


# (1) Forged approval: a bot logs entitled:true. Consistency-only misses it; the registry catches it.
pf = fresh(); lf = EventLog(pf)
rf = lf.append(_ev("recommendation", actor=actor(id="agent.coordinator"),
                   requires_approval=True, scope="publish.ta_report"))
af = lf.append(_ev("approval", actor=actor(id="agent.ta-reporting"), scope="publish.ta_report",
                   causation_id=rf["event_id"],
                   approval={"decision": "approved", "entitled": True, "by": "agent.ta-reporting",
                             "scope": "publish.ta_report"}))
lf.append(_ev("action", actor=actor(id="agent.ta-reporting"), gated=True, scope="publish.ta_report",
              causation_id=af["event_id"], payload={"published": True}))
ok(validate_log(pf) == [], "forged approval passes CONSISTENCY-only checks (the gap a registry closes)")
ok(any("FORGED" in v for v in validate_log(pf, registry=reg)),
   "forged approval is caught once the registry re-verifies entitlement")

# (2) Scope confusion: approval for one scope, action under another.
ps = fresh(); ls = EventLog(ps)
rs = ls.append(_ev("recommendation", requires_approval=True, scope="publish.ta_report"))
aps = ls.append(_ev("approval", actor=actor("human", "hr.business-partner", "hr_approver"),
                    scope="publish.ta_report", causation_id=rs["event_id"],
                    approval={"decision": "approved", "entitled": True, "by": "hr.business-partner",
                              "scope": "publish.ta_report"}))
ls.append(_ev("action", gated=True, scope="publish.comp_summary", causation_id=aps["event_id"],
              payload={"published": True}))
ok(any("scope" in v for v in validate_log(ps, registry=reg)),
   "an action under a different scope than was approved is caught")

# (3) HMAC: a wholesale rewrite (recomputed hashes) is caught when the ledger is signed.
KEY = b"demo-signing-key"
ph = fresh(); lh = EventLog(ph, secret=KEY)
lh.append(_ev("fyi", payload={"v": 1})); lh.append(_ev("fyi", payload={"v": 2}))
ok(validate_log(ph, secret=KEY) == [], "signed ledger validates with the key")
lines = ph.read_text().splitlines()
ev = _json.loads(lines[-1]); ev["payload"] = {"v": 999}
for f in ("event_id", "event_hash", "hmac"):
    ev.pop(f, None)
ev["event_id"] = event_log._sha(event_log.canonical(ev))[:16]
ev["event_hash"] = event_log._sha(event_log.canonical(ev))
ev["hmac"] = event_log._hmac(b"attacker-key", ev["event_hash"])  # attacker lacks the real key
lines[-1] = event_log.canonical(ev); ph.write_text("\n".join(lines) + "\n")
ok(any("HMAC" in v for v in validate_log(ph, secret=KEY)),
   "a wholesale rewrite is caught by the HMAC signature")

# (4) Duplicate JSON keys are rejected.
pd = fresh()
pd.write_text('{"type":"fyi","type":"action","ts":"t","actor":{"id":"a","display":"A","kind":"agent","role":"r"},'
              '"channel":"c","payload":{},"schema_version":"1.0","sequence":0,'
              f'"prev_hash":"{event_log.GENESIS}","event_id":"x","event_hash":"y"}}\n')
ok(any("duplicate JSON key" in v for v in validate_log(pd)), "a line with duplicate JSON keys is rejected")

# (5) Non-canonical encoding is flagged.
pn = fresh(); pn.write_text('{"a": 1}\n')
ok(any("non-canonical" in v for v in validate_log(pn)), "a non-canonical line is flagged")

# (6) event_id must be content-addressed.
pe = fresh(); le = EventLog(pe); le.append(_ev("fyi", payload={"v": 1}))
one = _json.loads(pe.read_text().strip()); one["event_id"] = "deadbeefdeadbeef"
pe.write_text(event_log.canonical(one) + "\n")
ok(any("content-addressed" in v for v in validate_log(pe)), "a non-content-addressed event_id is caught")

# (7) Validator is at least as strict as the writer (missing required field caught).
pm = fresh()
bad = {"ts": "t", "channel": "c", "type": "fyi", "schema_version": "1.0", "sequence": 0,
       "prev_hash": event_log.GENESIS, "event_id": "x", "event_hash": "y"}  # no actor, no payload
pm.write_text(event_log.canonical(bad) + "\n")
ok(any("missing field" in v for v in validate_log(pm)), "validator catches missing required fields")


# Helper: write one already-stamped event as a valid single-line ledger, then mutate a field
# and re-stamp id/hash so the chain stays intact but the field is what we want to test.
def _restamped(ev):
    pp = fresh()
    e = dict(ev)
    for f in ("event_id", "event_hash", "hmac"):
        e.pop(f, None)
    e.setdefault("schema_version", "1.0")
    e.setdefault("sequence", 0)
    e.setdefault("prev_hash", event_log.GENESIS)
    e["event_id"] = event_log._sha(event_log.canonical(e))[:32]
    e["event_hash"] = event_log._sha(event_log.canonical(e))
    pp.write_text(event_log.canonical(e) + "\n")
    return pp


# (8) Validator is as strict as the WRITER on actor.kind — a forged 'bot' kind is caught on replay.
pk = _restamped({"ts": "t", "actor": {"id": "a", "display": "A", "kind": "bot", "role": "r"},
                 "channel": "c", "type": "fyi", "payload": {}})
ok(any("actor.kind" in v for v in validate_log(pk)), "validator rejects actor.kind not in {agent,human}")

# (9) payload must be an object on replay (not just at append).
pp_ = _restamped({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi", "payload": "oops"})
ok(any("payload must be an object" in v for v in validate_log(pp_)), "validator rejects non-object payload")

# (10) malformed approval shape is caught on replay.
pa = _restamped({"ts": "t", "actor": actor("human", "hr.business-partner", "hr_approver"),
                 "channel": "people-analytics", "type": "approval", "case_ref": "X", "correlation_id": "X",
                 "approval": {"decision": "maybe", "entitled": "yes"}, "payload": {}})
va = validate_log(pa)
ok(any("approval.decision" in v for v in va), "validator rejects a bad approval.decision")
ok(any("approval.entitled" in v for v in va), "validator rejects a non-boolean approval.entitled")

# (11) ACL re-verification: an approval from a NON-channel-member is caught against the registry.
# obs.engineering is a member of people-analytics; use a channel it is NOT in to trigger the ACL check.
pacl = fresh(); lacl = EventLog(pacl)
racl = lacl.append(_ev("recommendation", channel="secret-room", requires_approval=True,
                       scope="publish.ta_report"))
lacl.append(_ev("approval", channel="secret-room",
                actor=actor("human", "obs.engineering", "viewer"), scope="publish.ta_report",
                causation_id=racl["event_id"],
                approval={"decision": "approved", "entitled": True, "by": "obs.engineering",
                          "scope": "publish.ta_report"}))
ok(any("ACL re-verification" in v for v in validate_log(pacl, registry=reg)),
   "an approval reaction from a non-channel-member is caught by ACL re-verification")

# (12) A committed/imported ledger carrying PII fails validation too (not just append-time).
ppii = _restamped({"ts": "t", "actor": actor(), "channel": "c", "type": "fyi",
                   "payload": {"note": "contact x@notallowed.test"}})
ok(any("likely PII" in v for v in validate_log(ppii)),
   "validate_log catches PII in a committed/imported ledger row")


# Helpers for the scope/policy/version regressions — real ACME members, in the real channel.
def _m(aid):
    a = dict(reg.actors[aid]); a["id"] = aid
    return a


CH = "people-analytics"
RV = reg.version()


def _chain(rec_scope, appr_scope, action_scope, *, gated=True, rv=RV, with_action=True):
    p = fresh(); lg = EventLog(p)
    rec = lg.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "recommendation",
                     "case_ref": "K", "correlation_id": "K", "requires_approval": True,
                     "scope": rec_scope, "payload": {}})
    ap = lg.append({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
                    "case_ref": "K", "correlation_id": "K", "scope": appr_scope,
                    "causation_id": rec["event_id"],
                    "approval": {"decision": "approved", "entitled": True, "by": "hr.business-partner",
                                 "scope": appr_scope, "registry_version": rv}, "payload": {}})
    if with_action:
        a = {"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "action",
             "case_ref": "K", "correlation_id": "K", "scope": action_scope,
             "causation_id": ap["event_id"], "payload": {"published": True}}
        if gated:
            a["gated"] = True
        lg.append(a)
    return p


# (13) CRITICAL: an approval/action cannot pivot to a different (even if also-entitled) scope.
v13 = validate_log(_chain("publish.ta_report", "publish.comp_summary", "publish.comp_summary"), registry=reg)
ok(any("scope pivot" in v for v in v13), "approval cannot pivot scope away from the recommendation")
ok(any("laundered" in v for v in v13), "the pivoted action does not ride a valid approval (laundered)")
# the matched-scope chain is clean
ok(validate_log(_chain("publish.ta_report", "publish.ta_report", "publish.ta_report"), registry=reg) == [],
   "a scope-consistent recommendation->approval->action validates clean")

# (14) HIGH: a scoped action is gated by POLICY, not the caller's `gated` flag.
pg = fresh(); lgg = EventLog(pg)
recg = lgg.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "recommendation",
                   "case_ref": "G", "correlation_id": "G", "requires_approval": True,
                   "scope": "publish.ta_report", "payload": {}})
lgg.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "action",
            "case_ref": "G", "correlation_id": "G", "scope": "publish.ta_report",  # NOTE: no gated flag
            "causation_id": recg["event_id"], "payload": {"published": True}})
ok(any("laundered" in v for v in validate_log(pg, registry=reg)),
   "a scoped action with no approval is laundered even without the gated flag")

# (15) HIGH: an approval stamped with a different registry version is a point-in-time mismatch.
ok(any("point-in-time mismatch" in v for v in
       validate_log(_chain("publish.ta_report", "publish.ta_report", "publish.ta_report", rv="deadbeefcafe"),
                    registry=reg)),
   "an approval made under a different registry version is flagged")

# (16) HIGH: per-event ACL catches an unknown/non-member actor on a NON-approval event.
pu = _restamped({"ts": "t", "actor": {"id": "ghost", "display": "Ghost", "kind": "agent", "role": "x"},
                 "channel": CH, "type": "fyi", "case_ref": "U", "correlation_id": "U", "payload": {}})
ok(any("ACL re-verification" in v for v in validate_log(pu, registry=reg)),
   "a non-member actor on any event is caught by per-event ACL")

# (17) CRITICAL (round 6): an action with NEITHER scope NOR gated must NOT slip through. The writer
# refuses it; a hand-crafted (restamped) ledger that smuggles it is caught on replay.
try:
    fresh_lg = EventLog(fresh())
    fresh_lg.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "action",
                     "case_ref": "S", "correlation_id": "S", "payload": {"published": True}})  # no scope
    ok(False, "writer should reject a scopeless action")
except LedgerError:
    ok(True, "writer refuses an action with no scope")
ps2 = fresh(); lps2 = EventLog(ps2)
recps2 = lps2.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "recommendation",
                      "case_ref": "S", "correlation_id": "S", "requires_approval": True,
                      "scope": "publish.ta_report", "payload": {}})
# restamp a scopeless, ungated action onto the same correlation (bypass the writer)
import json as _j2  # noqa: E402
scopeless = {"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "action",
             "case_ref": "S", "correlation_id": "S", "causation_id": recps2["event_id"],
             "payload": {"published": True}, "schema_version": "1.0", "sequence": 1,
             "prev_hash": recps2["event_hash"]}
scopeless["event_id"] = event_log._sha(event_log.canonical(scopeless))[:32]
scopeless["event_hash"] = event_log._sha(event_log.canonical(scopeless))
with open(ps2, "a", encoding="utf-8") as fh:
    fh.write(event_log.canonical(scopeless) + "\n")
v17 = validate_log(ps2, registry=reg)
ok(any("action missing scope" in v for v in v17), "a scopeless action is flagged on replay")
ok(any("laundered" in v for v in v17), "a scopeless, unapproved action is still laundered (not invisible)")

# (18) HIGH (round 6): with a registry, an approval MUST carry registry_version (missing => fail).
nov = validate_log(_chain("publish.ta_report", "publish.ta_report", "publish.ta_report", rv=None), registry=reg)
ok(any("missing registry_version" in v for v in nov),
   "an approval with no registry_version fails registry-backed validation")

# (19) HIGH (round 6): a known actor cannot spoof a richer kind/role/display than the registry holds.
spoof = _restamped({"ts": "t", "actor": {"id": "agent.ta-reporting", "display": "People Business Partner",
                                          "kind": "human", "role": "hr_approver"},
                    "channel": CH, "type": "fyi", "case_ref": "SP", "correlation_id": "SP", "payload": {}})
ok(any("spoofed identity" in v for v in validate_log(spoof, registry=reg)),
   "an event spoofing a richer kind/role/display than the registry is caught")

# (20) HIGH (round 8): DENIED approvals are registry-verified too (not just approved ones).
# A denied approval missing registry_version, or logging entitled:true when the registry says false,
# is caught — even though a denial authorizes nothing.
den_norv = _restamped({"ts": "t", "actor": _m("obs.engineering"), "channel": CH, "type": "approval",
                       "case_ref": "D1", "correlation_id": "D1", "scope": "publish.ta_report",
                       "approval": {"decision": "denied", "entitled": False, "by": "obs.engineering",
                                    "scope": "publish.ta_report"}, "payload": {}})
ok(any("registry_version" in v for v in validate_log(den_norv, registry=reg)),
   "a denied approval missing registry_version is flagged")
den_spoof = _restamped({"ts": "t", "actor": _m("obs.engineering"), "channel": CH, "type": "approval",
                        "case_ref": "D2", "correlation_id": "D2", "scope": "publish.ta_report",
                        "approval": {"decision": "denied", "entitled": True, "by": "obs.engineering",
                                     "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
ok(any("disagrees" in v for v in validate_log(den_spoof, registry=reg)),
   "a denied approval logging entitled:true (registry says false) is flagged")


def _stamp(e, seq, prev):
    """Hash-stamp one raw event onto a chain (bypasses the writer's input guards)."""
    e = dict(e)
    for f in ("event_id", "event_hash", "hmac"):
        e.pop(f, None)
    e["schema_version"] = "1.0"
    e["sequence"] = seq
    e["prev_hash"] = prev
    e["event_id"] = event_log._sha(event_log.canonical(e))[:32]
    e["event_hash"] = event_log._sha(event_log.canonical(e))
    return e


# (21) CRITICAL (round 9): an EMPTY channel must not bypass ACL/identity re-verification. A fully
# hash-bound forged chain with channel:"" previously validated clean against the registry.
rec21 = _stamp({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": "", "type": "recommendation",
                "case_ref": "E", "correlation_id": "E", "requires_approval": True,
                "scope": "publish.ta_report", "payload": {}}, 0, event_log.GENESIS)
ap21 = _stamp({"ts": "t", "actor": _m("hr.business-partner"), "channel": "", "type": "approval",
               "case_ref": "E", "correlation_id": "E", "scope": "publish.ta_report",
               "causation_id": rec21["event_id"],
               "approval": {"decision": "approved", "entitled": True, "by": "hr.business-partner",
                            "scope": "publish.ta_report", "registry_version": RV}, "payload": {}},
              1, rec21["event_hash"])
act21 = _stamp({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": "", "type": "action",
                "case_ref": "E", "correlation_id": "E", "scope": "publish.ta_report", "gated": True,
                "causation_id": ap21["event_id"], "payload": {"published": True}}, 2, ap21["event_hash"])
p21 = fresh(); p21.write_text("\n".join(event_log.canonical(e) for e in (rec21, ap21, act21)) + "\n")
ok(any("channel must be a non-empty string" in v for v in validate_log(p21, registry=reg)),
   "an empty channel is flagged on replay (no ACL/identity bypass)")
try:
    EventLog(fresh()).append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": "",
                              "type": "fyi", "payload": {}})
    ok(False, "writer should reject an empty channel")
except LedgerError:
    ok(True, "writer refuses an empty channel")

# (22) HIGH (round 9): latest decision wins. A denial revokes a standing approval, so a later action
# riding the earlier "approved" is laundered; and denied-then-approved leaves a valid approval.
p22 = fresh(); l22 = EventLog(p22)
rec22 = l22.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "recommendation",
                    "case_ref": "L", "correlation_id": "L", "requires_approval": True,
                    "scope": "publish.ta_report", "payload": {}})
ap22 = l22.append({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
                   "case_ref": "L", "correlation_id": "L", "scope": "publish.ta_report",
                   "causation_id": rec22["event_id"],
                   "approval": {"decision": "approved", "entitled": True, "by": "hr.business-partner",
                                "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
l22.append({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
            "case_ref": "L", "correlation_id": "L", "scope": "publish.ta_report",
            "causation_id": rec22["event_id"],
            "approval": {"decision": "denied", "entitled": True, "by": "hr.business-partner",
                         "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
l22.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "action",
            "case_ref": "L", "correlation_id": "L", "scope": "publish.ta_report", "gated": True,
            "causation_id": ap22["event_id"], "payload": {"published": True}})
ok(any("laundered" in v for v in validate_log(p22, registry=reg)),
   "an action riding an approval that a later denial revoked is laundered (latest decision wins)")

p22b = fresh(); l22b = EventLog(p22b)
recb = l22b.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "recommendation",
                    "case_ref": "Lb", "correlation_id": "Lb", "requires_approval": True,
                    "scope": "publish.ta_report", "payload": {}})
l22b.append({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
             "case_ref": "Lb", "correlation_id": "Lb", "scope": "publish.ta_report",
             "causation_id": recb["event_id"],
             "approval": {"decision": "denied", "entitled": True, "by": "hr.business-partner",
                          "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
apb = l22b.append({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
                   "case_ref": "Lb", "correlation_id": "Lb", "scope": "publish.ta_report",
                   "causation_id": recb["event_id"],
                   "approval": {"decision": "approved", "entitled": True, "by": "hr.business-partner",
                                "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
l22b.append({"ts": "t", "actor": _m("agent.ta-reporting"), "channel": CH, "type": "action",
             "case_ref": "Lb", "correlation_id": "Lb", "scope": "publish.ta_report", "gated": True,
             "causation_id": apb["event_id"], "payload": {"published": True}})
ok(validate_log(p22b, registry=reg) == [],
   "denied-then-approved leaves a valid standing approval (latest decision wins)")

# (23) MED (round 9): approval.by must equal the event actor id (no attribution laundering).
try:
    EventLog(fresh()).append({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
                              "case_ref": "B", "correlation_id": "B", "scope": "publish.ta_report",
                              "approval": {"decision": "approved", "entitled": True, "by": "hr.people-ops",
                                           "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
    ok(False, "writer should reject approval.by != actor")
except LedgerError:
    ok(True, "writer refuses approval.by != event actor")
by_mismatch = _restamped({"ts": "t", "actor": _m("hr.business-partner"), "channel": CH, "type": "approval",
                          "case_ref": "B", "correlation_id": "B", "scope": "publish.ta_report",
                          "approval": {"decision": "approved", "entitled": True, "by": "hr.people-ops",
                                       "scope": "publish.ta_report", "registry_version": RV}, "payload": {}})
ok(any("attribution laundering" in v for v in validate_log(by_mismatch, registry=reg)),
   "an approval.by disagreeing with the event actor is caught on replay")

# --- head-count anchor: the suffix-truncation defense the forward chain can't provide ---
from core.event_log import write_anchor, compute_anchor, verify_anchor, GENESIS  # noqa: E402

pa = fresh()
la = EventLog(pa)
for n in range(4):
    la.append({"ts": "t", "actor": actor(id="agent.coordinator"), "channel": "c",
               "type": "fyi", "case_ref": "A", "correlation_id": "A", "payload": {"n": n}})
anchor = compute_anchor(la.events())
ok(anchor["count"] == 4 and anchor["head_hash"] == la.last_hash(), "anchor records count + head hash")
ok(compute_anchor([])["head_hash"] == GENESIS, "empty-ledger anchor head is GENESIS")

# baseline: a valid ledger passes with its anchor; the chain ALONE cannot detect truncation
ok(not validate_log(pa, anchor=anchor), "valid ledger validates against its anchor")
full_lines = pa.read_text(encoding="utf-8").strip().splitlines()
pa.write_text("\n".join(full_lines[:-1]) + "\n", encoding="utf-8")   # drop the last row
ok(not validate_log(pa), "chain-only validation does NOT catch suffix truncation (the gap this closes)")
trunc = validate_log(pa, anchor=anchor)
ok(any("truncated" in v.lower() for v in trunc), "the anchor CATCHES suffix truncation (count mismatch)")

# truncating a trailing DENIAL would reinstate a revoked approval — the anchor blocks exactly that
pa.write_text("\n".join(full_lines) + "\n", encoding="utf-8")        # restore
pa.write_text("\n".join(full_lines[:2]) + "\n", encoding="utf-8")    # aggressive truncation
ok(any("ANCHOR MISMATCH" in v for v in validate_log(pa, anchor=anchor)), "deeper truncation is caught too")
pa.write_text("\n".join(full_lines) + "\n", encoding="utf-8")        # restore full

# a signed anchor: an attacker who truncates the ledger AND rewrites an (unsigned) sidecar still fails HMAC
KEY2 = b"kms-checkpoint-key"
signed = compute_anchor(la.events(), secret=KEY2)
ok("hmac" in signed, "a secret produces an HMAC-signed anchor")
ok(not verify_anchor(la.events(), signed, secret=KEY2), "signed anchor verifies with the right key")
forged = compute_anchor(la.events()[:3])                              # unsigned anchor for a 3-row prefix
ok(any("HMAC invalid" in v for v in verify_anchor(la.events()[:3], forged, secret=KEY2)),
   "under a secret, an unsigned/forged anchor is rejected before the count even matches")
ok(any("wrong key" in v or "HMAC invalid" in v
       for v in verify_anchor(la.events(), compute_anchor(la.events(), secret=b"other"), secret=KEY2)),
   "an anchor signed with the wrong key is rejected")

# a malformed anchor fails closed (never silently passes)
for bad, why in ((None, "non-dict"), ({"count": 4, "head_hash": "x"}, "missing schema_version"),
                 ({"schema_version": "1.0", "count": -1, "head_hash": "x"}, "negative count"),
                 ({"schema_version": "1.0", "count": 4, "head_hash": ""}, "empty head_hash")):
    ok(verify_anchor(la.events(), bad) != [], f"malformed anchor rejected: {why}")

# an EXTENDED ledger (rows appended past the anchor) is caught as well
la.append({"ts": "t", "actor": actor(id="agent.coordinator"), "channel": "c", "type": "fyi",
           "case_ref": "A", "correlation_id": "A", "payload": {"n": 99}})
ok(any("extended" in v for v in verify_anchor(la.events(), anchor)), "appending past the anchor is caught")

# write_anchor round-trips through a file, and validate_log accepts an anchor PATH
pw = fresh()
lw = EventLog(pw)
lw.append({"ts": "t", "actor": actor(id="agent.coordinator"), "channel": "c", "type": "fyi",
           "case_ref": "A", "correlation_id": "A", "payload": {}})
ap = write_anchor(pw)
ok(ap.exists() and not validate_log(pw, anchor=ap), "write_anchor sidecar validates by path")
ok(any("anchor not found" in v for v in validate_log(pw, anchor=pw.with_suffix(".missing"))),
   "a missing anchor file fails closed")

# a SIGNED anchor may not be silently downgraded to unsigned: verifying it WITHOUT the secret is a
# violation (else a truncating attacker who keeps a garbage `hmac` field slips past a keyless check)
signed2 = compute_anchor(lw.events(), secret=b"k")
ok(any("no secret" in v for v in verify_anchor(lw.events(), signed2)),
   "a signed anchor verified without a secret is flagged (no silent downgrade)")
ok(any("no secret" in v for v in verify_anchor(lw.events(), {**signed2, "hmac": "0" * 64})),
   "a garbage hmac field is flagged when no secret is supplied, not ignored")

# verify_anchor on a bad events PATH fails closed with a violation, not a raw exception
ok(any("cannot read ledger" in v for v in verify_anchor(pw.with_suffix(".nope"), anchor)),
   "a missing events path fails closed in verify_anchor")
bad_ev = fresh(); bad_ev.write_text("{not json\n", encoding="utf-8")
ok(verify_anchor(bad_ev, anchor) != [], "a malformed events path fails closed in verify_anchor")

# ROLLBACK is a documented limit: an OLDER but genuinely-signed anchor rubber-stamps a truncation to
# that earlier count (a genuine earlier state). The defense holds only against the CURRENT anchor.
pr = fresh(); lr = EventLog(pr, secret=b"k3")
for n in range(4):
    lr.append({"ts": "t", "actor": actor(id="agent.coordinator"), "channel": "c", "type": "fyi",
               "case_ref": "A", "correlation_id": "A", "payload": {"n": n}})
old_anchor = compute_anchor(lr.events()[:3], secret=b"k3")     # a genuine, signed count=3 checkpoint
cur_anchor = compute_anchor(lr.events(), secret=b"k3")         # the current, signed count=4 checkpoint
all_lines = pr.read_text(encoding="utf-8").strip().splitlines()
pr.write_text("\n".join(all_lines[:3]) + "\n", encoding="utf-8")   # truncate 4 -> 3
ok(not validate_log(pr, secret=b"k3", anchor=old_anchor),
   "a rolled-back OLDER signed anchor rubber-stamps a truncation (documented rollback limit)")
ok(any("truncated" in v.lower() for v in validate_log(pr, secret=b"k3", anchor=cur_anchor)),
   "the CURRENT (latest) signed anchor still catches the same truncation")

# min_count CLOSES that rollback gap: given the last-known height (from monotonic/WORM storage), an OLDER
# genuine anchor is rejected as stale even though its signature is valid and the truncated ledger matches it.
ok(any("ROLLBACK" in v for v in validate_log(pr, secret=b"k3", anchor=old_anchor, min_count=4)),
   "min_count rejects a rolled-back OLDER signed anchor (the freshness the signature can't provide)")
ok(not validate_log(pr, secret=b"k3", anchor=old_anchor, min_count=3),
   "min_count equal to the anchor's own height is not a rollback (no false positive at the known height)")
ok(any("ROLLBACK" in v for v in verify_anchor(lr.events()[:3], old_anchor, secret=b"k3", min_count=4)),
   "verify_anchor surfaces the rollback directly under min_count")
# and min_count never masks a plain truncation caught by the head-count check
_pf = fresh(); _lf = EventLog(_pf, secret=b"k3")
for n in range(4):
    _lf.append({"ts": "t", "actor": actor(id="agent.coordinator"), "channel": "c", "type": "fyi",
                "case_ref": "A", "correlation_id": "A", "payload": {"n": n}})
ok(not validate_log(_pf, secret=b"k3", anchor=compute_anchor(_lf.events(), secret=b"k3"), min_count=4),
   "a fresh full ledger + current anchor + min_count passes clean")

# the CLI wires --min-count: a rollback exits non-zero; the flag is refused where it would be a silent no-op
_env0 = {**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
import subprocess as _sp0  # noqa: E402
_ap = write_anchor(_pf)                                   # unsigned current anchor (count 4) on disk
_rc = _sp0.run([sys.executable, "-m", "core.event_log", "validate", str(_pf), "--anchor", str(_ap),
                "--min-count", "5"], capture_output=True, text=True, env=_env0)
ok(_rc.returncode == 1 and "ROLLBACK" in _rc.stderr, "CLI --min-count above the anchor height reports a rollback (rc 1)")
_rc = _sp0.run([sys.executable, "-m", "core.event_log", "validate", str(_pf), "--min-count", "4"],
               capture_output=True, text=True, env=_env0)
ok(_rc.returncode == 2, "CLI refuses --min-count without --anchor (no false rollback guard)")
for _bad in ("abc", "-3"):
    _rc = _sp0.run([sys.executable, "-m", "core.event_log", "validate", str(_pf), "--anchor", str(_ap),
                    "--min-count", _bad], capture_output=True, text=True, env=_env0)
    ok(_rc.returncode == 2, f"CLI rejects a non-natural --min-count value ({_bad})")

# the CLI rejects an unknown flag (a typo'd --anhor must not silently skip the anchor check)
import subprocess as _sp, os as _os  # noqa: E402
_env = {**_os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
_r = _sp.run([sys.executable, "-m", "core.event_log", "validate", str(pr), "--anhor", "x"],
             capture_output=True, text=True, env=_env)
ok(_r.returncode == 2 and "unknown flag" in _r.stderr, "the CLI errors on an unknown/typo'd flag")

print(f"OK — {passed} ledger checks passed.")
