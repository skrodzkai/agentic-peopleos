#!/usr/bin/env python3
"""Messaging surface for Agentic PeopleOS — Slack-first, swappable.

This is the *conversation* surface where agents (as named role-bots) and humans talk in
public, observable channels. It is deliberately a thin adapter: it records messages and
reactions and renders a human-readable transcript. It is NOT the decision record — the
event ledger is. Swap this `SimulatedChat` for a Slack/Teams/Discord adapter behind the
same interface; the ledger and approval model are unchanged.
"""
from __future__ import annotations


class SimulatedChat:
    """An offline stand-in for Slack/Teams/Discord. Records a channel conversation."""

    def __init__(self, adapter: str = "slack", registry=None):
        self.adapter = adapter
        self.registry = registry  # if set (an ApprovalRegistry), the surface enforces channel ACLs
        self._messages = []   # list of message dicts
        self._reactions = []  # list of reaction dicts

    def _check_member(self, actor: dict, channel: str):
        if self.registry is not None and not self.registry.is_member(actor["id"], channel):
            raise PermissionError(f"{actor['id']} is not a member of '{channel}'")

    def post(self, channel: str, actor: dict, *, type: str, text: str,
             case_ref: str = None, requires_approval: bool = False, payload: dict = None,
             ts: str = None) -> str:
        self._check_member(actor, channel)
        ref = f"msg-{len(self._messages) + 1}"
        self._messages.append({
            "ref": ref, "channel": channel, "ts": ts,
            "actor_id": actor["id"], "actor_display": actor["display"], "kind": actor["kind"],
            "type": type, "text": text, "case_ref": case_ref,
            "requires_approval": requires_approval, "payload": payload or {},
        })
        return ref

    def react(self, message_ref: str, actor: dict, emoji: str, ts: str = None) -> dict:
        msg = next((m for m in self._messages if m["ref"] == message_ref), None)
        if msg is None:
            raise ValueError(f"reaction to unknown message_ref '{message_ref}'")
        self._check_member(actor, msg["channel"])
        rec = {"message_ref": message_ref, "actor_id": actor["id"],
               "actor_display": actor["display"], "kind": actor["kind"], "emoji": emoji, "ts": ts}
        self._reactions.append(rec)
        return rec

    def messages(self, channel: str = None):
        return [m for m in self._messages if channel is None or m["channel"] == channel]

    def reactions(self, message_ref: str):
        return [r for r in self._reactions if r["message_ref"] == message_ref]

    # -- human-readable transcript (commit this as the conversation artifact) ----
    def transcript(self, channel: str) -> str:
        lines = [f"# #{channel}", "", f"_Conversation surface: {self.adapter} (simulated). "
                 f"The decision record lives in the event ledger, not here._", ""]
        for m in self.messages(channel):
            badge = "🤖" if m["kind"] == "agent" else "👤"
            gate = "  ·  _awaiting approval_" if m["requires_approval"] else ""
            lines.append(f"**{badge} {m['actor_display']}** · `{m['type']}`{gate}")
            lines.append("")
            lines.append(m["text"])
            for r in self.reactions(m["ref"]):
                lines.append(f"> {r['emoji']} — {r['actor_display']}")
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
