---
type: reference
owner: Talent Management
status: approved
last-reviewed: 2026-06-20
---

# Performance & Talent metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Promotion rate**<br><sub>Core KPI</sub> | Share of the eligible population promoted in the period, cut by level. | `count(distinct promoted_employees in level) / promotion_eligible_population in level`<br>_group:_ `enterprise rate = distinct promoted / average_headcount; fairness rate = distinct promoted / average_eligible_population`<br>_protocol:_ Always cut by level/band; use a promotion-eligible denominator; enable demographic cuts for advancement equity. | cohort | Advancement equity and calibration. | ✓ calculate, trend, segment, draft_summary<br>✗ change_rating, recommend_termination |
| **Promotion velocity (time in level)**<br><sub>Diagnostic</sub> | Median time employees spend in a level before promotion. | `median(promotion_date - level_entry_date)`<br>_protocol:_ Report median (and p90); cut by level; enable demographic cuts to surface advancement-equity gaps. | per_event | Career-pathing and advancement-equity diagnostics. | ✓ calculate, segment, draft_summary<br>✗ change_rating, recommend_termination |
| **Performance rating distribution**<br><sub>Diagnostic</sub> | Distribution of performance ratings across the rated population for a cycle. | `count(by rating) / total_rated`<br>_protocol:_ Report unrated_share = unrated / total_in_scope alongside; the value is in the cuts (by manager, level, demographic). | cohort | Calibration health. | ✓ calculate, segment, draft_summary<br>✗ change_rating, recommend_termination |
