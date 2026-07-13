# Event log — the decision ledger

The ledger ([`core/event_log.py`](../core/event_log.py)) is the source of record for
**decisions, actions, and approvals**. (Chat is the source of truth for the *conversation*;
the HRIS/ATS for employee/candidate *data*.) JSONL is the format; the guarantees below are
what make it an audit log.

## Data dictionary

One JSON object per line. **Caller fields** are supplied when an event is appended;
**ledger-assigned fields** are stamped by `EventLog.append` and must never be set by the
caller. Validation re-derives every assigned field on replay.

### Caller fields

| Field | Type | Req? | Meaning |
|---|---|---|---|
| `ts` | string (ISO-8601 UTC) | **yes** | When the event occurred. |
| `actor` | object | **yes** | Who acted — see actor sub-fields below. |
| `channel` | string | **yes** | The surface it happened on (e.g. `people-analytics`). |
| `type` | enum | **yes** | One of `request, response, recommendation, approval, action, escalation, fyi`. |
| `payload` | object | **yes** | Type-specific body. Minimized + pseudonymous by convention; a heuristic backstop in `append()` rejects obvious direct identifiers (see [data-classification](data-classification.md)). |
| `case_ref` | string | no | Human-facing case label (e.g. `TA-2026-W03`). |
| `correlation_id` | string | **governed decision events** | Groups every event in one case/thread; required on a gated recommendation, approval, and every action. |
| `causation_id` | string | no | `event_id` of the event that directly caused this one. Must reference an earlier event. |
| `idempotency_key` | string | no | De-dupes re-processed inputs (e.g. a re-delivered reaction). Repeats are no-ops, not new events. |
| `requires_approval` | bool | no | On a `recommendation`: marks it as gating a downstream action. |
| `scope` | string | no | The capability being approved/exercised (e.g. `publish.ta_report`). Bound action↔approval. |
| `gated` | bool | no | On an `action`: this action ran only because an entitled approval exists. |
| `approval` | object | no | On an `approval`: the adjudication — see approval sub-fields below. |
| `authorization` | object | **yes for every governed decision event** | Exact content-addressed evidence-bundle envelope. Required on a recommendation with `requires_approval:true`, every approval, and every action, regardless of scope spelling; copied unchanged across the decision chain. See [Evidence Graph v1](evidence-graph.md). |

**`actor` sub-fields** (all required): `id` (stable identity, e.g. `hr.business-partner`),
`display` (human label), `kind` (`agent` or `human`), `role` (e.g. `hr_approver`).

**`approval` sub-fields:** `decision` (`approved`/`denied`), `entitled` (bool — what the
*caller claimed*; **never trusted** on replay), `by` (actor id), `scope`, `reason`,
`policy_ref`.

### Ledger-assigned fields (stamped by `append`, re-derived by `validate_log`)

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Schema version (`1.2`). Validator rejects a mismatch. |
| `sequence` | int | Monotonic index, no gaps. |
| `prev_hash` | hex(64) | `event_hash` of the previous event; first links to GENESIS (`000…0`). |
| `event_id` | hex(32) | Content address — SHA-256 of the event minus id/hash/hmac, truncated. |
| `event_hash` | hex(64) | SHA-256 over the event minus hash/hmac. In-place edits break it. |
| `hmac` | hex(64) | *Optional.* Present only when the ledger is constructed with a `secret`; detects a wholesale rewrite. |

## A valid event (abridged — from the committed sample ledger)

A real `approval` row from
[`examples/visible-handoff/output/events.jsonl`](../examples/visible-handoff/output/events.jsonl).
The `prev_hash`/`event_id`/`event_hash` are truncated with `…` for readability — see the file
for the exact one-line values:

```json
{"actor":{"display":"People Business Partner","id":"hr.business-partner","kind":"human","role":"hr_approver"},
 "approval":{"by":"hr.business-partner","decision":"approved","entitled":true,
   "policy_ref":"governance/approval-registry","reason":"entitled channel member","scope":"publish.ta_report"},
 "case_ref":"TA-2026-W03","causation_id":"1e0f3ae6f53f9a7eaedf6846991bd8c9","channel":"people-analytics",
 "correlation_id":"TA-2026-W03","idempotency_key":"react:hr.business-partner:msg-2:approve",
 "authorization":{"bundle_id":"bundle.visible-handoff.ta-2026-w03","bundle_hash":"sha256:6d1e…",
   "artifacts":[{"artifact_id":"artifact.ta-reporting.digest","content_hash":"sha256:…","evidence_hash":"sha256:…"}],
   "material_claim_ids_hash":"sha256:…"},
 "payload":{},"scope":"publish.ta_report","ts":"2026-01-19T09:05:00Z","type":"approval",
 "schema_version":"1.2","sequence":2,
 "prev_hash":"56da37a6…","event_id":"4bf8f6cb…","event_hash":"59b2011e…"}
```

## Rejected events (each fails closed, with the rule that catches it)

| Bad event | Rejected by | Rule |
|---|---|---|
| `{"type":"approval", ...}` from `obs.engineering` (not in the approver pool) | `validate_log(registry=…)` | **#11** `FORGED — re-derives as NOT entitled`. The logged `entitled:true` is ignored. |
| An `action` with `gated:true` and no matching approval for the case | `validate_log` | **#8** `ungated/laundered action — no entitled approval`. |
| An `action` whose `scope` ≠ the approved scope | `validate_log` | **#8** `action scope … != approved scope …` (anti scope-confusion). |
| A governed approval/action carrying a different bundle than its predecessor | `validate_log` | `artifact substitution` — authorization must match exactly across all three decision events. |
| A second action reusing the same approval | `validate_log` | `approval already consumed — one-shot authorization replay`. |
| Any byte changed in a committed line | `validate_log` | **#4** `TAMPER — event_hash does not match content`. |
| A line with a duplicate JSON key | `validate_log` | **#5** `duplicate JSON key` (parsed with `object_pairs_hook`). |
| `append` of an event missing `payload`, or `actor.kind` ∉ {agent,human} | `EventLog.append` | writer fails closed (`LedgerError`) — bad data never reaches the file. |

The denied path is committed end-to-end at
[`examples/visible-handoff/output/denied.events.sample.jsonl`](../examples/visible-handoff/output/denied.events.sample.jsonl)
(and its [transcript](../examples/visible-handoff/output/denied.transcript.sample.md)) — the
ledger still **validates**; it just records a denial + escalation instead of an action.

## Invariants (enforced by `validate_log`, exercised in `core/tests/test_event_log.py`)

1. **Schema** — the validator is at least as strict as the writer: required fields, `actor`
   shape **and `actor.kind ∈ {agent, human}`**, `payload` is an object, any `approval` has a
   valid `decision` and a boolean `entitled`, the type enum, and the schema version.
2. **Ordering** — `sequence` is monotonic with no gaps.
3. **Chain** — `prev_hash[i] == event_hash[i-1]`; first links to GENESIS.
4. **Content addressing** — `event_id` and `event_hash` are recomputed and must match (in-place
   edits are caught).
5. **Canonical encoding** — every line must be canonical JSON with no duplicate keys.
6. **Exactly-once** — duplicate `idempotency_key` is rejected.
7. **Causation** — `causation_id` must reference an earlier event.
8. **No decision laundering or scope pivot** — a **scoped** `action` is consequential by *policy*
   (not by the caller's `gated` flag) and must bind to an **entitled** `approval` by `causation_id`
   **and** scope. The approval in turn must bind to its `recommendation` by causation **and** the
   same scope — so an approval can't pivot to a different (even if also-entitled) scope than was
   recommended.
9. **Exact evidence authorization** — every governed recommendation, approval, and action carries
   a schema-validated authorization envelope. The approval must exactly match its recommendation; the
   action must exactly match its approval. Rendered-byte, evidence-graph, or material-claim substitution
   invalidates the chain.
10. **One-shot action authority** — the first fully valid action consumes its approval. A second action
    using that approval is rejected as replay. Invalid/substituted attempts do not consume the genuine
    authorization.
11. **Entitlement + ACL re-verification** — with the approval registry, both are re-derived for
   **every** event: entitlement to the scope (`can_approve`) and channel membership (`is_member`).
   The logged `entitled` flag is never trusted (catches forged approvals, non-member actors on any
   event, and unknown actors).
12. **Point-in-time authority** — an approval is stamped with the approval-registry `version` in
    force when it was made; on replay a registry that has since changed surfaces as a version
    mismatch rather than silently revaluing a past approval. (Production stores a full snapshot per
    version; the reference hashes the live config.)
13. **PII backstop** — a heuristic scan rejects events carrying obvious direct identifiers
    (emails/SSNs/phones), at both `append` and replay (a backstop, not a guarantee).

## Integrity vs non-repudiation (be honest)

The SHA-256 hash chain proves **internal consistency / no in-place edit**. On its own it does
**not** prove non-repudiation (an attacker who rewrites the whole file can recompute every hash)
and it does **not** detect **suffix truncation** — dropping the last N rows leaves a consistent
prefix that validates clean, which would silently reinstate a decision a later "denied" had revoked.
Two controls close those gaps:

- **HMAC signatures.** Construct `EventLog(path, secret=...)` to sign each event — a wholesale
  rewrite then fails `validate_log(path, secret=...)` for anyone without the key.
- **Head-count anchor.** `EventLog.checkpoint()` (CLI: `python3 -m core.event_log checkpoint
  <log>`) writes a small sidecar `{schema_version, count, head_hash}` — itself HMAC-signable when
  the log carries a secret. `validate_log(path, anchor=<sidecar>)` fails a truncated (fewer rows),
  extended (more rows), or head-rewritten ledger. The visible-handoff example commits an anchor
  beside every sample ledger, and CI regenerates them byte-for-byte AND proves a truncated ledger
  is rejected against its anchor.

Two honest limits on the anchor:

1. The **committed sample anchors are unsigned** — a demonstration of the mechanism. An unsigned,
   co-located sidecar is only a real control when the attacker cannot also rewrite it: store it on
   separate/WORM media, or HMAC-sign it (pass a `secret`).
2. Even a **signed anchor must be the latest one.** Verifying a truncated ledger against an *older*
   but genuinely-signed anchor (rolled back to that earlier `count`/`head`) passes on the anchor checks
   alone — because that is a genuine earlier state, indistinguishable from it having been the real one.
   Defending against anchor **rollback** requires a trusted, monotonic record of how tall the ledger has
   already grown — the append-only WORM / KMS property. The reference makes that requirement **explicit and
   enforceable**: pass the last-known height as `validate_log(path, anchor=<sidecar>, min_count=N)` (CLI:
   `--min-count N`) and an anchor shorter than `N` is rejected as a rollback/replay, *even when its HMAC is
   valid*. `N` comes from the same monotonic store that holds the checkpoint; without it, anchor freshness is
   trusted, so always verify against the current, highest-count anchor. A truncation test that exercises the
   `min_count` rollback guard is in [`core/tests/test_event_log.py`](../core/tests/test_event_log.py).

**Production** stores that anchor as a KMS-signed checkpoint on WORM / append-only media; the
mechanism here is the reference shape of exactly that control, and that append-only storage is what
closes both limits above.

## Scale

The reference uses JSONL (readable, git-diffable, zero-dependency). At volume, project the same
schema and invariants onto SQLite/Postgres; the event model is unchanged.
