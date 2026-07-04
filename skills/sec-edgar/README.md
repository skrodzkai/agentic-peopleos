# sec-edgar

A portable **agent skill** — the foundation layer for working with SEC EDGAR. Point an agent at a company
or a filing and it can resolve the ticker, list the filings, **identify what each filing type is and what's
inside it**, and fetch a filing plus its document index — with SEC fair-access built in. Specialized skills
(like [`sec-comp-research`](../sec-comp-research/)) build on top of it. **Public SEC data only — no login,
no API key, standard library only.**

> A research/navigation aid, **not** investment, tax, accounting, or legal advice. It tells you *what a
> filing is and where to look*; the filing itself is the source of truth.

## What's in here

| File | What it does |
|---|---|
| `SKILL.md` | The procedure (resolve → list/identify → fetch + index → route) + the source-of-truth rule + fair-access |
| `scripts/edgar.py` | Ticker → CIK → **filings**, form-filtered lookups, **filing index**, latest proxy (FPI-aware), fair-access `_get` |
| `scripts/forms.py` | The **form-type knowledge map** — what each filing is, what's inside, how it's disclosed, where the signal is |

Pure standard library (no `pip install`); Python 3.9+.

## Quick start

```bash
export SEC_UA="Your Name your.email@example.com"     # REQUIRED (must contain an email) — SEC refuses a generic UA

python3 scripts/edgar.py PCTY                          # company + recent filings, each labeled with what it is
python3 scripts/edgar.py AAPL --form "8-K"             # recent filings of one form, with URLs
python3 scripts/edgar.py MNDY --def14a                 # latest proxy — or, for a foreign issuer, its 20-F
python3 scripts/edgar.py PCTY --index <ACCESSION>      # every document in a filing (exhibits + iXBRL instance)

python3 scripts/forms.py "8-K"                          # what an 8-K IS and where the signal is
python3 scripts/forms.py "DEF 14A/A"                    # amendments + aliases ("proxy", "10K", "13D") resolve
```

## Why it's designed this way

- **Proxy HTML is the source of truth for executive comp, not the XBRL `companyfacts` API.** The skill uses
  `submissions` to *find* filings and reads the proxy HTML for the pay tables — the single most common
  mistake (assuming `companyfacts` has the Summary Compensation Table) is designed out.
- **Fair access is correctness.** SEC silently throttles/blocks abusive callers, so the client **requires a
  contact User-Agent** (refuses without one), paces itself under the 10 req/s ceiling, and retries with
  backoff on 429/5xx. `data.sec.gov` has no CORS, so this is a server-side/CLI tool by design.
- **Honest about what it doesn't know.** `forms.py` returns "no catalog entry" for an unrecognized form
  rather than guessing; a foreign issuer's 20-F comp is flagged as a *different basis* from a US SCT.

## Install

Copy `sec-edgar/` into your agent's skills directory (`.claude/skills/` for Claude Code / the Agent SDK, or
`~/.claude/skills/` for a personal skill). The `sec-comp-research` skill uses these primitives — install both
for the executive-compensation workflow.

## Compliance

SEC's data programs ask automated users to send a descriptive `User-Agent` and stay within fair-access
limits — see SEC's [webmaster FAQ](https://www.sec.gov/os/webmaster-faq#developers) and
[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

Part of the [Agentic PeopleOS](../../README.md) portfolio.
