# SEC skills — layering & roadmap

The SEC skills are deliberately **layered**, so each stays sharp and the foundation doesn't bloat. This is
the intended shape; the last layer is planned, not yet built, and is named here so the boundary is honest.

```
sec-edgar            (built)   navigation + form intelligence — the map
  └─ sec-comp-research (built)   the compensation analyst workflow, on top of the map
       └─ sec-proxy-extractor (PLANNED)  deterministic proxy-table extraction with confidence scoring
```

## What each layer is — and is NOT

### `sec-edgar` — built
The **map / navigation** layer: resolve a ticker, list a company's filings, **classify what a filing type
is** (the `forms.py` knowledge map), fetch a document and its index, and enforce SEC fair access. It is
**not** a proxy-table extractor — it locates and identifies filings and hands off to a workflow that reads
them. Fetches are locked to `https://www.sec.gov/…` and `https://data.sec.gov/…`.

### `sec-comp-research` — built
The **compensation analyst workflow** on top of the foundation: find the DEF 14A, read the Summary
Compensation Table, screen a size + industry peer group, and position pay at target percentiles. Today the
SCT is read by the **agent** (WebFetch of the filing, or the foundation's `--section` window) — an honest,
working *semantic* read, but **not** a deterministic table parser.

### `sec-proxy-extractor` — PLANNED (the next public increment)
A focused, deterministic extraction layer — the piece that turns "read the table" into structured, audited
data. Scope:

- **Deterministic-first table parsing** of the proxy comp tables — Summary Compensation Table, Director
  Compensation, Grants of Plan-Based Awards, Outstanding Equity Awards, Option Exercises & Stock Vested,
  Pension Benefits, Nonqualified Deferred Compensation, CEO Pay Ratio, Pay-vs-Performance.
- **Candidate scoring** — enumerate all tables, score by nearby headings + column headers, pick the SCT
  (not brittle single-XPath scraping).
- **Cell normalization** — units captions ("in thousands"), parentheses = negative, em-dash/blank = no
  value, footnote superscripts; preserve the **raw cell text** alongside the normalized number.
- **Validation** — reconcile components to the reported Total; flag impossible/duplicate/missing values.
- **Confidence scoring** — every extraction reports **high / medium / low** with the reason, never a bare
  row set; low-confidence rows are surfaced for review, not silently dropped.
- **Provenance per row** — accession, document URL, table index, raw cell text, footnote text, **parser
  method**, and the confidence score.
- **Labeled fallback** — a deterministic parser runs first; a semantic/LLM fallback runs **only** when
  parser confidence is low, and the output is labeled as fallback (a different data-handling posture).
- **Optional dependencies, kept out of the foundation** — real HTML table extraction likely wants `lxml`
  / `pandas.read_html`; those stay in this extractor skill (with an optional-deps install), so `sec-edgar`
  and `sec-comp-research` remain **stdlib-only and portable**.

## Design rule that holds across all of it

**Proxy HTML is the source of truth for executive compensation — not the XBRL `companyfacts` API.** The
`data.sec.gov` XBRL endpoints cover financial-statement facts (10-K/10-Q/8-K/20-F/…), not the Summary
Compensation Table. Use `submissions` to *find* the proxy, read the proxy HTML for pay, and treat Inline
XBRL as an additional source only for the specifically-tagged tables (e.g. Pay-vs-Performance, Item 402(v)).
