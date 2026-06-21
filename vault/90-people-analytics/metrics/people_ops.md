---
type: reference
owner: People Operations
status: approved
last-reviewed: 2026-06-20
---

# People Operations metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Case volume**<br><sub>Core KPI</sub> | Count of distinct People Ops cases opened in the period, also reported per capita. | `count(distinct case_id where opened_in_period)`<br>_group:_ `per-capita = cases per 100 FTE`<br>_protocol:_ Count distinct case_id, not messages; segment by category (the mix matters more than the total). | period | Staffing and self-service investment. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **SLA attainment**<br><sub>Core KPI</sub> | Share of SLA-due cases resolved within their SLA target, counting open-but-breached cases against the rate. | `cases_within_sla / (resolved_cases + open_past_sla)`<br>_protocol:_ Denominator includes open-and-breached; or use a due-based cohort evaluated at resolution or breach (whichever first); pair with reopen_rate. | cohort | Service quality and capacity. | ✓ calculate, trend, flag_outliers, draft_summary<br>✗ alter_record |
| **Time to resolution**<br><sub>Core KPI</sub> | Wall-clock elapsed time from case open to resolved. | `median(resolved_at - opened_at)  + p90`<br>_protocol:_ Report median + p90 on WALL-CLOCK elapsed time (the reference engine computes resolved_at - opened_at); a production deployment refines this to an SLA/business-hours calendar. Segment by type/priority; pick one canonical reopen treatment (full lifecycle = truth). | per_case | Where to automate or add staff. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **Self-service deflection**<br><sub>Diagnostic</sub> | Share of inquiries genuinely resolved via self-service (with a resolution signal), excluding those that became human cases. | `self_service_resolutions_with_signal / (self_service_resolutions_with_signal + human_cases_from_tracked_inquiries)`<br>_protocol:_ Require a resolution signal (explicit 'this helped' OR no follow-up case from the same user on the same topic within N days); state channel-coverage limits. | cohort | ROI of self-service and agents. | ✓ calculate, trend, draft_summary<br>✗ alter_record |
| **Reopen rate**<br><sub>Diagnostic</sub> | Share of resolved cases that were reopened -- the guard against SLA gaming and premature closure. | `reopened_cases / resolved_cases`<br>_protocol:_ Define the reopen window (e.g. reopened within N days of resolution); pair with sla_attainment and time_to_resolution. | cohort | Quality check on SLA attainment and resolution. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **First-contact resolution (FCR)**<br><sub>Diagnostic</sub> | Share of resolved cases closed on the first touch with no follow-up. | `cases_resolved_first_touch / resolved_cases`<br>_protocol:_ Read alongside reopen_rate so 'first-touch closes' aren't gamed via reopens. | cohort | Service quality and efficiency. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **Case CSAT**<br><sub>Diagnostic</sub> | Satisfaction with resolved People Ops cases. | `satisfied_responses / total_responses`<br>_protocol:_ Report response rate alongside; an SLA hit != a satisfied employee. | cohort | Employee experience of HR service, beyond speed. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **Open-case backlog / aging**<br><sub>Operational alert</sub> | Open cases bucketed by age, with breached cases highlighted. | `count(open cases) by age_bucket; breached = open and past SLA`<br>_protocol:_ This open-and-breached population is exactly what sla_attainment's corrected denominator needs. | point_in_time | Where the queue is silting up; feeds the SLA-attainment denominator. | ✓ calculate, flag_outliers, draft_summary<br>✗ alter_record |
