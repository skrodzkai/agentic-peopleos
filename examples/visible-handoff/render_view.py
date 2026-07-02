#!/usr/bin/env python3
"""Render the decision ledger as a dark HTML view (presentation helper, not a test).

    python3 render_view.py        # reads output/events.jsonl -> output/ledger.sample.html

It re-validates the ledger and shows the integrity verdict. Skrodzkai house style.
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from core.event_log import validate_log  # noqa: E402
from core.approval_registry import ApprovalRegistry  # noqa: E402

LEDGER = HERE / "output" / "events.jsonl"
OUT = HERE / "output" / "ledger.sample.html"

BADGE = {"request": "#8db1ce", "recommendation": "#1ba7ff", "approval": "#43d477",
         "action": "#43d477", "escalation": "#f7b955", "fyi": "#6d8294"}

STYLE = """
body{margin:0;background:#000;color:#eef7ff;font-family:Inter,-apple-system,'Segoe UI',sans-serif;}
.wrap{max-width:820px;margin:0 auto;padding:28px 24px;}
.brand{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #1ba7ff;padding-bottom:12px;}
.brand b{font-weight:800;font-size:16px}.brand .os{color:#1ba7ff}
.ok{background:rgba(67,212,119,.14);color:#43d477;border:1px solid rgba(67,212,119,.4);font-size:11px;font-weight:700;padding:4px 11px;border-radius:999px}
h1{font-size:18px;font-weight:800;margin:16px 0 2px}.sub{color:#8db1ce;font-size:12.5px;margin-bottom:16px}
.ev{border:1px solid rgba(27,167,255,.28);border-radius:10px;padding:11px 14px;margin:8px 0;background:#05080d}
.row{display:flex;align-items:center;gap:10px}
.seq{font-family:'JetBrains Mono',monospace;color:#6d8294;font-size:12px;width:34px}
.type{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;padding:2px 9px;border-radius:6px}
.who{color:#eef7ff;font-size:13px}.who .k{color:#8db1ce}
.scope{margin-left:auto;color:#8db1ce;font-size:11px;font-family:'JetBrains Mono',monospace}
.txt{color:#d7e4ef;font-size:12.5px;margin:7px 0 0}
.hash{color:#48566a;font-size:10.5px;font-family:'JetBrains Mono',monospace;margin-top:7px}
.foot{color:#6d8294;font-size:11px;margin-top:18px;border-top:1px solid #193044;padding-top:12px}
"""


def _txt(ev):
    p = ev.get("payload", {})
    t = ev["type"]
    if t == "request":
        return p.get("ask", "")
    if t == "recommendation":
        k = p.get("kpis", {})
        return f"{k.get('total_open','?')} open reqs, {k.get('at_risk','?')} at risk — requires approval."
    if t == "approval":
        a = ev.get("approval", {})
        return f"decision: {a.get('decision')} · entitled: {a.get('entitled')} · by {a.get('by')}"
    if t == "action":
        return "published the weekly TA digest (gated on approval)."
    if t == "fyi":
        return f"security: {p.get('security','')}"
    return ev.get("payload", {}).get("reason", "")


def main():
    events = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    violations = validate_log(LEDGER, registry=ApprovalRegistry.from_json(HERE / "approval_registry.json"))
    verdict = ('<span class="ok">LEDGER OK · chain verified</span>' if not violations
               else f'<span class="ok" style="color:#ff4d4f">{len(violations)} violation(s)</span>')
    rows = []
    for ev in events:
        c = BADGE.get(ev["type"], "#8db1ce")
        actor = ev["actor"]
        icon = "🤖" if actor["kind"] == "agent" else "👤"
        rows.append(
            f'<div class="ev"><div class="row">'
            f'<span class="seq">#{ev["sequence"]}</span>'
            f'<span class="type" style="background:{c}22;color:{c}">{html.escape(ev["type"])}</span>'
            f'<span class="who">{icon} {html.escape(actor["display"])} <span class="k">· {html.escape(actor["kind"])}</span></span>'
            f'<span class="scope">{html.escape(ev.get("scope","") or "")}</span></div>'
            f'<div class="txt">{html.escape(_txt(ev))}</div>'
            f'<div class="hash">id {ev["event_id"]} · prev {ev["prev_hash"][:10]}… · hash {ev["event_hash"][:10]}…</div>'
            f'</div>')
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}</style></head><body>"
            f"<div class='wrap'><div class='brand'><b>Agentic People<span class='os'>OS</span></b>{verdict}</div>"
            f"<h1>Decision ledger — case TA-2026-W03</h1>"
            f"<div class='sub'>The audit record of decisions, actions, and approvals · synthetic data</div>"
            f"{''.join(rows)}"
            f"<div class='foot'>Every action binds to an entitled approval by causation + scope. "
            f"Re-derive entitlement and detect tampering: "
            f"<code style='color:#8db1ce'>python3 -m core.event_log validate output/events.jsonl --registry …</code></div>"
            f"</div></body></html>")
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT.name} ({'valid' if not violations else 'INVALID'})")


if __name__ == "__main__":
    main()
