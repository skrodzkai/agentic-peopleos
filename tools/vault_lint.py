#!/usr/bin/env python3
"""Lint the Agentic PeopleOS vault: every note must carry valid frontmatter.

The vault is the human-readable knowledge layer (Obsidian/Git). It is NOT compliance
infrastructure — the event ledger is. But disciplined frontmatter is what makes the vault
queryable, citable, and safe for agents to ground in. This linter enforces that discipline
and fails closed.

Checks (exit 1 on any error):
- required keys: type, owner, status, last-reviewed
- type ∈ {foundation, policy, process, case, reference, agent}
- status ∈ {draft, in-review, approved, retired}
- last-reviewed is a YYYY-MM-DD date
- content_type (if present) is a valid content type
- notes under policy/ must be type: policy AND content_type: trusted_policy (provenance == trust)
- policy notes carry a full lifecycle: effective-date, review-due, jurisdiction,
  approved-by-role, exception-path, source-of-record (and supersedes); the dates are
  YYYY-MM-DD and review-due is not before effective-date
- a heuristic PII backstop: note bodies must not contain obvious personal data (email
  addresses, US SSNs, phone numbers). This is a regex backstop, NOT a guarantee — the real
  guarantee is the convention that the vault is process-centric and holds no records.

Optional staleness report with --as-of YYYY-MM-DD (warnings, non-failing):
review-due in the past, or last-reviewed > 365 days old.
Usage: python3 tools/vault_lint.py vault [--as-of 2026-01-15]
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.pii import scan as scan_pii  # noqa: E402  (shared heuristic PII backstop)

TYPES = {"foundation", "policy", "process", "case", "reference", "agent"}
STATUS = {"draft", "in-review", "approved", "retired"}
CONTENT_TYPES = {"trusted_policy", "draft", "case_note", "transcript", "external_message"}
REQUIRED = ("type", "owner", "status", "last-reviewed")
# A trusted policy is a controlled document — it must declare its full lifecycle so an agent
# (or a human) can tell whether it is in force, who owns exceptions, and where the real data lives.
POLICY_REQUIRED = ("effective-date", "review-due", "jurisdiction", "approved-by-role",
                   "supersedes", "exception-path", "source-of-record")
POLICY_DATES = ("effective-date", "review-due")


def _date(value):
    try:
        return datetime.strptime(value or "", "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_frontmatter(text):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = {}
    for line in text[3:end].strip("\n").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def lint(root, as_of=None):
    errors, warnings = [], []
    for md in sorted(Path(root).rglob("*.md")):
        rel = md.relative_to(root)
        if md.name == "README.md":
            continue
        text = md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if fm is None:
            errors.append(f"{rel}: missing frontmatter")
            continue
        body_start = text.find("\n---", 3)
        body = text[body_start + 4:] if body_start != -1 else text
        for hit in scan_pii(body):
            errors.append(f"{rel}: possible PII in body ({hit}) — the vault holds no personal data")
        for k in REQUIRED:
            if k not in fm:
                errors.append(f"{rel}: missing '{k}'")
        if fm.get("type") not in TYPES:
            errors.append(f"{rel}: invalid type '{fm.get('type')}'")
        if fm.get("status") not in STATUS:
            errors.append(f"{rel}: invalid status '{fm.get('status')}'")
        reviewed = None
        try:
            reviewed = datetime.strptime(fm.get("last-reviewed", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            errors.append(f"{rel}: last-reviewed is not a YYYY-MM-DD date")
        ct = fm.get("content_type")
        if ct and ct not in CONTENT_TYPES:
            errors.append(f"{rel}: invalid content_type '{ct}'")
        if str(rel).replace("\\", "/").startswith("policy/"):
            # Require type: policy so the lifecycle enforcement below cannot be bypassed by
            # mistyping a controlled document as type: process/reference.
            if fm.get("type") != "policy":
                errors.append(f"{rel}: notes under policy/ must be type: policy")
            if ct != "trusted_policy":
                errors.append(f"{rel}: notes under policy/ must be content_type: trusted_policy")

        # Policy notes are controlled documents: enforce the full lifecycle frontmatter.
        if fm.get("type") == "policy":
            for k in POLICY_REQUIRED:
                if not fm.get(k):
                    errors.append(f"{rel}: policy note missing '{k}'")
            eff, due = _date(fm.get("effective-date")), _date(fm.get("review-due"))
            for k in POLICY_DATES:
                if fm.get(k) is not None and _date(fm.get(k)) is None:
                    errors.append(f"{rel}: {k} is not a YYYY-MM-DD date")
            if eff and due and due < eff:
                errors.append(f"{rel}: review-due ({fm.get('review-due')}) precedes effective-date ({fm.get('effective-date')})")
            if as_of:
                asof = _date(as_of)
                if asof and due and due < asof:
                    warnings.append(f"{rel}: review overdue (review-due {fm.get('review-due')})")

        if as_of and reviewed:
            asof = _date(as_of)
            if asof and (asof - reviewed).days > 365:
                warnings.append(f"{rel}: stale (last-reviewed {fm.get('last-reviewed')})")
    return errors, warnings


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    as_of = next((argv[i + 1] for i, a in enumerate(argv) if a == "--as-of" and i + 1 < len(argv)), None)
    root = args[0] if args else "vault"
    errors, warnings = lint(root, as_of)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    if errors:
        print(f"vault lint: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"vault lint OK ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
