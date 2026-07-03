#!/usr/bin/env python3
"""Resolve a US-listed ticker to its SEC filings and find its latest proxy (DEF 14A).

Standard library only. Uses SEC's PUBLIC JSON endpoints (no login, no API key). SEC requires a
descriptive User-Agent — set SEC_UA to your own "name email" before real use (see --help).

    python3 edgar.py AAPL                 # latest DEF 14A url (or a foreign-issuer note)
    python3 edgar.py AAPL --fetch         # also print the Summary Compensation Table text window

As a library:
    from edgar import cik_for_ticker, latest_filing, def14a
    print(def14a("PCTY"))
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error

# SEC asks for a descriptive User-Agent (a real contact). It is REQUIRED — a placeholder gets 403'd and is
# non-compliant with SEC's fair-access policy, so we refuse to call SEC until SEC_UA is a real "Name email".
_UA_PLACEHOLDER = "sec-comp-research (set SEC_UA to your name+email)"
UA = os.environ.get("SEC_UA", _UA_PLACEHOLDER)
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"


class EdgarError(RuntimeError):
    pass


def _get(url, want_json=True):
    if UA == _UA_PLACEHOLDER:
        raise EdgarError("SEC_UA is not set — export SEC_UA='Your Name your.email@example.com' before "
                         "querying SEC (its fair-access policy requires a real contact in the User-Agent)")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                data = gzip.decompress(data)
            text = data.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise EdgarError(f"SEC returned HTTP {e.code} for {url} "
                         f"(set SEC_UA to a real 'name email' if this is a 403)") from e
    except urllib.error.URLError as e:                      # DNS / connection refused / TLS
        raise EdgarError(f"network error fetching {url}: {e.reason}") from e
    except (TimeoutError, OSError) as e:                    # socket timeout / reset — must not escape as a traceback
        raise EdgarError(f"network error fetching {url}: {e}") from e
    return json.loads(text) if want_json else text


def cik_for_ticker(ticker: str) -> tuple[str, str]:
    """(cik_str_10_digit, company_title) for a ticker, via SEC's ticker->CIK map. Case-insensitive."""
    t = ticker.strip().upper()
    data = _get(_TICKERS_URL)
    for row in data.values():
        if str(row.get("ticker", "")).upper() == t:
            return f"{int(row['cik_str']):010d}", row.get("title", "")
    raise EdgarError(f"ticker {ticker!r} not found in SEC company_tickers.json")


def _submissions(cik10: str) -> dict:
    return _get(_SUBMISSIONS_URL.format(cik10=cik10))


def latest_filing(cik10: str, forms=("DEF 14A",)):
    """Most recent filing whose form is in `forms`. Returns {form, date, accession, primary_doc, url} or None.
    Searches the recent block; that covers the last ~1000 filings, which always includes the latest proxy."""
    sub = _submissions(cik10)
    recent = sub.get("filings", {}).get("recent", {})
    form = recent.get("form", [])
    acc = recent.get("accessionNumber", [])
    doc = recent.get("primaryDocument", [])
    date = recent.get("filingDate", [])
    want = {f.upper() for f in forms}
    for i in range(len(form)):
        if form[i].upper() in want:
            cik_int = int(cik10)
            acc_nodash = acc[i].replace("-", "")
            return {"form": form[i], "date": date[i], "accession": acc[i], "primary_doc": doc[i],
                    "url": _ARCHIVE_URL.format(cik=cik_int, acc=acc_nodash, doc=doc[i])}
    return None


def def14a(ticker: str) -> dict:
    """Resolve a ticker to its latest DEF 14A. If none exists, flag a likely foreign private issuer and
    return its most recent 20-F / 6-K instead (comp is disclosed differently there)."""
    cik10, title = cik_for_ticker(ticker)
    proxy = latest_filing(cik10, ("DEF 14A",))
    if proxy:
        return {"ticker": ticker.upper(), "cik": cik10, "company": title, "disclosure": "def14a", **proxy}
    alt = latest_filing(cik10, ("20-F", "40-F", "6-K"))
    return {"ticker": ticker.upper(), "cik": cik10, "company": title,
            "disclosure": "foreign_issuer_or_no_def14a",
            "note": "No DEF 14A found — likely a foreign private issuer; exec comp is disclosed on a 20-F/40-F "
                    "or furnished via 6-K, in a non-US format.",
            **(alt or {"url": None, "form": None, "date": None})}


def _print_sct_window(url, window=2800):
    """Fetch the filing and print a readable text window around the Summary Compensation Table heading.
    Modern proxies are inline-XBRL (the SCT sits ~100k+ chars in, far past any fixed head slice), so we
    locate it by NAME rather than printing the boilerplate header."""
    doc = _get(url, want_json=False)
    idx = doc.lower().find("summary compensation table")
    if idx == -1:
        print("\n--- could not locate the 'Summary Compensation Table' heading by name; open the filing URL "
              "above in a browser (or WebFetch it) and search for that table ---")
        return
    snippet = doc[max(0, idx - 200): idx + window]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", snippet)).strip()   # strip tags for terminal reading
    print("\n--- Summary Compensation Table (text window; tags stripped, numbers as-disclosed) ---")
    print(text[:window])


def _main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if not args or "--help" in flags:
        print(__doc__)
        print("\nSET a User-Agent first (SEC 403s a generic one):\n"
              "    export SEC_UA='Your Name your.email@example.com'")
        return 0
    info = def14a(args[0])
    print(f"{info.get('company','?')} (CIK {info.get('cik','?')}) — {info.get('disclosure','?')}")
    if info.get("url"):
        print(f"  latest {info.get('form')}: {info.get('date')}")
        print(f"  {info['url']}")
    if info.get("note"):
        print(f"  NOTE: {info['note']}")
    if "--fetch" in flags and info.get("url"):
        _print_sct_window(info["url"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except EdgarError as e:
        print(f"edgar: {e}", file=sys.stderr)
        sys.exit(1)
