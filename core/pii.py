#!/usr/bin/env python3
"""Heuristic PII backstop shared by the vault linter and the decision ledger.

This is a deliberately conservative regex scan for *obvious direct identifiers* — email
addresses, US SSNs, and phone numbers. It is a **backstop, not a guarantee**: it cannot catch
every form of personal data. The real control is the convention that the vault is
process-centric and the ledger carries pseudonymous, minimized payloads. Placeholder domains
(example.com, acme.test, ...) are allowed so synthetic fixtures don't trip it.
"""
import re

ALLOW_DOMAINS = ("example.com", "example.org", "example.net", "acme.test", "acme.example")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Separators allow '-', '.', or whitespace, so space-separated SSNs/phones are caught too.
_SSN_RE = re.compile(r"\b\d{3}[-.\s]\d{2}[-.\s]\d{4}\b")
_PHONE_RE = re.compile(r"(?<!\d)\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")


def _allowed(domain: str) -> bool:
    """A placeholder domain is allowed only on an exact match or a real subdomain — NOT a
    suffix match (which would wrongly clear badexample.com / corp-example.com)."""
    domain = domain.lower()
    return any(domain == a or domain.endswith("." + a) for a in ALLOW_DOMAINS)


def scan(text: str) -> list:
    """Return heuristic PII hit *types* found in `text` (emails/SSNs/phones).

    Deliberately returns the TYPE only, never the matched value — so a scanner, a ledger
    violation, or a CI log can report a hit without itself echoing the identifier.
    """
    hits = []
    if any(not _allowed(m.group(1)) for m in _EMAIL_RE.finditer(text)):
        hits.append("email-like address")
    if _SSN_RE.search(text):
        hits.append("SSN-like number")
    if _PHONE_RE.search(text):
        hits.append("phone-like number")
    return hits
