#!/usr/bin/env python3
"""Offline smoke test for the sec-edgar foundation skill (no network — SEC calls are stubbed).
Run: python3 test_skill.py

Proves: the form-type catalog is well-formed and classify() handles aliases/amendments/unknowns; the
fair-access UA guard refuses a non-contact User-Agent; filing listing filters by form; def14a prefers a US
DEF 14A and, for a foreign issuer, the ANNUAL 20-F over a stray 6-K; and URLs are built correctly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forms  # noqa: E402
import edgar  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def raises(exc, fn, label):
    global passed
    try:
        fn()
        assert False, f"FAILED (no raise): {label}"
    except exc:
        passed += 1


# ---- forms.py: catalog well-formed + classify() ----
_REQ = {"name", "what", "contains", "disclosure", "comp_relevance", "notes"}
for k, v in forms.FORMS.items():
    ok(_REQ <= set(v), f"catalog entry {k} has all required fields")
    ok(isinstance(v["contains"], list) and v["contains"], f"catalog entry {k} lists contents")
ok(forms.classify("DEF 14A")["name"].startswith("Definitive proxy"), "DEF 14A classifies")
ok(forms.classify("proxy")["form"] == "DEF 14A", "an alias ('proxy') resolves to DEF 14A")
ok(forms.classify("10K")["form"] == "10-K", "an alias ('10K') resolves to 10-K")
ok(forms.classify("13D")["form"] == "SC 13D", "an alias ('13D') resolves to SC 13D")
amd = forms.classify("8-K/A")
ok(amd["form"] == "8-K/A" and "AMENDMENT" in amd["notes"], "an amendment (/A) is flagged")
ok(forms.classify("NOTAFORM") is None, "an unknown form returns None (honest, no guess)")
ok(forms.classify("") is None and forms.classify(None) is None, "empty/None classify to None")
# the comp-relevant forms are present
for f in ("DEF 14A", "8-K", "4", "20-F", "10-K", "S-1", "SC 13D"):
    ok(f in forms.FORMS, f"the catalog covers {f}")

# ---- edgar fair-access UA guard ----
_orig_ua = edgar.UA
try:
    edgar.UA = edgar._UA_PLACEHOLDER
    raises(edgar.EdgarError, edgar._require_ua, "the placeholder UA is refused")
    edgar.UA = "no-email-here"
    raises(edgar.EdgarError, edgar._require_ua, "a UA without an email is refused")
    edgar.UA = "Real Person real@example.com"
    edgar._require_ua()          # should not raise
    ok(True, "a contact UA (with an email) passes the guard")
finally:
    edgar.UA = _orig_ua

# ---- edgar navigation with a STUBBED _get (offline) ----
_TICKERS = {"0": {"cik_str": 1234567, "ticker": "TEST", "title": "Test Co"},
            "1": {"cik_str": 1845338, "ticker": "FPI", "title": "Foreign Co Ltd."}}


def _fake_get(url, want_json=True):
    if "company_tickers" in url:
        return _TICKERS
    if "submissions/CIK0001234567" in url:      # a US filer: has a DEF 14A + 10-K + 8-Ks
        return {"filings": {"recent": {
            "form": ["8-K", "DEF 14A", "10-K", "8-K", "4"],
            "accessionNumber": ["0000000000-25-000005", "0000000000-25-000004", "0000000000-25-000003",
                                "0000000000-25-000002", "0000000000-25-000001"],
            "primaryDocument": ["e.htm", "proxy.htm", "10k.htm", "d.htm", "f.xml"],
            "filingDate": ["2025-05-01", "2025-04-01", "2025-03-01", "2025-02-01", "2025-01-01"]}}}
    if "submissions/CIK0001845338" in url:      # a foreign issuer: NO DEF 14A; a NEWER 6-K + an older 20-F
        return {"filings": {"recent": {
            "form": ["6-K", "6-K", "20-F", "6-K"],
            "accessionNumber": ["0000000000-26-000004", "0000000000-26-000003",
                                "0000000000-26-000002", "0000000000-26-000001"],
            "primaryDocument": ["pr.htm", "circular.htm", "annual20f.htm", "old.htm"],
            "filingDate": ["2026-06-01", "2026-05-01", "2026-03-13", "2026-01-01"]}}}
    raise AssertionError(f"unexpected URL in offline test: {url}")


_orig_get = edgar._get
edgar._get = _fake_get
try:
    cik, title = edgar.cik_for_ticker("test")
    ok(cik == "0001234567" and title == "Test Co", "ticker->CIK resolves + zero-pads")
    raises(edgar.EdgarError, lambda: edgar.cik_for_ticker("NOPE"), "an unknown ticker fails closed")

    allf = edgar.company_filings(cik)
    ok(len(allf) == 5 and allf[0]["form"] == "8-K", "company_filings returns all, newest first")
    eightks = edgar.company_filings(cik, forms=("8-K",))
    ok(len(eightks) == 2 and all(f["form"] == "8-K" for f in eightks), "company_filings filters by form")
    ok(eightks[0]["url"].endswith("/000000000025000005/e.htm"), "the archive URL is built (accession de-dashed)")

    d = edgar.def14a("TEST")
    ok(d["disclosure"] == "def14a" and d["form"] == "DEF 14A", "a US filer resolves to its DEF 14A")

    fpi = edgar.def14a("FPI")
    ok(fpi["disclosure"] == "foreign_issuer_or_no_def14a", "a foreign issuer is flagged (no DEF 14A)")
    ok(fpi["form"] == "20-F" and "annual20f.htm" in fpi["url"],
       "the FPI fallback prefers the ANNUAL 20-F over a newer 6-K (Codex fix)")
    ok("foreign private issuer" in fpi["note"], "the FPI note explains the different basis")

    ok(edgar.classify_form("8-K")["name"].startswith("Current report"), "classify_form is wired to the catalog")
finally:
    edgar._get = _orig_get

print(f"OK — {passed} sec-edgar foundation checks passed "
      f"({len(forms.FORMS)} form types in the catalog).")
