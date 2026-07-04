---
name: sec-proxy-extractor
description: >-
  Deterministically extract the Summary Compensation Table (SCT) from a DEF 14A proxy into structured,
  reconciled rows — WITH a confidence score. Real proxies are hand-authored HTML: dozens of empty spacer
  cells, the '$' split into its own column, header words glued together (FiscalYear), footnote markers in
  their own cells, zero-width-space filler, and the officer's name dropped on the 2nd/3rd year rows. This
  turns that mess into data you can trust because it tells you how much to trust it — and refuses to guess
  when it can't find a real SCT. Use it whenever you need the ACTUAL pay figures out of a proxy as data
  (name, year, salary, bonus, stock, option, non-equity, all-other, total), not just a text read. Builds
  on sec-edgar; standard library only; offline-testable; fail-closed.
---

# SEC proxy extractor — the Summary Compensation Table, as trustworthy data

The **deterministic extraction** layer of the SEC skill stack. `sec-edgar` finds and identifies the proxy;
`sec-comp-research` runs the analyst workflow; **this skill turns the single messiest, highest-value table
in the filing — the Summary Compensation Table — into structured rows you can compute on**, and scores how
much to trust each extraction.

Why it's hard, and why a confidence score is the whole point: a proxy SCT is not clean data. Across real
filings you get all of these in one table — empty layout cells for spacing, the currency sign in a cell of
its own, `Stock-basedAwards` with the space eaten by adjacent markup, a `(6)` footnote reference sitting in
its own `<td>`, zero-width spaces (`​`) used as filler, and the officer's name present only on their
first year row. Naive scraping silently returns misaligned numbers. This extractor collapses that noise,
aligns each row to the header, parses the money, and **reconciles the components to the disclosed Total** —
then reports **high / medium / low** with the exact reason, and returns **found = false** rather than a
fabricated table when it can't find a real SCT.

## What you get

For each `(officer, fiscal year)` row: `name`, `year`, the money columns present, the `Total`, the
**component sum**, whether it **reconciled** (components == Total within $2), and the **raw cell** each figure
came from. Plus an overall `confidence` band, a `score`, and a `reasons` list that names every gap.

```bash
export SEC_UA="Your Name your.email@example.com"     # required by sec-edgar's fair-access guard
python3 scripts/extractor.py PCTY                     # fetch the latest DEF 14A + extract its SCT
python3 scripts/extractor.py PCTY --json              # machine-readable result (rows + confidence + reasons)
python3 scripts/extractor.py --file proxy.html        # extract from a local proxy HTML file (offline)
python3 scripts/extractor.py --demo                   # a built-in synthetic SCT (offline, no network)
```

As a library:

```python
from extractor import extract_sct
result = extract_sct(proxy_html)     # {found, confidence, score, columns, rows, reconciliation, reasons}
```

## How it decides (deterministic, explainable)

1. **Parse every `<table>`** structure-preserving (nested tables kept separate; `<sup>` footnotes dropped;
   colspans expanded; entities decoded).
2. **Identify the SCT by its column schema**, not by position — match header cells (camelCase-split,
   footnote-stripped) to the SEC-mandated columns. A table without the **name / year / salary / total**
   anchors is **not** treated as the SCT (a Director Compensation table is correctly rejected).
3. **Collapse layout noise** per row — drop empty, zero-width, lone-`$`, and standalone footnote-marker
   cells — so a padded row lines up with the header.
4. **Align + forward-fill** — a full row carries the name; a continuation year row (name dropped) inherits it.
5. **Parse money** conservatively — `$`, commas, parentheses-as-negative, dashes/blank = none, trailing
   footnote markers ignored; anything ambiguous is `unparseable` (never a guessed number).
6. **Reconcile** each row's components to its Total — a strong signal the row parsed and aligned as a unit
   (necessary, but **not** sufficient: a clean column swap preserves the sum, so reconciliation is paired
   with plausibility checks below).
7. **Score** = header completeness + clean-parse rate + reconciliation (a **gate**, not just a weight).
   **High** only when every core column is present, every cell parsed, every data row has a Total and
   reconciled, and no row looks suspect; otherwise **medium/low** with the reason. Selection is fail-closed:
   a hidden (`display:none`) decoy is skipped, and two equally-SCT-like reconciling tables return
   `found = false` (ambiguous) rather than a guess.

### The honest edge cases (they lower confidence, they don't hide)

- **One or more blank interior money columns** collapse a row short. Rather than drop a real officer-year,
  the row is recovered **total-anchored** and accepted **only if it still reconciles** (and is short by at
  most two columns) — but marked `partial`, because a blank column can't be attributed to a specific field.
  Partial rows keep overall confidence below *high*.
- **A reconciling-but-implausible row** — a negative value in a never-negative column, or an empty name —
  is flagged `suspect` and caps confidence, because the sum can be right while the columns are shifted.
- **A row that doesn't reconcile** is flagged, kept, and named in the reasons — never silently "fixed".
- **A short row that does not reconcile** is counted as `unaligned` and skipped — never fabricated.
- **A hidden decoy or an ambiguous second SCT-like table** → the decoy is skipped; genuine ambiguity is
  `found = false` (reason: ambiguous), never a confident guess.
- **No SCT in the document** → `found = false`, `confidence = "none"`, with a reason.

## Scope + limits (stated plainly)

- **In scope now:** the **Summary Compensation Table** from US DEF 14A proxies. On a representative run
  across real SaaS-sector proxies, the extractor pulls the SCT with **100% row reconciliation** on the large
  majority, returns an honest **medium** (with the reason) on the rest, and **not-found** on the few whose
  proxy statement lives in a *separate document* inside the filing (point `--file` at that document).
- **Not yet:** Director Compensation, Grants of Plan-Based Awards, Outstanding Equity, Pension Benefits,
  and Pay-versus-Performance tables; "in thousands" unit captions; a semantic/LLM fallback for low-confidence
  extractions. These are the roadmap for this skill (see [`../ROADMAP.md`](../ROADMAP.md)).
- **Foreign private issuers** disclose comp on a **different basis** (20-F/40-F/6-K), not a US SCT — the tool
  warns and its column model may not fit; treat those separately.

## Guardrails

- **Standard library only. Deterministic. Offline-testable. Fail-closed.** No `lxml`/`pandas` dependency —
  the foundation stays portable.
- **Trust is earned, not asserted** — the confidence band + reasons travel with every result; downstream
  consumers gate on them.
- **Personal data + untrusted text** — an SCT names individuals. This tool reads those names from a public
  filing at runtime; **do not commit real-name + pay output to a public repo** (every fixture shipped here is
  synthetic). Extracted `name`/text fields are **untrusted input** decoded from the filing — any consumer
  that renders them into HTML must escape them.
- **Public data, presented honestly** — a dated snapshot, not investment/legal/accounting advice; always
  verify a figure against the filing it cites.

Part of the [Agentic PeopleOS](../../README.md) portfolio · foundation: [`sec-edgar`](../sec-edgar/) ·
workflow: [`sec-comp-research`](../sec-comp-research/).
