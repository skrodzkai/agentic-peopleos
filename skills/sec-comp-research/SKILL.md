---
name: sec-comp-research
description: >-
  Research executive compensation and build compensation peer groups from PUBLIC SEC filings
  (DEF 14A proxy statements and the Summary Compensation Table). Use this when the user wants to
  benchmark executive pay, analyze or build a peer group, or pull comp data during proxy season —
  e.g. "how does our CEO's pay compare to peers", "pull the exec comp for these companies",
  "build me a comp peer group for <company>". All data is public SEC data; no login or paid source.
---

# SEC Executive-Compensation Research

A procedure + helper scripts for pulling **real, public** executive-compensation data from SEC EDGAR
and building a defensible compensation **peer group** — the work a Total Rewards team does during
proxy season. Everything here uses only public SEC endpoints (no login, no paid data provider).

**What you can do with this skill**
1. Resolve any US-listed company to its SEC filings and find its latest **DEF 14A** (proxy statement).
2. Read the **Summary Compensation Table (SCT)** to get each Named Executive Officer's pay
   (salary, bonus, non-equity incentive, stock awards, option awards, all-other, total).
3. Build a **peer group** by a transparent screen (same industry — an exact match on the label you provide — plus revenue and market cap each within 0.5–2.0×).
4. Position a subject company's exec pay against the peer distribution at target percentiles.

**Guardrails (always apply)**
- This is **public** proxy data. Present it as an illustrative, dated snapshot — **not** investment,
  accounting, tax, or legal advice. A compensation professional must sanity-check before any real use.
- SCT figures are **actual/as-disclosed** pay (equity at grant-date fair value), **not** target
  opportunity. Say so when you position pay against a target-percentile policy.
- Use **medians and percentiles**, never a mean — a single mega-grant (a front-loaded founder award)
  would distort a mean.
- If a role has too few peer data points (e.g. fewer than ~6), **suppress** it — do not report a
  spurious percentile off two data points.
- Some peers are **foreign private issuers** (they file a 20-F / furnish a proxy circular via 6-K, not a
  DEF 14A) and disclose comp differently or in aggregate — note this rather than forcing a US-format read.
- Always cite the exact SEC filing URL for every figure you report.

---

## Step 1 — Find a company's latest proxy (DEF 14A)

Use the **`sec-edgar` foundation skill** (installed alongside this one) to resolve the ticker and find the
proxy — it owns EDGAR navigation, form-type intelligence, and the SEC fair-access discipline:

```bash
export SEC_UA="Your Name your.email@example.com"          # required contact (SEC fair-access)
python3 ../sec-edgar/scripts/edgar.py AAPL --def14a
# -> Apple Inc. (CIK 0000320193)
#    latest DEF 14A: <date>
#    https://www.sec.gov/Archives/edgar/data/320193/.../ny...def14a.htm
```

Under the hood it uses `company_tickers.json` (ticker → CIK) → `submissions/CIK##########.json` (filing
history) → the archive URL. If there is **no** DEF 14A the company is a **foreign private issuer** — the
foundation flags it and points you at its **annual 20-F/40-F** (preferred over a furnished 6-K), where comp
is disclosed on a **different, non-US basis** (do not treat it as a US SCT).

## Step 2 — Read the Summary Compensation Table

Fetch the DEF 14A URL and locate the **Summary Compensation Table** — WebFetch the URL, or use the
foundation's section finder: `python3 ../sec-edgar/scripts/edgar.py AAPL --section "Summary Compensation
Table"`. For a **deterministic, reconciled** parse of the SCT into structured rows with a confidence score
(instead of a semantic read), hand off to the [`sec-proxy-extractor`](../sec-proxy-extractor/) skill:
`python3 ../sec-proxy-extractor/scripts/extractor.py AAPL`. It is the canonical exec-pay table; every NEO
(usually 5) appears with, for the latest fiscal year:

| Column | Maps to |
|---|---|
| Salary | base salary |
| Bonus + Non-Equity Incentive Plan Compensation | annual cash incentive (STI) |
| Stock Awards + Option Awards | long-term incentive / equity (grant-date fair value) |
| Change in Pension Value + All Other Compensation | "all other" |
| **Total** | the SCT **Total** (as-disclosed — reconcile the components to it) |

> **The SCT Total is not "Total Direct Comp".** The **Total** column is authoritative for reconciliation,
> but market-standard **Total Direct Compensation (TDC)** is a *derived* figure that typically **strips
> Change in Pension Value / NQDC** (a non-performance actuarial artifact) and often All-Other. When you
> position pay against a **TDC-percentile** policy (Step 4), compute TDC from the components — don't equate
> it to raw SCT Total. For most software/SaaS issuers pension value is ~$0, so Total ≈ TDC there, but say so.

Extraction tips:
- Take the **latest fiscal year** row for each NEO (the SCT shows up to 3 years).
- Map each NEO's title to a role bucket (principal executive officer → CEO; principal financial
  officer → CFO; COO/President, General Counsel/CLO, CHRO/CPO, etc.).
- In a **transition year** the table lists an outgoing *and* incoming officer for a role — keep the
  incumbent (title without "Former"/"Interim") for a market distribution.
- **Reconcile**: the component columns must sum to the reported **Total** (within rounding). If they
  don't, you missed a column — re-read; do not silently proceed.

**SCT reading traps (why a number looks wrong):**
- **Footnotes are load-bearing.** A mega-grant, a one-time award, a modification, or a pension adjustment is
  explained in a footnote, not the grid — read the footnotes before trusting an outlier.
- **Parentheses = negative;** an **em-dash / blank = no value** (not zero-with-meaning). Don't coerce them.
- **Watch a units caption** — most SCTs are in whole dollars, but check for a rare "in thousands" note.
- **Foreign issuers** (e.g. Canada NI 51-102F6) use different column names (no separate Bonus column; STI is
  all in the incentive column) — map by meaning, not by header — and their pay is a **different basis**, so
  keep it out of a US-SCT distribution.
- **State your confidence.** "Found the SCT, latest FY, N NEOs, expected columns → high confidence" vs.
  "multiple comp-like tables, no clean SCT heading → medium, verify" — don't silently guess.

## Step 3 — Build a peer group

Use `scripts/peer_screen.py` (portable, standard-library). Give it a subject (revenue + market cap +
GICS/industry) and a candidate list; it applies a transparent screen and ranks by size fit:

```bash
python3 scripts/peer_screen.py --demo          # runs a worked example
```

The screen (disclosed-market norm): **same industry** (an exact match on the industry label you supply) + revenue **and** market cap each within
**0.5–2.0×** of the subject; headcount is a soft factor, not a gate. Then rank by revenue-weighted
size-closeness. Membership is defensible on one line: "same industry, within 0.5–2.0× our size."

To **use your own peers**: pass `--subject` and `--peers` (see `python3 scripts/peer_screen.py --help`),
or just ask the user for their subject company and candidate peers and screen them.

## Step 4 — Position pay against the peer group

For each subject executive and each pay element (base, STI, total cash, LTI/equity, total direct comp),
compute the subject's **percentile** within the peer distribution for that role, and compare to the
committee's **target-percentile policy** (e.g. base P45–55, total direct comp P50–65). Flag each as
below / within / above target. Report peer P25 / median / P75 alongside the subject value, and always
say the peer figures are actual SCT-disclosed pay.

---

## A typical run

> "Build a comp peer group for a ~$850M-revenue SaaS company and pull the CEO pay for the peers."

1. Ask for (or infer) the subject's revenue, market cap, and industry.
2. Screen a candidate list with `peer_screen.py` → the peer group.
3. For each peer, `../sec-edgar/scripts/edgar.py <TICKER> --def14a` → latest DEF 14A → read the SCT CEO row.
4. Summarize: peer CEO median / P25 / P75 (medians!), each with its SEC source URL, dated snapshot,
   actual-not-target caveat.

Keep it honest, cite every filing, and defer the judgment to the human.
