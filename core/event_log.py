#!/usr/bin/env python3
"""Append-only, hash-chained decision ledger for Agentic PeopleOS.

JSONL is the *format*; this module supplies the *integrity guarantees* that turn it
into an audit log:

- a schema version and a strict, re-validated event schema,
- a monotonic, gap-checked sequence,
- a content-addressed event id (re-derived on replay),
- an idempotency key for exactly-once processing,
- a SHA-256 hash chain (detects edits) + an optional HMAC signature (detects a
  wholesale rewrite when a key is held — see the integrity note below),
- approval re-verification against the approval registry (the logged `entitled`
  flag is never trusted), and binding of action -> approval -> recommendation by
  causation id AND matching scope (no decision laundering, no scope confusion).

Integrity note (be honest in interviews): a bare hash chain proves *internal
consistency / no in-place edit*. It does NOT prove non-repudiation — an attacker
who rewrites the whole file can recompute every hash. Pass a `secret` to sign each
event (HMAC); production anchors the head hash in a KMS-signed checkpoint and stores
the ledger on WORM/append-only media. The chat surface is the source of truth for the
*conversation*; this ledger for *decisions/actions/approvals*; the HRIS/ATS for *data*.

CLI:
    python -m core.event_log validate <log.jsonl> [--registry registry.json]
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

from core.pii import scan as pii_scan

SCHEMA_VERSION = "1.0"
GENESIS = "0" * 64

_INPUT_REQUIRED = ("ts", "actor", "channel", "type", "payload")
_ACTOR_REQUIRED = ("id", "display", "kind", "role")
EVENT_TYPES = {"request", "response", "recommendation", "approval", "action", "escalation", "fyi"}
_HASH_EXCLUDE = ("event_hash", "hmac")
_ID_EXCLUDE = ("event_id", "event_hash", "hmac")


def canonical(obj) -> str:
    # allow_nan=False: NaN/Infinity aren't valid JSON and would break cross-parser hashing.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hmac(secret: bytes, text: str) -> str:
    return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _subset(ev: dict, exclude) -> dict:
    return {k: v for k, v in ev.items() if k not in exclude}


def _no_dup_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate JSON key '{k}'")
        seen[k] = v
    return seen


class LedgerError(ValueError):
    """Raised when an event cannot be appended (fail closed — never write bad data)."""


class EventLog:
    """An append-only JSONL ledger with a verifiable hash chain (and optional HMAC)."""

    def __init__(self, path, secret: bytes = None):
        self.path = Path(path)
        self.secret = secret
        self._events = []
        self._idempotency = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                self._events.append(ev)
                if ev.get("idempotency_key"):
                    self._idempotency[ev["idempotency_key"]] = ev
        if self._events:
            # Refuse to extend a ledger that already fails its integrity checks.
            problems = validate_log(self.path, secret=self.secret)
            if problems:
                raise LedgerError(f"refusing to open a ledger that fails integrity: {problems[0]}")

    def events(self):
        return list(self._events)

    def last_hash(self):
        return self._events[-1]["event_hash"] if self._events else GENESIS

    def append(self, event: dict) -> dict:
        ev = dict(event)
        for field in _INPUT_REQUIRED:
            if field not in ev:
                raise LedgerError(f"event missing required field '{field}'")
        if ev["type"] not in EVENT_TYPES:
            raise LedgerError(f"unknown event type '{ev['type']}'")
        # Channel must be a non-empty string: an empty channel cannot be ACL-checked, and an empty
        # value would otherwise slip past the registry membership/identity re-verification on replay.
        if not (isinstance(ev.get("channel"), str) and ev["channel"].strip()):
            raise LedgerError("channel must be a non-empty string")
        actor = ev.get("actor")
        if not isinstance(actor, dict) or any(a not in actor for a in _ACTOR_REQUIRED):
            raise LedgerError(f"actor must include {list(_ACTOR_REQUIRED)}")
        if actor["kind"] not in ("agent", "human"):
            raise LedgerError(f"actor.kind must be 'agent' or 'human'")
        # The writer is at least as strict as the replay validator: payload + approval shape.
        if not isinstance(ev.get("payload"), dict):
            raise LedgerError("payload must be an object")
        appr = ev.get("approval")
        if appr is not None:
            if not isinstance(appr, dict):
                raise LedgerError("approval must be an object")
            if appr.get("decision") not in ("approved", "denied"):
                raise LedgerError("approval.decision must be 'approved' or 'denied'")
            if not isinstance(appr.get("entitled"), bool):
                raise LedgerError("approval.entitled must be a boolean")
            # The attributed approver must BE the event actor — no recording an approval under one
            # actor while crediting another (attribution laundering).
            if appr.get("by") != actor.get("id"):
                raise LedgerError("approval.by must equal the event actor id")
        # Every action is consequential — it must declare the scope it exercised.
        if ev["type"] == "action" and not ev.get("scope"):
            raise LedgerError("an action must declare a scope")
        # Heuristic PII backstop: the ledger carries pseudonymous, minimized data — refuse to
        # write a direct identifier. A backstop, not a guarantee (see core/pii.py).
        pii = pii_scan(canonical(_subset(ev, _ID_EXCLUDE)))
        if pii:
            raise LedgerError(f"refusing to write likely PII into the ledger: {pii[0]}")

        key = ev.get("idempotency_key")
        if key and key in self._idempotency:
            return self._idempotency[key]

        ev["schema_version"] = SCHEMA_VERSION
        ev["sequence"] = len(self._events)
        ev["prev_hash"] = self.last_hash()
        for f in _ID_EXCLUDE:
            ev.pop(f, None)
        ev["event_id"] = _sha(canonical(ev))[:32]
        ev["event_hash"] = _sha(canonical(ev))
        if self.secret:
            ev["hmac"] = _hmac(self.secret, ev["event_hash"])

        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(canonical(ev) + "\n")
        self._events.append(ev)
        if key:
            self._idempotency[key] = ev
        return ev


# ---------------------------------------------------------------------------
#  Validation / replay
# ---------------------------------------------------------------------------

def validate_log(path, registry=None, secret: bytes = None) -> list:
    """Replay a ledger and return violations ([] == valid).

    If `registry` (an ApprovalRegistry) is given, approvals are re-verified against it (the
    logged `entitled` flag is never trusted). If `secret` is given, HMAC signatures are
    verified (detects a wholesale rewrite).
    """
    path = Path(path)
    if not path.exists():
        return [f"ledger not found: {path}"]

    events, violations = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line, object_pairs_hook=_no_dup_keys)
        except ValueError as exc:
            return [f"line {n}: {exc}"]
        if canonical(ev) != line:
            violations.append(f"line {n}: non-canonical encoding (re-serialization differs)")
        events.append(ev)

    seen_ids, seen_idem = set(), set()
    prev_hash = GENESIS
    pending, approved = {}, {}  # correlation_id -> recommendation eid / {eid, scope}

    for i, ev in enumerate(events):
        tag = f"seq {ev.get('sequence', i)} ({ev.get('type', '?')})"

        # --- schema (validator must be at least as strict as the writer) ---
        for f in _INPUT_REQUIRED + ("schema_version", "sequence", "event_id", "prev_hash", "event_hash"):
            if f not in ev:
                violations.append(f"{tag}: missing field '{f}'")
        actor = ev.get("actor")
        if not isinstance(actor, dict) or any(a not in actor for a in _ACTOR_REQUIRED):
            violations.append(f"{tag}: actor missing required keys")
        elif actor.get("kind") not in ("agent", "human"):
            # The validator is at least as strict as the writer (append() rejects this too).
            violations.append(f"{tag}: actor.kind must be 'agent' or 'human'")
        if not isinstance(ev.get("payload"), dict):
            violations.append(f"{tag}: payload must be an object")
        appr_field = ev.get("approval")
        if appr_field is not None:
            if not isinstance(appr_field, dict):
                violations.append(f"{tag}: approval must be an object")
            else:
                if appr_field.get("decision") not in ("approved", "denied"):
                    violations.append(f"{tag}: approval.decision must be 'approved' or 'denied'")
                if not isinstance(appr_field.get("entitled"), bool):
                    violations.append(f"{tag}: approval.entitled must be a boolean")
        if ev.get("type") not in EVENT_TYPES:
            violations.append(f"{tag}: unknown event type")
        if ev.get("schema_version") != SCHEMA_VERSION:
            violations.append(f"{tag}: schema_version != {SCHEMA_VERSION}")
        # Heuristic PII backstop on replay too (not just at append) — so an imported/committed
        # ledger that contains a direct identifier fails validation, not just one written here.
        for hit in pii_scan(canonical(_subset(ev, _ID_EXCLUDE))):
            violations.append(f"{tag}: likely PII in event ({hit})")

        # --- ordering + chain + content addressing + tamper ---
        if ev.get("sequence") != i:
            violations.append(f"{tag}: sequence gap/disorder (expected {i})")
        if ev.get("prev_hash") != prev_hash:
            violations.append(f"{tag}: broken chain — prev_hash mismatch")
        if ev.get("event_id") != _sha(canonical(_subset(ev, _ID_EXCLUDE)))[:32]:
            violations.append(f"{tag}: event_id is not content-addressed")
        if ev.get("event_hash") != _sha(canonical(_subset(ev, _HASH_EXCLUDE))):
            violations.append(f"{tag}: TAMPER — event_hash does not match content")
        if secret is not None and ev.get("hmac") != _hmac(secret, ev.get("event_hash", "")):
            violations.append(f"{tag}: bad/missing HMAC signature (possible wholesale rewrite)")

        eid = ev.get("event_id")
        if eid in seen_ids:
            violations.append(f"{tag}: duplicate event_id {eid}")
        seen_ids.add(eid)
        key = ev.get("idempotency_key")
        if key:
            if key in seen_idem:
                violations.append(f"{tag}: duplicate idempotency_key '{key}' (double-processed)")
            seen_idem.add(key)
        cause = ev.get("causation_id")
        if cause and cause not in seen_ids:
            violations.append(f"{tag}: causation_id references no earlier event")

        # --- governance: approvals bind to requests (by causation AND scope); actions bind to approvals ---
        corr = ev.get("correlation_id")
        etype = ev.get("type")
        actor_id = (actor or {}).get("id")
        appr = ev.get("approval")
        appr = appr if isinstance(appr, dict) else {}  # malformed approval => {} (fail closed, no crash)

        # Every event MUST carry a non-empty channel — an empty channel can't be ACL-checked and an
        # earlier version let a forged chain slip past membership/identity re-verification (CVE-class).
        ch = ev.get("channel")
        if not (isinstance(ch, str) and ch.strip()):
            violations.append(f"{tag}: channel must be a non-empty string (ACL cannot be verified)")

        # Channel ACL + identity re-verified for EVERY event with a registry — not just approvals.
        # A non-member could not have posted/reacted, and a known actor's kind/role/display must
        # match the registry (an event cannot spoof a richer identity than the actor really holds).
        # This runs whenever a registry is present and the event has an actor — NOT gated on a
        # truthy channel (an empty channel is itself a violation above, never a free pass).
        if registry is not None and actor_id is not None:
            if isinstance(ch, str) and ch.strip() and not registry.is_member(actor_id, ch):
                violations.append(f"{tag}: actor '{actor_id}' is not a member of channel "
                                  f"'{ch}' (ACL re-verification)")
            reg_actor = registry.actors.get(actor_id)
            if reg_actor and isinstance(actor, dict):
                for attr in ("kind", "role", "display"):
                    if actor.get(attr) != reg_actor.get(attr):
                        violations.append(f"{tag}: actor '{actor_id}' {attr} '{actor.get(attr)}' does not "
                                          f"match the registry '{reg_actor.get(attr)}' (spoofed identity)")

        if etype == "recommendation" and ev.get("requires_approval"):
            pending[corr] = {"eid": eid, "scope": ev.get("scope")}
        elif etype == "approval":
            # EVERY approval event (approved AND denied) is registry-verified — version, entitlement
            # consistency, causation, and scope binding. Only an approved+entitled+bound decision is
            # recorded as authorization; a denied approval must still be well-formed, not garbage.
            decision = appr.get("decision")
            ev_scope, appr_scope = ev.get("scope"), appr.get("scope")
            scope = ev_scope or appr_scope
            if ev_scope and appr_scope and ev_scope != appr_scope:
                violations.append(f"{tag}: approval event scope '{ev_scope}' != approval.scope '{appr_scope}'")
            # The attributed approver must BE the event actor (no attribution laundering).
            if appr.get("by") != actor_id:
                violations.append(f"{tag}: approval.by '{appr.get('by')}' != event actor '{actor_id}' "
                                  f"(attribution laundering)")
            pend = pending.get(corr)
            bound = bool(pend) and cause == pend["eid"] and pend["scope"] == scope
            if not pend or cause != pend["eid"]:
                violations.append(f"{tag}: approval not bound to its recommendation (causation)")
            elif pend["scope"] != scope:
                violations.append(f"{tag}: approval scope '{scope}' != recommended scope "
                                  f"'{pend['scope']}' (scope pivot)")
            if registry is not None:
                # Point-in-time: every approval MUST carry the registry version it was evaluated under,
                # and it must match the version in force now (missing => fail; later change => mismatch).
                rv, ver = appr.get("registry_version"), getattr(registry, "version", lambda: None)()
                if not rv:
                    violations.append(f"{tag}: approval missing registry_version (point-in-time authority not provable)")
                elif ver and rv != ver:
                    violations.append(f"{tag}: approval evaluated against a different approval-registry "
                                      f"version (point-in-time mismatch)")
                entitled, _reason = registry.can_approve(actor_id, scope)
                # The logged entitled flag must match the registry — for approved AND denied alike.
                if appr.get("entitled") != entitled:
                    violations.append(f"{tag}: logged 'entitled' flag disagrees with the approval registry")
                if decision == "approved":
                    if not entitled:
                        violations.append(f"{tag}: FORGED — approval re-derives as NOT entitled ({actor_id}/{scope})")
                    if entitled and bound:
                        approved[corr] = {"eid": eid, "scope": scope}
                elif decision == "denied":
                    # Latest decision wins: a denial revokes any standing approval on this thread, so a
                    # later "denied" can't be followed by a "published" action that quietly relies on an
                    # earlier "approved".
                    approved.pop(corr, None)
            else:
                if decision == "approved":
                    if appr.get("entitled") and bound:
                        approved[corr] = {"eid": eid, "scope": scope}
                    elif not appr.get("entitled"):
                        violations.append(f"{tag}: approved by a non-entitled actor (logged)")
                elif decision == "denied":
                    approved.pop(corr, None)  # a denial supersedes a prior approval (latest wins)
        elif etype == "action":
            # EVERY action is consequential by POLICY and must declare a scope and bind to an
            # entitled, scope-matched approval — a scopeless/ungated action is invalid, not invisible.
            if not ev.get("scope"):
                violations.append(f"{tag}: action missing scope (every action must declare a scope)")
            a = approved.get(corr)
            if not a:
                violations.append(f"{tag}: ungated/laundered action — no entitled, scope-matched approval for this case")
            else:
                if cause != a["eid"]:
                    violations.append(f"{tag}: action not bound to its approval (causation)")
                if ev.get("scope") != a["scope"]:
                    violations.append(f"{tag}: action scope '{ev.get('scope')}' != approved scope '{a['scope']}'")

        prev_hash = ev.get("event_hash")

    return violations


def _main(argv) -> int:
    reg_path, positional, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--registry" and i + 1 < len(argv):
            reg_path = argv[i + 1]
            i += 2
            continue
        if not a.startswith("--"):
            positional.append(a)
        i += 1
    args = positional
    if len(args) != 2 or args[0] != "validate":
        print("usage: python -m core.event_log validate <log.jsonl> [--registry registry.json]", file=sys.stderr)
        return 2
    registry = None
    if reg_path:
        from core.approval_registry import ApprovalRegistry
        try:
            registry = ApprovalRegistry.from_json(reg_path)
        except Exception as exc:  # missing/unparseable/invalid registry — fail closed, no traceback
            print(f"LEDGER INVALID — registry unavailable: {exc}", file=sys.stderr)
            return 1
    violations = validate_log(args[1], registry=registry)
    if violations:
        print(f"LEDGER INVALID — {len(violations)} violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    if registry:
        print("LEDGER OK — chain intact, no gaps/dupes/laundered approvals + approval registry re-verified.")
    else:
        print("LEDGER OK — structural + chain checks only (DIAGNOSTIC). This does NOT verify approval\n"
              "entitlement; pass --registry <registry.json> for the full integrity check.")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
