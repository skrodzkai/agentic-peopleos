#!/usr/bin/env python3
"""Shared evidence UI, value primitives, and render-coverage verification.

The sidecar JSON is the machine contract.  This module gives the same graph a human
surface: evidence-aware values, section scopes, a keyboard-accessible drawer, and
offline coverage checks.  Manifest data is base64-embedded and rendered with DOM
``textContent`` only, so an untrusted source label cannot become executable markup.
"""
from __future__ import annotations

import base64
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser

from core import evidence as core_evidence


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,159}$")
_MD_RE = re.compile(r"<!--\s*evidence:([a-z0-9][a-z0-9._:@/-]{0,159})\s*-->")
_EMBED_RE = re.compile(r"<script type='application/octet-stream' id='evidence-manifest'[^>]*>([^<]+)</script>")
_INTERACTIVE = {"a", "button"}


class EvidenceRenderError(ValueError):
    """Raised when a renderer tries to emit an invalid or unsupported evidence reference."""


@dataclass(frozen=True)
class EvidenceValue:
    display: str
    claim_id: str
    label: str = ""
    raw: object = None


@dataclass(frozen=True)
class MarkdownReference:
    display: str
    claim_id: str
    anchor: str = ""


def _claim_id(value):
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise EvidenceRenderError("invalid evidence claim id: %r" % value)
    return value


def value(display, claim_id, label="", raw=None):
    """A semantic value that shared renderers turn into an evidence trigger."""
    return EvidenceValue(str(display), _claim_id(claim_id), str(label or ""), raw)


def trigger(display, claim_id, label=""):
    """Render one escaped value as an accessible evidence button."""
    claim_id = _claim_id(claim_id)
    display = str(display)
    aria = label or "Open evidence for %s" % display
    return ("<button type='button' class='evidence-trigger' data-evidence-id='%s' "
            "aria-label='%s' aria-haspopup='dialog'>%s<span class='evidence-dot' "
            "aria-hidden='true'></span></button>" %
            (html.escape(claim_id, quote=True), html.escape(aria, quote=True), html.escape(display)))


def render_value(item):
    if isinstance(item, EvidenceValue):
        return trigger(item.display, item.claim_id, item.label)
    return html.escape(str(item))


def scope(body_html, claim_ids, label="Open evidence for this section", css_class=""):
    """Wrap supporting values in a traceable scope without making every axis tick a graph node."""
    ids = [_claim_id(claim_id) for claim_id in claim_ids]
    if not ids:
        raise EvidenceRenderError("an evidence scope must reference at least one claim")
    joined = " ".join(ids)
    classes = "evidence-scope" + ((" " + css_class.strip()) if css_class.strip() else "")
    return ("<div class='%s' data-evidence-scope='%s'>%s"
            "<button type='button' class='evidence-scope-button' data-evidence-scope-open='%s' "
            "aria-haspopup='dialog'>Trace this view</button></div>" %
            (html.escape(classes, quote=True), html.escape(joined, quote=True), body_html,
             html.escape(joined, quote=True)))


def reference(display, claim_id, anchor=""):
    """Declare the exact visible Markdown value and optional unique context to annotate."""
    return MarkdownReference(str(display), _claim_id(claim_id), str(anchor or ""))


def _bounded_starts(text, needle):
    starts = []
    offset = 0
    while True:
        found = text.find(needle, offset)
        if found < 0:
            return starts
        end = found + len(needle)
        left_ok = not needle[:1].isalnum() or found == 0 or not text[found - 1].isalnum()
        right_ok = not needle[-1:].isalnum() or end == len(text) or not text[end].isalnum()
        if left_ok and right_ok:
            starts.append(found)
        offset = found + 1


def markdown_refs(text, references):
    """Attach each claim marker immediately after its exact visible Markdown value.

    All spans are planned against the original text and must be unique and
    non-overlapping. This prevents a short value from matching a date/title or a
    marker inserted for an earlier claim.
    """
    original = str(text)
    plans = []
    for item in references:
        if not isinstance(item, MarkdownReference):
            raise EvidenceRenderError("markdown_refs requires reference(display, claim_id, anchor) objects")
        anchor = item.anchor or item.display
        if anchor.count(item.display) != 1:
            raise EvidenceRenderError("Markdown anchor for %s must contain its display exactly once" %
                                      item.claim_id)
        starts = _bounded_starts(original, anchor)
        if len(starts) != 1:
            raise EvidenceRenderError("Markdown anchor for %s must be unique (found %d)" %
                                      (item.claim_id, len(starts)))
        start = starts[0] + anchor.index(item.display)
        plans.append((start, start + len(item.display), item.claim_id))
    ordered = sorted(plans)
    for left, right in zip(ordered, ordered[1:]):
        if right[0] < left[1]:
            raise EvidenceRenderError("Markdown evidence spans overlap for %s and %s" %
                                      (left[2], right[2]))
    output = original
    for _start, end, claim_id in sorted(plans, reverse=True):
        output = output[:end] + "<!-- evidence:%s -->" % claim_id + output[end:]
    return output


class _EvidenceHTMLParser(HTMLParser):
    """Parse rendered evidence semantics and reject structurally unsafe triggers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.violations = []
        self.triggers = defaultdict(list)
        self.scopes = set()
        self._button_stack = []
        self._interactive_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        names = [name.lower() for name, _value in attrs]
        if len(names) != len(set(names)):
            self.violations.append("HTML contains duplicate attributes on <%s>" % tag)
        attr = {name.lower(): (value or "") for name, value in attrs}
        for name, value in attrs:
            lowered = (value or "").lower()
            if "<button" in lowered or "data-evidence-id=" in lowered:
                self.violations.append("evidence markup appears inside the %s attribute of <%s>" %
                                       (name, tag))
        classes = set(attr.get("class", "").split())
        if tag in _INTERACTIVE:
            if self._interactive_stack:
                self.violations.append("nested interactive element <%s> inside <%s>" %
                                       (tag, self._interactive_stack[-1]))
            self._interactive_stack.append(tag)
        evidence_id = attr.get("data-evidence-id")
        is_trigger = tag == "button" and "evidence-trigger" in classes
        if evidence_id is not None and not is_trigger:
            self.violations.append("data-evidence-id must appear on an evidence-trigger button")
        context = None
        if is_trigger:
            if not evidence_id or not _ID_RE.fullmatch(evidence_id):
                self.violations.append("evidence trigger has an invalid or missing claim id")
            else:
                context = {"claim_id": evidence_id, "text": []}
        if tag == "button":
            self._button_stack.append(context)
        scope_ids = attr.get("data-evidence-scope")
        if scope_ids is not None:
            ids = scope_ids.split()
            if not ids or any(not _ID_RE.fullmatch(claim_id) for claim_id in ids):
                self.violations.append("data-evidence-scope contains an invalid claim id")
            else:
                self.scopes.update(ids)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self._button_stack and self._button_stack[-1] is not None:
            self._button_stack[-1]["text"].append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "button":
            if not self._button_stack:
                self.violations.append("HTML closes a button that was never opened")
            else:
                context = self._button_stack.pop()
                if context is not None:
                    self.triggers[context["claim_id"]].append("".join(context["text"]))
        if tag in _INTERACTIVE:
            if not self._interactive_stack:
                self.violations.append("HTML closes interactive <%s> that was never opened" % tag)
            elif self._interactive_stack[-1] != tag:
                self.violations.append("interactive HTML closes <%s> while <%s> is open" %
                                       (tag, self._interactive_stack[-1]))
                if tag in self._interactive_stack:
                    self._interactive_stack = self._interactive_stack[:self._interactive_stack.index(tag)]
            else:
                self._interactive_stack.pop()

    @property
    def references(self):
        return set(self.triggers) | set(self.scopes)


def _parse_html(content):
    parser = _EvidenceHTMLParser()
    try:
        parser.feed(content)
        parser.close()
    except (TypeError, ValueError) as exc:
        parser.violations.append("HTML evidence parse failed: %s" % exc)
    if parser._button_stack:
        parser.violations.append("HTML contains an unclosed button")
    if parser._interactive_stack:
        parser.violations.append("HTML contains an unclosed interactive element")
    return parser


def referenced_claims(content, artifact_type):
    if artifact_type in ("dashboard", "report"):
        return _parse_html(content).references
    if artifact_type in ("digest", "decision_packet"):
        return set(_MD_RE.findall(content))
    return set()


def coverage_violations(content, manifest, require_shell=True):
    """Verify references point to the correct visible values in structurally safe markup."""
    manifest_issues = core_evidence.validate_manifest(manifest)
    if manifest_issues:
        return ["invalid evidence manifest: %s" % issue for issue in manifest_issues]
    artifact = manifest.get("artifact", {}) if isinstance(manifest, dict) else {}
    artifact_type = artifact.get("artifact_type")
    claims = {claim.get("id"): claim for claim in manifest.get("claims", [])
              if isinstance(claim, dict) and claim.get("id")}
    material = {claim_id for claim_id, claim in claims.items() if claim.get("material")}
    violations = []
    parser = None
    if artifact_type in ("dashboard", "report"):
        parser = _parse_html(content)
        refs = parser.references
        violations.extend(parser.violations)
        for claim_id, displays in sorted(parser.triggers.items()):
            claim = claims.get(claim_id)
            if claim is None:
                continue
            for display in displays:
                if display != claim.get("display_value"):
                    violations.append("trigger for '%s' renders %r, expected display_value %r" %
                                      (claim_id, display, claim.get("display_value")))
        for claim_id in sorted(material):
            if not parser.triggers.get(claim_id):
                violations.append("material claim '%s' has no direct display-value trigger" % claim_id)
    else:
        refs = referenced_claims(content, artifact_type)
        for match in _MD_RE.finditer(content):
            claim_id = match.group(1)
            claim = claims.get(claim_id)
            if claim is None:
                continue
            line_start = content.rfind("\n", 0, match.start()) + 1
            before = content[line_start:match.start()]
            if not before.endswith(claim.get("display_value", "")):
                violations.append("Markdown reference for '%s' is not attached to display_value %r" %
                                  (claim_id, claim.get("display_value")))
    for claim_id in sorted(refs - set(claims)):
        violations.append("render references missing claim '%s'" % claim_id)
    for claim_id in sorted(material - refs):
        violations.append("material claim '%s' is not referenced by the rendered artifact" % claim_id)
    if artifact_type in ("dashboard", "report") and require_shell:
        if "id='evidence-manifest'" not in content or "id='evidence-drawer'" not in content:
            violations.append("HTML artifact is missing the embedded evidence shell")
    return violations


def coverage_report(content, manifest):
    if core_evidence.validate_manifest(manifest):
        return {"material": 0, "material_referenced": 0, "all_claims": 0,
                "all_referenced": 0, "unknown_references": 0}
    claims = {claim["id"]: claim for claim in manifest.get("claims", []) if isinstance(claim, dict)}
    material = {claim_id for claim_id, claim in claims.items() if claim.get("material")}
    refs = referenced_claims(content, manifest.get("artifact", {}).get("artifact_type"))
    return {
        "material": len(material),
        "material_referenced": len(material & refs),
        "all_claims": len(claims),
        "all_referenced": len(set(claims) & refs),
        "unknown_references": len(refs - set(claims)),
    }


def extract_embedded_manifest(content):
    match = _EMBED_RE.search(content)
    if not match:
        raise EvidenceRenderError("HTML artifact has no embedded evidence manifest")
    try:
        return json.loads(base64.b64decode(match.group(1), validate=True).decode("utf-8"),
                          object_pairs_hook=core_evidence._no_dup_keys)
    except (ValueError, UnicodeDecodeError) as exc:
        raise EvidenceRenderError("embedded evidence manifest is not valid base64 JSON: %s" % exc)


def embedded_manifest_violations(content, manifest):
    try:
        embedded = extract_embedded_manifest(content)
    except EvidenceRenderError as exc:
        return [str(exc)]
    if core_evidence.canonical(embedded) != core_evidence.canonical(manifest):
        return ["embedded evidence manifest differs from its committed sidecar"]
    return []


_CSS = r"""
.evidence-trigger{all:unset;display:inline;cursor:pointer;color:inherit;font:inherit;font-variant-numeric:inherit;
  border-bottom:1px dotted rgba(72,199,255,.8);border-radius:2px;position:relative;padding:0 1px;}
.evidence-trigger:hover{background:rgba(27,167,255,.12);}.evidence-trigger:focus-visible,
.evidence-fab:focus-visible,.evidence-close:focus-visible,.evidence-scope-button:focus-visible,
.evidence-claim-pick:focus-visible{outline:2px solid #48c7ff;outline-offset:3px;}
.evidence-dot{display:inline-block;width:5px;height:5px;margin-left:4px;vertical-align:super;border-radius:50%;background:#48c7ff;
  box-shadow:0 0 0 2px rgba(72,199,255,.14);}
.evidence-scope{position:relative;border-radius:10px;}.evidence-scope:hover{box-shadow:inset 0 0 0 1px rgba(72,199,255,.22);}
.evidence-scope-button{position:absolute;z-index:2;right:7px;top:7px;border:1px solid rgba(72,199,255,.4);
  background:rgba(6,19,29,.92);color:#8fd8ff;border-radius:999px;padding:3px 8px;font:700 8.5px ui-monospace,'SF Mono',Menlo,monospace;
  text-transform:uppercase;letter-spacing:.06em;cursor:pointer;opacity:0;transition:opacity .15s;}
.evidence-scope:hover .evidence-scope-button,.evidence-scope-button:focus{opacity:1;}
.evidence-fab{position:fixed;z-index:9997;right:18px;bottom:18px;border:1px solid rgba(72,199,255,.62);
  color:#dff5ff;background:linear-gradient(180deg,#0d3850,#082536);box-shadow:0 12px 36px rgba(0,0,0,.45);
  border-radius:999px;padding:9px 13px;cursor:pointer;font:700 10px ui-monospace,'SF Mono',Menlo,monospace;
  letter-spacing:.04em;text-transform:uppercase;}.evidence-fab strong{color:#48c7ff;}
.evidence-backdrop{position:fixed;z-index:9998;inset:0;background:rgba(1,8,13,.72);backdrop-filter:blur(2px);}
.evidence-drawer{position:fixed;z-index:9999;right:0;top:0;height:100vh;width:min(480px,94vw);overflow:auto;
  background:#071a26;color:#dbe7f0;border-left:1px solid rgba(72,199,255,.42);box-shadow:-20px 0 60px rgba(0,0,0,.55);
  padding:20px 20px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
.evidence-backdrop[hidden],.evidence-drawer[hidden]{display:none;}.evidence-head{display:flex;align-items:flex-start;gap:12px;
  position:sticky;top:-20px;background:#071a26;padding:20px 0 12px;z-index:1;border-bottom:1px solid rgba(141,177,206,.18);}
.evidence-head-copy{flex:1;min-width:0;}.evidence-kicker{font:700 9px ui-monospace,'SF Mono',Menlo,monospace;color:#48c7ff;
  text-transform:uppercase;letter-spacing:.14em;}.evidence-title{margin:4px 0 0;color:#fff;font-size:18px;line-height:1.28;}
.evidence-close{border:1px solid rgba(141,177,206,.28);color:#dbe7f0;background:#0a2838;width:34px;height:34px;
  border-radius:9px;cursor:pointer;font-size:20px;}.evidence-body{padding-top:14px;}.evidence-badges{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px;}
.evidence-badge{font:700 9px ui-monospace,'SF Mono',Menlo,monospace;text-transform:uppercase;letter-spacing:.06em;
  border:1px solid rgba(141,177,206,.25);border-radius:999px;padding:3px 8px;color:#8db1ce;background:#0a2433;}
.evidence-badge.supported,.evidence-badge.passed{color:#8be7aa;border-color:rgba(67,212,119,.42);background:rgba(67,212,119,.09);}
.evidence-badge.caveated,.evidence-badge.warning{color:#ffd68e;border-color:rgba(247,185,85,.44);background:rgba(247,185,85,.09);}
.evidence-badge.blocked,.evidence-badge.failed{color:#ff9a9b;border-color:rgba(255,77,79,.44);background:rgba(255,77,79,.09);}
.evidence-value-big{font:800 30px ui-monospace,'SF Mono',Menlo,monospace;color:#fff;margin:7px 0 4px;}
.evidence-meta{font:11px ui-monospace,'SF Mono',Menlo,monospace;color:#8296ab;line-height:1.55;word-break:break-word;}
.evidence-section{margin-top:18px;}.evidence-section h3{font:700 10px ui-monospace,'SF Mono',Menlo,monospace;
  color:#48c7ff;text-transform:uppercase;letter-spacing:.12em;margin:0 0 8px;}.evidence-card{border:1px solid rgba(141,177,206,.18);
  background:#0a2130;border-radius:9px;padding:10px 11px;margin:7px 0;}.evidence-card-title{color:#eef7ff;font-weight:700;font-size:12px;}
.evidence-card-sub{color:#8296ab;font:10px/1.5 ui-monospace,'SF Mono',Menlo,monospace;margin-top:3px;word-break:break-word;}
.evidence-card a{color:#48c7ff;text-decoration:none}.evidence-card a:hover{text-decoration:underline}.evidence-note{font-size:12px;
  color:#b7c9d8;line-height:1.55}.evidence-claim-pick{display:block;width:100%;text-align:left;border:1px solid rgba(141,177,206,.18);
  background:#0a2130;color:#dbe7f0;border-radius:9px;padding:10px 11px;margin:7px 0;cursor:pointer;}
.evidence-claim-pick:hover{border-color:rgba(72,199,255,.5);background:#0b293a}.evidence-claim-pick strong{display:block;color:#eef7ff;font-size:12px;}
.evidence-claim-pick span{display:block;color:#8296ab;font:10px ui-monospace,'SF Mono',Menlo,monospace;margin-top:3px;}
body.evidence-open{overflow:hidden}@media(max-width:620px){.evidence-drawer{width:100vw}.evidence-fab{right:10px;bottom:10px}.evidence-scope-button{opacity:1}}
"""


_JS = r"""
(()=>{'use strict';
const dataNode=document.getElementById('evidence-manifest'),drawer=document.getElementById('evidence-drawer'),
backdrop=document.getElementById('evidence-backdrop'),body=document.getElementById('evidence-body'),
title=document.getElementById('evidence-title'),kicker=document.getElementById('evidence-kicker'),
closeBtn=document.getElementById('evidence-close'),summaryBtn=document.getElementById('evidence-summary');
if(!dataNode||!drawer||!body)return;let manifest=null,lastFocus=null;
try{const raw=atob(dataNode.textContent.trim()),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
manifest=JSON.parse(new TextDecoder().decode(bytes));}catch(err){if(summaryBtn){summaryBtn.disabled=true;summaryBtn.textContent='Evidence unavailable';}return;}
const collections=['sources','transformations','assumptions','checks','caveats','claims','reviews','decisions'];
const index={};collections.forEach(name=>{index[name]=new Map((manifest[name]||[]).map(x=>[x.id,x]));});
const E=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
const section=(name)=>{const s=E('section','evidence-section');s.append(E('h3','',name));body.append(s);return s;};
const badge=(text,tone)=>E('span','evidence-badge '+(tone||''),text);
const card=(parent,heading,sub,href)=>{const c=E('div','evidence-card'),h=href?E('a','evidence-card-title',heading):E('div','evidence-card-title',heading);
if(href){h.href=href;h.target='_blank';h.rel='noopener noreferrer';}c.append(h);if(sub)c.append(E('div','evidence-card-sub',sub));parent.append(c);};
const sourceHref=(uri)=>{if(/^https?:\/\//.test(uri))return uri;if(uri&&uri.startsWith('repo:'))return 'https://github.com/skrodzkai/agentic-peopleos/blob/main/'+uri.slice(5);return null;};
const clear=()=>{while(body.firstChild)body.removeChild(body.firstChild);};
const meta=(value)=>{body.append(E('div','evidence-meta',value));};
function renderClaim(id){const c=index.claims.get(id);if(!c)return;clear();kicker.textContent='Claim evidence · '+c.id;title.textContent=c.statement;
const bs=E('div','evidence-badges');bs.append(badge(c.status,c.status));bs.append(badge(c.material?'material':'supporting',''));body.append(bs);
body.append(E('div','evidence-value-big',c.display_value));meta(c.unit+' · '+c.period+' · as of '+c.as_of);
const ss=section('Sources');(c.source_ids||[]).forEach(sid=>{const s=index.sources.get(sid);if(s)card(ss,s.label,s.kind+' · '+s.version+' · as of '+s.as_of+' · '+s.content_hash.slice(0,19)+'…',sourceHref(s.uri));});
if(c.transformation_id){const t=index.transformations.get(c.transformation_id);if(t){const s=section('Transformation');card(s,t.name,t.implementation+' · '+t.version+' · '+t.description);}}
if((c.assumption_ids||[]).length){const s=section('Assumptions');c.assumption_ids.forEach(aid=>{const a=index.assumptions.get(aid);if(a)card(s,a.name,String(a.value)+' '+a.unit+' · '+a.status+' · '+a.version);});}
if((c.check_ids||[]).length){const s=section('Checks');c.check_ids.forEach(cid=>{const x=index.checks.get(cid);if(x){const d=E('div','evidence-card');d.append(badge(x.status,x.status));d.append(badge(x.attestation||'unclassified',''));d.append(E('div','evidence-card-title',x.name));d.append(E('div','evidence-card-sub',x.details+' · '+x.implementation+' · '+(x.source_ids||[]).length+' hashed input(s)'));s.append(d);}});}
if((c.caveat_ids||[]).length){const s=section('Caveats');c.caveat_ids.forEach(cid=>{const x=index.caveats.get(cid);if(x){const d=E('div','evidence-card');d.append(badge(x.severity,x.severity));d.append(E('div','evidence-note',x.text));s.append(d);}});}
if(c.change){const s=section('Change from prior cycle');card(s,c.change.prior_display_value+' → '+c.display_value,c.change.comparability+' · change '+String(c.change.absolute));(c.change.drivers||[]).forEach(d=>card(s,d.label,String(d.effect)+' '+d.unit+' · '+d.type));}
const reviews=(manifest.reviews||[]).filter(r=>(r.claim_ids||[]).includes(id)).sort((a,b)=>b.reviewed_at.localeCompare(a.reviewed_at)),decisions=(manifest.decisions||[]).filter(d=>(d.claim_ids||[]).includes(id));
if(reviews.length){const s=section('Reviews');reviews.forEach(r=>card(s,r.actor_role,r.status+' · '+r.reviewed_at+' · '+r.notes));}
if(decisions.length){const s=section('Decisions');decisions.forEach(d=>card(s,d.decision_type,d.status+' · '+d.owner_role+' · '+d.decided_at));}
meta('Evidence manifest '+document.getElementById('evidence-shell').dataset.evidenceHash);}
function renderList(ids,heading){clear();kicker.textContent='Evidence scope';title.textContent=heading;const bs=E('div','evidence-badges');bs.append(badge(ids.length+' linked claims','supported'));body.append(bs);
ids.forEach(id=>{const c=index.claims.get(id);if(!c)return;const b=E('button','evidence-claim-pick');b.type='button';b.dataset.pickClaim=id;b.append(E('strong','',c.display_value+' · '+c.statement));b.append(E('span','',c.status+' · '+c.as_of));body.append(b);});}
function renderSummary(){const claims=manifest.claims||[],material=claims.filter(c=>c.material),traceable=material.filter(c=>c.transformation_id&&c.check_ids.length&&(c.source_ids.length||c.supporting_claim_ids.length));clear();
kicker.textContent='Evidence Graph v1';title.textContent=manifest.artifact.title;const bs=E('div','evidence-badges');bs.append(badge(traceable.length+'/'+material.length+' material traced','supported'));bs.append(badge((manifest.sources||[]).length+' sources',''));bs.append(badge(material.filter(c=>c.status==='caveated').length+' caveated','caveated'));body.append(bs);
meta(manifest.artifact.period+' · as of '+manifest.artifact.as_of+' · '+manifest.artifact.status);const s=section('Material claims');material.forEach(c=>{const b=E('button','evidence-claim-pick');b.type='button';b.dataset.pickClaim=c.id;b.append(E('strong','',c.display_value+' · '+c.statement));b.append(E('span','',c.status+' · '+c.id));s.append(b);});meta('Evidence manifest '+document.getElementById('evidence-shell').dataset.evidenceHash);}
function open(mode){lastFocus=document.activeElement;if(Array.isArray(mode))renderList(mode,'Evidence behind this view');else if(mode)renderClaim(mode);else renderSummary();drawer.hidden=false;backdrop.hidden=false;document.body.classList.add('evidence-open');closeBtn.focus();}
function close(){drawer.hidden=true;backdrop.hidden=true;document.body.classList.remove('evidence-open');if(lastFocus&&lastFocus.focus)lastFocus.focus();}
document.addEventListener('click',e=>{const pick=e.target.closest('[data-pick-claim]');if(pick){renderClaim(pick.dataset.pickClaim);return;}const t=e.target.closest('[data-evidence-id]');if(t){open(t.dataset.evidenceId);return;}const s=e.target.closest('[data-evidence-scope-open]');if(s){open(s.dataset.evidenceScopeOpen.split(/\s+/).filter(Boolean));}});
if(summaryBtn)summaryBtn.addEventListener('click',()=>open(null));closeBtn.addEventListener('click',close);backdrop.addEventListener('click',close);document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!drawer.hidden)close();});
})();
"""


def decorate_page(html_doc, manifest):
    """Embed a validated graph and append the fixed evidence button/drawer to a complete HTML page."""
    violations = core_evidence.validate_manifest(manifest)
    if violations:
        raise EvidenceRenderError("cannot decorate with invalid manifest: %s" % violations[0])
    if "</head>" not in html_doc or "</body>" not in html_doc:
        raise EvidenceRenderError("evidence decoration requires a complete HTML document")
    if "id='evidence-manifest'" in html_doc:
        raise EvidenceRenderError("HTML document is already evidence-decorated")
    render_violations = coverage_violations(html_doc, manifest, require_shell=False)
    if render_violations:
        raise EvidenceRenderError("cannot decorate invalid evidence markup: %s" % render_violations[0])
    encoded = base64.b64encode(core_evidence.canonical(manifest).encode("utf-8")).decode("ascii")
    cov = core_evidence.coverage(manifest)
    manifest_hash = core_evidence.evidence_hash(manifest)
    shell = (
        "<div id='evidence-shell' data-evidence-hash='%s'>"
        "<button type='button' id='evidence-summary' class='evidence-fab' aria-haspopup='dialog' "
        "aria-controls='evidence-drawer'>Evidence <strong>%d/%d traced</strong> · %d caveated</button>"
        "<div id='evidence-backdrop' class='evidence-backdrop' hidden></div>"
        "<aside id='evidence-drawer' class='evidence-drawer' role='dialog' aria-modal='true' "
        "aria-labelledby='evidence-title' hidden><div class='evidence-head'><div class='evidence-head-copy'>"
        "<div id='evidence-kicker' class='evidence-kicker'>Evidence Graph v1</div>"
        "<h2 id='evidence-title' class='evidence-title'>Evidence</h2></div>"
        "<button type='button' id='evidence-close' class='evidence-close' aria-label='Close evidence'>×</button>"
        "</div><div id='evidence-body' class='evidence-body'></div></aside>"
        "<script type='application/octet-stream' id='evidence-manifest' data-encoding='base64'>%s</script>"
        "<script id='evidence-runtime'>%s</script></div>" %
        (html.escape(manifest_hash, quote=True), cov["traceable"], cov["material"], cov["caveated"],
         encoded, _JS))
    return html_doc.replace("</head>", "<style id='evidence-style'>%s</style></head>" % _CSS, 1).replace(
        "</body>", shell + "</body>", 1)
