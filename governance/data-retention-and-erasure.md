# Data retention & erasure

How long each store keeps data, and how a deletion request (GDPR Art. 17 / CCPA) is honored
without breaking the audit trail. The design principle: **keep the proof, minimize the person.**
Directly identifying records live only in the system of record; the governance layer holds
process data, decisions, and **pseudonymous** references. (Under GDPR Art. 4 / Recital 26,
pseudonymized data that remains *linkable* with additional information is still personal data —
so the ledger is *minimized*, not magically non-personal.) The payoff is that an erasure request
is satisfied in the system of record and the ledger rarely has to change at all.

## What lives where (and for how long)

| Store | Holds | Contains PII? | Retention | Erasure path |
|---|---|---|---|---|
| **HRIS / ATS** (system of record) | employee & candidate records | **yes** | per the company's existing record-retention schedule | the authoritative deletion happens here; this is the only store an erasure request mutates by default |
| **Decision ledger** ([event-log](event-log.md)) | decisions, approvals, actions | minimized — pseudonymous ids + aggregate payloads (linkable ⇒ still personal data, see above) | retained as an immutable audit record (e.g. 6–7 yrs, per employment-law guidance) | **not deleted.** It is append-only and tamper-evident; see "Erasure vs the immutable ledger" below |
| **Vault** ([data-classification](data-classification.md)) | policies, process notes, cases | **no** — process-centric by convention; a heuristic lint backstop flags obvious PII | living docs; reviewed on the cadence in each note's frontmatter | a note should never contain PII; if one did, that's a content fix, not an erasure case |
| **Chat surface** | the human-readable conversation | possibly (free text) | per the messaging platform's retention policy | platform-native deletion / redaction; the ledger remains the durable decision record |
| **Agent run logs / cost trackers** | model usage, spend, timings | **no** | short operational window (e.g. 30–90 days) | rotated/expired on schedule |

## Erasure vs the immutable ledger (the apparent conflict)

A right-to-erasure request and an append-only audit log look like they collide. They don't,
because the ledger is engineered to **never need** the personal data:

1. **Minimize at write time.** Events reference a pseudonymous `actor.id` and case refs, with
   aggregate payloads — never names, contact details, or protected attributes. The
   [data-classification](data-classification.md) convention plus `tools/vault_lint.py`'s
   heuristic PII backstop are designed to keep identifiers out of the vault (a backstop, not a
   guarantee).
2. **Erase in the system of record.** A deletion request is fulfilled in the HRIS/ATS, where
   the identifiable record actually lives. The pseudonymous reference in the ledger then points
   to a record that no longer exists — the audit trail stays intact, and the data left in the
   ledger can no longer be resolved to a person.
3. **Break the link if required.** If a regulator requires severing a pseudonym↔person mapping,
   delete the mapping (held in the system of record), not the ledger. The ledger keeps proving
   *what was decided*; it can no longer be tied back to an individual.
4. **Legal-basis carve-out.** Audit records retained to meet a legal obligation (employment,
   anti-discrimination, financial) are a recognized basis for retention under GDPR Art. 17(3) —
   they are not in scope for routine erasure.

> Net: erasing a person is a **system-of-record** operation. The governance layer is designed
> so that operation requires **no** edit to the tamper-evident ledger.

## What a production deployment must add

This repo documents the design; a real deployment implements: the erasure pipeline against the
live HRIS/ATS, a Data Processing Agreement and Records of Processing (GDPR Art. 30), retention
timers per store, and a documented data-subject request (DSR) intake + SLA. See the ⛔ column in
[regulatory-readiness](regulatory-readiness.md).
