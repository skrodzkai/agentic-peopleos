#!/usr/bin/env python3
"""Navigate SEC EDGAR: resolve a ticker, list a company's filings, identify what each filing IS, and fetch
a filing (and its document index) — with SEC fair-access built in. This is the foundation an agent points
at ANY filing to know how to read it; specialized skills (e.g. sec-comp-research) build on top.

Standard library only. Public SEC JSON endpoints, no login/API key. SEC's fair-access policy REQUIRES a
descriptive contact User-Agent — set SEC_UA to "Your Name your.email@example.com" (it must contain an
email) or calls are refused before they hit SEC.

    export SEC_UA="Your Name you@example.com"
    python3 edgar.py AAPL                     # company + its recent filings, each labeled with what it is
    python3 edgar.py AAPL --form "8-K"        # recent filings of one form
    python3 edgar.py AAPL --def14a            # latest proxy (executive compensation)
    python3 edgar.py AAPL --index <ACCESSION> # every document in a filing (the index)
    python3 edgar.py --explain "8-K"          # what a form IS (delegates to forms.py; accepts amendments)

As a library:
    from edgar import cik_for_ticker, company_filings, latest_filing, def14a, filing_index, classify_form
    from edgar import fetch_document, find_section     # raw document (table structure) / a text window by heading
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

try:
    from forms import classify as classify_form            # form-type knowledge map (same dir)
except ImportError:                                        # allow running from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from forms import classify as classify_form

_UA_PLACEHOLDER = "sec-edgar (set SEC_UA to your name+email)"
UA = os.environ.get("SEC_UA", _UA_PLACEHOLDER)
# a real contact carries an email — name@example.com — not just an '@'. SEC's fair-access policy expects a
# descriptive contact, so a bare '@', whitespace, or the placeholder is refused before any request goes out.
_UA_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _effective_ua():
    """The User-Agent to send, resolved at CALL time — so setting SEC_UA after import (or in library use)
    takes effect (the module-level UA is only the import-time default)."""
    return os.environ.get("SEC_UA") or UA
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# fair access: SEC's ceiling is 10 req/s — stay well under it, and retry politely on throttle/5xx.
_MIN_INTERVAL = 0.2          # ~5 req/s
_RETRY_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_last_request = [0.0]


class EdgarError(RuntimeError):
    pass


def _require_ua():
    ua = _effective_ua()
    if ua == _UA_PLACEHOLDER or not _UA_RE.search(ua):
        raise EdgarError("SEC_UA must be a real contact with an email (name@example.com), e.g. "
                         "SEC_UA='Your Name your.email@example.com' — SEC's fair-access policy requires it")
    return ua


def _throttle():
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()


_ALLOWED_HOSTS = ("https://www.sec.gov/", "https://data.sec.gov/")


def _get(url, want_json=True):
    ua = _require_ua()
    # only ever fetch from SEC over https — a reusable public skill must not be turned into an SSRF/file
    # reader (e.g. find_section(url=...) with file:///etc/hosts or an internal host).
    if not any(str(url).startswith(h) for h in _ALLOWED_HOSTS):
        raise EdgarError(f"refusing a non-SEC URL {url!r} — only {' and '.join(_ALLOWED_HOSTS)} are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    data = gzip.decompress(data)
                text = data.decode("utf-8", errors="replace")
            if not want_json:
                return text
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise EdgarError(f"SEC returned invalid JSON for {url}: {e}") from e
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code in _RETRY_CODES and attempt < _MAX_RETRIES:
                time.sleep(0.5 * (2 ** attempt))            # exponential backoff on throttle / transient 5xx
                continue
            if e.code == 403:
                raise EdgarError(f"SEC returned 403 for {url} — set SEC_UA to a real 'Name email' contact") from e
            raise EdgarError(f"SEC returned HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise EdgarError(f"network error fetching {url}: {e}") from e
    raise EdgarError(f"failed to fetch {url}: {last_exc}")   # pragma: no cover (loop always returns/raises)


def cik_for_ticker(ticker: str) -> tuple[str, str]:
    """(10-digit CIK, company title) for a ticker, via SEC's ticker->CIK map. Case-insensitive."""
    t = ticker.strip().upper()
    for row in _get(_TICKERS_URL).values():
        if str(row.get("ticker", "")).upper() == t:
            return f"{int(row['cik_str']):010d}", row.get("title", "")
    raise EdgarError(f"ticker {ticker!r} not found in SEC company_tickers.json")


def _submissions(cik10: str) -> dict:
    return _get(_SUBMISSIONS_URL.format(cik10=cik10))


def company_filings(cik10: str, forms=None, limit: int = 40) -> list[dict]:
    """Recent filings for a company as {form, date, accession, primary_doc, url}, newest first. `forms` (an
    iterable of form strings, case-insensitive) filters; None returns all. Reads the submissions 'recent'
    block (the last ~1000 filings — always covers current proxies/annuals)."""
    recent = _submissions(cik10).get("filings", {}).get("recent", {})
    form, acc = recent.get("form", []), recent.get("accessionNumber", [])
    doc, date = recent.get("primaryDocument", []), recent.get("filingDate", [])
    want = {f.upper() for f in forms} if forms else None
    cik_int = int(cik10)
    out = []
    for i in range(len(form)):
        if want is not None and form[i].upper() not in want:
            continue
        a, d = str(acc[i]), str(doc[i])
        # defensive: the accession + primary-doc come from SEC's JSON, but validate the path components
        # anyway (an 18-digit dashed accession; a flat filename with no traversal, encoded or nested) first.
        safe = _safe_doc_name(d)
        if not _ACCESSION_RE.match(a) or safe is None:
            continue
        out.append({"form": form[i], "date": date[i], "accession": a, "primary_doc": d,
                    "url": f"{_ARCHIVE}/{cik_int}/{a.replace('-', '')}/{safe}"})
        if len(out) >= limit:
            break
    return out


def latest_filing(cik10: str, forms) -> dict | None:
    """The single most recent filing whose form is in `forms` (or None)."""
    hits = company_filings(cik10, forms=forms, limit=1)
    return hits[0] if hits else None


_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def _safe_doc_name(name):
    """A single safe path component for an Archives URL, or None. Decodes percent-encoding FIRST (so
    %2e%2e / %2f nested-or-encoded traversal can't sneak through), rejects path separators, '..', whitespace
    and control chars, then re-quotes the component. A SEC primary-doc name is a flat filename like
    'pcty-20251021.htm' — anything with a slash or traversal is not one."""
    if not name:
        return None
    raw = urllib.parse.unquote(str(name))
    if "/" in raw or "\\" in raw or ".." in raw or any(c.isspace() or ord(c) < 32 for c in raw):
        return None
    return urllib.parse.quote(raw)


def filing_index(cik10: str, accession: str) -> dict:
    """The document index for one filing — every file in the submission (name, type, size). Lets an agent see
    all documents (the primary proxy, exhibits, the Inline-XBRL instance) rather than trusting one URL."""
    if not _ACCESSION_RE.match(str(accession).strip()):
        raise EdgarError(f"invalid accession {accession!r} — expected ##########-##-###### "
                         f"(18 digits, e.g. 0001591698-25-000102)")
    acc_nodash = accession.replace("-", "")
    base = f"{_ARCHIVE}/{int(cik10)}/{acc_nodash}"
    idx = _get(f"{base}/index.json")
    items = idx.get("directory", {}).get("item", [])
    docs = []
    for it in items:
        name = it.get("name")
        # each item name must be a single safe path component before it becomes a URL — a directory listing
        # carrying '..', an absolute/nested path (encoded or not), or whitespace would build a traversing URL.
        safe = _safe_doc_name(name)
        if safe is None:
            continue
        docs.append({"name": name, "type": it.get("type"), "size": it.get("size"),
                     "url": f"{base}/{safe}"})
    return {"accession": accession, "base_url": base, "documents": docs}


def def14a(ticker: str) -> dict:
    """Resolve a ticker to its latest DEF 14A (US executive-comp proxy). If none exists the company is a
    foreign private issuer — return its most recent ANNUAL foreign form (20-F/40-F preferred over a furnished
    6-K), with a note, since comp is disclosed there on a different basis."""
    cik10, title = cik_for_ticker(ticker)
    proxy = latest_filing(cik10, ("DEF 14A",))
    if proxy:
        return {"ticker": ticker.upper(), "cik": cik10, "company": title, "disclosure": "def14a", **proxy}
    # FPI fallback: prefer the newest ANNUAL report (either 20-F OR 40-F, whichever is more recent) over a
    # recent unrelated 6-K — a Canadian MJDS filer may file 40-F, not 20-F.
    alt = latest_filing(cik10, ("20-F", "40-F")) or latest_filing(cik10, ("6-K",))
    return {"ticker": ticker.upper(), "cik": cik10, "company": title, "disclosure": "foreign_issuer_or_no_def14a",
            "note": "No DEF 14A — likely a foreign private issuer; exec comp is on the 20-F/40-F (annual) or "
                    "furnished via a 6-K circular, on a non-US basis.",
            **(alt or {"url": None, "form": None, "date": None, "accession": None, "primary_doc": None})}


def fetch_document(url: str) -> str:
    """Fetch a filing document (HTML/text) as a string, with the fair-access UA + SEC-host guard + retry
    that _get enforces. The public entry point a higher layer (e.g. sec-proxy-extractor) uses to get the
    RAW document when it needs the table STRUCTURE, not the tag-stripped text window that find_section
    returns. Refuses any non-SEC URL."""
    return _get(url, want_json=False)


def find_section(url: str, heading: str, window: int = 2800) -> str | None:
    """Fetch a filing and return a readable text window around a named heading (e.g. 'Summary Compensation
    Table'), tags stripped — modern inline-XBRL filings bury such tables deep, so locate them by NAME. The
    WHOLE document is first normalized (tags -> spaces, HTML entities decoded, whitespace collapsed) so a
    heading split by markup or non-breaking spaces ('Summary&nbsp;Compensation<br>Table') still matches.
    Returns None if the heading isn't found; a blank heading is refused."""
    import html
    h = " ".join(str(heading).split()).lower()
    if not h:
        raise EdgarError("find_section: heading must be non-empty")
    doc = _get(url, want_json=False)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", doc)))   # normalize the whole document
    idx = text.lower().find(h)
    if idx == -1:
        return None
    return text[max(0, idx - 200): idx + window].strip()[:window]


# ---------------------------------------------------------------- CLI
def _print_overview(ticker):
    cik10, title = cik_for_ticker(ticker)
    print(f"{title} (CIK {cik10})")
    print("Recent filings — each labeled with what it is:\n")
    seen_forms = []
    for f in company_filings(cik10, limit=18):
        info = classify_form(f["form"])
        label = info["name"] if info else "(form not in the catalog)"
        print(f"  {f['date']}  {f['form']:9s} {label}")
        if info and f["form"] not in seen_forms:
            seen_forms.append(f["form"])
    print("\nExplain a form:  python3 edgar.py --explain \"<FORM>\"   |   proxy/comp:  --def14a   |   index:  --index <ACC>")


_VALUE_FLAGS = {"--explain", "--form", "--index", "--section"}


def _flag_value(argv, flag):
    """The token after a value-taking flag. Fail closed if it is missing or is itself another flag —
    `edgar AAPL --form` (or `--form --def14a`) would otherwise silently search for the empty form."""
    i = argv.index(flag)
    if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
        raise EdgarError(f"{flag} needs a value (e.g. `{flag} "
                         f"{'8-K' if flag in ('--form', '--explain') else 'Summary Compensation Table' if flag == '--section' else '0001591698-25-000102'}`)")
    return argv[i + 1]


def _main(argv):
    flags = {a for a in argv if a.startswith("--")}
    # a value-taking flag consumes the NEXT token, so it is NOT the positional ticker (e.g. `--form 8-K`
    # must not be read as `ticker=8-K`). Exclude those value tokens from the positional args.
    value_idx = {i + 1 for i, a in enumerate(argv) if a in _VALUE_FLAGS and i + 1 < len(argv)}
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and i not in value_idx]
    if "--help" in flags or (not args and "--explain" not in flags):
        print(__doc__)
        return 0
    if "--explain" in flags:
        form = _flag_value(argv, "--explain")
        info = classify_form(form)
        if not info:
            print(f"edgar: no catalog entry for {form!r}", file=sys.stderr)
            return 1
        print(json.dumps(info, indent=2))
        return 0

    ticker = args[0]
    if "--section" in flags:
        heading = _flag_value(argv, "--section")
        info = def14a(ticker)                         # section lookups default to the latest proxy (e.g. the SCT)
        url = info.get("url")
        if not url:
            print(f"edgar: no filing URL for {ticker} to search", file=sys.stderr)
            return 1
        window = find_section(url, heading)
        print(f"{info.get('company')} — '{heading}' in {info.get('form')} ({info.get('date')}):\n")
        print(window if window else f"  (heading '{heading}' not found by name — open {url} and search for it)")
        return 0
    if "--def14a" in flags:
        info = def14a(ticker)
        print(f"{info.get('company','?')} (CIK {info.get('cik','?')}) — {info.get('disclosure')}")
        if info.get("url"):
            print(f"  latest {info.get('form')}: {info.get('date')}\n  {info['url']}")
        if info.get("note"):
            print(f"  NOTE: {info['note']}")
        return 0
    if "--index" in flags:
        acc = _flag_value(argv, "--index")
        cik10, _ = cik_for_ticker(ticker)
        idx = filing_index(cik10, acc)
        print(f"Documents in {acc} ({idx['base_url']}):")
        for d in idx["documents"]:
            print(f"  {str(d['type'] or ''):10s} {d['name']}")
        return 0
    if "--form" in flags:
        form = _flag_value(argv, "--form")
        cik10, title = cik_for_ticker(ticker)
        hits = company_filings(cik10, forms=(form,), limit=12)
        info = classify_form(form)
        print(f"{title} — recent {form} ({info['name'] if info else 'form'}):")
        for f in hits:
            print(f"  {f['date']}  {f['url']}")
        if not hits:
            print("  (none in the recent block)")
        return 0
    _print_overview(ticker)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except EdgarError as e:
        print(f"edgar: {e}", file=sys.stderr)
        sys.exit(1)
