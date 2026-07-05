# sec-proxy-extractor

**Deterministically extract the Summary Compensation Table (SCT) from a DEF 14A proxy — into structured,
reconciled rows, with a confidence score.**

The third layer of the SEC skill stack. [`sec-edgar`](../sec-edgar/) finds and identifies the proxy;
[`sec-comp-research`](../sec-comp-research/) runs the comp-analyst workflow; this skill turns the messiest,
highest-value table in the filing into data you can compute on — and tells you how much to trust it.

## The problem it solves

A proxy SCT is hand-authored HTML. In real filings you hit, often in the *same* table:

- dozens of empty **spacer cells** used for column layout,
- the **`$` in a cell of its own**, apart from the number,
- header words **glued together** by adjacent markup (`FiscalYear`, `Stock-basedAwards`),
- a **footnote reference `(6)` in its own `<td>`**,
- **zero-width spaces** (`​`) used as filler,
- the officer's **name dropped** on their 2nd/3rd fiscal-year rows.

Naive scraping returns silently-misaligned numbers. This extractor collapses the noise, aligns each row,
parses the money, and **reconciles components to the disclosed Total** — a strong (necessary, though not
sufficient) check that the row parsed and aligned as a unit — then reports a confidence band with reasons,
and refuses to invent a table it can't find. It also **fails closed** on the traps that fool naive
scrapers: a hidden (`display:none`) decoy table is never chosen over the real one, two tables that both
look like the SCT and reconcile are reported as *ambiguous* rather than guessed, and a row that reconciles
but looks implausible (a negative in a never-negative column, an empty name) is flagged suspect and cannot
reach high confidence.

## Use it

```bash
export SEC_UA="Your Name your.email@example.com"     # sec-edgar fair-access guard (must contain an email)
python3 scripts/extractor.py PCTY                     # latest DEF 14A -> the SCT, reconciled, with confidence
python3 scripts/extractor.py PCTY --json              # machine-readable: rows + per-cell raw + reasons
python3 scripts/extractor.py --file proxy.html        # a local proxy HTML file (offline)
python3 scripts/extractor.py --demo                   # a built-in synthetic SCT (offline)
python3 scripts/test_skill.py                         # offline self-check (no network)
```

Example (built-in synthetic demo):

```
Summary Compensation Table — confidence: HIGH (score 1.00)
  columns: name, year, salary, bonus, stock_awards, option_awards, non_equity_incentive, other_comp, total
  rows: 3   reconciled: 3/3

  Dana Fielding, Chief Executive Officer       2025  total=$5,837,500  Σcomponents=$5,837,500
  Dana Fielding, Chief Executive Officer       2024  total=$5,336,000  Σcomponents=$5,336,000
  Morgan Ellison, Chief Financial Officer      2025  total=$3,129,800  Σcomponents=$3,129,800
```

## As a library

```python
from extractor import extract_sct
r = extract_sct(proxy_html)
# r["found"], r["confidence"] in {high, medium, low, none}, r["score"],
# r["columns"], r["rows"][i] = {name, year, values{...}, raw{...}, cell_flags{...},
#                               component_sum, reconciled, reconcile_diff, alignment},
# r["reconciliation"] = {rows, reconciled, failed, partial, unaligned}, r["reasons"]
```

## Trust model

| Band | Means |
|---|---|
| **high** | every core column present, every money cell parsed, **every data row has a Total and reconciled**, and no row looks suspect |
| **medium** | extracted and ≥50% reconciled, but something is noted (a missing optional column, a `partial` row, a scale caption, a not-fully-clean parse) |
| **low** | found the table but reconciliation/parse/alignment is weak, or a row looks implausible — review before use |
| **none** | no table with the SCT anchor columns was found, **or two tables were ambiguously SCT-like** (`found = false`) — never a fabricated table |

**Reconciliation is the anchor — necessary, not sufficient.** A row's components summing to its disclosed
Total is a strong signal the row *parsed and aligned as a unit*; it does **not** prove each figure sits in
the correct column (a clean column swap preserves the sum). So reconciliation is combined with plausibility
checks: a negative value in a never-negative column, an empty name, or a row with no Total all downgrade
confidence with a named reason. A row that doesn't reconcile is flagged, never silently corrected; a short
row (one or more blank interior columns) is recovered only if it *still* reconciles and is marked `partial`
because a blank column can't be attributed to a specific field. And the selection is fail-closed: a hidden
(`display:none`) decoy table is never chosen over the real one, and two tables that both look like the SCT
and reconcile are reported `none` (ambiguous) rather than guessed. Hidden-ness is a **heuristic** (inline
styles, `hidden`/`aria-hidden`, and classes hidden by the document's own `<style>` rules or conventional
utilities) — not a full browser CSS engine.

Extracted **name/text fields are untrusted input** decoded from the filing — any consumer that renders them
into HTML must escape them.

## Guardrails

- **Standard library only, deterministic, offline-testable, fail-closed** — no `lxml`/`pandas` dependency.
- **Personal data:** an SCT names individuals. This tool reads those names from a public filing at runtime;
  **do not commit real-name + pay output to a public repo.** All fixtures shipped here are synthetic.
- **Public data, presented honestly** — a dated snapshot, not investment/legal/accounting advice. Verify
  every figure against the filing it cites.

Scope, edge cases, and the roadmap for the other proxy tables: [`SKILL.md`](SKILL.md) · [`../ROADMAP.md`](../ROADMAP.md).

Part of the [Agentic PeopleOS](../../README.md) portfolio.
