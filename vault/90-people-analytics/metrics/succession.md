---
type: reference
owner: Talent Management
status: approved
last-reviewed: 2026-06-20
---

# Succession metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Succession coverage (bench strength)**<br><sub>Core KPI</sub> | Share of critical roles with at least one identified, ready successor. | `critical_roles_with_ready_successor / total_critical_roles`<br>_protocol:_ Split ready-now vs ready-later (see successor_readiness); state the critical-role definition. | cohort | Key-person risk and talent-pipeline credibility (ISO 30414 succession). | ✓ calculate, segment, flag_outliers, draft_summary<br>✗ identify_individual_publicly, alter_record |
| **Successor readiness mix**<br><sub>Diagnostic</sub> | Distribution of identified successors by readiness (ready-now / 1-2 yrs / 3+ yrs). | `count(successors by readiness_band) / total_identified_successors`<br>_protocol:_ Companion to succession_coverage; readiness bands defined consistently with the talent review. | cohort | Depth and timing of the succession bench. | ✓ calculate, segment, flag_outliers, draft_summary<br>✗ identify_individual_publicly, alter_record |
