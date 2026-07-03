# sec-comp-research

A portable **agent skill** for pulling **real, public** executive-compensation data from SEC EDGAR and
building a defensible compensation **peer group** — the work a Total Rewards team does during proxy
season. Point an agent at this folder and it can find a company's latest proxy, read the Summary
Compensation Table, screen a peer group, and position pay at target percentiles. **Public SEC data
only — no login, no API key, no paid data provider.**

> This is a research aid, **not** investment, tax, accounting, or legal advice. Everything it produces
> is an illustrative, dated snapshot that a compensation professional should sanity-check before use.

## Install

Copy the `sec-comp-research/` folder into your agent's skills directory. For Claude Code / the Claude
Agent SDK that is `.claude/skills/` in your project (or `~/.claude/skills/` for a personal skill):

```bash
# from the repo root
cp -r skills/sec-comp-research ~/.claude/skills/
# or, to try it against a checkout of this repo, just run the scripts directly (below)
```

The agent reads `SKILL.md` for the procedure and calls the two helper scripts in `scripts/`.

## What's in here

| File | What it does |
|---|---|
| `SKILL.md` | The procedure the agent follows (find proxy → read SCT → screen peers → position pay) + guardrails |
| `scripts/edgar.py` | Ticker → CIK → **latest DEF 14A URL** via SEC's public JSON APIs (stdlib only) |
| `scripts/peer_screen.py` | Portable **size + industry peer screen** (0.5–2.0× revenue & market cap), fit-ranked (stdlib only) |

Both scripts are pure standard library (no `pip install`) and run on Python 3.9+.

## Quick start

**1. Set a User-Agent.** SEC asks every automated caller to identify itself, and returns HTTP 403 to a
generic one. Set yours once:

```bash
export SEC_UA="Your Name your.email@example.com"
```

**2. Find any US company's latest proxy statement** (example outputs below — *your dates/URLs will differ
as companies file new proxies*):

```bash
$ python3 scripts/edgar.py PCTY
Paylocity Holding Corp (CIK 0001591698) — def14a
  latest DEF 14A: 2025-10-23
  https://www.sec.gov/Archives/edgar/data/1591698/000159169825000102/pcty-20251021.htm
```

Foreign private issuers don't file a DEF 14A — the script detects that and points you at the right form:

```bash
$ python3 scripts/edgar.py MNDY
monday.com Ltd. (CIK 0001845338) — foreign_issuer_or_no_def14a
  latest 6-K: <recent date>          # 6-Ks are furnished often, so this drifts
  https://www.sec.gov/Archives/edgar/data/1845338/...
  NOTE: No DEF 14A found — likely a foreign private issuer; exec comp is disclosed on a 20-F/40-F
        or furnished via 6-K, in a non-US format.
```

Add `--fetch` to also print a readable text window around the filing's **Summary Compensation Table**
(located by name, since modern inline-XBRL proxies bury it deep in the document) so you can read each
Named Executive Officer's pay.

**3. Build a peer group** — the transparent screen a compensation committee uses (same industry group,
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
2. For each peer, `edgar.py <TICKER>` → latest DEF 14A → read the CEO row of the Summary Compensation
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
their fair-access rate limits. `edgar.py` sends the `SEC_UA` you set and makes only a handful of small
requests per company. See SEC's [webmaster FAQ](https://www.sec.gov/os/webmaster-faq#developers) and
[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

Part of the [Agentic PeopleOS](../../README.md) portfolio.
