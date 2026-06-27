#!/usr/bin/env python3
"""Pre-push preflight: the public repo only shows TRACKED files, so assert the critical paths
exist and that nothing required is left untracked before a push.

    python tools/preflight.py

Checks:
  1. Every critical entrypoint/tree CI and the docs depend on exists on disk.
  2. Every relative link in the top-level README resolves.
  3. (When the repo already has commits) no required file is untracked — the "pushed repo is
     missing core/ or vault/" failure mode. Skipped in an all-untracked draft.

Catches the case where local runs pass but the pushed repo is missing files.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Trees/files the public repo must contain to build, test, and document itself.
REQUIRED_GLOBS = [
    "core/*.py", "core/tests/*.py", "tools/*.py",
    "foundation/data/generate.py", "foundation/data/acme/*.csv",
    "foundation/compute/*.py", "foundation/compute/tests/*.py",
    "foundation/render/*.py", "foundation/render/tests/*.py",
    "examples/*/run.py", "examples/*/evals/*.py",
    # The example anatomy + committed artifacts the READMEs/CI reference must ship too — otherwise
    # the pushed repo renders a README that points at a SOUL/SPEC/screenshot that isn't there.
    "examples/*/README.md", "examples/*/SOUL.md", "examples/*/SPEC.md",
    "examples/*/tools.yaml", "examples/*/cost_tracker.json", "examples/*/data/*.json",
    "examples/*/output/*.html", "examples/*/output/*.md",
    "examples/*/output/*.png", "examples/*/output/*.jsonl",
    "vault/**/*.md", "vault/90-people-analytics/metrics/metrics.registry.json",
    "governance/*.md", "docs/*.md", ".github/workflows/ci.yml", "README.md", "LICENSE",
]


def _required_files():
    out = set()
    for g in REQUIRED_GLOBS:
        for f in REPO.glob(g):
            rel = str(f.relative_to(REPO))
            if "__pycache__" not in rel and f.is_file():
                out.add(rel)
    return out


def _readme_links():
    rd = (REPO / "README.md").read_text(encoding="utf-8")
    out = set()
    for m in re.finditer(r"\]\(([^)]+)\)", rd):
        href = m.group(1).split("#")[0].strip()
        if href and not href.startswith(("http://", "https://", "mailto:")):
            out.add(href)
    return sorted(out)


def _tracked():
    try:
        r = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
        return set(r.stdout.split())
    except Exception:
        return None


def main():
    errors = []

    # 1. critical trees exist (and aren't empty)
    required = _required_files()
    for must in ("core/event_log.py", "core/metrics.py", "tools/render_glossary.py",
                 "foundation/compute/engine.py", "foundation/compute/regression.py",
                 "foundation/compute/peers.py", "foundation/compute/rtsr.py",
                 "foundation/render/dashboard.py", "foundation/render/charts.py",
                 "vault/90-people-analytics/metrics/metrics.registry.json"):
        if must not in required:
            errors.append(f"required file missing on disk: {must}")

    # 2. README links resolve
    bad_links = [h for h in _readme_links() if not (REPO / h).exists()]
    for h in bad_links:
        errors.append(f"README links a missing path: {h}")

    # 3. nothing required is untracked (only meaningful once the repo has commits)
    tracked = _tracked()
    untracked = sorted(rel for rel in required if rel not in tracked) if tracked else []
    for rel in untracked:
        errors.append(f"required file is UNTRACKED (won't be in the pushed repo): {rel}")

    for e in errors:
        print(f"PREFLIGHT {e}", file=sys.stderr)
    if errors:
        print(f"preflight FAILED — {len(errors)} issue(s); {len(required)} required files checked", file=sys.stderr)
        return 1
    note = "all required files tracked" if tracked else "draft (nothing committed yet — re-run before push)"
    print(f"preflight OK — {len(required)} required files present, README links resolve; {note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
