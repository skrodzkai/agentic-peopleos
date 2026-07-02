# Notes for automated reviewers

This is a **public, synthetic** reference project (Acme Corp). It contains **no secrets,
credentials, or real PII** — everything is illustrative. `core/approval_registry.py` and
`governance/approval-registry.md` are governance **code/docs** (an access-control model); they
hold no secrets.

## Running the checks (from the repo root)

This block mirrors `.github/workflows/ci.yml`; keep the two in sync.

```bash
python -m py_compile core/*.py core/tests/*.py tools/*.py \
  foundation/data/generate.py foundation/compute/*.py foundation/compute/tests/*.py \
  foundation/render/*.py foundation/render/tests/*.py examples/*/run.py examples/*/evals/*.py
# public-safety + release preflight
python tools/pii_scan.py .                  # whole repo (tests/evals excluded)
python tools/preflight.py                   # CI/doc-linked paths exist + nothing required is untracked
# core spine
python core/tests/test_event_log.py
python core/tests/test_approval_registry.py
python core/tests/test_content.py
python core/tests/test_messaging.py
python core/tests/test_metrics.py
# data foundation is deterministic + compute reconciles
python foundation/data/generate.py          # then `git diff --exit-code -- foundation/data/acme` (deterministic)
python foundation/compute/tests/test_engine.py
python foundation/compute/tests/test_regression.py
python foundation/compute/tests/test_peers.py
# shared renderer + chart toolkit
python foundation/render/tests/test_dashboard.py
python foundation/render/tests/test_charts.py
# measurement governance
python -m core.metrics validate vault/90-people-analytics/metrics/metrics.registry.json
python tools/render_glossary.py             # then `git diff --exit-code` on vault/90-people-analytics (glossary in sync)
# Analytics arm (eval + run, then `git diff --exit-code` on each output/report.sample.* + digest)
(cd examples/headcount-reporting && python evals/test_headcount.py && python run.py)
(cd examples/attrition-reporting && python evals/test_attrition.py && python run.py)
(cd examples/people-ops-reporting && python evals/test_people_ops.py && python run.py)
(cd examples/operating-review && python evals/test_operating_review.py && python run.py --publish --approved-by hr.business-partner)
(cd examples/people-intelligence && python evals/test_people_intelligence.py && python run.py)
# Executive Compensation arm (eval + run, then `git diff --exit-code` on each output/report.sample.html + day1-digest.sample.md)
(cd examples/executive-comp-peer-builder && python evals/test_peer_builder.py && python run.py)
(cd examples/rtsr-psu-valuation && python evals/test_rtsr_psu.py && python run.py)
(cd examples/iss-pay-screen && python evals/test_iss_pay_screen.py && python run.py)
# shared exec-comp compute engines
python foundation/compute/tests/test_rtsr.py
python foundation/compute/tests/test_iss_screen.py
# retention-risk model (glass-box hazard + eval + segment layer)
python foundation/compute/tests/test_retention.py
python foundation/compute/retention.py validate   # re-fits + reproduces coefficients/calibration/bands + provenance
# reference example agents
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
