#!/usr/bin/env python3
"""Pre-push preflight: the public repo only shows TRACKED files, so assert the critical paths
exist and that nothing required is left untracked before a push.

    python3 tools/preflight.py

Checks:
  1. Every critical entrypoint/tree CI and the docs depend on exists on disk.
  2. Every relative link in the top-level README resolves.
  3. (When the repo already has commits) no required file is untracked — the "pushed repo is
     missing core/ or vault/" failure mode. Skipped in an all-untracked draft.
  4. The GitHub Pages tour (docs/index.html): every local src/href/poster reference and every
     Pages-absolute meta image (og:image / twitter:image) resolves to a file under docs/, and
     every docs/assets/*.svg parses as XML — a broken tour image/link or malformed diagram
     fails preflight instead of shipping to the live site.

Catches the case where local runs pass but the pushed repo is missing files.
"""
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Trees/files the public repo must contain to build, test, and document itself.
REQUIRED_GLOBS = [
    "core/*.py", "core/tests/*.py", "tools/*.py",
    "foundation/data/generate.py", "foundation/data/acme/*.csv",
    "foundation/compute/*.py", "foundation/compute/tests/*.py",
    "foundation/compute/manifests/*.json",
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
    # portable SEC skills — every skill ships its doc + README + runnable scripts (incl. its offline test)
    "skills/*/SKILL.md", "skills/*/README.md", "skills/*/scripts/*.py",
]


# EXPLICIT committed artifacts that must ship — enumerated, NOT glob-derived, so a DELETED output can't
# silently shrink the required set and pass. Every example agent commits its rendered dashboard + digest +
# screenshot; the governance/ledger examples commit their ledgers.
# NB: the .png screenshots are manually-rendered illustrative snapshots (Chrome-headless is not in CI) — they
# are EXISTENCE-checked here + linked from the READMEs, but not byte-regenerated/diffed in CI like the
# deterministic .html/.md/.csv artifacts are. That split is intentional.
_STD_OUTPUTS = ("output/report.sample.html", "output/report.sample.png", "output/day1-digest.sample.md")
REQUIRED_OUTPUTS = [
    f"examples/{ex}/{a}"
    for ex in ("headcount-reporting", "attrition-reporting", "people-ops-reporting", "operating-review",
               "people-intelligence", "executive-comp-peer-builder", "executive-comp-benchmarking",
               "rtsr-psu-valuation", "iss-pay-screen", "equity-spend", "glass-lewis-screen",
               "pay-versus-performance", "merit-comp-planning", "ta-reporting", "comp-reporting")
    for a in _STD_OUTPUTS
] + [
    # retention-risk uses a committee-digest (not the day1-digest tuple above) — enumerate it explicitly so a
    # deleted retention output can't shrink the glob set and slip the deletion-resistant gate
    "examples/retention-risk/output/report.sample.html",
    "examples/retention-risk/output/report.sample.png",
    "examples/retention-risk/output/committee-digest.sample.md",
    "examples/merit-comp-planning/output/equity_refresh_grants.sample.csv",   # the equity-handoff artifact
    "examples/operating-review/output/decision.sample.events.jsonl",
    "examples/operating-review/output/decision.sample.events.jsonl.anchor.json",
    "examples/visible-handoff/output/ledger.sample.html",
    "examples/visible-handoff/output/ledger.sample.png",
    "examples/visible-handoff/output/events.jsonl",
    "examples/visible-handoff/output/transcript.md",
    # the visible-handoff README also links the approved/denied sample ledgers + transcripts — enumerate them
    # so a generator change that drops one can't pass preflight while the README points at a missing file
    "examples/visible-handoff/output/approved.events.sample.jsonl",
    "examples/visible-handoff/output/approved.transcript.sample.md",
    "examples/visible-handoff/output/denied.events.sample.jsonl",
    "examples/visible-handoff/output/denied.transcript.sample.md",
    # head-count anchors committed beside each ledger — the suffix-truncation defense
    "examples/visible-handoff/output/events.jsonl.anchor.json",
    "examples/visible-handoff/output/approved.events.sample.jsonl.anchor.json",
    "examples/visible-handoff/output/denied.events.sample.jsonl.anchor.json",
]


def _required_files():
    out = set()
    for g in REQUIRED_GLOBS:
        for f in REPO.glob(g):
            rel = str(f.relative_to(REPO))
            if "__pycache__" not in rel and f.is_file():
                out.add(rel)
    out.update(REQUIRED_OUTPUTS)   # enumerated artifacts are required even if a glob wouldn't find them
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


# The Pages site serves docs/ as its root; a Pages-absolute URL in a meta tag maps back onto docs/.
_PAGES_BASE = "https://skrodzkai.github.io/agentic-peopleos/"


class _TourRefParser(HTMLParser):
    """Collect every local file reference the tour page makes: src/href/poster attributes plus the
    og:image / twitter:image meta contents (which are Pages-absolute by necessity)."""

    def __init__(self):
        super().__init__()
        self.refs = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "href", "poster"):
            v = (a.get(key) or "").split("#")[0].strip()
            if v and not v.startswith(("http://", "https://", "mailto:", "data:", "#")):
                self.refs.add(v)
        # srcset carries comma-separated "url [descriptor]" candidates — validate each local one too
        for cand in (a.get("srcset") or "").split(","):
            u = cand.strip().split()[0] if cand.strip() else ""
            if u and not u.startswith(("http://", "https://", "data:")):
                self.refs.add(u)
        if tag == "meta":
            content = (a.get("content") or "").strip()
            if content.startswith(_PAGES_BASE):
                rest = content[len(_PAGES_BASE):]
                if rest:                       # og:url IS the bare Pages base — that's the site, not a file
                    self.refs.add(rest)


def _tour_errors():
    """Tour-surface integrity: local refs in docs/index.html resolve under docs/, and every committed
    SVG asset parses as XML (a malformed diagram would ship as a broken image on the live site)."""
    errors = []
    tour = REPO / "docs" / "index.html"
    if not tour.is_file():
        return ["tour page missing: docs/index.html"]
    parser = _TourRefParser()
    parser.feed(tour.read_text(encoding="utf-8"))
    docs_root = (REPO / "docs").resolve()
    for ref in sorted(parser.refs):
        target = (REPO / "docs" / ref).resolve()
        # the live Pages site serves docs/ as its root — a ../-escaping ref can resolve to a real repo
        # file locally yet 404 in production, so the boundary itself is enforced, not just existence
        if docs_root != target and docs_root not in target.parents:
            errors.append(f"tour reference escapes the Pages root (docs/): {ref}")
        elif not target.is_file():
            errors.append(f"tour references a missing file: docs/{ref}")
    for svg in sorted((REPO / "docs" / "assets").glob("*.svg")):
        try:
            ET.parse(svg)
        except ET.ParseError as exc:
            errors.append(f"malformed SVG asset: {svg.relative_to(REPO)} ({exc})")
    return errors


def main():
    errors = []

    # 1. critical trees exist (and aren't empty)
    required = _required_files()
    for must in ("core/event_log.py", "core/metrics.py", "tools/render_glossary.py",
                 "foundation/compute/engine.py", "foundation/compute/regression.py",
                 "foundation/compute/peers.py", "foundation/compute/rtsr.py",
                 "foundation/compute/retention.py",
                 "foundation/compute/manifests/retention_model_manifest.json",
                 "foundation/render/dashboard.py", "foundation/render/charts.py",
                 "vault/90-people-analytics/metrics/metrics.registry.json"):
        if must not in required:
            errors.append(f"required file missing on disk: {must}")

    # 1b. every ENUMERATED committed artifact exists (a deleted dashboard/screenshot can't slip through a
    # glob-derived set) — catches the "pushed repo renders a README pointing at a missing sample output"
    for rel in REQUIRED_OUTPUTS:
        if not (REPO / rel).is_file():
            errors.append(f"required committed artifact missing on disk: {rel}")

    # 2. README links resolve
    bad_links = [h for h in _readme_links() if not (REPO / h).exists()]
    for h in bad_links:
        errors.append(f"README links a missing path: {h}")

    # 2b. tour surface: local refs resolve + SVG assets parse
    errors.extend(_tour_errors())

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
    print(f"preflight OK — {len(required)} required files present, README + tour links resolve, "
          f"SVG assets parse; {note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
