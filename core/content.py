#!/usr/bin/env python3
"""Content typing + injection-safe handling for Agentic PeopleOS.

Everything an agent reads — vault notes, channel messages, transcripts — is *content*,
and most of it is untrusted. The architectural rule: only `trusted_policy` is
authoritative, and instructions found inside any retrieved content are **data, never
commands**. This neutralizes prompt injection from poisoned notes or messages.

There is no LLM in the reference examples, so we make the rule concrete and testable:
`policy_lookup` answers strictly from `trusted_policy` notes and cites the source +
its `last-reviewed` date; injected instructions in lower-trust content are ignored.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

CONTENT_TYPES = ("trusted_policy", "draft", "case_note", "transcript", "external_message")
TRUSTED = {"trusted_policy"}

# Heuristic markers used only for defense-in-depth logging — NOT the primary control.
_INJECTION_MARKERS = [
    r"ignore (all )?(previous|prior|above)",
    r"disregard (the )?(policy|rules|instructions)",
    r"you are now",
    r"system\s*:",
    r"assistant\s*:",
    r"approve (everything|all|anything)",
    r"exfiltrate|leak|send .*secret",
    r"override",
]


def is_authoritative(content_type: str) -> bool:
    return content_type in TRUSTED


def resolve_content_type(note: dict) -> str:
    """Derive trust from PROVENANCE, not a self-declared label.

    A note is `trusted_policy` only if a TRUSTED LOADER stamped it (`_trusted_source`,
    set by `load_note()` from the real file path — never by the note body), it lives under
    `policy/`, and it is human-approved (`status == approved`). A note that merely *claims*
    `content_type: trusted_policy`, or even fakes `source`/`status` in its own body, is
    downgraded — that is what stops a poisoned note from self-certifying as authoritative.
    """
    source = str(note.get("source") or note.get("id") or "")
    if note.get("_trusted_source") and source.startswith("policy/") and note.get("status") == "approved":
        return "trusted_policy"
    declared = note.get("content_type")
    if declared in CONTENT_TYPES and declared != "trusted_policy":
        return declared
    return "external_message"


def load_note(path, root) -> dict:
    """Load a vault note and stamp provenance the caller can trust.

    `source` is the REAL path on disk; `_trusted_source` is set HERE (by the loader),
    never by the note body. Channel messages arrive through the messaging adapter as
    `external_message` and never pass through this loader — so they can never be policy.
    """
    p, root = Path(path), Path(root)
    text = p.read_text(encoding="utf-8")
    fm, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip("\n").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
            body = text[end + 4:]
    src = str(p.relative_to(root)).replace("\\", "/")
    fm["source"] = src[:-3] if src.endswith(".md") else src
    fm["_trusted_source"] = True
    fm.setdefault("statement", body.strip())
    return fm


def scan_injection(text: str):
    """Return the injection markers found (for logging/alerting, not enforcement)."""
    found = []
    low = (text or "").lower()
    for pat in _INJECTION_MARKERS:
        if re.search(pat, low):
            found.append(pat)
    return found


def as_data(text: str) -> str:
    """Wrap untrusted text so a prompt treats it as inert data, not instructions.

    Any occurrence of the fence inside the content is stripped so the text cannot break out.
    """
    fence = "<<<UNTRUSTED_DATA>>>"
    return f"{fence}\n{(text or '').replace(fence, '')}\n{fence}"


def _as_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def is_stale(note: dict, as_of, max_age_days: int = 365) -> bool:
    """A policy note is stale if last-reviewed is older than max_age_days.

    Fails closed: a missing or unparseable date is treated as stale (escalate),
    never silently served as current.
    """
    reviewed = note.get("last_reviewed") or note.get("last-reviewed")
    if not reviewed:
        return True
    try:
        return (_as_date(as_of) - _as_date(reviewed)).days > max_age_days
    except (ValueError, TypeError):
        return True


def policy_lookup(notes, topic: str, as_of=None, max_age_days: int = 365):
    """Answer a policy question strictly from trusted_policy notes.

    Returns a dict with the cited answer, or an escalation if no current trusted policy
    exists. Instructions embedded in non-trusted notes are never consulted.
    """
    candidates = [n for n in notes if resolve_content_type(n) == "trusted_policy" and n.get("topic") == topic]
    if not candidates:
        return {"status": "escalate", "reason": f"no trusted policy for topic '{topic}'", "answer": None}
    if len(candidates) > 1:
        return {"status": "escalate", "answer": None,
                "reason": f"multiple approved policies for '{topic}' — resolve ambiguity before answering"}

    note = candidates[0]
    reviewed = note.get("last_reviewed") or note.get("last-reviewed")
    cite = note.get("source") or note.get("id")
    if as_of is not None and is_stale(note, as_of, max_age_days):
        return {"status": "escalate", "reason": f"policy '{topic}' is stale (last reviewed {reviewed})",
                "answer": None, "cite": cite}

    return {
        "status": "answered",
        "answer": note.get("statement"),
        "cite": cite,
        "last_reviewed": reviewed,
    }
