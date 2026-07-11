#!/usr/bin/env python3
"""Validate one or every committed Agentic PeopleOS evidence manifest."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.evidence import coverage, evidence_hash, load_manifest, validate_manifest  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--all", action="store_true", help="validate examples/*/output/*.evidence.json")
    parser.add_argument("--verify-sources", action="store_true")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.paths]
    if args.all:
        paths.extend(sorted(REPO.glob("examples/*/output/*.evidence.json")))
    paths = sorted({p.resolve() for p in paths})
    if not paths:
        parser.error("provide a manifest path or --all")

    failures = 0
    for path in paths:
        try:
            manifest = load_manifest(path)
            violations = validate_manifest(manifest, root=REPO, verify_sources=args.verify_sources)
        except (OSError, ValueError) as exc:
            violations = [str(exc)]
            manifest = None
        rel = str(path.relative_to(REPO)) if REPO in path.parents else str(path)
        if violations:
            failures += 1
            print("INVALID %s" % rel, file=sys.stderr)
            for violation in violations:
                print("  - %s" % violation, file=sys.stderr)
        else:
            cov = coverage(manifest)
            print("OK %s — material %d/%d traceable; %s" %
                  (rel, cov["traceable"], cov["material"], evidence_hash(manifest)))
    if failures:
        print("evidence verification FAILED — %d/%d invalid" % (failures, len(paths)), file=sys.stderr)
        return 1
    print("evidence verification OK — %d manifest(s)" % len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
