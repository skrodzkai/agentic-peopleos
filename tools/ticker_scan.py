#!/usr/bin/env python3
"""Public-safety scan for real ticker collisions in synthetic public artifacts.

This is a defense-in-depth backstop for portfolio examples. Source code and tests may contain
real ticker fixtures deliberately; sample data, generated dashboards, and public docs should not.

    python3 tools/ticker_scan.py examples foundation/data
    python3 tools/ticker_scan.py --self-test
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundation.compute.peers import REAL_TICKERS  # noqa: E402

SCAN_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt", ".html", ".yml", ".yaml"}
SKIP_PARTS = {"tests", "evals", "__pycache__", ".git", "node_modules"}
# One-letter real tickers such as C/V/T are too collision-prone in prose and report labels. The
# structured peer loader still rejects exact ticker fields; this text scan guards real-looking
# public artifact symbols of 2-6 characters.
TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")


def scan_text(text):
    """Return sorted real-ticker tokens found in public artifact text."""
    tokens = {m.group(0).upper() for m in TOKEN_RE.finditer(text)}
    return sorted(tokens & REAL_TICKERS)


def _iter_files(roots):
    for root in roots:
        p = Path(root)
        if p.is_file():
            if p.suffix in SCAN_SUFFIXES and not (set(p.parts) & SKIP_PARTS):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix in SCAN_SUFFIXES and not (set(f.parts) & SKIP_PARTS):
                    yield f


def _self_test():
    failures = []
    if "NOVA" not in scan_text("synthetic peer ticker NOVA should be rejected"):
        failures.append("expected NOVA to be detected")
    if scan_text("synthetic peer tickers ACMQ AXQA BEXQ QEXQ should pass"):
        failures.append("synthetic Q-marked tickers should not be flagged")
    for failure in failures:
        print(f"ticker-scan self-test FAILED: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ticker-scan self-test OK")
    return 0


def main(argv):
    if argv == ["--self-test"]:
        return _self_test()

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
