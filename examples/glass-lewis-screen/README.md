# glass-lewis-screen — ISS vs Glass Lewis say-on-pay war room

The Executive-Compensation arm's **two-proxy-advisor deliverable**: the view a Compensation Committee needs
before the say-on-pay vote. It renders an illustrative reconstruction of **Glass Lewis's current (2026)
pay-for-performance scorecard** — a 0–100 composite across five quantitative tests mapping to a **concern
level** (Negligible/Low/Medium/High/Severe; the legacy A–F grade is retired) — beside the illustrative **ISS**
concern level (Low/Medium/High), both scoring the *same* synthetic executive-pay facts, then reconciles them:
**agree or diverge**, *why*, the committee considerations, and a directional say-on-pay support band. Every number comes
from `foundation/compute/glass_lewis_screen.py`; the agent renders and governs — it does no scoring and makes
no vote prediction.

```bash
python3 run.py                                                   # draft dashboard + digest (nothing sent)
python3 run.py --publish --approved-by "Compensation Committee Chair"
python3 evals/test_glass_lewis_agent.py                         # agent evals
python3 ../../foundation/compute/tests/test_glass_lewis_screen.py  # engine + war-room tests
```

**Why it matters.** A single advisor's verdict is easy to read; the hard, board-relevant question is what to
do when **two advisors disagree**. This arm's value is the reconciliation — and the committed case is a
genuine divergence: Glass Lewis's 5-test scorecard reads **Low** concern (composite 70/100) while ISS reads
**Medium** → **ISS-ONLY FLAG**. This is a deliberately-constructed teaching case, and its mechanism is
transparent: Acme is a disciplined-pay,
lagging-stock company — the granted CEO pay percentile is high against a weak 5-yr TSR, so GL's *Granted CEO
Pay vs TSR* test scores Severe (exactly what ISS flags); but the NEO team is lean, the STI is lean, the CEO's
equity is underwater (CAP below granted), and the financials are solid — so the other four tests read
Negligible/Low and the composite lands Low. The dashboard's **two-pole counterfactual** makes the mechanism
transparent: a pay-vs-TSR-only read ≈ Severe, financials-only ≈ Negligible, and the blended composite = Low.

**Honesty.** The Glass Lewis model here — the current 2026 five-test scorecard (the legacy A–F grade is
retired); test weights, score bands, peer rules — and the ISS side are **illustrative reconstructions**,
**NOT** Glass Lewis or ISS output, **not affiliated** with either firm, and built only from **public
methodology**. The universe is synthetic (no real issuer, no individual names). The say-on-pay support band
is a **directional** practitioner range, **not** a vote forecast or probability. Presentation + governance
only — the agent never scores a program itself or forecasts a vote. Provenance:
[`governance/glass-lewis-model.md`](../../governance/glass-lewis-model.md).

Part of the [Agentic PeopleOS](../../README.md) Executive Compensation arm.
