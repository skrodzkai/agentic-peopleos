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
who rewrites the whole file can recompute every hash — and, on its own, it does NOT
detect SUFFIX TRUNCATION: dropping the last N rows leaves a consistent prefix that
validates clean. Two controls close those gaps: pass a `secret` to HMAC-sign each
event (detects a wholesale rewrite by anyone without the key), and take a head-count
`anchor` (checkpoint) — {count, head_hash}, itself HMAC-signable — that
validate_log(..., anchor=...) checks so a truncated (or extended, or head-rewritten)
ledger fails. Production stores that anchor on WORM / a KMS-signed checkpoint. The chat
surface is the source of truth for the *conversation*; this ledger for
*decisions/actions/approvals*; the HRIS/ATS for *data*.

CLI:
    python3 -m core.event_log validate <log.jsonl> [--registry registry.json]
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

    def anchor(self) -> dict:
        """This ledger's current head-count anchor (HMAC-signed when the log carries a secret)."""
        return compute_anchor(self._events, secret=self.secret)

    def checkpoint(self, anchor_path=None) -> Path:
        """Write this ledger's anchor sidecar (default `<log>.anchor.json`), signed with the log's
        secret if it has one. A subsequent validate_log(..., anchor=<path>) then detects truncation."""
        return write_anchor(self.path, anchor_path=anchor_path, secret=self.secret)

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

def validate_log(path, registry=None, secret: bytes = None, anchor=None) -> list:
    """Replay a ledger and return violations ([] == valid).

    If `registry` (an ApprovalRegistry) is given, approvals are re-verified against it (the
    logged `entitled` flag is never trusted). If `secret` is given, HMAC signatures are
    verified (detects a wholesale rewrite). If `anchor` is given (a checkpoint dict or a path to
    one), the ledger's length and head hash are checked against it — this is what detects SUFFIX
    TRUNCATION (deleting the last N rows), which the forward hash chain alone cannot: a truncated
    prefix is internally consistent, so nothing but an external head-count anchor catches it.
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

    if anchor is not None:
        violations.extend(verify_anchor(events, anchor, secret=secret))

    return violations


# ---------------------------------------------------------------------------
#  Head-count anchor (truncation defense)
# ---------------------------------------------------------------------------
#
# The forward hash chain proves no INTERIOR edit/insert/reorder, but a suffix-truncated ledger (drop
# the last N rows) is a consistent prefix and validates clean — so deleting a trailing "denied" that
# revoked an approval would silently reinstate it. The defense is an EXTERNAL anchor recording the
# ledger's length and head hash; production stores it on WORM / a KMS-signed checkpoint. Here it is a
# small sidecar committed alongside the ledger. Passing a `secret` HMAC-signs the anchor so an attacker
# who also rewrites the sidecar cannot forge a matching one without the key.
#
# TWO limits, honestly: (1) an UNSIGNED co-located sidecar is only a control on separate/WORM media —
# an attacker who can rewrite the ledger can rewrite the sidecar too. (2) Even a SIGNED anchor must be
# the LATEST one: verifying a truncated ledger against an OLDER but genuinely-signed anchor (rolled
# back to that earlier count+head) passes, because that IS a genuine earlier state. Defending rollback
# needs monotonic anchor storage (the append-only WORM/KMS property) — a checkpoint sequence that can
# only advance. Always verify against the current, highest-count anchor.

ANCHOR_SCHEMA_VERSION = "1.0"
_ANCHOR_HMAC_EXCLUDE = ("hmac",)


def head_state(events) -> tuple:
    """(count, head_hash) for a list of events — head_hash is GENESIS for an empty ledger."""
    count = len(events)
    head = events[-1].get("event_hash", "") if events else GENESIS
    return count, head


def compute_anchor(events, secret: bytes = None) -> dict:
    """Build a checkpoint dict {schema_version, count, head_hash[, hmac]} for `events` (a list of event
    dicts, or a ledger path). Deterministic: no wall-clock — the same ledger yields the same anchor, so a
    committed anchor is byte-stable and CI-diffable."""
    if isinstance(events, (str, Path)):
        events = _read_events(Path(events))
    count, head = head_state(events)
    anchor = {"schema_version": ANCHOR_SCHEMA_VERSION, "count": count, "head_hash": head}
    if secret is not None:
        anchor["hmac"] = _hmac(secret, canonical(anchor))    # signs {schema_version, count, head_hash}
    return anchor


def write_anchor(log_path, anchor_path=None, secret: bytes = None) -> Path:
    """Checkpoint a ledger: write its anchor sidecar (default `<log>.anchor.json`). Returns the path."""
    log_path = Path(log_path)
    anchor_path = Path(anchor_path) if anchor_path else log_path.with_suffix(log_path.suffix + ".anchor.json")
    anchor = compute_anchor(_read_events(log_path), secret=secret)
    anchor_path.write_text(json.dumps(anchor, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return anchor_path


def _read_events(path: Path) -> list:
    if not Path(path).exists():
        raise LedgerError(f"ledger not found: {path}")
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line, object_pairs_hook=_no_dup_keys))
    return out


def verify_anchor(events, anchor, secret: bytes = None) -> list:
    """Return violations ([] == the ledger matches its anchor). `anchor` may be a dict or a path. Checks
    the anchor's own shape + (if a secret is given) its HMAC first, THEN that the ledger's length and head
    hash equal the anchored ones — so a truncated (or extended, or head-rewritten) ledger is caught."""
    if isinstance(anchor, (str, Path)):
        p = Path(anchor)
        if not p.exists():
            return [f"anchor not found: {p}"]
        try:
            anchor = json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=_no_dup_keys)
        except ValueError as exc:
            return [f"anchor is not valid JSON: {exc}"]
    if not isinstance(anchor, dict):
        return ["anchor must be an object"]
    if anchor.get("schema_version") != ANCHOR_SCHEMA_VERSION:
        return [f"anchor schema_version != {ANCHOR_SCHEMA_VERSION}"]
    if not isinstance(anchor.get("count"), int) or isinstance(anchor.get("count"), bool) or anchor["count"] < 0:
        return ["anchor count must be a non-negative integer"]
    if not (isinstance(anchor.get("head_hash"), str) and anchor["head_hash"]):
        return ["anchor head_hash must be a non-empty string"]
    problems = []
    if secret is not None:
        want = _hmac(secret, canonical(_subset(anchor, _ANCHOR_HMAC_EXCLUDE)))
        if anchor.get("hmac") != want:
            problems.append("anchor HMAC invalid (anchor forged or wrong key)")
    elif "hmac" in anchor:
        # a signed anchor MUST be verified with its key — never silently downgraded to unsigned, or a
        # truncating attacker who keeps a stale/garbage hmac field would slip past a keyless validate.
        problems.append("anchor is HMAC-signed but no secret was supplied to verify it (would downgrade to unsigned)")
    try:
        count, head = head_state(events if not isinstance(events, (str, Path)) else _read_events(Path(events)))
    except (LedgerError, ValueError) as exc:                # missing/malformed events path -> fail closed
        return problems + [f"cannot read ledger to check against anchor: {exc}"]
    if count != anchor["count"]:
        problems.append(f"ANCHOR MISMATCH — ledger has {count} row(s), anchor expects {anchor['count']} "
                        f"({'truncated' if count < anchor['count'] else 'extended'})")
    if head != anchor["head_hash"]:
        problems.append("ANCHOR MISMATCH — ledger head hash does not match the anchored head")
    return problems


_USAGE = ("usage: python3 -m core.event_log validate <log.jsonl> [--registry registry.json] [--anchor anchor.json]\n"
          "       python3 -m core.event_log checkpoint <log.jsonl> [--anchor anchor.json]")


def _main(argv) -> int:
    reg_path, anchor_path, positional, i = None, None, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--registry", "--anchor"):
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                # a flag with no value must NOT be silently dropped — that would disable the check it names
                print(f"error: {a} requires a value\n{_USAGE}", file=sys.stderr)
                return 2
            if a == "--registry":
                reg_path = argv[i + 1]
            else:
                anchor_path = argv[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            # an unrecognized flag (e.g. a typo'd `--anhor`) must NOT be silently ignored — that would
            # quietly skip the check it was meant to request
            print(f"error: unknown flag {a}\n{_USAGE}", file=sys.stderr)
            return 2
        positional.append(a)
        i += 1
    args = positional
    if len(args) != 2 or args[0] not in ("validate", "checkpoint"):
        print(_USAGE, file=sys.stderr)
        return 2

    if args[0] == "checkpoint":
        try:
            written = write_anchor(args[1], anchor_path=anchor_path)   # CLI writes an UNSIGNED anchor
        except Exception as exc:                                       # missing/unreadable ledger — fail closed
            print(f"CHECKPOINT FAILED — {exc}", file=sys.stderr)
            return 1
        print(f"WROTE {written} (count + head hash; store on WORM/immutable media for truncation defense).")
        return 0

    registry = None
    if reg_path:
        from core.approval_registry import ApprovalRegistry
        try:
            registry = ApprovalRegistry.from_json(reg_path)
        except Exception as exc:  # missing/unparseable/invalid registry — fail closed, no traceback
            print(f"LEDGER INVALID — registry unavailable: {exc}", file=sys.stderr)
            return 1
    violations = validate_log(args[1], registry=registry, anchor=anchor_path)
    if violations:
        print(f"LEDGER INVALID — {len(violations)} violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    anchored = " + head-count anchor (truncation-checked)" if anchor_path else ""
    if registry:
        print(f"LEDGER OK — chain intact, no gaps/dupes/laundered approvals + approval registry re-verified{anchored}.")
    else:
        print("LEDGER OK — structural + chain checks only (DIAGNOSTIC)" + anchored + ". This does NOT verify\n"
              "approval entitlement; pass --registry <registry.json> for the full integrity check.")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
