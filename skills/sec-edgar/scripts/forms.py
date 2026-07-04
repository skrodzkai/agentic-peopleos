#!/usr/bin/env python3
"""A form-type knowledge map for SEC EDGAR — what each filing IS, what's inside it, how it's disclosed,
and which items/tables matter. This is the "understand what you're looking at" layer: an agent pointed at
any filing can look up the form and know how to read it (and which specialized workflow to route to).

Standard library only. Data + a small classifier; no network.

    python3 forms.py                 # list the catalog
    python3 forms.py "DEF 14A"       # explain one form (accepts amendments like 'DEF 14A/A', '10-K/A')
    python3 forms.py --json "8-K"    # machine-readable

As a library:
    from forms import classify, FORMS
    print(classify("8-K/A")["name"])
"""
from __future__ import annotations

import json
import sys

# disclosure: how the substantive data is encoded — "html" (read the document/tables), "ixbrl" (Inline XBRL
# tags are authoritative), "xbrl_financials" (the data.sec.gov companyfacts API covers the financial
# statements), "text" (older plain-text). Many forms are "html" with an "ixbrl" island (e.g. pay-vs-performance
# inside a proxy). comp_relevance flags where executive-compensation signal lives.
FORMS = {
    "DEF 14A": {
        "name": "Definitive proxy statement",
        "what": "The annual proxy sent to shareholders for the annual meeting — the authoritative source for "
                "executive & director compensation and governance.",
        "contains": ["Summary Compensation Table (SCT)", "Grants of Plan-Based Awards",
                     "Outstanding Equity Awards at Fiscal Year-End", "Option Exercises & Stock Vested",
                     "Pension Benefits", "Nonqualified Deferred Compensation", "Director Compensation",
                     "CEO Pay Ratio", "Pay Versus Performance (Item 402(v))", "say-on-pay proposal", "board & committees"],
        "disclosure": "html + ixbrl (Pay-vs-Performance is Inline-XBRL tagged under Item 402(v))",
        "comp_relevance": "primary — the SCT is THE exec-pay table; read the proxy HTML, not companyfacts",
        "notes": "Amendments/additional material: DEFA14A. Merger proxy: DEFM14A. Preliminary: PRE 14A. "
                 "Information statement (no vote): DEF 14C.",
    },
    "DEFA14A": {
        "name": "Additional proxy soliciting material",
        "what": "Supplemental material filed alongside/after a DEF 14A (extra solicitation, corrections, "
                "presentations). Not a standalone comp source.",
        "contains": ["supplemental solicitation", "corrections", "investor presentations"],
        "disclosure": "html",
        "comp_relevance": "secondary — read the DEF 14A for the SCT; DEFA14A may carry a comp correction",
        "notes": "Look for a superseding DEFA14A before trusting a DEF 14A figure in a contested year.",
    },
    "DEFM14A": {
        "name": "Definitive merger/transaction proxy",
        "what": "Proxy seeking a shareholder vote on a merger, acquisition, or major transaction.",
        "contains": ["deal terms", "fairness opinion", "golden-parachute (Item 402(t)) comp", "background of the merger"],
        "disclosure": "html",
        "comp_relevance": "change-in-control comp (Item 402(t) golden parachutes), not the annual SCT",
        "notes": "For annual pay use the DEF 14A; DEFM14A carries transaction/parachute pay.",
    },
    "10-K": {
        "name": "Annual report",
        "what": "The company's comprehensive annual report — audited financials, business, risk factors, MD&A.",
        "contains": ["audited financial statements", "risk factors (Item 1A)", "MD&A (Item 7)",
                     "business (Item 1)", "properties", "legal proceedings", "share-based comp footnote"],
        "disclosure": "html + xbrl_financials (companyfacts covers the financial statements)",
        "comp_relevance": "indirect — Item 11 usually INCORPORATES exec comp BY REFERENCE to the proxy; the "
                          "SBC footnote gives aggregate stock-comp expense, not per-NEO pay",
        "notes": "Executive comp detail lives in the DEF 14A, not the 10-K, for most filers.",
    },
    "10-Q": {
        "name": "Quarterly report",
        "what": "Unaudited quarterly financial report.",
        "contains": ["unaudited financials", "MD&A", "quarterly risk-factor updates"],
        "disclosure": "html + xbrl_financials",
        "comp_relevance": "minimal — quarterly SBC expense only",
        "notes": "",
    },
    "8-K": {
        "name": "Current report (material event)",
        "what": "A near-real-time report of a material event, filed within ~4 business days.",
        "contains": ["Item 5.02 (departure/appointment/comp of directors & officers)",
                     "Item 1.01 (material agreement)", "Item 2.02 (results)", "Item 5.07 (vote results)"],
        "disclosure": "html (+ exhibits)",
        "comp_relevance": "EVENT-level — Item 5.02 is where an exec departure/appointment and new comp "
                          "arrangements are disclosed AS THEY HAPPEN (ahead of the next proxy)",
        "notes": "Filter 8-Ks to Item 5.02 to track executive changes and new comp agreements in real time.",
    },
    "20-F": {
        "name": "Foreign private issuer annual report",
        "what": "The annual report for a foreign private issuer (the FPI analogue of a 10-K).",
        "contains": ["Item 6.B compensation (often AGGREGATE, sometimes per-officer)", "financials (IFRS/US GAAP)",
                     "risk factors"],
        "disclosure": "html + xbrl_financials",
        "comp_relevance": "FPI exec comp — a DIFFERENT basis from a US SCT; often aggregate, not per-NEO "
                          "grant-date detail; not cleanly comparable to a US Summary Compensation Table",
        "notes": "FPIs do NOT file a DEF 14A. Comp is on the 20-F (Item 6) or furnished via 6-K.",
    },
    "40-F": {
        "name": "Canadian issuer annual report (MJDS)",
        "what": "Annual report for a Canadian issuer under the Multijurisdictional Disclosure System.",
        "contains": ["the Canadian annual disclosure (AIF, MD&A, financials)", "exec comp per Canadian rules"],
        "disclosure": "html",
        "comp_relevance": "FPI/Canadian exec comp (NI 51-102F6 basis), not a US SCT",
        "notes": "Comp may be in the 40-F or an attached/ furnished circular.",
    },
    "6-K": {
        "name": "Foreign issuer furnished report",
        "what": "A report FURNISHED (not filed) by a foreign private issuer to convey material info, including "
                "the management information / proxy circular.",
        "contains": ["proxy/information circular", "interim results", "press releases"],
        "disclosure": "html",
        "comp_relevance": "FPI exec comp is often furnished HERE as a circular — non-US format, different "
                          "basis from a US SCT",
        "notes": "6-Ks are frequent; when hunting FPI comp prefer the annual 20-F/40-F, then the circular 6-K.",
    },
    "S-1": {
        "name": "IPO registration statement",
        "what": "The registration statement for an initial public offering.",
        "contains": ["business", "risk factors", "use of proceeds", "executive compensation (pre-IPO)",
                     "principal stockholders", "financials"],
        "disclosure": "html",
        "comp_relevance": "pre-IPO exec comp + founder equity — a snapshot at the offering, not an annual SCT",
        "notes": "Follow-on/shelf prospectuses: 424B*. Amendments: S-1/A.",
    },
    "424B4": {
        "name": "Prospectus (final)",
        "what": "The final prospectus for a securities offering (the priced deal).",
        "contains": ["offering terms", "use of proceeds", "risk factors"],
        "disclosure": "html",
        "comp_relevance": "offering-time comp/dilution context, not an annual SCT",
        "notes": "424B1/2/3/5 are prospectus variants under Rule 424(b).",
    },
    "3": {
        "name": "Initial statement of beneficial ownership",
        "what": "An insider's initial ownership statement (filed when they become an officer/director/10% owner).",
        "contains": ["initial holdings of an insider"],
        "disclosure": "ixbrl (Section 16 forms are XML/structured)",
        "comp_relevance": "who the insiders ARE (baseline holdings); pairs with Form 4 activity",
        "notes": "Section 16. Structured XML — parse the ownership tags directly.",
    },
    "4": {
        "name": "Statement of changes in beneficial ownership",
        "what": "An insider's report of a transaction (buy/sell/grant/exercise), due within 2 business days.",
        "contains": ["transaction (code, shares, price, date)", "post-transaction holdings"],
        "disclosure": "ixbrl (structured XML)",
        "comp_relevance": "insider BUYING/SELLING + option grants/exercises — real-time equity signal",
        "notes": "Section 16. The single best real-time insider-activity feed; parse the XML.",
    },
    "5": {
        "name": "Annual statement of changes in beneficial ownership",
        "what": "An insider's annual catch-up for transactions exempt from immediate Form 4 reporting.",
        "contains": ["deferred/exempt insider transactions"],
        "disclosure": "ixbrl (structured XML)",
        "comp_relevance": "insider transactions not already on a Form 4",
        "notes": "Section 16.",
    },
    "SC 13D": {
        "name": "Beneficial ownership report (activist, >5%)",
        "what": "Filed by a person/group acquiring >5% with intent to INFLUENCE control (activist posture).",
        "contains": ["identity of the acquirer", "purpose of the transaction", "plans/proposals"],
        "disclosure": "html",
        "comp_relevance": "not comp — but signals an activist stake / potential board & pay pressure",
        "notes": "Passive >5% holders file the lighter SC 13G instead.",
    },
    "SC 13G": {
        "name": "Beneficial ownership report (passive, >5%)",
        "what": "The short-form >5% ownership report for passive institutional holders.",
        "contains": ["holder identity", "amount owned"],
        "disclosure": "html",
        "comp_relevance": "not comp — ownership concentration context",
        "notes": "Active/control intent uses SC 13D.",
    },
    "13F-HR": {
        "name": "Institutional investment manager holdings",
        "what": "A quarterly report of an institutional manager's (>$100M) 13(f) equity holdings.",
        "contains": ["list of holdings (issuer, CUSIP, value, shares)"],
        "disclosure": "xbrl (structured XML information table)",
        "comp_relevance": "not comp — who OWNS a company (fund positioning)",
        "notes": "Parse the structured information table.",
    },
    "11-K": {
        "name": "Employee benefit / ESOP plan annual report",
        "what": "The annual report for an employee stock-purchase / savings / ESOP plan.",
        "contains": ["plan financial statements", "participant activity"],
        "disclosure": "html + xbrl_financials",
        "comp_relevance": "broad-based employee equity/benefit plans (not NEO pay)",
        "notes": "",
    },
}

# common alias -> canonical key, so 'proxy'/'annual report'/'10K' resolve
_ALIASES = {
    "PROXY": "DEF 14A", "PROXY STATEMENT": "DEF 14A", "10K": "10-K", "10Q": "10-Q", "8K": "8-K",
    "20F": "20-F", "40F": "40-F", "6K": "6-K", "S1": "S-1", "FORM 4": "4", "FORM 3": "3", "FORM 5": "5",
    "SCHEDULE 13D": "SC 13D", "SCHEDULE 13G": "SC 13G", "13F": "13F-HR", "13D": "SC 13D", "13G": "SC 13G",
}


def classify(form: str) -> dict | None:
    """Return the knowledge-map entry for a form string, tolerating amendments (a trailing '/A') and common
    aliases. Returns None for an unknown form (an honest 'I don't have a note for this' rather than a guess)."""
    if not form:
        return None
    raw = str(form).strip().upper()
    amended = raw.endswith("/A")
    base = raw[:-2].strip() if amended else raw
    key = base if base in FORMS else _ALIASES.get(base)
    if key is None:
        return None
    entry = dict(FORMS[key])
    entry["form"] = key + ("/A" if amended else "")
    if amended:
        entry["notes"] = ("This is an AMENDMENT (/A) to a " + key + " — read it together with the original. "
                          + entry.get("notes", "")).strip()
    return entry


def _main(argv):
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    if not args:
        if as_json:
            print(json.dumps(FORMS, indent=2))
            return 0
        print("SEC EDGAR form-type catalog (comp-relevant forms first):\n")
        for k, v in FORMS.items():
            print(f"  {k:9s} {v['name']} — {v['comp_relevance'].split(' — ')[0]}")
        print("\nExplain one:  python3 forms.py \"8-K\"   (accepts amendments + aliases; --json for machine form)")
        return 0
    info = classify(args[0])
    if info is None:
        print(f"forms: no catalog entry for {args[0]!r} (unknown/less-common form)", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"{info['form']} — {info['name']}")
    print(f"  what        : {info['what']}")
    print(f"  disclosure  : {info['disclosure']}")
    print(f"  comp signal : {info['comp_relevance']}")
    print(f"  contains    : {', '.join(info['contains'])}")
    if info.get("notes"):
        print(f"  notes       : {info['notes']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
