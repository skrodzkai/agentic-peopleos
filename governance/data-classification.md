# Data classification

Where each kind of data lives, and why the vault and the ledger carry **no real
system-of-record PII** — minimized and pseudonymous by convention (synthetic illustrative names
like "Acme Corp" or a sample approver may appear in examples), with a heuristic PII backstop in
both `tools/vault_lint.py` and the ledger's `append()` (a backstop, not a cryptographic
guarantee).

| Class | Examples | System of record | In this repo |
|---|---|---|---|
| Public | job posts, policy summaries | website / vault | vault `policy/` (synthetic) |
| Internal | process flows, metrics defs | vault | vault (synthetic) |
| Confidential | comp bands, headcount plans | HRIS | not in the public repo |
| **PII / personal** | real names, contact, identifiers | **HRIS/ATS only** | **no real PII** — sample names are fictional |
| Sensitive personal | health, ER cases, demographics | restricted HRIS | never here |

## Rules

1. **One source per fact.** Employee/candidate data lives in the HRIS/ATS. The vault holds
   *process and knowledge*; the ledger holds *decisions*. Neither duplicates PII.
2. **Vault is process-centric, not employee-centric.** Cases reference a requisition or a
   cycle, not a person (see `vault/cases/req-1008.md`). The person record stays in the ATS.
3. **Ledger payloads are minimized.** Events carry ids, scopes, and decisions — not employee
   records. Each event also carries an actor `display` label; in the sample ledgers that is a
   synthetic **agent/role** label ("Coordinator", "TA Reporting", "hr.business-partner"), not a real
   person — so the ledger is not claimed to be *name-free*, it is claimed to carry no *real,
   identifiable* PII. An approval records *who approved* by a pseudonymous workforce id; that id is
   minimized but, being linkable, is still personal data (see [data-retention](data-retention-and-erasure.md)).
4. **Right to human review (GDPR Art. 22)** is structural: consequential decisions are
   human-owned by the [HITL matrix](hitl-matrix.md).
5. **Retention & deletion** follow the system of record; the ledger is append-only for audit
   and references data by id, so subject-deletion happens in the SoR without breaking the chain.
6. **Synthetic illustrative names are not PII.** Examples use fictional names (Acme Corp) for
   realism. The "no PII" guarantee is about *real, identifiable* data from a system of record —
   none of which appears in the vault or ledger. In production, the ledger references workforce
   identities by id, not by personal record.

All data in this repository is synthetic (Acme Corp).
