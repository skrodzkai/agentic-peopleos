#!/usr/bin/env python3
"""Public-safety scan for real ticker collisions in synthetic public artifacts.

This is a defense-in-depth backstop for portfolio examples. Source code and tests may contain
real ticker fixtures deliberately; sample data, generated dashboards, and public docs should not.

    python3 tools/ticker_scan.py examples foundation/data
    python3 tools/ticker_scan.py --self-test
"""
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundation.compute.peers import REAL_TICKERS, real_peer_identifiers  # noqa: E402

# Real tickers that are ALSO common English/business abbreviations are too collision-prone to flag in prose
# (same rationale as the one-letter tickers C/V/T below). 'GTM' is ZoomInfo's ticker but overwhelmingly means
# "go-to-market" in People-analytics copy. The STRUCTURED loaders (ISS/rTSR) reject real tickers by SHAPE
# regardless; this exclusion only relaxes the text backstop for genuinely ambiguous words.
PROSE_AMBIGUOUS = {"GTM"}

# The scan set is the static deny-list UNION the REAL peer tickers actually in peer_universe.csv (GTLB,
# MNDY, QTWO, ...), minus the prose-ambiguous words. The deny-list alone can't keep up with a growing
# real-peer roster, so we load the live roster and block every one of those symbols anywhere OUTSIDE the
# allow-listed real-peer arm below. The synthetic subject (ACMQ) is is_subject=yes and never returned by
# real_peer_identifiers(), so it stays legal.
SCAN_TICKERS = (set(REAL_TICKERS) | real_peer_identifiers()[0]) - PROSE_AMBIGUOUS

SCAN_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt", ".html", ".yml", ".yaml"}
SKIP_PARTS = {"tests", "evals", "__pycache__", ".git", "node_modules"}
# The exec-comp PEER-BUILDER arm intentionally benchmarks against REAL public companies (real tickers +
# as-disclosed public financials, sourced in governance/real-peer-data.md), so its data + rendered outputs
# are ALLOWED to carry real tickers. Everything else must stay synthetic. Entries are FULL repo-relative
# paths (files) or directory prefixes ending in "/", matched at a path BOUNDARY (not a loose substring) so a
# look-alike like examples/iss-pay-screen/output/real-peer-data.md is NOT allow-listed.
REAL_PEER_ALLOW = ("foundation/data/acme/peer_universe.csv", "governance/real-peer-data.md",
                   "examples/executive-comp-peer-builder/output/")


def _allowed_real(path):
    # repo-relative, ROOT-anchored: a file frag must match the WHOLE repo-relative path (not just its tail),
    # and a dir frag must be a leading prefix — so a look-alike under a different root (evil/governance/
    # real-peer-data.md, x/examples/.../output/nested.html) is NOT allow-listed and still gets scanned.
    s = posixpath.normpath(str(path).replace("\\", "/"))      # resolve ../ and ./ FIRST (normpath drops a
    #                                                         # leading ./ and collapses interior ..)
    if s == ".." or s.startswith("../") or s.startswith("/"):  # escapes the repo root (or absolute) -> it is
        return False                                          # NOT a repo-relative allow-listed artifact
    for frag in REAL_PEER_ALLOW:                               # (../governance/real-peer-data.md must NOT pass)
        if frag.endswith("/"):
            if s == frag.rstrip("/") or s.startswith(frag):   # directory prefix, anchored at the repo root
                return True
        elif s == frag:                                       # exact repo-relative file path
            return True
    return False
# One-letter real tickers such as C/V/T are too collision-prone in prose and report labels. The
# structured peer loader still rejects exact ticker fields; this text scan guards real-looking
# public artifact symbols of 2-6 characters.
TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")


def scan_text(text):
    """Return sorted real-ticker tokens found in public artifact text."""
    tokens = {m.group(0).upper() for m in TOKEN_RE.finditer(text)}
    return sorted(tokens & SCAN_TICKERS)


def _iter_files(roots):
    for root in roots:
        p = Path(root)
        if p.is_file():
            if p.suffix in SCAN_SUFFIXES and not (set(p.parts) & SKIP_PARTS) and not _allowed_real(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix in SCAN_SUFFIXES and not (set(f.parts) & SKIP_PARTS) \
                        and not _allowed_real(f):
                    yield f


def _self_test():
    failures = []
    if "NOVA" not in scan_text("synthetic peer ticker NOVA should be rejected"):
        failures.append("expected NOVA to be detected")
    if scan_text("synthetic peer tickers ACMQ AXAQ BEXQ QEXQ should pass"):
        failures.append("synthetic Q-marked tickers should not be flagged")
    # the live roster must be NON-EMPTY — an empty roster would silently drop dynamic peer tickers from the
    # scan set (the real-peer guard would degrade to the static deny-list without anyone noticing)
    peer_tickers = real_peer_identifiers()[0]
    if not peer_tickers:
        failures.append("live real-peer roster is EMPTY — dynamic ticker scanning is silently disabled")
    # a REAL peer ticker from the live roster must be caught outside the allow-listed arm
    if peer_tickers and not (peer_tickers & set(scan_text(" ".join(sorted(peer_tickers))))):
        failures.append("expected live real-peer tickers (e.g. GTLB) to be detected")
    # GTM is intentionally prose-safe (ZoomInfo's ticker collides with "go-to-market"); the STRUCTURED
    # ISS/rTSR loaders reject it by shape regardless, so the text scan need not flag it
    if "GTM" in scan_text("our GTM motion drove pipeline"):
        failures.append("GTM must be prose-safe (excluded from the text scan)")
    if not _allowed_real("examples/executive-comp-peer-builder/output/committee.html"):
        failures.append("real-peer-builder output must be allow-listed")
    if _allowed_real("examples/iss-pay-screen/output/committee.html"):
        failures.append("non-peer-builder arms must NOT be allow-listed")
    # a LOOK-ALIKE path must not slip past the (now root-anchored) allowlist
    if _allowed_real("examples/iss-pay-screen/output/real-peer-data.md"):
        failures.append("a look-alike real-peer-data.md outside governance/ must NOT be allow-listed")
    if _allowed_real("foundation/data/other/peer_universe.csv"):
        failures.append("a look-alike peer_universe.csv outside foundation/data/acme/ must NOT be allow-listed")
    # ...and a same-tail path under a DIFFERENT top-level root must NOT be allow-listed either
    for evil in ("evil/governance/real-peer-data.md", "attacker/foundation/data/acme/peer_universe.csv",
                 "x/examples/executive-comp-peer-builder/output/deep/nested.html",
                 "examples/executive-comp-peer-builder/output/../../../secret.md",   # .. traversal
                 "../governance/real-peer-data.md",                                   # repo-escape traversal
                 "/etc/governance/real-peer-data.md",                                 # absolute path
                 "governance/../foundation/data/acme/../../../etc/passwd"):
        if _allowed_real(evil):
            failures.append(f"a look-alike / path-traversal must NOT be allow-listed: {evil}")
    for failure in failures:
        print(f"ticker-scan self-test FAILED: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ticker-scan self-test OK")
    return 0


def main(argv):
    if argv == ["--self-test"]:
        return _self_test()

    # the scanner defaults to require=False (the static deny-list is a backstop), so a missing/drifted roster
    # would SILENTLY drop dynamic real-peer ticker coverage — warn loudly instead of failing quietly
    if not real_peer_identifiers()[0]:
        print("ticker-scan WARNING: the live peer roster is empty — dynamic real-peer ticker coverage is "
              "DISABLED (only the static deny-list is active). Regenerate foundation/data/acme/peer_universe.csv.",
              file=sys.stderr)
    roots = argv or ["."]
    hits = []
    scanned = 0
    for f in _iter_files(roots):
        scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = scan_text(text)
        if found:
            hits.append(f"{f}: {', '.join(found)}")

    for hit in hits:
        print(f"TICKER {hit}", file=sys.stderr)
    if hits:
        print(f"ticker-scan: {len(hits)} artifact(s) contain real ticker collisions "
              f"across {scanned} scanned file(s)", file=sys.stderr)
        return 1
    print(f"ticker-scan OK — no real ticker collisions in {scanned} public artifact(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
