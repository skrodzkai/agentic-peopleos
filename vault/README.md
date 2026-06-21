# Vault — the knowledge layer

This is the human-readable knowledge layer for an agent-run People function: an
Obsidian/Git vault of policies, processes, cases, and reference. Agents **ground in** it;
humans **read and edit** it; Git **versions** it.

> The vault is a knowledge layer, **not compliance infrastructure**. The audit record of
> decisions/actions/approvals lives in the event ledger (`core/event_log.py`), not here.

All content is synthetic (Acme Corp). No real company, person, or PII.

## Taxonomy

| Folder | Holds | `type` |
|---|---|---|
| `00-foundation/` | operating principles, glossary | `foundation` |
| `policy/` | **canonical, approved** policies (the only `trusted_policy` source) | `policy` |
| `40-process-flows/` | end-to-end process flows (Mermaid) | `process` |
| `cases/` | process-centric case notes (a requisition, a comp cycle) | `case` |
| `90-people-analytics/` | metrics glossary, definitions | `reference` |
| `30-agents/` | the agent layer (links to `core/` + `examples/`) | `agent` |

## Frontmatter (enforced by `tools/vault_lint.py`)

Every note carries:

```yaml
---
type: policy            # foundation | policy | process | case | reference | agent
content_type: trusted_policy   # trust is by PROVENANCE: only policy/ + status:approved
status: approved        # draft | in-review | approved | retired
owner: Total Rewards    # the accountable role (not a person)
last-reviewed: 2025-12-01
links: [cases/req-1008]
---
```

## Trust & linkage

- **Agents retrieve by metadata, not folders.** Notes are found by their frontmatter
  (`type`, `topic`, `content_type`, `status`) and related notes by `links:` / backlinks —
  never by an agent walking the folder tree. Folders are a shallow convenience for humans and
  a provenance signal (`policy/`), not the agent's retrieval path.
- **Trust is provenance, not a label.** Only notes under `policy/` with `status: approved`
  are `trusted_policy` — the authoritative source an agent may cite. A note that merely
  *claims* `trusted_policy` elsewhere is treated as untrusted data (see
  `core/content.py` + `governance/prompt-injection-threat-model.md`).
- **Linkage is process-centric** here (requisition → policy → approval), deliberately avoiding
  employee-centric notes so no PII lives in the vault.
- **Staleness matters:** `last-reviewed` is checked; out-of-date policy escalates instead of
  being served as current.
