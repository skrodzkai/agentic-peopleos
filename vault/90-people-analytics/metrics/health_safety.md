---
type: reference
owner: People Operations
status: approved
last-reviewed: 2026-06-20
---

# Health & Safety metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Recordable incident rate (TRIR)**<br><sub>Core KPI</sub> | OSHA-style total recordable incident rate per 100 FTE-years. | `(recordable_incidents * 200000) / total_hours_worked`<br>_protocol:_ 200,000 = 100 FTE x 2,000 hours; use actual hours worked as the base. | period | Workplace-safety governance (ISO 30414 health & safety). | ✓ calculate, trend, draft_summary<br>✗ alter_record, identify_individual_publicly |
| **Lost-time injury rate (LTIFR)**<br><sub>Core KPI</sub> | Lost-time injuries per 1,000,000 hours worked (LTIFR, ILO convention). | `(lost_time_injuries * 1000000) / total_hours_worked`<br>_protocol:_ Per 1,000,000 hours worked (LTIFR). Distinct from the OSHA recordable rate (TRIR), which is per 200,000 hours — keep the two bases separate and consistent across periods. | period | Severity-weighted safety view. | ✓ calculate, trend, draft_summary<br>✗ alter_record, identify_individual_publicly |
| **Absence rate**<br><sub>Core KPI</sub> | Share of scheduled work time lost to unplanned absence. | `absence_days / scheduled_workdays`<br>_protocol:_ Unplanned absence only; denominator = scheduled workdays; segment by org unit. | cohort | Workforce health, capacity, and cost. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record, identify_individual_publicly |
