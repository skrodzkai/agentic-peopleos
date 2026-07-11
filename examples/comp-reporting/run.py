#!/usr/bin/env python3
"""Acme Corp — Compensation reporting agent (Agentic PeopleOS example).

A second reporting agent on the SAME metric registry as ta-reporting. It reads a synthetic
comp snapshot, computes compa-ratio / range penetration / out-of-band rate / exception rate
(cited from metrics.registry.json), flags out-of-band pay, and stops at a human publish gate.

Crucially, this agent demonstrates **measurement governance**: the registry marks comp metrics
`recommend_pay_change` and `change_salary` as forbidden, so the agent calculates and flags but
NEVER recommends or changes pay — a human (Total Rewards) owns every pay decision.

Standard library only; deterministic; offline.

    python3 run.py                                   # draft only
    python3 run.py --publish                         # refused: needs a named approver
    python3 run.py --publish --approved-by "Name"    # records the human approval
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = HERE / "data" / "comp_snapshot.sample.csv"
OUT = HERE / "output"
REPORT = OUT / "report.sample.html"
DIGEST = OUT / "day1-digest.sample.md"
COMPANY = "Acme Corp"
AS_OF = "Jan 2026"
SCOPE = "publish.comp_summary"

REQUIRED_COLUMNS = ["emp_id", "level", "job_family", "location", "base_salary",
                    "range_min", "range_mid", "range_max", "exception_flag"]
TEXT_COLUMNS = ["emp_id", "level", "job_family", "location"]
MONEY_COLUMNS = ["base_salary", "range_min", "range_mid", "range_max"]
# Strict charsets reject newlines, control chars, and markdown/HTML metacharacters at INGEST —
# so a contract-valid CSV can never inject a bullet (e.g. "set salary to X") or smuggle a real
# name into the report. This is the first line of the "agent never recommends pay" guarantee.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,&/()'-]{0,63}$")
TEXT_PATTERNS = {"emp_id": ID_RE, "level": TEXT_RE, "job_family": TEXT_RE, "location": TEXT_RE}
# The top band is open-ended so every (positive) compa-ratio lands in exactly one band.
COMPA_BANDS = [("<0.80", 0, 0.7999), ("0.80–0.90", 0.80, 0.8999), ("0.90–1.10", 0.90, 1.10),
               ("1.10–1.20", 1.1001, 1.20), (">1.20", 1.2001, float("inf"))]


class CompContractError(ValueError):
    """Raised when the comp snapshot violates the data contract (fail closed)."""


def _load_registry():
    """Load AND govern-validate the canonical registry. Returns (registry, error).

    The agent will not run without a valid registry — its metric definitions and the
    'never change pay' boundary both come from it, so failing open (rendering an uncited
    report on hardcoded numbers) would defeat the point. main() fails closed on error.
    """
    try:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from core.metrics import MetricRegistry, validate
        reg = MetricRegistry.load()
        problems = validate({"schema_version": reg.schema_version, "metrics": reg.all()})
        if problems:
            return None, f"metric registry failed governance validation: {problems[0]}"
        return reg, None
    except Exception as exc:  # missing file, bad JSON, duplicate keys, import error
        return None, f"metric registry unavailable: {exc}"


METRICS, REGISTRY_ERROR = _load_registry()
from foundation import evidence_portfolio as portfolio_ev  # noqa: E402


def _md(value) -> str:
    """Escape markdown structural characters (defense in depth on top of the ingest charset)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|])", r"\\\1", str(value))


def load_snapshot(path=DATA) -> list:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"comp snapshot not found: {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise CompContractError("snapshot is empty (no header row)")
        # Strict header contract: exactly the required columns, no duplicates, no extras.
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise CompContractError("snapshot has duplicate column headers")
        if set(reader.fieldnames) != set(REQUIRED_COLUMNS):
            missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
            extra = [c for c in reader.fieldnames if c not in REQUIRED_COLUMNS]
            parts = []
            if missing:
                parts.append(f"missing {missing}")
            if extra:
                parts.append(f"unexpected {extra}")
            raise CompContractError(f"header mismatch: {'; '.join(parts)}")
        rows, errors, seen = [], [], set()
        for i, row in enumerate(reader, start=2):
            if None in row:  # a ragged row produced more values than headers
                errors.append(f"line {i}: too many fields (ragged row)")
                continue
            for c in REQUIRED_COLUMNS:
                if isinstance(row.get(c), str):
                    row[c] = row[c].strip()
            eid = row.get("emp_id") or f"line {i}"
            for col in TEXT_COLUMNS:
                val = row.get(col)
                if not val:
                    errors.append(f"{eid}: empty required field '{col}'")
                elif not TEXT_PATTERNS[col].match(val):
                    errors.append(f"{eid}: '{col}' has illegal characters (control/markdown chars rejected)")
            if row.get("emp_id"):
                if row["emp_id"] in seen:
                    errors.append(f"{eid}: duplicate emp_id")
                seen.add(row["emp_id"])
            for col in MONEY_COLUMNS:
                try:
                    row[col] = int(row[col])
                    if row[col] <= 0:
                        errors.append(f"{eid}: {col} must be positive")
                except (ValueError, TypeError):
                    errors.append(f"{eid}: {col} not an integer ('{row.get(col)}')")
            if all(isinstance(row.get(c), int) for c in MONEY_COLUMNS):
                # Strict ordering: rules out zero-width bands (undefined range penetration).
                if not (row["range_min"] < row["range_mid"] < row["range_max"]):
                    errors.append(f"{eid}: band not strictly ordered (min < mid < max)")
            if row.get("exception_flag") not in ("yes", "no"):
                errors.append(f"{eid}: exception_flag must be yes/no")
            rows.append(row)
        if not rows:
            raise CompContractError("snapshot has a header but no data rows")
        if errors:
            shown = "; ".join(errors[:8])
            more = f" (+{len(errors) - 8} more)" if len(errors) > 8 else ""
            raise CompContractError(f"{len(errors)} contract violation(s): {shown}{more}")
        return rows


def enrich(rows):
    for r in rows:
        r["compa_ratio"] = round(r["base_salary"] / r["range_mid"], 2)
        span = r["range_max"] - r["range_min"]
        r["range_penetration"] = round(100 * (r["base_salary"] - r["range_min"]) / span) if span else 0
        below = r["base_salary"] < r["range_min"]
        above = r["base_salary"] > r["range_max"]
        r["out_of_band"] = below or above
        r["direction"] = "below min" if below else ("above max" if above else "in band")
        r["unexcepted_oob"] = r["out_of_band"] and r["exception_flag"] == "no"
    return rows


def build_report(rows):
    enrich(rows)
    n = len(rows)
    oob = [r for r in rows if r["out_of_band"]]
    exceptions = [r for r in rows if r["exception_flag"] == "yes"]
    unexcepted = [r for r in rows if r["unexcepted_oob"]]
    compas = [r["compa_ratio"] for r in rows]

    dist = {label: 0 for label, _, _ in COMPA_BANDS}
    for r in rows:
        for label, lo, hi in COMPA_BANDS:
            if lo <= r["compa_ratio"] <= hi:
                dist[label] += 1
                break
    # Every employee must land in exactly one band — the distribution always reconciles.
    assert sum(dist.values()) == n, "compa-ratio distribution failed to reconcile to population"

    by_level = {}
    for r in rows:
        s = by_level.setdefault(r["level"], {"n": 0, "compa": [], "oob": 0})
        s["n"] += 1
        s["compa"].append(r["compa_ratio"])
        if r["out_of_band"]:
            s["oob"] += 1
    for s in by_level.values():
        s["avg_compa"] = round(statistics.mean(s["compa"]), 2)
        del s["compa"]

    return {
        "as_of": AS_OF,
        "kpis": {
            "population": n,
            "avg_compa": round(statistics.mean(compas), 2) if compas else 0,
            "out_of_band": len(oob),
            "out_of_band_rate": round(100 * len(oob) / n) if n else 0,
            "exceptions": len(exceptions),
            "unexcepted_oob": len(unexcepted),
        },
        "narrative": build_narrative(rows, oob, unexcepted, compas),
        "distribution": dist,
        "by_level": dict(sorted(by_level.items())),
        "oob_flags": sorted(
            ({"emp_id": r["emp_id"], "level": r["level"], "family": r["job_family"],
              "base": r["base_salary"], "band": f"{r['range_min']:,}–{r['range_max']:,}",
              "direction": r["direction"], "exception": r["exception_flag"]}
             for r in oob), key=lambda x: x["exception"]),
    }


def build_narrative(rows, oob, unexcepted, compas):
    avg = round(statistics.mean(compas), 2) if compas else 0
    parts = [f"Average compa-ratio is {avg} across {len(rows)} employees."]
    if oob:
        parts.append(f"{len(oob)} sit outside their band; "
                     f"{len(unexcepted)} of those have NO documented exception — the governance gap to close.")
    else:
        parts.append("Everyone is within band.")
    return " ".join(parts)


# ---------- rendering (skrodzkai dark) ----------

_STYLE = """
:root{--bg:#06131d;--panel:#0a1f2c;--panel2:#0f2a3e;--text:#eef7ff;--muted:#8db1ce;--soft:#8296ab;--cyan:#1ba7ff;--cyan2:#48c7ff;
--green:#43d477;--red:#ff4d4f;--amber:#f7b955;--line:rgba(27,167,255,.30);--hair:rgba(141,177,206,.16);--rule:#14364a;--track:rgba(255,255,255,.06);}
*{box-sizing:border-box;}body{margin:0;background:radial-gradient(1100px 420px at 78% -10%,rgba(27,167,255,.10),transparent 70%),var(--bg);background-repeat:no-repeat;color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:15px;line-height:1.45;}
.wrap{max-width:920px;margin:0 auto;padding:28px 26px 40px;}.mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;}
.brand-row{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid var(--cyan);padding-bottom:14px;margin-bottom:22px;}
.brand{font-weight:800;font-size:17px;color:#fff;}.brand .os{color:var(--cyan);}
.status{border:1px solid rgba(247,185,85,.5);color:var(--amber);background:rgba(247,185,85,.12);font-size:11px;font-weight:800;padding:4px 11px;border-radius:999px;text-transform:uppercase;}
.kicker{color:var(--cyan);text-transform:uppercase;font-size:11px;letter-spacing:.1em;font-weight:800;}
h1{margin:6px 0 4px;font-size:25px;color:#fff;font-weight:800;}.date{color:var(--muted);font-size:13px;}
.callout{margin:18px 0 4px;background:rgba(27,167,255,.06);border:1px solid rgba(27,167,255,.22);border-left:3px solid var(--cyan);border-radius:0 8px 8px 0;padding:12px 15px;}
.callout .label{color:var(--cyan2);text-transform:uppercase;font-size:10.5px;font-weight:800;letter-spacing:.06em;}.callout p{margin:4px 0 0;font-size:14px;}
.meter{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:20px 0 6px;}
.metric{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--hair);border-radius:12px;padding:13px 15px;}
.metric strong{display:block;font-size:24px;font-weight:700;color:#fff;}.metric span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;margin-top:4px;}
.section-title{color:var(--cyan);text-transform:uppercase;font-size:13px;font-weight:900;letter-spacing:.05em;margin:24px 0 10px;}
.row{display:flex;align-items:center;gap:9px;margin:6px 0;}.lbl{width:90px;font-size:12px;color:var(--muted);}
.bar{flex:1;background:var(--track);border-radius:4px;height:13px;overflow:hidden;}.bar>div{height:13px;border-radius:4px;}.val{width:18px;text-align:right;font-size:12px;}
table.data{width:100%;border-collapse:collapse;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:12.5px;}
table.data th{text-align:left;color:var(--cyan);background:#0a1f2c;padding:7px 8px;font-size:10px;text-transform:uppercase;font-weight:800;}
table.data td{padding:7px 8px;border-top:1px solid rgba(141,177,206,.20);}table.data .c{text-align:center;}
.footer{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;margin-top:24px;border-top:1px solid #14364a;padding-top:14px;color:var(--soft);font-size:11px;}
"""


def _bar(label, value, maxv, color):
    pct = round(100 * value / maxv) if maxv else 0
    return (f'<div class="row"><div class="lbl">{html.escape(label)}</div>'
            f'<div class="bar"><div style="width:{pct}%;background:{color};"></div></div>'
            f'<div class="val mono">{value}</div></div>')


def render_html(report):
    k = report["kpis"]
    cards = (
        f'<div class="metric"><strong class="mono">{k["population"]}</strong><span>Employees</span></div>'
        f'<div class="metric"><strong class="mono">{k["avg_compa"]}</strong><span>Avg compa-ratio</span></div>'
        f'<div class="metric" style="border-color:rgba(255,77,79,.5)"><strong class="mono" style="color:var(--red)">{k["out_of_band"]}</strong><span>Out of band</span></div>'
        f'<div class="metric" style="border-color:rgba(255,77,79,.5)"><strong class="mono" style="color:var(--red)">{k["unexcepted_oob"]}</strong><span>OOB · no exception</span></div>'
        f'<div class="metric"><strong class="mono">{k["exceptions"]}</strong><span>Documented exceptions</span></div>'
    )
    dmax = max(report["distribution"].values(), default=1)
    dist_bars = "".join(_bar(b, n, dmax, "#ff4d4f" if b in ("<0.80", ">1.20") else "#1ba7ff")
                        for b, n in report["distribution"].items())
    level_rows = "".join(
        f'<tr><td>{html.escape(lv)}</td><td class="c mono">{s["n"]}</td><td class="c mono">{s["avg_compa"]}</td>'
        f'<td class="c mono" style="color:{"var(--red)" if s["oob"] else "var(--text)"}">{s["oob"]}</td></tr>'
        for lv, s in report["by_level"].items())
    flag_rows = "".join(
        f'<tr><td class="mono">{html.escape(f["emp_id"])}</td><td>{html.escape(f["level"])} {html.escape(f["family"])}</td>'
        f'<td class="mono">{f["base"]:,}</td><td class="mono">{html.escape(f["band"])}</td>'
        f'<td style="color:var(--red)">{html.escape(f["direction"])}</td>'
        f'<td class="c">{"✓" if f["exception"] == "yes" else "✗ none"}</td></tr>'
        for f in report["oob_flags"]) or '<tr><td colspan="6">No employees out of band.</td></tr>'

    defs_block = ""
    if METRICS:
        ids = ["compa_ratio", "range_penetration", "out_of_band_rate", "comp_exception_rate"]
        items = "".join(f'<li><b style="color:#eef7ff">{html.escape(METRICS.get(i)["name"])}</b> — '
                        f'{html.escape(METRICS.get(i)["definition"])}</li>' for i in ids if METRICS.get(i))
        defs_block = ('<div class="section-title">Metric definitions</div>'
                      f'<ul style="font-size:12px;color:#8db1ce;line-height:1.6;margin:0;padding-left:18px;">{items}</ul>'
                      '<div style="font-size:11px;color:#687b95;margin-top:6px;">Cited from '
                      '<b style="color:#8db1ce">metrics.registry.json</b>. Per the registry, this agent may '
                      'calculate and flag — it must <b style="color:#8db1ce">never recommend or change pay</b>.</div>')

    body = f"""<div class="wrap">
  <div class="brand-row"><span class="brand">Agentic People<span class="os">OS</span></span>
    <span class="status">Draft · awaiting publish approval</span></div>
  <div class="kicker">Total Rewards</div><h1>Compensation Report</h1>
  <div class="date">{html.escape(COMPANY)} &middot; as of {html.escape(report['as_of'])} &middot; synthetic data</div>
  <div class="callout"><div class="label">What needs attention</div><p>{html.escape(report['narrative'])}</p></div>
  <div class="meter">{cards}</div>
  <div class="section-title">Compa-ratio distribution</div>{dist_bars}
  <div class="section-title">By level</div>
  <table class="data"><thead><tr><th>Level</th><th class="c">Employees</th><th class="c">Avg compa</th><th class="c">Out of band</th></tr></thead><tbody>{level_rows}</tbody></table>
  <div class="section-title">Out-of-band flags</div>
  <table class="data"><thead><tr><th>Employee</th><th>Role</th><th>Base</th><th>Band</th><th>Direction</th><th class="c">Exception</th></tr></thead><tbody>{flag_rows}</tbody></table>
  {defs_block}
  <div class="footer"><span>Generated by the <b style="color:var(--muted)">comp-reporting</b> agent &middot; Agentic PeopleOS</span>
    <span>Human-in-the-loop: agent flags; Total Rewards owns every pay decision</span></div>
</div>"""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(COMPANY)} — Compensation Report</title>"
            f"<style>{_STYLE}</style></head><body>{body}</body></html>")


def render_digest(report):
    k = report["kpis"]
    lines = [f"# {COMPANY} — Compensation digest", f"_As of {report['as_of']} · draft for review_", "",
             f"- {report['narrative']}",
             f"- **{k['population']}** employees · avg compa-ratio **{k['avg_compa']}** · "
             f"**{k['out_of_band']}** out of band ({k['out_of_band_rate']}%), **{k['unexcepted_oob']}** without an exception."]
    if report["oob_flags"]:
        worst = [f for f in report["oob_flags"] if f["exception"] == "no"]
        if worst:
            w = worst[0]
            lines.append(f"- Fix first: {_md(w['emp_id'])} ({_md(w['level'])} {_md(w['family'])}) is "
                         f"{w['direction']} with no documented exception.")
    lines += ["", "_Metrics cited from metrics.registry.json. This agent flags; it never recommends or changes pay._",
              "", "_Publish gate: a human (Total Rewards) must approve before this is distributed._"]
    return "\n".join(lines) + "\n"


# ---------- fail-closed + entrypoint ----------

def _one_line(text, limit=300) -> str:
    """Collapse whitespace (incl. newlines) and truncate — a failure/approval record is one line."""
    return " ".join(str(text).split())[:limit]


def _fail_closed(message) -> int:
    for p in portfolio_ev.managed_outputs(REPORT, DIGEST):
        try:
            if p.exists():
                p.rename(p.with_name(p.name + ".stale"))
        except OSError:
            pass
    (OUT / "PUBLISHED.json").unlink(missing_ok=True)   # a FAILED run must not leave a prior "published" flag
    print(f"FAIL CLOSED: {_one_line(message)}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str):
    """Write fully to a temp sibling, then os.replace — a crash never leaves a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acme Corp compensation reporting agent (example).")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--approved-by", default=None)
    args = ap.parse_args(argv)

    # Fail closed if the governed metric registry is missing or doesn't pass its own validator —
    # this agent must not produce an uncited report on un-governed numbers.
    if METRICS is None:
        return _fail_closed(REGISTRY_ERROR or "metric registry unavailable")

    # Validate the RAW approver: reject any control character (newline/tab/CR) outright, and require a
    # full charset match (re.fullmatch — no trailing-newline bypass).
    raw_approver = args.approved_by or ""
    approver = raw_approver.strip()
    if args.publish and (any(ord(c) < 32 for c in raw_approver)
                         or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,'&()-]{0,79}", approver)):
        print("PUBLISH GATE: refused. Distribution requires a valid named human approver (Total Rewards).\n"
              "  Re-run with:  --publish --approved-by \"Your Name\"", file=sys.stderr)
        return 2

    try:
        rows = load_snapshot(Path(args.data))
        report = build_report(rows)
        # Render both artifacts fully in memory BEFORE touching disk (atomic + all-or-nothing).
        html_doc, digest_doc = render_html(report), render_digest(report)
        html_doc, digest_doc, report_evidence, digest_evidence = portfolio_ev.prepare_pair(
            "comp-reporting", report, html_doc, digest_doc, REPO)
    except FileNotFoundError as exc:
        return _fail_closed(str(exc))
    except CompContractError as exc:
        return _fail_closed(str(exc))

    pub_path = OUT / "PUBLISHED.json"
    pub_path.unlink(missing_ok=True)   # remove any stale approval BEFORE writing — a draft or a
    #                                  # failed run must never inherit a prior run's "published" flag
    try:
        OUT.mkdir(exist_ok=True)
        for p in portfolio_ev.managed_outputs(REPORT, DIGEST):
            stale = p.with_name(p.name + ".stale")
            if stale.exists():
                stale.unlink()
        _atomic_write(REPORT, html_doc)
        _atomic_write(DIGEST, digest_doc)
        portfolio_ev.write_sidecars(REPORT, DIGEST, report_evidence, digest_evidence)
        # The approval record is part of the same all-or-nothing transaction (no false "approved"
        # with no record): structured JSON so the approver name can't inject extra fields/lines.
        if args.publish:
            _atomic_write(pub_path,
                          json.dumps({"approved_by": approver, "scope": SCOPE, "as_of": report["as_of"]},
                                     indent=2) + "\n")
    except OSError as exc:
        for p in (REPORT, DIGEST, pub_path):
            tmp = p.with_name(p.name + ".tmp")
            try:
                tmp.unlink()
            except OSError:
                pass
        return _fail_closed(f"could not write output: {exc}")

    k = report["kpis"]
    print(f"{COMPANY} compensation report — as of {report['as_of']}")
    print(f"  population: {k['population']} | avg compa: {k['avg_compa']} | out of band: {k['out_of_band']} "
          f"({k['unexcepted_oob']} without exception)")
    print("  wrote report.sample.html and day1-digest.sample.md")

    if args.publish:
        print(f"\nPublish approved by {approver}. Recorded locally (no external send).")
    else:
        print("\nDRAFT only. A human (Total Rewards) must approve before distribution. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
