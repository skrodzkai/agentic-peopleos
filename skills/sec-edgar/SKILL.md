---
name: sec-edgar
description: >-
  Navigate and understand SEC EDGAR filings. Resolve any US-listed ticker, list a company's filings,
  identify WHAT each filing type is and what's inside it (proxy/comp, 10-K, 8-K exec changes, insider
  Form 4, activist 13D, IPO S-1, foreign 20-F, …), and fetch a filing plus its document index — with SEC
  fair-access built in. Use this whenever the task touches an SEC filing: "what is this 8-K", "find the
  latest proxy / 10-K / Form 4 for <ticker>", "pull the executive comp", "who's an insider here",
  "what filings did <company> make". The foundation that specialized skills (e.g. sec-comp-research)
  build on. Public SEC data only; no login or API key.
---

# SEC EDGAR — navigate & understand any filing

The foundation layer for working with SEC disclosures. Point it at a company or a filing and it tells you
**what you're looking at, what's inside, how it's disclosed, and how to read it** — then hands off to a
specialized workflow for deep extraction. Everything here uses only public SEC endpoints (no login, no key).

**What you can do**
1. Resolve any US-listed **ticker → CIK** and list the company's recent **filings**.
2. **Identify** any form: what it is, what's inside, how it's disclosed (HTML / Inline-XBRL / XBRL
   financials), and where the exec-comp (or other) signal lives — via `scripts/forms.py`.
3. **Fetch** a filing and its **document index** (every exhibit + the Inline-XBRL instance), not just one URL.
4. **Route**: for executive-compensation depth, hand off to the `sec-comp-research` skill; the primitives
   here (find the proxy, read the SCT window) are the same ones it uses.

## The source-of-truth rule (read this first)

- **Proxy HTML is the source of truth for executive compensation**, not the XBRL `companyfacts` API. SEC's
  `data.sec.gov/api/xbrl/companyfacts` covers **financial-statement** XBRL (from 10-K/10-Q/8-K/20-F/40-F/6-K)
  — it does **not** contain the Summary Compensation Table. Read the **DEF 14A** proxy HTML for pay.
- The one comp exception that IS tagged: **Pay Versus Performance (Item 402(v))** is Inline-XBRL in the proxy.
- So: use the **submissions** API to *find* filings, fetch the **proxy HTML** to read the SCT, and treat
  Inline-XBRL as an *additional* source for the specific tables that carry it — never assume `companyfacts`
  has proxy comp.

## Step 1 — resolve a company and see its filings

```bash
export SEC_UA="Your Name your.email@example.com"   # REQUIRED (must contain an email); SEC refuses otherwise
python3 scripts/edgar.py AAPL                       # company + recent filings, each labeled with what it is
python3 scripts/edgar.py AAPL --form "8-K"          # recent filings of one form (with URLs)
python3 scripts/edgar.py AAPL --def14a              # latest proxy (or a foreign-issuer note)
python3 scripts/edgar.py AAPL --index <ACCESSION>   # every document in a filing (exhibits, iXBRL instance)
```

Under the hood: `company_tickers.json` (ticker→CIK) → `submissions/CIK##########.json` (filing history) →
archive URLs (the actual documents + `index.json` for the document list).

## Step 2 — identify what a filing IS

`scripts/forms.py` is a **form-type knowledge map** — the "understand what you're looking at" layer.

```bash
python3 scripts/forms.py                # the catalog
python3 scripts/forms.py "8-K"          # what an 8-K is, what's inside, where the comp/insider signal is
python3 scripts/forms.py "DEF 14A/A"    # amendments (/A) and aliases ("proxy", "10K", "13D") resolve
```

The forms that carry the most signal:

| Form | What it is | Where the signal is |
|---|---|---|
| **DEF 14A** | Definitive proxy | **Executive comp (SCT), pay ratio, pay-vs-performance, board, say-on-pay** |
| **8-K** (Item 5.02) | Material event | **Exec departures/appointments + new comp arrangements, in real time** |
| **Form 4** | Insider transaction | **Insider buys/sells, option grants/exercises** (structured XML) |
| **SC 13D / 13G** | >5% ownership | Activist (13D) vs passive (13G) stakes |
| **10-K** | Annual report | Risk factors, MD&A, financials, SBC footnote (comp is *by reference* to the proxy) |
| **S-1** | IPO registration | Pre-IPO comp + founder equity |
| **20-F / 40-F / 6-K** | Foreign issuer | FPI comp — a **different basis** from a US SCT (annual 20-F/40-F, or a furnished 6-K circular) |

## Step 3 — fetch a filing (and its index)

Always look at the **filing index** rather than trusting one URL — a submission has the primary document,
exhibits, and (for tagged forms) the Inline-XBRL instance:

```bash
python3 scripts/edgar.py <TICKER> --index <ACCESSION>
```

To read a specific table, fetch the primary document and locate the section by **name** (modern inline-XBRL
filings bury tables ~100k+ characters in). The library exposes `find_section(url, "Summary Compensation
Table")` for exactly this, and WebFetch of the document URL also works.

## Step 4 — route to a specialized workflow

- **Executive compensation** → the [`sec-comp-research`](../sec-comp-research/) skill (reads the SCT, builds
  a peer group, positions pay at percentiles). It uses these same primitives.
- **Insider activity** (Form 4), **activist stakes** (13D/G), **risk factors** (10-K) → read the structured
  document the form's catalog entry points to.

## Fair-access rules (baked in, not optional)

SEC treats these as *correctness*, not ops polish — it silently rate-limits/blocks abusive callers:

- **Declared contact User-Agent** — set `SEC_UA="Name email"`; the scripts **refuse** to call SEC without a
  real contact (an email), so you can't accidentally send a non-compliant request.
- **Throttle** well under SEC's 10 req/s ceiling (the client paces itself to ~5/s) and **retry with backoff**
  on 429/5xx.
- **Cache** what you fetch; don't run uncontrolled parallel crawls.
- **Server-side only** — `data.sec.gov` has no CORS, so this belongs in a CLI/tool runtime, not browser JS.
- **Cite the filing** — every figure should carry its exact SEC URL (accession + document).

## Guardrails

- **Public data, presented honestly** — a dated snapshot, not investment/legal/accounting advice.
- **Know the disclosure basis** — a foreign issuer's 20-F comp is *not* a US SCT; don't compare them as if.
- **Unknown form? Say so** — `forms.py` returns "no catalog entry" rather than guessing.
- **Verify against the filing** — the catalog tells you where to look; the filing is the source of truth.

Part of the [Agentic PeopleOS](../../README.md) portfolio.
