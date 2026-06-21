# Notes for automated reviewers

This is a **public, synthetic** reference project (Acme Corp). It contains **no secrets,
credentials, or real PII** — everything is illustrative. `core/approval_registry.py` and
`governance/approval-registry.md` are governance **code/docs** (an access-control model); they
hold no secrets.

## Running the checks (from the repo root)

```bash
python -m py_compile core/*.py core/tests/*.py tools/*.py examples/*/run.py examples/*/evals/*.py
# core spine
python core/tests/test_event_log.py
python core/tests/test_approval_registry.py
python core/tests/test_content.py
python core/tests/test_messaging.py
python core/tests/test_metrics.py
# measurement governance
python -m core.metrics validate vault/90-people-analytics/metrics/metrics.registry.json
python tools/render_glossary.py     # then `git diff --exit-code` on vault/90-people-analytics to confirm the glossary is in sync
# example agents
(cd examples/ta-reporting && python evals/test_report.py)
(cd examples/comp-reporting && python evals/test_comp.py)
(cd examples/visible-handoff && python evals/test_handoff.py)
# both handoff outcomes + ledger integrity (approved AND denied)
(cd examples/visible-handoff && python scenarios.py)
python -m core.event_log validate examples/visible-handoff/output/events.jsonl \
  --registry examples/visible-handoff/approval_registry.json
python -m core.event_log validate examples/visible-handoff/output/denied.events.sample.jsonl \
  --registry examples/visible-handoff/approval_registry.json
python tools/vault_lint.py vault
```

Standard library only; deterministic; offline; fail-closed. The decision ledger
(`core/event_log.py`) is the source of record for decisions/actions/approvals; the HRIS/ATS for
employee/candidate *data*; chat for the *conversation*. The metric registry
(`vault/90-people-analytics/metrics/metrics.registry.json`) is the single source of truth for
every reported number, and `core/metrics.py` enforces that no metric grants a decisional action.
