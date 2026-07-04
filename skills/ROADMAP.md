# SEC skills — layering & roadmap

The SEC skills are deliberately **layered**, so each stays sharp and the foundation doesn't bloat.

```
sec-edgar            (built)   navigation + form intelligence — the map
  └─ sec-comp-research (built)   the compensation analyst workflow, on top of the map
       └─ sec-proxy-extractor (built — SCT)  deterministic proxy-table extraction with confidence scoring
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

### `sec-proxy-extractor` — built (Summary Compensation Table)
The focused, deterministic extraction layer — the piece that turns "read the table" into structured, audited
data. **Built and shipping** for the **Summary Compensation Table**:

- **Deterministic table parsing**, standard library only — a structure-preserving `html.parser` table
  reader (no `lxml`/`pandas` dependency, so the whole stack stays portable).
- **Candidate scoring** — enumerate all tables, match header cells (camelCase-split, footnote-stripped) to
  the SEC-mandated SCT columns, and pick the best; a table without the name/year/salary/total anchors is
  **not** treated as the SCT (not brittle single-XPath scraping).
- **Cell normalization** — parentheses = negative, em-dash/blank = no value, footnote superscripts + lone
  footnote cells + zero-width filler dropped, `$`-in-its-own-cell collapsed; the **raw cell text** is kept
  alongside the parsed number.
- **Validation** — reconcile each row's components to the reported Total; a row that doesn't reconcile is
  flagged and named, never silently corrected.
- **Confidence scoring** — every extraction reports **high / medium / low / none** with the reasons, never a
  bare row set; a blank-column row is recovered total-anchored only if it still reconciles, and marked
  `partial`; a short non-reconciling row is counted and skipped, never fabricated.
- **Offline-testable** — the whole parser/aligner/scorer runs against synthetic fixtures with no network.

**Future scope in this same skill** (not yet built, named so the boundary is honest):

- The **other proxy tables** — Director Compensation, Grants of Plan-Based Awards, Outstanding Equity Awards,
  Option Exercises & Stock Vested, Pension Benefits, Nonqualified Deferred Compensation, CEO Pay Ratio,
  Pay-versus-Performance (Item 402(v), Inline-XBRL).
- **Unit-adjusting** from scale captions. A **table-local** "in thousands"/"in millions" caption is already
  *detected* and caps confidence below high with a reason; what's not yet done is applying the scale to the
  numbers, and detecting a caption that sits **outside** the table (a section header above it).
- **Per-row provenance** in the output — accession, document URL, table index, footnote text.
- **Labeled semantic/LLM fallback** — runs **only** when deterministic confidence is low, and is labeled as
  fallback (a different data-handling posture), so the default path stays fully deterministic.
- **Multi-document filings** — when a filer puts the proxy statement in a separate document inside the DEF
  14A filing, walk the filing index to find the document that actually contains the SCT.

## Design rule that holds across all of it

**Proxy HTML is the source of truth for executive compensation — not the XBRL `companyfacts` API.** The
`data.sec.gov` XBRL endpoints cover financial-statement facts (10-K/10-Q/8-K/20-F/…), not the Summary
Compensation Table. Use `submissions` to *find* the proxy, read the proxy HTML for pay, and treat Inline
XBRL as an additional source only for the specifically-tagged tables (e.g. Pay-vs-Performance, Item 402(v)).
