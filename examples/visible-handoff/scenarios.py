#!/usr/bin/env python3
"""Generate the committed sample ledgers/transcripts for both handoff outcomes.

`run.py` runs the happy path (an entitled human approves → publish). This script also
commits the **denied** path so a reviewer can read, without running anything, what the
system does when a *non-entitled* actor reacts: the registry refuses to count the
reaction as an approval, the gated action never runs, and the agent escalates — every
step still a row in a hash-chained ledger that validates.

    python3 scenarios.py        # writes output/<approved|denied>.{transcript,events}.sample.*

Deterministic and offline. All data synthetic (Acme Corp).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import run  # noqa: E402
from core.event_log import write_anchor  # noqa: E402

OUT = HERE / "output"

# Each scenario: (label, approver_id, expectation).
# hr.business-partner is in the hr_approver pool (entitled); obs.engineering is the
# Engineering Observer — present in the channel as read-only, never entitled to approve.
SCENARIOS = [
    ("approved", "hr.business-partner", {"decision": "approved", "action_taken": True}),
    ("denied", "obs.engineering", {"decision": "denied", "action_taken": False}),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rc = 0
    for label, approver, expect in SCENARIOS:
        r = run.run_handoff(OUT, approver_id=approver)

        # Re-home the generated ledger/transcript to stable, committed sample names so the
        # two scenarios don't clobber each other (run_handoff writes events.jsonl/transcript.md).
        sample_ledger = OUT / f"{label}.events.sample.jsonl"
        sample_ledger.write_text((OUT / "events.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        (OUT / f"{label}.transcript.sample.md").write_text(r["transcript"], encoding="utf-8")
        # commit a matching head-count anchor beside each sample ledger (truncation defense)
        write_anchor(sample_ledger)

        ok = (r["decision"] == expect["decision"]
              and r["action_taken"] == expect["action_taken"]
              and not r["violations"])  # ledger must validate in BOTH outcomes
        status = "OK" if ok else "MISMATCH"
        if not ok:
            rc = 1
        print(f"{label:8} approver={approver:20} decision={r['decision']:8} "
              f"action_taken={r['action_taken']!s:5} ledger={'valid' if not r['violations'] else 'INVALID'}  [{status}]")

    # Leave the canonical output/{events.jsonl,transcript.md} as the approved (happy) path,
    # which the README references and CI regenerates via run.py.
    run.run_handoff(OUT, approver_id="hr.business-partner")
    return rc


if __name__ == "__main__":
    sys.exit(main())
