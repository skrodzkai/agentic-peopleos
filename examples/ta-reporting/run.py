#!/usr/bin/env python3
"""Acme Corp — Talent Acquisition reporting agent (Agentic PeopleOS example).

Reads open requisitions, computes the weekly operating report deterministically,
renders a branded HTML dashboard + a Day-1 digest, and STOPS at a human publish
gate. All data is synthetic. Standard library only (Python 3.9+).

    python3 run.py                                   # draft only — nothing is sent
    python3 run.py --publish                         # refused: needs a named approver
    python3 run.py --publish --approved-by "Name"    # records the human approval

Fails closed: on missing/malformed/contract-violating data it writes no report,
marks any prior output `.stale`, prints a clean message, and exits non-zero.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "requisitions.sample.csv"
OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
DEFAULT_AS_OF = "2026-01-15"  # fixed so the sample output is reproducible

# Risk-flag policy (see SPEC.md)
AGING_DAYS = 90
STALE_DAYS = 14
THIN_PIPELINE = 3
AGE_BANDS = [("0–30", 0, 30), ("31–60", 31, 60), ("61–90", 61, 90), ("90+", 91, 10**9)]


def _load_metric_registry():
    """Definitions and thresholds come from the canonical registry (single source of truth).

    Returns (registry, error). The registry is a HARD dependency: the literals above are only
    the values the registry confirms, not a silent fallback. If the registry is missing or
    fails its own governance validator, main() fails closed rather than emit an uncited report
    on un-governed numbers. Returns (None, reason) on any failure.
    """
    try:
        repo = Path(__file__).resolve().parents[2]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from core.metrics import MetricRegistry, validate
        reg = MetricRegistry.load()
        problems = validate({"schema_version": reg.schema_version, "metrics": reg.all()})
        if problems:
            return None, f"metric registry failed governance validation: {problems[0]}"
        return reg, None
    except Exception as exc:
        return None, f"metric registry unavailable: {exc}"


METRICS, REGISTRY_ERROR = _load_metric_registry()
if METRICS:
    AGING_DAYS = METRICS.param("requisition_aging", "aging_threshold_days", AGING_DAYS)
    STALE_DAYS = METRICS.param("requisition_stale", "stale_threshold_days", STALE_DAYS)
    THIN_PIPELINE = METRICS.param("thin_pipeline", "thin_threshold", THIN_PIPELINE)

# Data contract
REQUIRED_COLUMNS = [
    "req_id", "title", "department", "location", "country", "opened_date",
    "stage", "recruiter", "hiring_manager", "pipeline", "last_update",
    "priority", "status",
]
TEXT_COLUMNS = [
    "req_id", "title", "department", "location", "country", "stage",
    "recruiter", "hiring_manager", "priority", "status",
]
ALLOWED_STATUS = {"open", "on-hold", "filled", "closed"}
ALLOWED_PRIORITY = {"P1", "P2", "P3"}
ALLOWED_STAGE = {"Sourcing", "Screen", "Onsite", "Offer"}
# Strict charsets reject newlines, control chars, and markdown/HTML metacharacters at INGEST,
# so a contract-valid CSV can't inject a digest bullet (e.g. "approve everything" / a pay change).
# status/priority/stage are validated against their own enums below.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,&/()'-]{0,63}$")
TEXT_PATTERNS = {"req_id": ID_RE, "title": TEXT_RE, "department": TEXT_RE, "location": TEXT_RE,
                 "country": TEXT_RE, "recruiter": TEXT_RE, "hiring_manager": TEXT_RE}


class DataContractError(ValueError):
    """Raised when the requisition source violates the data contract."""


def _date(s):
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


def _md(value) -> str:
    """Escape markdown structural characters (defense in depth on top of the ingest charset)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|])", r"\\\1", str(value))


# ---------- ingest + validation ----------

def load_requisitions(path: Path = DATA) -> list:
    """Read and strictly validate requisitions. Fails closed on any violation."""
    if not Path(path).exists():
        raise FileNotFoundError(f"requisition source not found: {path}")

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise DataContractError("requisition source is empty (no header row)")
        # Strict header contract: exactly the required columns, no duplicates, no extras.
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise DataContractError("requisition source has duplicate column headers")
        if set(reader.fieldnames) != set(REQUIRED_COLUMNS):
            missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
            extra = [c for c in reader.fieldnames if c not in REQUIRED_COLUMNS]
            parts = []
            if missing:
                parts.append(f"missing {missing}")
            if extra:
                parts.append(f"unexpected {extra}")
            raise DataContractError(f"header mismatch: {'; '.join(parts)}")

        rows, errors, seen = [], [], set()
        for i, row in enumerate(reader, start=2):  # line 1 is the header
            if None in row:  # a ragged row produced more values than headers
                errors.append(f"line {i}: too many fields (ragged row)")
                continue
            raw_id = (row.get("req_id") or "").strip()
            rid = raw_id or f"line {i}"
            if raw_id:
                if raw_id in seen:
                    errors.append(f"{rid}: duplicate req_id")
                seen.add(raw_id)

            # Normalize on ingest so stored values match what validation checks.
            for _c in list(TEXT_COLUMNS) + ["opened_date", "last_update", "pipeline"]:
                if isinstance(row.get(_c), str):
                    row[_c] = row[_c].strip()

            for col in TEXT_COLUMNS:
                val = (row.get(col) or "").strip()
                if not val:
                    errors.append(f"{rid}: empty required field '{col}'")
                elif col in TEXT_PATTERNS and not TEXT_PATTERNS[col].match(val):
                    errors.append(f"{rid}: '{col}' has illegal characters (control/markdown chars rejected)")

            try:
                p = int(str(row["pipeline"]).strip())
                if p < 0:
                    errors.append(f"{rid}: negative pipeline ({p})")
                row["pipeline"] = p
            except (ValueError, TypeError):
                errors.append(f"{rid}: pipeline not an integer ('{row.get('pipeline')}')")

            opened = last = None
            try:
                opened = _date(row["opened_date"])
            except (ValueError, TypeError):
                errors.append(f"{rid}: bad opened_date ('{row.get('opened_date')}')")
            try:
                last = _date(row["last_update"])
            except (ValueError, TypeError):
                errors.append(f"{rid}: bad last_update ('{row.get('last_update')}')")
            if opened and last and last < opened:
                errors.append(f"{rid}: last_update precedes opened_date")

            if (row.get("status") or "").strip() not in ALLOWED_STATUS:
                errors.append(f"{rid}: invalid status '{row.get('status')}' (allowed: {', '.join(sorted(ALLOWED_STATUS))})")
            if (row.get("priority") or "").strip() not in ALLOWED_PRIORITY:
                errors.append(f"{rid}: invalid priority '{row.get('priority')}'")
            if (row.get("stage") or "").strip() not in ALLOWED_STAGE:
                errors.append(f"{rid}: invalid stage '{row.get('stage')}'")

            rows.append(row)

        if not rows:
            raise DataContractError("requisition source has a header but no data rows")
        if errors:
            shown = "; ".join(errors[:8])
            more = f" (+{len(errors) - 8} more)" if len(errors) > 8 else ""
            raise DataContractError(f"{len(errors)} schema violation(s): {shown}{more}")
        return rows


def enrich(reqs: list, as_of) -> list:
    for r in reqs:
        r["days_open"] = (as_of - _date(r["opened_date"])).days
        r["days_since_update"] = (as_of - _date(r["last_update"])).days
        flags = []
        if r["status"] == "open":
            if r["days_open"] > AGING_DAYS:
                flags.append("AGING")
            if r["days_since_update"] > STALE_DAYS:
                flags.append("STALE")
            if r["priority"] == "P1" and r["pipeline"] < THIN_PIPELINE:
                flags.append("THIN_PIPELINE")
        r["flags"] = flags
    return reqs


def _counts(items) -> dict:
    out: dict = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return out


def build_report(reqs: list, as_of) -> dict:
    """Compute every section of the operating report. Pure function — no I/O."""
    ahead = [r["req_id"] for r in reqs if _date(r["opened_date"]) > as_of]
    if ahead:
        raise DataContractError(
            f"--as-of {as_of.isoformat()} precedes opened_date of {', '.join(ahead[:3])}")
    enrich(reqs, as_of)
    open_reqs = [r for r in reqs if r["status"] == "open"]
    on_hold = [r for r in reqs if r["status"] == "on-hold"]

    age_bands = {label: 0 for label, _, _ in AGE_BANDS}
    for r in open_reqs:
        for label, lo, hi in AGE_BANDS:
            if lo <= r["days_open"] <= hi:
                age_bands[label] += 1
                break

    scorecard: dict = {}
    for r in open_reqs:
        s = scorecard.setdefault(r["recruiter"], {"reqs": 0, "days": [], "at_risk": 0, "pipeline": 0})
        s["reqs"] += 1
        s["days"].append(r["days_open"])
        s["pipeline"] += r["pipeline"]
        if r["flags"]:
            s["at_risk"] += 1
    for s in scorecard.values():
        s["avg_days_open"] = round(statistics.mean(s["days"])) if s["days"] else 0
        del s["days"]

    pipelines = [r["pipeline"] for r in open_reqs]
    kpis = {
        "total_open": len(open_reqs),
        "on_hold": len(on_hold),
        "avg_days_open": round(statistics.mean([r["days_open"] for r in open_reqs])) if open_reqs else 0,
        "at_risk": len([r for r in open_reqs if r["flags"]]),
        "median_pipeline": round(statistics.median(pipelines)) if pipelines else 0,
    }

    return {
        "as_of": as_of.isoformat(),
        "as_of_display": as_of.strftime("%b %d, %Y"),
        "kpis": kpis,
        "narrative": build_narrative(open_reqs, kpis),
        "stage_mix": _counts([r["stage"] for r in open_reqs]),
        "age_bands": age_bands,
        "department_mix": _counts([r["department"] for r in open_reqs]),
        "country_mix": _counts([r["country"] for r in open_reqs]),
        "scorecard": scorecard,
        "risk_flags": sorted(
            ({"req_id": r["req_id"], "title": r["title"], "recruiter": r["recruiter"], "flags": r["flags"]}
             for r in open_reqs if r["flags"]),
            key=lambda x: len(x["flags"]), reverse=True,
        ),
        "watchlist": sorted(
            ({"req_id": r["req_id"], "title": r["title"], "days_since_update": r["days_since_update"]}
             for r in open_reqs if r["days_since_update"] > STALE_DAYS),
            key=lambda x: x["days_since_update"], reverse=True,
        ),
    }


def build_narrative(open_reqs: list, kpis: dict) -> str:
    """One business-facing insight, derived from the data (the Analytics Narrator)."""
    parts = []
    aging = [r for r in open_reqs if r["days_open"] > AGING_DAYS]
    if aging:
        dept, n = max(_counts([r["department"] for r in aging]).items(), key=lambda x: x[1])
        parts.append(f"{len(aging)} of {len(open_reqs)} open reqs have aged past {AGING_DAYS} days, "
                     f"concentrated in {dept} ({n} of {len(aging)}) — the clearest risk to hiring plans.")
    risk_by_rec = _counts([r["recruiter"] for r in open_reqs if r["flags"]])
    if risk_by_rec:
        rec, n = max(risk_by_rec.items(), key=lambda x: x[1])
        parts.append(f"{rec} carries {n} of the {kpis['at_risk']} at-risk reqs — "
                     f"the highest-leverage place to rebalance load.")
    if not parts:
        parts.append("No requisitions are currently flagged at risk.")
    return " ".join(parts)


# ---------- rendering: branded HTML (skrodzkai dark) ----------

_STYLE = """
:root{--bg:#000;--text:#eef7ff;--muted:#8db1ce;--soft:#6d8294;--cyan:#1ba7ff;--cyan2:#48c7ff;
--green:#43d477;--red:#ff4d4f;--amber:#f7b955;--line:rgba(27,167,255,.46);--line-soft:rgba(27,167,255,.22);
--hair:rgba(141,177,206,.20);--track:rgba(255,255,255,.06);}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;line-height:1.45;}
.wrap{max-width:920px;margin:0 auto;padding:28px 26px 40px;}
.mono{font-family:'JetBrains Mono','SFMono-Regular',Consolas,ui-monospace,monospace;}
.brand-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-bottom:14px;border-bottom:2px solid var(--cyan);margin-bottom:22px;}
.brand{display:flex;align-items:center;gap:9px;font-weight:800;font-size:17px;color:#fff;letter-spacing:.2px;}
.brand .os{color:var(--cyan);}
.status{border:1px solid rgba(247,185,85,.5);color:var(--amber);background:rgba(247,185,85,.12);font-size:11px;font-weight:800;padding:4px 11px;border-radius:999px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;}
.kicker{color:var(--cyan);text-transform:uppercase;font-size:11px;letter-spacing:.1em;font-weight:800;}
h1{margin:6px 0 4px;font-size:25px;line-height:1.1;color:#fff;font-weight:800;}
.date{color:var(--muted);font-size:13px;}
.callout{margin:18px 0 4px;background:rgba(27,167,255,.06);border:1px solid var(--line-soft);border-left:3px solid var(--cyan);border-radius:0 8px 8px 0;padding:12px 15px;}
.callout .label{color:var(--cyan2);text-transform:uppercase;font-size:10.5px;font-weight:800;letter-spacing:.06em;}
.callout p{margin:4px 0 0;color:var(--text);font-size:14px;line-height:1.5;}
.meter{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:20px 0 6px;}
.metric{background:#000;border:1px solid var(--line);border-radius:10px;padding:13px 15px;}
.metric strong{display:block;font-size:26px;font-weight:700;color:#fff;line-height:1.05;}
.metric span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-top:4px;}
.section-title{color:var(--cyan);text-transform:uppercase;font-size:13px;font-weight:900;letter-spacing:.05em;margin:26px 0 10px;}
.cols{display:flex;gap:26px;flex-wrap:wrap;}
.col{flex:1;min-width:250px;}
.row{display:flex;align-items:center;gap:9px;margin:6px 0;}
.lbl{width:74px;font-size:12px;color:var(--muted);}
.bar{flex:1;background:var(--track);border-radius:4px;height:13px;overflow:hidden;}
.bar>div{height:13px;border-radius:4px;}
.val{width:18px;text-align:right;font-size:12px;color:var(--text);}
table.data{width:100%;border-collapse:collapse;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:12.5px;}
table.data th{text-align:left;color:var(--cyan);background:#07101a;padding:7px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.04em;font-weight:800;}
table.data td{padding:7px 8px;border-top:1px solid var(--hair);color:var(--text);}
table.data td.c,table.data th.c{text-align:center;}
.flagrow{display:flex;align-items:center;gap:7px;font-size:12.5px;margin:6px 0;flex-wrap:wrap;}
.flagrow .req{min-width:172px;color:var(--text);}
.pill{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap;}
.pill.warn{background:rgba(247,185,85,.15);color:var(--amber);}
.pill.bad{background:rgba(255,77,79,.15);color:var(--red);}
.watch{font-size:12.5px;color:var(--text);margin:5px 0;}
.watch b{color:#fff;}
.watch .d{color:var(--red);}
.footer{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;margin-top:26px;border-top:1px solid #193044;padding-top:14px;color:var(--soft);font-size:11px;}
"""


def _bar(label, value, maxv, color, label_w=74):
    pct = round(100 * value / maxv) if maxv else 0
    return (f'<div class="row"><div class="lbl" style="width:{label_w}px">{html.escape(label)}</div>'
            f'<div class="bar"><div style="width:{pct}%;background:{color};"></div></div>'
            f'<div class="val mono">{value}</div></div>')


def _metric(value, label, accent=False):
    num_color = "var(--red)" if accent else "#fff"
    border = "rgba(255,77,79,.5)" if accent else "var(--line)"
    return (f'<div class="metric" style="border-color:{border}">'
            f'<strong class="mono" style="color:{num_color}">{value}</strong>'
            f'<span>{html.escape(label)}</span></div>')


def _pill(flag):
    cls = "warn" if flag == "AGING" else "bad"
    return f'<span class="pill {cls}">{html.escape(flag.replace("_", " "))}</span>'


def render_html(report: dict) -> str:
    k = report["kpis"]
    cyan, red = "var(--cyan)", "var(--red)"

    cards = (
        _metric(k["total_open"], "Open reqs")
        + _metric(k["at_risk"], "At risk", accent=True)
        + _metric(k["avg_days_open"], "Avg days open")
        + _metric(k["median_pipeline"], "Median pipeline")
        + _metric(k["on_hold"], "On hold")
    )

    stage_max = max(report["stage_mix"].values(), default=1)
    stage_bars = "".join(_bar(s, n, stage_max, cyan) for s, n in
                         sorted(report["stage_mix"].items(), key=lambda x: x[1], reverse=True))

    age_max = max(report["age_bands"].values(), default=1)
    age_bars = "".join(_bar(b, n, age_max, red if b == "90+" else cyan) for b, n in report["age_bands"].items())

    dept_max = max(report["department_mix"].values(), default=1)
    dept_bars = "".join(_bar(d, n, dept_max, cyan, label_w=92) for d, n in
                        sorted(report["department_mix"].items(), key=lambda x: x[1], reverse=True))

    country_max = max(report["country_mix"].values(), default=1)
    country_bars = "".join(_bar(c, n, country_max, cyan) for c, n in
                           sorted(report["country_mix"].items(), key=lambda x: x[1], reverse=True))

    score_rows = "".join(
        f'<tr><td>{html.escape(r)}</td><td class="c mono">{s["reqs"]}</td>'
        f'<td class="c mono">{s["avg_days_open"]}</td>'
        f'<td class="c mono" style="color:{"var(--red)" if s["at_risk"] else "var(--text)"}">{s["at_risk"]}</td>'
        f'<td class="c mono">{s["pipeline"]}</td></tr>'
        for r, s in sorted(report["scorecard"].items(), key=lambda x: x[1]["reqs"], reverse=True)
    )

    flag_rows = "".join(
        f'<div class="flagrow"><span class="req">{html.escape(f["req_id"])} {html.escape(f["title"])}</span>'
        + "".join(_pill(fl) for fl in f["flags"]) + "</div>"
        for f in report["risk_flags"]
    ) or '<div class="watch">No requisitions flagged at risk.</div>'

    watch_rows = "".join(
        f'<div class="watch"><b>{html.escape(w["req_id"])}</b> {html.escape(w["title"])} — '
        f'<span class="d">no update for {w["days_since_update"]} days</span></div>'
        for w in report["watchlist"][:5]
    ) or '<div class="watch">Every open req has been updated within the last two weeks.</div>'

    mark = ('<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="6" cy="7" r="2.4" fill="#1ba7ff"/><circle cx="18" cy="7" r="2.4" fill="#1ba7ff"/>'
            '<circle cx="12" cy="17" r="2.4" fill="#48c7ff"/>'
            '<path d="M6 7 L12 17 L18 7" stroke="#1e88e5" stroke-width="1.4" fill="none"/></svg>')

    defs_block = ""
    if METRICS:
        ids = ["open_reqs", "requisition_aging", "requisition_stale", "thin_pipeline"]
        items = "".join(
            f'<li><b style="color:#eef7ff">{html.escape(METRICS.get(i)["name"])}</b> — '
            f'{html.escape(METRICS.get(i)["definition"])}</li>'
            for i in ids if METRICS.get(i))
        defs_block = (
            '<div class="section-title">Metric definitions</div>'
            f'<ul style="font-size:12px;color:#8db1ce;line-height:1.6;margin:0;padding-left:18px;">{items}</ul>'
            '<div style="font-size:11px;color:#687b95;margin-top:6px;">Definitions cited from '
            '<b style="color:#8db1ce">metrics.registry.json</b> — the agent does not redefine metrics.</div>')

    body = f"""<div class="wrap">
  <div class="brand-row">
    <span class="brand">{mark}Agentic People<span class="os">OS</span></span>
    <span class="status">Draft · awaiting publish approval</span>
  </div>
  <div class="kicker">Talent Acquisition</div>
  <h1>Operating Report</h1>
  <div class="date">{html.escape(COMPANY)} &middot; as of {report['as_of_display']} &middot; synthetic data</div>

  <div class="callout"><div class="label">What needs attention</div><p>{html.escape(report['narrative'])}</p></div>

  <div class="meter">{cards}</div>

  <div class="cols">
    <div class="col">
      <div class="section-title">Pipeline by stage</div>{stage_bars}
      <div class="section-title">Requisition age</div>{age_bars}
      <div class="section-title">Open reqs by location</div>{country_bars}
    </div>
    <div class="col">
      <div class="section-title">Open reqs by department</div>{dept_bars}
      <div class="section-title">Recruiter scorecard</div>
      <table class="data"><thead><tr><th>Recruiter</th><th class="c">Reqs</th><th class="c">Avg&nbsp;days</th><th class="c">At&nbsp;risk</th><th class="c">Pipeline</th></tr></thead><tbody>{score_rows}</tbody></table>
    </div>
  </div>

  <div class="section-title">Top risk flags</div>{flag_rows}
  <div class="section-title">Update-recency watchlist</div>{watch_rows}

  {defs_block}

  <div class="footer">
    <span>Generated by the <b style="color:var(--muted)">ta-reporting</b> agent &middot; Agentic PeopleOS</span>
    <span>Human-in-the-loop: agent recommends, a named human approves before publish</span>
  </div>
</div>"""

    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(COMPANY)} — TA Operating Report</title>"
            f"<style>{_STYLE}</style></head><body>{body}</body></html>")


def render_digest(report: dict) -> str:
    k = report["kpis"]
    lines = [
        f"# {COMPANY} — TA Day-1 digest",
        f"_As of {report['as_of_display']} · draft for review_",
        "",
        f"- {report['narrative']}",
        f"- **{k['total_open']}** open reqs ({k['on_hold']} on hold), averaging **{k['avg_days_open']}** days open; "
        f"**{k['at_risk']}** at risk, median pipeline **{k['median_pipeline']}**.",
    ]
    multi = [f for f in report["risk_flags"] if len(f["flags"]) >= 2]
    if multi:
        lines.append("- **Triage first (2+ flags):** "
                     + "; ".join(f"{_md(f['req_id'])} {_md(f['title'])} ({', '.join(f['flags'])})" for f in multi[:5]) + ".")
    if report["watchlist"]:
        w = report["watchlist"][0]
        lines.append(f"- Stalest req: {_md(w['req_id'])} {_md(w['title'])} — no update for {w['days_since_update']} days.")
    lines += ["", f"_Metrics defined in metrics.registry.json (aging >{AGING_DAYS}d, stale >{STALE_DAYS}d, "
              f"thin pipeline = P1 with <{THIN_PIPELINE} candidates)._",
              "", "_Publish gate: a human must approve before this is distributed._"]
    return "\n".join(lines) + "\n"


# ---------- fail-closed helpers ----------

def _mark_stale() -> bool:
    """A failed run must not leave a prior success looking current. Best-effort."""
    marked = False
    for path in (REPORT, DIGEST):
        try:
            if path.exists():
                path.rename(path.with_name(path.name + ".stale"))
                marked = True
        except OSError:
            pass
    return marked


def _clear_stale():
    for path in (REPORT, DIGEST):
        stale = path.with_name(path.name + ".stale")
        if stale.exists():
            stale.unlink()


def _one_line(text, limit=300) -> str:
    """Collapse whitespace (incl. newlines) and truncate — a failure/approval record is one line."""
    return " ".join(str(text).split())[:limit]


def _fail_closed(message: str) -> int:
    note = " (prior output marked .stale)" if _mark_stale() else ""
    print(f"FAIL CLOSED: {_one_line(message)}{note}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str):
    """Write fully to a temp sibling, then os.replace — a crash never leaves a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------- entrypoint ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp TA reporting agent (example).")
    ap.add_argument("--as-of", default=DEFAULT_AS_OF, help="reporting date YYYY-MM-DD")
    ap.add_argument("--data", default=str(DATA), help="path to requisition CSV")
    ap.add_argument("--publish", action="store_true", help="attempt to publish (gated)")
    ap.add_argument("--approved-by", default=None, help="name of the human approving publish")
    args = ap.parse_args(argv)

    # The governed metric registry is a hard dependency — fail closed if it's missing/invalid.
    if METRICS is None:
        return _fail_closed(REGISTRY_ERROR or "metric registry unavailable")

    # Publish gate refuses before any side effects (and validates the approver name).
    # Validate the RAW approver: reject any control character (newline/tab/CR) outright, and require a
    # full charset match (re.fullmatch — no trailing-newline bypass).
    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver)
                         or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}", approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver.\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        as_of = _date(args.as_of)
    except (ValueError, TypeError):
        return _fail_closed(f"invalid --as-of date '{args.as_of}' (expected YYYY-MM-DD)")

    try:
        reqs = load_requisitions(Path(args.data))
        report = build_report(reqs, as_of)
        # Render both artifacts fully in memory BEFORE touching disk (atomic + all-or-nothing).
        html_doc, digest_doc = render_html(report), render_digest(report)
    except FileNotFoundError as exc:
        return _fail_closed(str(exc))
    except DataContractError as exc:
        return _fail_closed(str(exc))

    pub_path = OUT / "PUBLISHED.json"
    try:
        OUT.mkdir(exist_ok=True)
        _clear_stale()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        # The approval record is part of the same all-or-nothing transaction (no false "approved").
        if args.publish:
            _atomic_write(pub_path,
                          json.dumps({"approved_by": approver, "report_as_of": report["as_of"]}, indent=2) + "\n")
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            tmp = p.with_name(p.name + ".tmp")
            try:
                tmp.unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    k = report["kpis"]
    print(f"{COMPANY} TA report — as of {report['as_of']}")
    print(f"  open reqs: {k['total_open']} | on hold: {k['on_hold']} | at risk: {k['at_risk']} | avg days open: {k['avg_days_open']}")
    print(f"  wrote {REPORT.name} and {DIGEST.name}")

    if args.publish:  # approver already validated at the top of main()
        print(f"\nPublish approved by {approver}. Recorded to output/PUBLISHED.json.")
        print("(This example records approval locally; it does not send anything externally.)")
    else:
        print("\nDRAFT only. Publish gate: a human must review output/ and approve with\n"
              "  --approved-by \"Your Name\" before anything is distributed. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
