#!/usr/bin/env python3
"""Deterministically extract the Summary Compensation Table (SCT) from a DEF 14A proxy — the messiest,
highest-value table in executive-comp disclosure — into structured rows, WITH a per-row and overall
confidence score, reconciliation, and honest fail-closed behavior.

Why this exists: a proxy SCT is hand-authored HTML. Real filings pad the table with dozens of empty
"spacer" cells, put the '$' in its own cell apart from the number, glue header words together
('FiscalYear', 'Stock-basedAwards'), hang footnote superscripts off values, and DROP the officer's name on
the 2nd/3rd year rows. Naive scraping silently returns misaligned garbage. This layer collapses that mess,
aligns each row to the header, parses the money, and RECONCILES components to the disclosed Total — then
reports how much to trust the result, and refuses to guess when it can't find a real SCT.

    export SEC_UA="Your Name you@example.com"
    python3 extractor.py PCTY                 # fetch the latest DEF 14A + extract its SCT (needs sec-edgar)
    python3 extractor.py --file proxy.html    # extract from a local proxy HTML file (offline)
    python3 extractor.py --demo               # extract from a built-in synthetic SCT (offline, no network)
    python3 extractor.py PCTY --json          # machine-readable result

As a library:
    from extractor import extract_sct
    result = extract_sct(html_string)         # -> {found, confidence, rows, reconciliation, reasons, ...}

Standard library only. Deterministic. Offline-testable. Fail-closed: an unrecognizable document yields a
low/none confidence result with a reason, never a fabricated table. Every extracted figure carries the raw
cell it came from so a human can audit it. Extracted NEO names are personal data from a public filing —
this tool reads them at runtime; do not commit real-name+pay output to a public repo.
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser

# ------------------------------------------------------------------ canonical SCT schema
# The SEC-mandated Summary Compensation Table columns (Item 402(c)). Each maps to header synonyms
# (lowercased, whitespace-collapsed, camelCase-split, footnotes stripped) seen across real proxies. "name"
# and "year" anchor a row; the money columns are parsed; "total" is the reconciliation target.
SCT_COLUMNS = (
    ("name", ("name and principal position", "name & principal position", "name and title",
              "named executive officer", "name")),
    ("year", ("fiscal year", "year")),
    ("salary", ("salary",)),
    ("bonus", ("bonus",)),
    ("stock_awards", ("stock-based awards", "stock based awards", "stock awards", "stock award")),
    ("option_awards", ("option awards", "option award")),
    ("non_equity_incentive", ("non-equity incentive plan compensation", "non equity incentive plan compensation",
                              "non-equity incentive plan", "non-equity incentive", "nonequity incentive")),
    ("pension_nqdc", ("change in pension value and nonqualified deferred compensation earnings",
                      "change in pension value", "pension value and nqdc earnings", "change in pension")),
    ("other_comp", ("all other compensation", "all other comp")),
    ("total", ("total compensation", "total")),
)
_COL_KEYS = [k for k, _ in SCT_COLUMNS]
# money columns whose sum should reconcile to Total (everything except the name/year anchors + total itself)
_MONEY_KEYS = ("salary", "bonus", "stock_awards", "option_awards", "non_equity_incentive",
               "pension_nqdc", "other_comp")
# columns that can NEVER legitimately be negative — only pension_nqdc can (a falling pension value). A negative
# elsewhere signals a mis-parse or a shifted column that reconciliation alone would miss.
_NONNEGATIVE_KEYS = ("salary", "bonus", "stock_awards", "option_awards", "non_equity_incentive",
                     "other_comp", "total")
_REQUIRED_FOR_MATCH = ("name", "year", "salary", "total")   # a table without these is not an SCT
# columns essentially every real SCT carries (used to score header completeness; option/pension are optional)
_CORE_KEYS = ("name", "year", "salary", "bonus", "stock_awards", "non_equity_incentive", "other_comp", "total")
_RECON_TOLERANCE = 2.0        # dollars: SCT components should sum to Total within rounding


class ExtractError(RuntimeError):
    pass


# void elements open no context — they can't contain a table, so they never go on the ancestor stack
_VOID = {"br", "img", "input", "hr", "meta", "link", "area", "base", "col", "embed", "source", "track",
         "wbr", "param"}


# ------------------------------------------------------------------ HTML table parsing (structure-preserving)
class _TableParser(HTMLParser):
    """Collect every <table> as a list of rows, each a list of cell TEXT. Nested tables are collected
    separately (a proxy often wraps the SCT in a layout table). Footnote superscripts (<sup>) are dropped so
    a '(5)' marker doesn't corrupt a value or name. Entities decoded; whitespace collapsed. colspan expands a
    cell into N padded columns so alignment survives. A general element stack tracks hidden state across ALL
    ancestors (a table inside <div style='display:none'> is hidden), and a hidden <tr> is dropped."""

    def __init__(self, hidden_classes=()):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.hidden = []            # parallel to self.tables: was this <table> visually hidden (self or ancestor)?
        self._hc = hidden_classes   # class names the document hides (parsed <style> + conventional utilities)
        self._stack = []            # open <table> frames
        self._el = []               # general open-element stack: (tag, hidden_here) for hidden-ancestor tracking
        self._suppress = 0          # inside <sup>/<style>/<script>: ignore text
        self._pending_colspan = 1

    def _anc_hidden(self):
        return any(h for _, h in self._el)

    def _close_open_row(self, frame):
        """Flush an unclosed cell/row (SEC HTML often omits </td>/</tr>; HTMLParser won't infer them)."""
        if frame["cell"] is not None:
            self._flush_cell(frame)
        if frame["row"] and not frame["row_hidden"]:
            frame["rows"].append(frame["row"])
        frame["row"] = None
        frame["row_hidden"] = False

    def handle_starttag(self, tag, attrs):
        hid = _is_hidden(attrs, self._hc)
        anc = self._anc_hidden()                         # ancestors' hidden state BEFORE pushing self
        if tag not in _VOID:
            self._el.append((tag, hid))
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None,
                                "hidden": hid or anc, "row_hidden": False})
        elif tag == "tr" and self._stack:
            self._close_open_row(self._stack[-1])        # close a previous unclosed <tr> before starting this one
            self._stack[-1]["row"] = []
            self._stack[-1]["row_hidden"] = hid or anc   # a hidden <tr> (or hidden ancestor) -> drop the row
        elif tag in ("td", "th") and self._stack:
            frame = self._stack[-1]
            if frame["cell"] is not None:                # close a previous unclosed <td>/<th> first
                self._flush_cell(frame)
            if frame["row"] is None:
                frame["row"] = []
                frame["row_hidden"] = hid or anc
            frame["cell"] = []
            span = dict(attrs).get("colspan", "1")
            try:
                self._pending_colspan = max(1, min(30, int(str(span).strip())))
            except (ValueError, TypeError):
                self._pending_colspan = 1
        elif tag in ("sup", "style", "script"):
            self._suppress += 1

    def handle_endtag(self, tag):
        if tag in ("sup", "style", "script"):
            self._suppress = max(0, self._suppress - 1)
        for i in range(len(self._el) - 1, -1, -1):       # pop the element stack to the last matching tag
            if self._el[i][0] == tag:                     # (tolerates unclosed inner tags)
                del self._el[i:]
                break
        if tag == "table" and self._stack:
            frame = self._stack.pop()
            if frame["row"] is not None and frame["cell"] is not None:
                self._flush_cell(frame)
            if frame["row"] and not frame["row_hidden"]:
                frame["rows"].append(frame["row"])
            if frame["rows"]:
                self.tables.append(frame["rows"])
                self.hidden.append(frame["hidden"])
        elif tag == "tr" and self._stack and self._stack[-1]["row"] is not None:
            frame = self._stack[-1]
            if frame["cell"] is not None:
                self._flush_cell(frame)
            if not frame["row_hidden"]:                   # a hidden <tr> is dropped entirely
                frame["rows"].append(frame["row"])
            frame["row"] = None
            frame["row_hidden"] = False
        elif tag in ("td", "th") and self._stack and self._stack[-1]["cell"] is not None:
            self._flush_cell(self._stack[-1])

    def _flush_cell(self, frame):
        text = " ".join("".join(frame["cell"]).split())
        frame["row"].append(text)
        for _ in range(self._pending_colspan - 1):
            frame["row"].append("")
        self._pending_colspan = 1
        frame["cell"] = None

    def handle_data(self, data):
        if self._suppress:
            return
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)


_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.I | re.S)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^}]*)\}", re.S)
# class names that conventionally mean "visually hidden" (accessibility / framework utilities) — treated as
# hidden even without a parsed rule, since a decoy could reference them without shipping the stylesheet.
_CONVENTIONAL_HIDDEN = {"hidden", "hide", "hidden-xs", "sr-only", "visually-hidden", "visuallyhidden",
                        "screen-reader-only", "screen-reader-text", "screenreader", "d-none", "is-hidden",
                        "u-hidden", "a11y-hidden", "offscreen", "off-screen", "visually-hidden-focusable"}


def _hidden_classes(doc):
    """Class names this document hides, from its OWN <style> rules (any selector whose body sets
    display:none / visibility:hidden) plus the conventional visually-hidden utility classes. A decoy SCT
    tucked under <div class='hidden'> must be as invisible to us as one styled inline."""
    classes = set(_CONVENTIONAL_HIDDEN)
    for block in _STYLE_BLOCK_RE.findall(doc or ""):
        for sel, body in _CSS_RULE_RE.findall(block):
            if _HIDDEN_STYLE_RE.search(body):
                classes.update(c.lower() for c in re.findall(r"\.([A-Za-z0-9_-]+)", sel))
    return classes


def _is_hidden(attrs, hidden_classes=()):
    """Was this element authored to be invisible? A decoy SCT is commonly injected hidden. We do NOT render
    CSS — this is a heuristic on the element's own hidden/aria-hidden/style attributes AND its class against
    the document's hidden-class set (parsed <style> rules + conventional utility classes)."""
    d = {k.lower(): (v or "") for k, v in attrs}
    if "hidden" in d:
        return True
    if str(d.get("aria-hidden", "")).strip().lower() == "true":
        return True
    if _HIDDEN_STYLE_RE.search(d.get("style", "")):
        return True
    return any(c.lower() in hidden_classes for c in str(d.get("class", "")).split())


def _parse(doc):
    p = _TableParser(_hidden_classes(doc or ""))
    try:
        p.feed(doc or "")
        p.close()
    except Exception as e:                                  # malformed HTML must not crash the extractor
        raise ExtractError(f"could not parse HTML tables: {e}") from e
    return p


def parse_tables(doc: str) -> list:
    """Every <table> in the document as rows-of-cell-text. Deterministic; entities decoded; footnotes dropped."""
    return _parse(doc).tables


def visible_tables(doc: str) -> list:
    """Only the tables that are NOT authored hidden (display:none / hidden / aria-hidden). A hidden decoy SCT
    must never be selected over the real one, so the extractor searches visible tables only."""
    p = _parse(doc)
    return [t for t, h in zip(p.tables, p.hidden) if not h]


# ------------------------------------------------------------------ cell cleaning + header normalization
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_FOOTNOTE_RE = re.compile(r"\(\d+\)|\[\d+\]")
_CURRENCY_ONLY = {"$", "€", "£", "¥"}
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# zero-width / soft-hyphen chars real proxies use as filler — str.strip() does NOT remove these, so a cell
# of only zero-width spaces looks non-empty and inflates the column count. Strip them before the empty test.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿­"), None)
# a lone footnote-reference cell (e.g. '(6)' or '[3]' in its OWN <td>, not attached to a value) is layout
# noise that shifts alignment. 1-2 digit only, so a negative money value like '(1,234)' is never mistaken.
_LONE_FOOTNOTE_RE = re.compile(r"^[\(\[]\d{1,2}[\)\]]$")


def _clean_cells(row):
    """Collapse layout noise so a padded row becomes [anchor, v1, v2, ...] that aligns to the cleaned header:
    drop empty/whitespace cells, zero-width-only cells, lone currency-sign cells (the '$' proxies put in a
    column of its own), and standalone footnote-reference cells like '(6)'."""
    out = []
    for c in row:
        if c is None:
            continue
        t = c.translate(_ZERO_WIDTH).strip()
        if t == "" or t in _CURRENCY_ONLY or _LONE_FOOTNOTE_RE.match(t):
            continue
        out.append(t)
    return out


def _norm(s: str) -> str:
    """Normalize a header cell for synonym matching: split glued camelCase ('FiscalYear' -> 'Fiscal Year'),
    drop footnote markers, decode nbsp, collapse whitespace, lowercase, trim punctuation."""
    s = str(s).replace("\xa0", " ")
    s = _CAMEL_RE.sub(" ", s)
    s = _FOOTNOTE_RE.sub(" ", s)
    return " ".join(s.split()).lower().strip(" :*()")


def _match_header(cleaned_header):
    """Assign each cleaned header cell a canonical SCT key (or None), in order. Greedy longest-synonym match;
    each key used at most once. Returns the ordered key list, or None if the required anchors aren't all
    present (so an arbitrary table is never treated as the SCT)."""
    keys = []
    used = set()
    for cell in cleaned_header:
        n = _norm(cell)
        matched, best_len = None, -1
        if n:
            for key, syns in SCT_COLUMNS:
                if key in used:
                    continue
                for syn in syns:
                    if _header_synonym_matches(n, key, syn) and len(syn) > best_len:
                        best_len, matched = len(syn), key
        if matched is not None:
            used.add(matched)
        keys.append(matched)
    if not all(k in used for k in _REQUIRED_FOR_MATCH):
        return None
    return keys


def _header_synonym_matches(normalized: str, key: str, synonym: str) -> bool:
    if normalized == synonym:
        return True
    if not normalized.startswith(synonym):
        return False
    suffix = normalized[len(synonym):].strip()
    if not suffix:
        return True
    if key == "total" and re.search(r"[a-z]", suffix):
        return False
    return True


def _find_header(table):
    """The best SCT header row in a table -> (row_index, matched_count, keys). Scans the first rows (a table
    may open with empty or spanner-title rows). None if no row qualifies."""
    best = None
    for ri in range(min(len(table), 12)):
        keys = _match_header(_clean_cells(table[ri]))
        if keys is None:
            continue
        matched = sum(1 for k in keys if k)
        if best is None or matched > best[1]:
            best = (ri, matched, keys)
    return best


def _sct_candidates(tables):
    """All tables whose header carries the required SCT anchors, best-scoring first. Scoring is (matched
    columns, data rows) — but selection between near-ties is decided by extract_sct via reconciliation, and
    genuine ambiguity fails closed there rather than silently picking one."""
    cands = []
    for t in tables:
        hdr = _find_header(t)
        if hdr is None:
            continue
        ri, matched, keys = hdr
        cands.append(((matched, len(t) - ri), t, ri, keys))
    cands.sort(key=lambda c: c[0], reverse=True)
    return cands


def find_sct(tables):
    """Pick the table that best matches the SCT schema -> (table, header_row_index, keys). None if no table
    has the required anchor columns. Fail-closed: an arbitrary table is never the SCT."""
    cands = _sct_candidates(tables)
    if not cands:
        return None
    _, t, ri, keys = cands[0]
    return t, ri, keys


# ------------------------------------------------------------------ money parsing
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_TRAILING_MARKERS_RE = re.compile(r"(?:[\s,*†‡]+|\(\d{1,3}\)|\[\d{1,3}\]|(?<!\d)\d{1,2}(?!\d))+")


def parse_money(cell: str):
    """(value, flag) for one SCT money cell. Handles $, commas, parentheses-as-negative, footnote markers,
    and blank/dash as None. flag is 'ok' | 'empty' | 'unparseable'. Conservative — an ambiguous cell is
    'unparseable' (lowers confidence), never a guessed number."""
    if cell is None:
        return None, "empty"
    s = str(cell).strip()
    if s in ("", "-", "–", "—", "n/a", "N/A", "——") or s in _CURRENCY_ONLY:
        return None, "empty"
    neg = s.startswith("(") and s.endswith(")")
    body = (s[1:-1] if neg else s).replace("$", "").strip()
    m = _NUM_RE.match(body)
    if not m:
        return None, "unparseable"
    try:
        val = float(m.group(0).replace(",", ""))
    except ValueError:
        return None, "unparseable"
    rest = body[m.end():].strip()               # trailing footnote markers ok; real prose is distrusted
    if rest and not _TRAILING_MARKERS_RE.fullmatch(rest):
        return None, "unparseable"
    return (-val if neg else val), "ok"


# ------------------------------------------------------------------ row extraction + reconciliation
def _new_row(name, year):
    return {"name": name, "year": year, "values": {}, "raw": {}, "cell_flags": {}, "alignment": "full",
            "suspect": None}


def _extract_rows(table, header_ri, keys):
    ncols = len(keys)
    name_first = keys[0] == "name"
    rows_out = []
    cell_ok = cell_total = unaligned = partial = suspicious = 0
    carried = ""
    last_year = {}                                                  # per carried-name: the previous (higher) year
    for ri in range(header_ri + 1, len(table)):
        cleaned = _clean_cells(table[ri])
        if not cleaned:
            continue
        if not _YEAR_RE.search(" ".join(cleaned)):                  # not a data row (no fiscal year at all)
            continue
        first_is_year = bool(re.fullmatch(r"(?:19|20)\d{2}", cleaned[0]))
        suspect = None

        if len(cleaned) == ncols:                                   # full row: name present, exact width
            aligned = dict(zip(keys, cleaned))
            if aligned.get("name"):
                carried = aligned["name"]
            elif carried:                                           # full-width row with an EMPTY name cell (e.g.
                aligned["name"] = carried                           # a nested table ate the name) — carry, but flag
                suspect = "name cell empty — carried from the row above"
            else:
                suspect = "name cell empty and no prior name to carry"
        elif name_first and first_is_year and len(cleaned) == ncols - 1 and carried:
            # continuation year row: name dropped. Guard against interleave/misattribution — the year MUST be
            # strictly below this officer's previous row year, or the forward-fill is not trustworthy.
            aligned = {"name": carried}
            aligned.update(zip(keys[1:], cleaned))
            yr_c = _YEAR_RE.search(str(aligned.get("year", "")))
            if yr_c and carried in last_year and not (int(yr_c.group(0)) < last_year[carried]):
                suspect = "continuation-row year not below the carried officer's prior year (possible misattribution)"
        else:
            # a short row means an interior money column was truly BLANK (dropped as a spacer). Don't drop the
            # NEO-year: recover it total-anchored — last number = Total, the rest are components — and accept
            # ONLY if they reconcile. A blank column can't be disambiguated, so per-column attribution is left
            # 'partial' (honest) rather than guessed. A non-reconciling short row is a genuine misalignment.
            rec = _partial_row(cleaned, keys, first_is_year, name_first, carried)
            if rec is None:
                unaligned += 1
                continue
            if rec["name"] and not first_is_year:
                carried = rec["name"]
            partial += 1
            cell_total += 1
            cell_ok += 1                                            # the Total cell parsed (reconciliation held)
            rows_out.append(rec)
            continue

        if not _YEAR_RE.search(str(aligned.get("year", ""))):       # header-repeat / non-data row
            continue
        year = int(_YEAR_RE.search(str(aligned["year"])).group(0))
        rec = _new_row(aligned.get("name", ""), year)
        for key in _MONEY_KEYS + ("total",):
            if key not in keys:
                continue
            raw = aligned.get(key, "")
            val, flag = parse_money(raw)
            rec["values"][key] = val
            rec["raw"][key] = raw
            rec["cell_flags"][key] = flag
            cell_total += 1
            if flag in ("ok", "empty"):
                cell_ok += 1
        comp_sum = sum(v for k, v in rec["values"].items() if k in _MONEY_KEYS and isinstance(v, (int, float)))
        total = rec["values"].get("total")
        has_unparseable_component = any(rec["cell_flags"].get(k) == "unparseable" for k in _MONEY_KEYS)
        rec["component_sum"] = round(comp_sum, 2)
        if isinstance(total, (int, float)):
            if has_unparseable_component:
                rec["reconciled"] = False
                rec["reconcile_diff"] = None
            else:
                rec["reconciled"] = abs(comp_sum - total) <= _RECON_TOLERANCE
                rec["reconcile_diff"] = round(comp_sum - total, 2)
        else:
            rec["reconciled"] = None
            rec["reconcile_diff"] = None
        # plausibility: a NEGATIVE figure in a column that is never negative (only pension_nqdc legitimately
        # can be) means a mis-parse or a shifted/misattributed column — reconciliation alone would not catch it.
        neg = [k for k in _NONNEGATIVE_KEYS if isinstance(rec["values"].get(k), (int, float))
               and rec["values"][k] < 0]
        if neg:
            suspect = f"negative value in {', '.join(neg)} (implausible — likely a misaligned column)"
        if suspect:
            rec["suspect"] = suspect
            suspicious += 1
        if rec["name"]:
            last_year[rec["name"]] = year
        rows_out.append(rec)
    return rows_out, cell_ok, cell_total, unaligned, partial, suspicious


def _partial_row(cleaned, keys, first_is_year, name_first, carried):
    """Recover a short data row (an interior money column was blank) by anchoring on the Total (last cell).
    Returns a record ONLY if the components reconcile to the Total — otherwise None (a true misalignment).
    Per-column money values are left empty (a blank interior column is genuinely ambiguous); name/year/total
    and the reconciling component_sum are captured, with alignment='partial' and the raw components kept."""
    if first_is_year and name_first:
        name, year_cell, nums = carried, cleaned[0], cleaned[1:]
    elif name_first and len(cleaned) >= 3 and re.fullmatch(r"(?:19|20)\d{2}", cleaned[1]):
        name, year_cell, nums = cleaned[0], cleaned[1], cleaned[2:]
    else:
        return None
    if not str(name).strip():
        return None
    if len(nums) < 3:                                               # need at least a couple components + a total
        return None
    # bound the recovery: a full row has this many value cells (money + total). Only recover a row that is
    # short by AT MOST 2 columns — a wildly-short row that still sums is more likely a coincidence than a
    # blank-column row, so fail closed (it becomes an 'unaligned' miss, not a fabricated 'partial').
    n_value_cols = sum(1 for k in keys if k in _MONEY_KEYS or k == "total")
    if len(nums) < n_value_cols - 2:
        return None
    total_val, total_flag = parse_money(nums[-1])
    if total_flag != "ok":
        return None
    comps = [parse_money(x) for x in nums[:-1]]
    if any(f == "unparseable" for _, f in comps):
        return None
    # partial rows skip the per-column negative guard (columns are unattributed), so apply it here: a
    # negative Total or any negative component means a mis-parse/shift — fail closed rather than "recover" it.
    if total_val < 0 or any(v is not None and v < 0 for v, f in comps):
        return None
    comp_sum = sum(v for v, f in comps if f == "ok")
    if abs(comp_sum - total_val) > _RECON_TOLERANCE:               # the recovery is only trusted if it reconciles
        return None
    ym = _YEAR_RE.search(str(year_cell))
    if not ym:
        return None
    rec = _new_row(name, int(ym.group(0)))
    rec["alignment"] = "partial"
    rec["values"]["total"] = total_val
    rec["raw"]["total"] = nums[-1]
    rec["raw"]["_components"] = nums[:-1]                          # kept raw for human audit (unattributed)
    rec["component_sum"] = round(comp_sum, 2)
    rec["reconciled"] = True
    rec["reconcile_diff"] = round(comp_sum - total_val, 2)
    return rec


# ------------------------------------------------------------------ confidence
def _confidence(present_keys, rows, cell_ok, cell_total, unaligned, partial, suspicious):
    """Deterministic, EXPLAINABLE confidence: header completeness + clean-parse rate + reconciliation ->
    a band with reasons. Reconciliation is a GATE, not just a weighted term — it is NECESSARY but not
    sufficient, so a suspicious/unreconciled/total-less table cannot reach the top band. Fail-loud: every
    gap SHOWS."""
    reasons = []
    have = set(present_keys)
    header_ratio = sum(1 for k in _CORE_KEYS if k in have) / len(_CORE_KEYS)
    missing_core = [k for k in _CORE_KEYS if k not in have]
    if "total" not in have:
        reasons.append("no Total column — rows cannot be reconciled")
    if missing_core:
        reasons.append("core columns not matched: " + ", ".join(missing_core))

    parse_ratio = (cell_ok / cell_total) if cell_total else 0.0
    if cell_total and cell_ok < cell_total:
        reasons.append(f"{cell_total - cell_ok} of {cell_total} money cells did not parse cleanly")

    recon_rows = [r for r in rows if r["reconciled"] is not None]
    recon_ok = sum(1 for r in recon_rows if r["reconciled"])
    recon_ratio = (recon_ok / len(recon_rows)) if recon_rows else 0.0
    missing_total = [r for r in rows if r["reconciled"] is None]
    if recon_rows and recon_ok < len(recon_rows):
        reasons.append(f"{len(recon_rows) - recon_ok} of {len(recon_rows)} rows did not reconcile "
                       "(components != Total)")
    if missing_total:
        reasons.append(f"{len(missing_total)} data row(s) had no Total to reconcile against")
    if suspicious:
        reasons.append(f"{suspicious} row(s) reconciled but look implausible (negative/misaligned/empty-name) "
                       "— the numbers may be in the wrong columns")
    if partial:
        reasons.append(f"{partial} row(s) reconciled on the Total but a blank column left the per-column "
                       "split unattributed")
    if unaligned:
        reasons.append(f"{unaligned} data row(s) did not align to the header and were skipped")
    if not rows:
        reasons.append("no data rows with a fiscal year were found under the header")

    score = round(0.35 * header_ratio + 0.30 * parse_ratio + 0.30 * recon_ratio
                  + 0.05 * (1.0 if not (unaligned or partial or suspicious) else 0.0), 3)
    clean = (not unaligned and not partial and not suspicious and not missing_total)
    if (rows and recon_rows and recon_ratio >= 0.999 and parse_ratio >= 0.999
            and header_ratio >= 0.999 and clean):
        band = "high"          # every column present, every cell parsed, every data row present-and-reconciled
    elif rows and recon_rows and recon_ratio >= 0.5 and score >= 0.7 and not suspicious:
        band = "medium"        # reconciliation is a GATE: <50% reconciled, or any suspicious row, cannot be medium
    else:
        band = "low"
    return band, score, reasons


# a scale caption — the phrases that actually appear ABOVE a table; must not match '000' inside a number
# like $4,200,000, so the bare-000 forms require surrounding parentheses.
_SCALE_RE = re.compile(r"in thousands|in millions|\(\s*\$?\s*000s?\s*\)|\$\s*in\s+thousands", re.I)


def _table_is_scaled(table):
    """Does the table itself caption a scale ('in thousands' / 'in millions')? The extractor does NOT unit-adjust,
    so if the SCT is stated in thousands every figure is 1000x low yet still self-reconciles — a caption near
    the table must therefore cap confidence. (Cross-document captions are not detected — an honest limit.)"""
    head = " ".join(" ".join(r) for r in table[:8])
    return bool(_SCALE_RE.search(head))


def _reconciles(hit):
    rows = _extract_rows(hit[1], hit[2], hit[3])[0]
    return any(r["reconciled"] for r in rows)


def extract_sct(doc: str) -> dict:
    """Extract the SCT from a proxy HTML document. Returns:
        {found, confidence: 'high'|'medium'|'low'|'none', score, columns, rows, reconciliation, reasons,
         n_rows}. Fail-closed: no matching table (or an ambiguous multiple) -> found=False / confidence='none'
    with a reason. Never fabricates a table or a Total."""
    empty = {"found": False, "confidence": "none", "score": 0.0, "rows": [], "columns": [],
             "reconciliation": {"rows": 0, "reconciled": 0, "failed": 0}, "n_rows": 0}
    if not doc or not str(doc).strip():
        return {**empty, "reasons": ["empty document"]}
    cands = _sct_candidates(visible_tables(doc))       # visible tables only — a hidden decoy is never selected
    if not cands:
        return {**empty, "reasons": ["no table with the Summary Compensation Table columns "
                                     "(name / year / salary / total) was found"]}
    # Selection is reconciliation-first, not score-first: a higher-scoring non-reconciling DECOY must not hide
    # a real, reconciling SCT lower in the list. So — if exactly one candidate reconciles, take it; if two or
    # more reconcile it is genuinely ambiguous (fail closed); if none reconcile, fall back to the best-scoring
    # candidate and let the confidence band come out low.
    reconciling = [c for c in cands if _reconciles((None, c[1], c[2], c[3]))]
    if len(reconciling) >= 2:
        return {**empty, "reasons": [f"ambiguous: {len(reconciling)} distinct tables match the SCT schema and "
                                     "reconcile — cannot disambiguate; extract from a specific document (--file)"]}
    _, table, header_ri, keys = reconciling[0] if len(reconciling) == 1 else cands[0]
    rows, cell_ok, cell_total, unaligned, partial, suspicious = _extract_rows(table, header_ri, keys)
    present = [k for k in _COL_KEYS if k in set(keys)]
    band, score, reasons = _confidence(present, rows, cell_ok, cell_total, unaligned, partial, suspicious)
    if _table_is_scaled(table):
        reasons.append("a scale caption ('in thousands'/'in millions') is present near the table and the "
                       "extractor does not unit-adjust — magnitudes may be understated; treat as ballpark")
        if band == "high":
            band = "medium"
    recon_rows = [r for r in rows if r["reconciled"] is not None]
    recon_ok = sum(1 for r in recon_rows if r["reconciled"])
    return {"found": True, "confidence": band, "score": score, "columns": present,
            "rows": rows, "n_rows": len(rows),
            "reconciliation": {"rows": len(recon_rows), "reconciled": recon_ok,
                               "failed": len(recon_rows) - recon_ok,
                               "partial": partial, "unaligned": unaligned, "suspicious": suspicious},
            "reasons": reasons}


# ------------------------------------------------------------------ a built-in synthetic SCT (offline demo/test)
DEMO_HTML = """
<html><body>
<h2>Summary&nbsp;Compensation Table</h2>
<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary ($)</th><th>Bonus ($)</th>
      <th>Stock Awards ($)</th><th>Option Awards ($)</th>
      <th>Non-Equity Incentive Plan Compensation ($)</th>
      <th>All Other Compensation ($)</th><th>Total ($)</th></tr>
  <tr><td>Dana Fielding<sup>(1)</sup>, Chief Executive Officer</td><td>2025</td><td>$650,000</td><td>$0</td>
      <td>$4,200,000</td><td>$0</td><td>$975,000</td><td>$12,500</td><td>$5,837,500</td></tr>
  <tr><td></td><td>2024</td><td>$625,000</td><td>$0</td><td>$3,800,000</td><td>$0</td>
      <td>$900,000</td><td>$11,000</td><td>$5,336,000</td></tr>
  <tr><td>Morgan Ellison, Chief Financial Officer</td><td>2025</td><td>$480,000</td><td>$0</td>
      <td>$2,100,000</td><td>$0</td><td>$540,000</td><td>$9,800</td><td>$3,129,800</td></tr>
</table>
</body></html>
"""


# ------------------------------------------------------------------ rendering
def _fmt(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def render_text(result: dict) -> str:
    out = []
    if not result["found"]:
        out.append("No Summary Compensation Table found.")
        out.append("  reason: " + "; ".join(result["reasons"]))
        return "\n".join(out)
    r = result["reconciliation"]
    out.append(f"Summary Compensation Table — confidence: {result['confidence'].upper()} "
               f"(score {result['score']:.2f})")
    out.append(f"  columns: {', '.join(result['columns'])}")
    out.append(f"  rows: {result['n_rows']}   reconciled: {r['reconciled']}/{r['rows']}"
               + (f"   FAILED: {r['failed']}" if r["failed"] else ""))
    if result["reasons"]:
        out.append("  notes: " + "; ".join(result["reasons"]))
    out.append("")
    for row in result["rows"]:
        if row["reconciled"] is False:
            flag = "  <-- does not reconcile"
        elif row.get("suspect"):
            flag = f"  <-- SUSPECT: {row['suspect']}"
        elif row.get("alignment") == "partial":
            flag = "  (totals only — a blank column left the split unattributed)"
        else:
            flag = ""
        out.append(f"  {row['name'][:44]:44s} {row['year']}  total={_fmt(row['values'].get('total'))}  "
                   f"Σcomponents={_fmt(row['component_sum'])}{flag}")
    return "\n".join(out)


# ------------------------------------------------------------------ CLI
def _fetch_proxy_html(ticker: str):
    """Resolve the ticker's latest DEF 14A via sec-edgar and fetch its HTML. Imported lazily so --demo/--file
    work with zero dependency on the sibling skill or the network."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    edgar_dir = os.path.normpath(os.path.join(here, "..", "..", "sec-edgar", "scripts"))
    if edgar_dir not in sys.path:
        sys.path.insert(0, edgar_dir)
    try:
        import edgar
    except ImportError as e:                                # pragma: no cover
        raise ExtractError("the sec-edgar foundation skill is required to fetch by ticker "
                           "(expected at ../../sec-edgar/scripts)") from e
    # convert sec-edgar's own errors (missing SEC_UA, ticker not found, HTTP/timeout) into a clean ExtractError
    # so the CLI never leaks a traceback — including in --json mode.
    try:
        info = edgar.def14a(ticker)
        if not info.get("url"):
            raise ExtractError(f"no proxy/annual filing URL found for {ticker!r} ({info.get('disclosure')})")
        if info.get("disclosure") != "def14a":
            sys.stderr.write(f"note: {ticker} has no US DEF 14A ({info.get('disclosure')}); "
                             f"extracting from {info.get('form')} — SCT columns may differ.\n")
        return edgar.fetch_document(info["url"]), info
    except edgar.EdgarError as e:
        raise ExtractError(str(e)) from e


def _main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("-")]

    if "--demo" in argv:
        doc, info = DEMO_HTML, {"company": "Synthetic Demo Corp", "ticker": "DEMO"}
    elif "--file" in argv:
        i = argv.index("--file")
        path = argv[i + 1] if i + 1 < len(argv) else None
        if not path or path.startswith("-"):                    # missing value or another flag -> clean error
            print("extractor: --file needs a path (e.g. --file proxy.html)", file=sys.stderr)
            return 2
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                doc = fh.read()
        except OSError as e:
            print(f"extractor: cannot read {path!r}: {e}", file=sys.stderr)
            return 2
        info = {"company": path, "ticker": "(file)"}
    elif args:
        try:
            doc, info = _fetch_proxy_html(args[0])
        except ExtractError as e:                           # missing SEC_UA / ticker not found / HTTP — clean, no traceback
            if as_json:
                import json
                print(json.dumps({"found": False, "confidence": "none", "error": str(e)}, indent=2))
            else:
                print(f"extractor: {e}", file=sys.stderr)
            return 2
    else:
        print(__doc__)
        return 0

    result = extract_sct(doc)
    result["source"] = {"company": info.get("company"), "ticker": info.get("ticker"),
                        "form": info.get("form"), "url": info.get("url")}
    if as_json:
        import json
        print(json.dumps(result, indent=2))
    else:
        hdr = info.get("company") or info.get("ticker") or ""
        if hdr:
            print(hdr + (f" — {info.get('form')} {info.get('date', '')}" if info.get("form") else ""))
        print(render_text(result))
    return 0 if result["found"] else 3         # 0 found (any confidence), 3 not found (fail-closed, not a crash)


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except ExtractError as e:
        print(f"extractor: {e}", file=sys.stderr)
        sys.exit(2)
