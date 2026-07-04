# sec-comp-research

A portable **agent skill** for pulling **real, public** executive-compensation data from SEC EDGAR and
building a defensible compensation **peer group** — the work a Total Rewards team does during proxy
season. It finds a company's latest proxy, reads the Summary Compensation Table, screens a peer group, and
positions pay at target percentiles. **Public SEC data only — no login, no API key, no paid data provider.**

> Builds on the **[`sec-edgar`](../sec-edgar/) foundation skill** for EDGAR navigation (resolve a ticker,
> find the proxy, read a section, fair-access). Install both. This one adds the comp-specific workflow.

> A research aid, **not** investment, tax, accounting, or legal advice. Everything it produces is an
> illustrative, dated snapshot that a compensation professional should sanity-check before use.

> **How it reads the SCT today:** the **agent** reads the Summary Compensation Table (WebFetch the filing,
> or the foundation's `--section` window) — an honest, working *semantic* read. A deterministic proxy-table
> extractor with confidence scoring + row-level provenance is a *planned* separate layer — see
> [ROADMAP](../ROADMAP.md).

## Install

Copy **both** `sec-edgar/` and `sec-comp-research/` into your agent's skills directory. For Claude Code /
the Claude Agent SDK that is `.claude/skills/` in your project (or `~/.claude/skills/` for a personal skill):

```bash
# from the repo root
cp -r skills/sec-edgar skills/sec-comp-research ~/.claude/skills/
```

The agent reads `SKILL.md` for the procedure, uses the foundation's `edgar.py` to find/read filings, and
calls this skill's `scripts/peer_screen.py` for the peer screen.

## What's in here

| File | What it does |
|---|---|
| `SKILL.md` | The procedure the agent follows (find proxy → read SCT → screen peers → position pay) + guardrails |
| `scripts/peer_screen.py` | Portable **size + industry peer screen** (0.5–2.0× revenue & market cap), fit-ranked (stdlib only) |
| *(EDGAR navigation)* | comes from the [`sec-edgar`](../sec-edgar/) foundation — `edgar.py` (ticker → proxy → SCT) + `forms.py` |

All scripts are pure standard library (no `pip install`) and run on Python 3.9+.

## Quick start

**1. Set a User-Agent.** SEC asks every automated caller to identify itself, and returns HTTP 403 to a
generic one. Set yours once:

```bash
export SEC_UA="Your Name your.email@example.com"
```

**2. Find any US company's latest proxy statement** with the foundation skill (example outputs below —
*your dates/URLs will differ as companies file new proxies*):

```bash
$ python3 ../sec-edgar/scripts/edgar.py PCTY --def14a
Paylocity Holding Corp (CIK 0001591698) — def14a
  latest DEF 14A: 2025-10-23
  https://www.sec.gov/Archives/edgar/data/1591698/000159169825000102/pcty-20251021.htm
```

Foreign private issuers don't file a DEF 14A — the foundation detects that and points you at the annual form:

```bash
$ python3 ../sec-edgar/scripts/edgar.py MNDY --def14a
monday.com Ltd. (CIK 0001845338) — foreign_issuer_or_no_def14a
  latest 20-F: <recent date>
  https://www.sec.gov/Archives/edgar/data/1845338/...
  NOTE: No DEF 14A — likely a foreign private issuer; exec comp is on the 20-F/40-F (annual) or
        furnished via a 6-K circular, on a non-US basis.
```

Use `--section "Summary Compensation Table"` to print a readable text window around that table (located by
name, since modern inline-XBRL proxies bury it deep in the document) so you can read each Named Executive
Officer's pay — e.g. `python3 ../sec-edgar/scripts/edgar.py PCTY --section "Summary Compensation Table"`.

**3. Build a peer group** — the transparent screen a compensation committee uses (same industry — an exact label match,
revenue **and** market cap each within 0.5–2.0× of the subject; headcount a soft factor), ranked by
size-fit:

```bash
$ python3 scripts/peer_screen.py --demo
Subject: Example SaaS Co — $852M rev · $6.4B cap · software
Screen : same industry · revenue $426M-$1.7B · market cap $3.2B-$12.8B

Peer group (3 of 5 screened), best size-fit first:
  BBB   Beta Systems            $729M rev     $5.9B cap  fit 81
  AAA   Alpha Cloud             $995M rev     $6.0B cap  fit 76
  CCC   Gamma Data              $1.4B rev     $4.0B cap  fit 41

Excluded (failing criterion):
  DDD   Delta Payments       — fails: industry, revenue, market_cap
  EEE   Eps Micro            — fails: revenue, market_cap
```

**Use your own peers** — pass a subject and a CSV (revenue and market cap in $ millions):

```bash
$ python3 scripts/peer_screen.py --subject "Acme,852,6400,software" --peers my_peers.csv
# my_peers.csv columns: ticker,name,revenue_musd,market_cap_musd,industry[,employees]
```

## The whole loop, end to end

> "How does our CEO's cash comp compare to peers?"

1. Screen a peer group for the subject (`peer_screen.py`) — same industry, within 0.5–2.0× your size.
2. For each peer, `../sec-edgar/scripts/edgar.py <TICKER> --def14a` → latest DEF 14A → read the CEO row of the Summary Compensation
   Table (salary, bonus + non-equity incentive = cash; stock + option = equity; Total).
3. Summarize the peer distribution with **medians and quartiles** (never a mean — one founder mega-grant
   would blow up an average), and cite the SEC URL behind every figure.
4. Position the subject's pay at a percentile and compare to the committee's target policy.

## Guardrails (baked into `SKILL.md`)

- **Public data, presented honestly** — a dated snapshot, not advice; a human signs off.
- **SCT is actual pay, not target** — equity is grant-date fair value of what was *granted*, not target
  opportunity. Say so when positioning against a target-percentile policy.
- **Medians, not means** — a single front-loaded award distorts an average.
- **Suppress thin roles** — don't report a percentile off two data points.
- **Note foreign issuers** — they disclose comp differently (20-F / 6-K), not on a DEF 14A.
- **Cite the filing** — every number carries its SEC URL.

## Provenance & compliance

SEC's public data programs ask automated users to send a descriptive `User-Agent` and to stay within
their fair-access rate limits. The `sec-edgar` foundation sends the `SEC_UA` you set and makes only a handful of small
requests per company. See SEC's [webmaster FAQ](https://www.sec.gov/os/webmaster-faq#developers) and
[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

Part of the [Agentic PeopleOS](../../README.md) portfolio.
