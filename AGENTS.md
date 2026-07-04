# Notes for automated reviewers

This is a **public** reference project (synthetic **Acme Corp**). It contains **no secrets, credentials, or
real PII**. It is synthetic throughout, with TWO deliberate exceptions — both **real public companies** with
as-disclosed public data with **no individual person names** (the peer universe is company-level; the proxy-pay dataset is role-based): (1) the exec-comp peer-screener
universe (`foundation/data/acme/peer_universe.csv` + the peer-builder outputs — real financials, provenance
in `governance/real-peer-data.md`), and (2) the benchmarking proxy-pay dataset
(`foundation/data/acme/proxy_comp.csv` — real DEF 14A Summary-Compensation-Table figures, provenance in
`governance/proxy-comp-data.md`; each figure traces to a SEC filing URL). No real name ever carries a
fabricated pay/TSR figure — the ISS + rTSR arms
run on separate synthetic universes. `core/approval_registry.py` and
`governance/approval-registry.md` are governance **code/docs** (an access-control model); they
hold no secrets.

## Running the checks (from the repo root)

This block runs the **core** checks from `.github/workflows/ci.yml` — enough to validate a change locally.
It is not every CI step: CI also runs the portable skills' offline self-checks, the whole-tree
"nothing stale/untracked" backstop, and the per-arm dashboard-in-sync `git diff` gates. Treat `ci.yml` as
authoritative and keep this in step with it.

```bash
python3 -m py_compile core/*.py core/tests/*.py tools/*.py \
  foundation/data/generate.py foundation/compute/*.py foundation/compute/tests/*.py \
  foundation/render/*.py foundation/render/tests/*.py examples/*/run.py examples/*/evals/*.py
# public-safety + release preflight
python3 tools/pii_scan.py .                  # whole repo (tests/evals excluded)
python3 tools/preflight.py                   # CI/doc-linked paths exist + nothing required is untracked
# core spine
python3 core/tests/test_event_log.py
python3 core/tests/test_approval_registry.py
python3 core/tests/test_content.py
python3 core/tests/test_messaging.py
python3 core/tests/test_metrics.py
# data foundation is deterministic + compute reconciles
python3 foundation/data/generate.py          # then `git diff --exit-code -- foundation/data/acme` (deterministic)
python3 foundation/compute/tests/test_engine.py
python3 foundation/compute/tests/test_regression.py
python3 foundation/compute/tests/test_peers.py
# shared renderer + chart toolkit
python3 foundation/render/tests/test_dashboard.py
python3 foundation/render/tests/test_charts.py
# measurement governance
python3 -m core.metrics validate vault/90-people-analytics/metrics/metrics.registry.json
python3 tools/render_glossary.py             # then `git diff --exit-code` on vault/90-people-analytics (glossary in sync)
# Analytics arm (eval + run, then `git diff --exit-code` on each output/report.sample.* + digest)
(cd examples/headcount-reporting && python3 evals/test_headcount.py && python3 run.py)
(cd examples/attrition-reporting && python3 evals/test_attrition.py && python3 run.py)
(cd examples/people-ops-reporting && python3 evals/test_people_ops.py && python3 run.py)
(cd examples/operating-review && python3 evals/test_operating_review.py && python3 run.py --publish --approved-by hr.business-partner)
(cd examples/people-intelligence && python3 evals/test_people_intelligence.py && python3 run.py)
# Executive Compensation arm (eval + run, then `git diff --exit-code` on each output/report.sample.html + day1-digest.sample.md)
(cd examples/executive-comp-peer-builder && python3 evals/test_peer_builder.py && python3 run.py)
(cd examples/executive-comp-benchmarking && python3 evals/test_benchmarking_agent.py && python3 run.py)
(cd examples/rtsr-psu-valuation && python3 evals/test_rtsr_psu.py && python3 run.py)
(cd examples/iss-pay-screen && python3 evals/test_iss_pay_screen.py && python3 run.py)
# shared exec-comp compute engines
python3 foundation/compute/tests/test_rtsr.py
python3 foundation/compute/tests/test_iss_screen.py
python3 foundation/compute/tests/test_benchmarking.py
# retention-risk model (glass-box hazard + eval + segment layer)
python3 foundation/compute/tests/test_retention.py
python3 foundation/compute/retention.py validate   # re-fits + reproduces coefficients/calibration/bands + provenance
# portable SEC skills — offline self-checks (no network)
python3 skills/sec-edgar/scripts/test_skill.py
python3 skills/sec-comp-research/scripts/peer_screen.py --demo >/dev/null
# reference example agents
(cd examples/ta-reporting && python3 evals/test_report.py)
(cd examples/comp-reporting && python3 evals/test_comp.py)
(cd examples/visible-handoff && python3 evals/test_handoff.py)
# both handoff outcomes + ledger integrity (approved AND denied)
(cd examples/visible-handoff && python3 scenarios.py)
python3 -m core.event_log validate examples/visible-handoff/output/events.jsonl \
  --registry examples/visible-handoff/approval_registry.json
python3 -m core.event_log validate examples/visible-handoff/output/denied.events.sample.jsonl \
  --registry examples/visible-handoff/approval_registry.json
python3 tools/vault_lint.py vault
```

CI byte-diffs the deterministic **HTML + Markdown + JSONL** outputs against a fresh run, so a drifted number
fails the build. The committed **`report.sample.png`** screenshots are **illustrative snapshots** re-rendered
from the same HTML — they are *not* byte-freshness-gated (cross-platform rendering is non-deterministic), so
the HTML/MD is the source of truth and the PNG is a convenience preview. Regenerate a PNG from its
`report.sample.html` if the underlying data changes.

Standard library only; deterministic; offline; fail-closed. The decision ledger
(`core/event_log.py`) is the source of record for decisions/actions/approvals; the HRIS/ATS for
employee/candidate *data*; chat for the *conversation*. The metric registry
(`vault/90-people-analytics/metrics/metrics.registry.json`) is the single source of truth for
every reported number, and `core/metrics.py` enforces that no metric grants a decisional action.
