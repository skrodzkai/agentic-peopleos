#!/usr/bin/env python3
"""Render the human-readable metric glossary from the canonical registry.

    python tools/render_glossary.py

Reads vault/90-people-analytics/metrics/metrics.registry.json and writes one Markdown
page per domain (under metrics/) plus the index (metrics-glossary.md). The registry is the
single source of truth — these pages are generated, never hand-edited, so they can't drift.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from core.metrics import MetricRegistry, validate  # noqa: E402

METRICS_DIR = REPO / "vault/90-people-analytics/metrics"
INDEX = REPO / "vault/90-people-analytics/metrics-glossary.md"
AS_OF = "2026-06-20"

TITLES = {"talent_acquisition": "Talent Acquisition", "headcount": "Headcount & Workforce",
          "total_rewards": "Total Rewards", "attrition": "Attrition & Retention",
          "people_ops": "People Operations", "performance": "Performance & Talent",
          "health_safety": "Health & Safety", "compliance_ethics": "Compliance & Ethics",
          "learning_development": "Learning & Development", "succession": "Succession",
          "diversity": "Diversity", "business_linkage": "Business Linkage (People & Finance)"}
OWNERS = {"talent_acquisition": "Talent Acquisition", "headcount": "People Analytics",
          "total_rewards": "Total Rewards", "attrition": "People Analytics",
          "people_ops": "People Operations", "performance": "Talent Management",
          "health_safety": "People Operations", "compliance_ethics": "People Operations",
          "learning_development": "Talent Management", "succession": "Talent Management",
          "diversity": "People Analytics", "business_linkage": "People Analytics + Finance"}
CLASS_LABEL = {"core_kpi": "Core KPI", "diagnostic": "Diagnostic", "operational_alert": "Operational alert"}


def _cell(value) -> str:
    """Make a registry string safe inside a Markdown table cell: a literal pipe would split the
    cell, a newline would break the row, and raw angle brackets render as HTML downstream."""
    return (str(value).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " ")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _fm(title_owner, **extra):
    lines = ["---", "type: reference", f"owner: {title_owner}", "status: approved",
             f"last-reviewed: {AS_OF}"]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def render_domain(reg, domain):
    rows = []
    for m in reg.by_domain(domain):
        may = ", ".join(_cell(a) for a in m["agent_allowed_actions"])
        mustnot = ", ".join(_cell(a) for a in m["agent_forbidden_actions"])
        cls = CLASS_LABEL.get(m.get("metric_class"), _cell(m.get("metric_class", "")))
        # Formula cell carries the per-unit formula, the group/aggregate form, and the protocol note.
        formula = f"`{_cell(m['formula'])}`"
        if m.get("aggregate_formula"):
            formula += f"<br>_group:_ `{_cell(m['aggregate_formula'])}`"
        if m.get("protocol"):
            formula += f"<br>_protocol:_ {_cell(m['protocol'])}"
        rows.append(
            f"| **{_cell(m['name'])}**<br><sub>{cls}</sub> | {_cell(m['definition'])} | {formula} | "
            f"{_cell(m['grain'])} | {_cell(m['decision_supported'])} | ✓ {may}<br>✗ {mustnot} |")
    body = "\n".join(rows)
    return (f"{_fm(OWNERS[domain])}\n\n# {TITLES[domain]} metrics\n\n"
            f"Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.\n\n"
            f"Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell "
            f"also carries the group/aggregate form (where it differs) and the implementation protocol.\n\n"
            f"| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |\n"
            f"|---|---|---|---|---|---|\n{body}\n")


def render_index(reg):
    links = []
    for d in sorted(reg.domains()):
        n = len(reg.by_domain(d))
        links.append(f"- [{TITLES.get(d, d)}](metrics/{d}.md) — {n} metric{'s' if n != 1 else ''}")
    return (f"{_fm('People Analytics')}\n\n# Metrics glossary\n\n"
            f"The canonical People Analytics measurement system. **One definition per metric**, so the "
            f"numbers an agent reports are the numbers everyone trusts. Machine-readable source of truth: "
            f"[`metrics/metrics.registry.json`](metrics/metrics.registry.json) ({len(reg.all())} metrics). "
            f"Reporting agents read and **cite** it; they never redefine a metric.\n\n"
            + "\n".join(links)
            + "\n\n> Every metric also declares what an agent **may** do (calculate, flag, trend) and what it "
            "**must not** (e.g. change or recommend pay) — making this a measurement-governance system, not "
            "just a glossary. Generated by `tools/render_glossary.py`.\n")


def main():
    reg = MetricRegistry.load()
    # Never render pages from an un-governed registry: validate first, fail closed before writing.
    problems = validate({"schema_version": reg.schema_version, "metrics": reg.all()})
    if problems:
        print(f"REGISTRY INVALID — refusing to render glossary ({len(problems)} issue(s)): {problems[0]}",
              file=sys.stderr)
        return 1
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    for d in sorted(reg.domains()):
        (METRICS_DIR / f"{d}.md").write_text(render_domain(reg, d), encoding="utf-8")
    INDEX.write_text(render_index(reg), encoding="utf-8")
    print(f"rendered {len(reg.domains())} domain pages + index from {len(reg.all())} metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
