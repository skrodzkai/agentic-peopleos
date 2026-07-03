#!/usr/bin/env python3
"""Public-safety PII scan over committed, public-facing artifacts.

A defense-in-depth backstop separate from the per-note vault lint and the ledger's append-time
check: it greps the data/output/knowledge artifacts that actually ship in the public repo
(sample CSVs, generated reports/ledgers, the vault) for obvious direct identifiers. Test files
are intentionally excluded — adversarial PII fixtures live there on purpose.

    python3 tools/pii_scan.py <path> [<path> ...]      # exits non-zero on any hit

Heuristic only (see core/pii.py) — a backstop, not a guarantee.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.pii import scan  # noqa: E402

# All public *text* suffixes (binary images are not scanned as text — they'd false-positive).
SCAN_SUFFIXES = {".jsonl", ".json", ".csv", ".md", ".txt", ".html", ".yml", ".yaml",
                 ".py", ".sh", ".toml", ".cfg", ".ini"}
# Adversarial PII fixtures live in tests/evals on purpose — they are not public data.
SKIP_PARTS = {"tests", "evals", "__pycache__", ".git", "node_modules"}


def _iter_files(roots):
    for root in roots:
        p = Path(root)
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix in SCAN_SUFFIXES and not (set(f.parts) & SKIP_PARTS):
                    yield f


def main(argv):
    roots = argv or ["."]
    hits = []
    scanned = 0
    for f in _iter_files(roots):
        scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for h in scan(text):
            hits.append(f"{f}: {h}")
    for h in hits:
        print(f"PII {h}", file=sys.stderr)
    if hits:
        print(f"pii-scan: {len(hits)} likely identifier(s) in {scanned} public artifact(s)", file=sys.stderr)
        return 1
    print(f"pii-scan OK — no OBVIOUS direct identifiers (email/SSN/phone heuristics) in {scanned} public "
          f"artifact(s); a pattern scan, not a guarantee")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
