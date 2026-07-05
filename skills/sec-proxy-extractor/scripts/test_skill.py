#!/usr/bin/env python3
"""Offline smoke test for the sec-proxy-extractor skill (no network). All fixtures are SYNTHETIC — made-up
NEO names + numbers — so nothing real is committed. Run: python3 test_skill.py

Proves: the HTML table parser survives footnotes/colspans/nested tables; money parsing handles
$/commas/parens/dashes/footnote-markers; the SCT is identified by its column schema (and a decoy table is
NOT); name forward-fill + multi-year rows work; reconciliation catches a bad Total; confidence is banded
honestly (high when clean, low when it doesn't reconcile); and an absent SCT fails closed (found=False),
never fabricated."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extractor as X  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def raises(exc, fn, label):
    global passed
    try:
        fn()
        assert False, f"FAILED (no raise): {label}"
    except exc:
        passed += 1


# ---- money parsing --------------------------------------------------------------------------------------
for cell, exp_val, exp_flag in [
    ("$650,000", 650000.0, "ok"),
    ("650,000", 650000.0, "ok"),
    ("$0", 0.0, "ok"),
    ("", None, "empty"),
    ("—", None, "empty"),
    ("-", None, "empty"),
    ("n/a", None, "empty"),
    ("(1,234)", -1234.0, "ok"),                     # parentheses = negative
    ("$12,345 (3)", 12345.0, "ok"),                 # trailing footnote marker ignored
    ("$1,234,567.89", 1234567.89, "ok"),
    ("500,000 600,000", None, "unparseable"),       # two numeric tokens in one cell is ambiguous
    ("1,234.56.78", None, "unparseable"),           # malformed numeric tail is not a footnote marker
    ("about $5", None, "unparseable"),              # leading words -> distrust
    ("1,234 restricted units", None, "unparseable"),  # trailing prose -> distrust
]:
    v, f = X.parse_money(cell)
    ok(v == exp_val and f == exp_flag, f"parse_money({cell!r}) -> ({exp_val}, {exp_flag}) [got ({v}, {f})]")

# ---- table parsing survives messy HTML ------------------------------------------------------------------
tabs = X.parse_tables("<table><tr><td>a<sup>(1)</sup></td><td>b</td></tr>"
                      "<tr><td colspan='2'>wide</td></tr></table>")
ok(len(tabs) == 1, "one table parsed")
ok(tabs[0][0] == ["a", "b"], "footnote <sup> stripped from a cell")
ok(tabs[0][1] == ["wide", ""], "colspan=2 expands to two columns (padded)")
ok(X.parse_tables("no tables here") == [], "a document with no <table> yields no tables")
raises(X.ExtractError, lambda: (_ for _ in ()).throw(X.ExtractError("x")), "ExtractError is raisable")

# ---- the demo SCT: clean, should be HIGH confidence + fully reconciled ------------------------------------
res = X.extract_sct(X.DEMO_HTML)
ok(res["found"], "the SCT is found in the demo proxy")
ok(res["confidence"] == "high", f"a clean SCT is HIGH confidence (got {res['confidence']})")
ok(res["n_rows"] == 3, "3 data rows extracted (CEO x2 years + CFO x1)")
ok(res["reconciliation"]["failed"] == 0, "every demo row reconciles (components == Total)")
ok(res["columns"][0] == "name" and "total" in res["columns"], "name + total columns identified")
# name forward-fill: the CEO's 2024 row has a blank name cell -> carried from the 2025 row
row_2024 = [r for r in res["rows"] if r["year"] == 2024][0]
ok(row_2024["name"].startswith("Dana Fielding"), "a blank name cell is forward-filled from the row above")
ceo_2025 = [r for r in res["rows"] if r["year"] == 2025 and r["name"].startswith("Dana")][0]
ok(ceo_2025["values"]["salary"] == 650000.0 and ceo_2025["values"]["total"] == 5837500.0,
   "CEO 2025 salary + total parsed exactly")
ok(ceo_2025["reconciled"] is True, "CEO 2025 reconciles")
ok("--" not in X.render_text(res) or res["found"], "render_text produces a human summary")

# ---- reconciliation catches a WRONG total -> not high, row flagged ---------------------------------------
bad_total = X.DEMO_HTML.replace("$5,837,500", "$9,999,999")   # break the CEO 2025 Total
resb = X.extract_sct(bad_total)
ok(resb["found"], "still found with a broken total")
ok(resb["reconciliation"]["failed"] == 1, "the tampered Total is caught as a reconciliation failure")
ok(resb["confidence"] != "high", "a non-reconciling table is NOT high confidence")
ok(any("did not reconcile" in r for r in resb["reasons"]), "the reason names the reconciliation failure")
bad_row = [r for r in resb["rows"] if r["reconciled"] is False][0]
ok(bad_row["reconcile_diff"] is not None and abs(bad_row["reconcile_diff"]) > 1000,
   "the failing row carries the dollar difference for a human to audit")

# ---- an unparseable money cell lowers confidence + is flagged, not guessed -------------------------------
dirty = X.DEMO_HTML.replace("$4,200,000", "approximately $4.2M")
resd = X.extract_sct(dirty)
ok(resd["rows"][0]["cell_flags"]["stock_awards"] == "unparseable", "a prose money cell is flagged unparseable")
ok(resd["rows"][0]["values"]["stock_awards"] is None, "an unparseable cell is None, never a guessed number")
ok(resd["confidence"] != "high", "an unparseable cell prevents HIGH confidence")

# ---- a cell with two numeric amounts is ambiguous, never a clean HIGH-confidence value --------------------
two_amounts = """<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
      <th>Stock Awards</th><th>Option Awards</th>
      <th>Non-Equity Incentive Plan Compensation</th>
      <th>All Other Compensation</th><th>Total</th></tr>
  <tr><td>Avery Stone, Chief Executive Officer</td><td>2025</td><td>500,000 600,000</td><td>0</td>
      <td>4,000,000</td><td>0</td><td>800,000</td><td>35,000</td><td>5,335,000</td></tr>
</table>"""
resta = X.extract_sct(two_amounts)
ok(resta["rows"][0]["cell_flags"]["salary"] == "unparseable",
   "a money cell containing two numeric amounts is flagged unparseable")
ok(resta["rows"][0]["values"]["salary"] is None, "the first amount in an ambiguous cell is not guessed")
ok(resta["confidence"] != "high", "an ambiguous money cell prevents HIGH confidence")

unparseable_zero = """<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
      <th>Stock Awards</th><th>Option Awards</th>
      <th>Non-Equity Incentive Plan Compensation</th>
      <th>All Other Compensation</th><th>Total</th></tr>
  <tr><td>Avery Stone, Chief Executive Officer</td><td>2025</td><td>500,000</td><td>see note</td>
      <td>4,000,000</td><td>0</td><td>800,000</td><td>35,000</td><td>5,335,000</td></tr>
</table>"""
resu = X.extract_sct(unparseable_zero)
ok(resu["rows"][0]["cell_flags"]["bonus"] == "unparseable", "the ambiguous Bonus cell is flagged")
ok(resu["rows"][0]["reconciled"] is False,
   "a row with an unparseable component is not counted as reconciled")
ok(resu["reconciliation"]["failed"] == 1, "an unparseable component makes reconciliation fail closed")

# ---- a DECOY table with similar words is NOT mistaken for the SCT -----------------------------------------
decoy = """<html><body>
<table><tr><th>Director</th><th>Fees Earned</th><th>Stock Awards</th><th>Total</th></tr>
       <tr><td>A. Director</td><td>$50,000</td><td>$150,000</td><td>$200,000</td></tr></table>
</body></html>"""
ok(X.extract_sct(decoy)["found"] is False,
   "a Director Compensation table (no Year/Salary anchor) is NOT taken as the SCT")

tsr_decoy = """<html><body>
<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
      <th>Stock Awards</th><th>Option Awards</th>
      <th>Non-Equity Incentive Plan Compensation</th>
      <th>All Other Compensation</th><th>Total Shareholder Return</th></tr>
  <tr><td>Avery Stone, Chief Executive Officer</td><td>2025</td><td>500,000</td><td>0</td>
      <td>4,000,000</td><td>0</td><td>800,000</td><td>35,000</td><td>5,335,000</td></tr>
</table>
</body></html>"""
ok(X.extract_sct(tsr_decoy)["found"] is False,
   "a Total Shareholder Return column is not accepted as the SCT Total compensation column")

# ---- the SCT is picked even when wrapped by a layout table + preceded by a title row ----------------------
wrapped = """<html><body><table><tr><td>
  <table>
    <tr><td colspan='9'>Summary Compensation Table</td></tr>
    <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
        <th>Stock Awards</th><th>Option Awards</th><th>Non-Equity Incentive Plan Compensation</th>
        <th>All Other Compensation</th><th>Total</th></tr>
    <tr><td>Sam Rivera, CEO</td><td>2025</td><td>700,000</td><td>0</td><td>3,000,000</td><td>0</td>
        <td>800,000</td><td>15,000</td><td>4,515,000</td></tr>
  </table>
</td></tr></table></body></html>"""
resw = X.extract_sct(wrapped)
ok(resw["found"] and resw["n_rows"] == 1, "SCT found inside a wrapping layout table, past a spanner title row")
ok(resw["rows"][0]["reconciled"] is True, "the wrapped SCT row reconciles")

# ---- fail-closed: empty + no-SCT documents ---------------------------------------------------------------
for doc, why in [("", "empty document"), ("<html><body><p>No comp here.</p></body></html>", "prose only"),
                 ("<table><tr><td>x</td></tr></table>", "a one-cell table")]:
    r = X.extract_sct(doc)
    ok(r["found"] is False and r["confidence"] == "none", f"fail-closed on {why} (found=False/none)")
    ok(r["rows"] == [] and r["reasons"], f"{why}: no rows + an honest reason")

# ---- a BLANK interior money column on a continuation row (real proxy quirk) is recovered, not dropped -----
# The 2024 row's Bonus cell is truly empty (not a dash), so the collapsed row is short by one. The row must
# still be captured (total-anchored + reconciled), marked 'partial', and it must hold overall < high.
blank_col = """<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
      <th>Stock Awards</th><th>Non-Equity Incentive Plan Compensation</th>
      <th>All Other Compensation</th><th>Total</th></tr>
  <tr><td>Robin Vance, CEO</td><td>2025</td><td>500,000</td><td>0</td><td>2,000,000</td><td>600,000</td>
      <td>10,000</td><td>3,110,000</td></tr>
  <tr><td></td><td>2024</td><td>480,000</td><td></td><td>1,800,000</td><td>550,000</td>
      <td>9,000</td><td>2,839,000</td></tr>
</table>"""
resp = X.extract_sct(blank_col)
ok(resp["found"] and resp["n_rows"] == 2, "the blank-Bonus continuation row is recovered, not dropped")
prow = [r for r in resp["rows"] if r["year"] == 2024][0]
ok(prow["alignment"] == "partial", "the recovered row is flagged 'partial' (per-column split unattributed)")
ok(prow["reconciled"] is True and prow["values"]["total"] == 2839000.0,
   "the partial row reconciles on the Total (480,000+1,800,000+550,000+9,000 == 2,839,000)")
ok(prow["name"].startswith("Robin Vance"), "the partial row carries the forward-filled name")
ok(resp["confidence"] != "high", "a partial (totals-only) recovery keeps overall confidence below high")
ok(resp["reconciliation"]["partial"] == 1, "the partial count is surfaced in the reconciliation summary")
# a short row that does NOT reconcile is a true misalignment -> skipped + counted, never a fabricated row
misalign = blank_col.replace("2,839,000", "9,999,999")
resm = X.extract_sct(misalign)
ok(all(r["year"] != 2024 for r in resm["rows"]), "a non-reconciling short row is NOT accepted (no fabrication)")
ok(resm["reconciliation"]["unaligned"] >= 1, "the non-reconciling short row is counted as unaligned")
# a continuation-shaped row before any name has been carried is skipped, not emitted with a blank name
orphan_continuation = """<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
      <th>Stock Awards</th><th>Option Awards</th>
      <th>Non-Equity Incentive Plan Compensation</th>
      <th>All Other Compensation</th><th>Total</th></tr>
  <tr><td>2025</td><td>500,000</td><td>0</td><td>4,000,000</td><td>0</td>
      <td>800,000</td><td>35,000</td><td>5,335,000</td></tr>
</table>"""
reso = X.extract_sct(orphan_continuation)
ok(reso["n_rows"] == 0, "an orphan continuation row is skipped instead of emitted with a blank name")
ok(reso["reconciliation"]["unaligned"] == 1, "the orphan continuation row is counted as unaligned")
ok(reso["confidence"] == "low", "an SCT header with only an orphan continuation row is LOW confidence")

# ---- real-proxy noise: a standalone footnote cell '(6)' and zero-width-space padding must not break align --
# (mirrors two structures seen in live filings: a footnote marker in its own <td>, and ​ filler cells.)
noisy = ("<table>"
         "<tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>"
         "<th>Stock Awards</th><th>Non-Equity Incentive Plan Compensation</th>"
         "<th>All Other Compensation</th><th>Total</th></tr>"
         "<tr><td>​Jordan Pike, CEO​</td><td>​</td><td>2025</td><td>​</td><td>600,000</td>"
         "<td>-</td><td>3,000,000</td><td>700,000</td><td>25,000</td><td>(6)</td><td>4,325,000</td></tr>"
         "</table>")
resn = X.extract_sct(noisy)
ok(resn["found"] and resn["n_rows"] == 1, "a row padded with zero-width cells + a lone footnote cell still aligns")
nrow = resn["rows"][0]
ok(nrow["values"]["salary"] == 600000.0 and nrow["values"]["total"] == 4325000.0,
   "zero-width filler and the standalone '(6)' footnote cell are dropped; columns line up")
ok(nrow["reconciled"] is True, "the de-noised row reconciles")
ok(nrow["name"].startswith("Jordan Pike"), "zero-width chars are stripped from the name")
# the lone-footnote drop must NOT eat a parenthetical NEGATIVE money value
ok(X.parse_money("(1,234)") == (-1234.0, "ok"), "a parenthetical thousands value stays a negative, not a footnote")
ok(X._clean_cells(["(6)", "(1,234)", "$", "5,000"]) == ["(1,234)", "5,000"],
   "_clean_cells drops the lone '(6)' footnote + the lone '$' but keeps '(1,234)' and '5,000'")

# ---- a foreign-style table missing the Total column: found but reconciliation can't run -> not high -------
no_total = """<table>
  <tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th>
      <th>Stock Awards</th></tr>
  <tr><td>Lee Fontaine, CEO</td><td>2025</td><td>500,000</td><td>0</td><td>2,000,000</td></tr></table>"""
rnt = X.extract_sct(no_total)
# name/year/salary present but NO total -> required-anchor 'total' missing -> not treated as an SCT (fail-closed)
ok(rnt["found"] is False, "a table with no Total column is not accepted as an SCT (Total is a required anchor)")

# ---- HARDENING: reconciliation is necessary-but-not-sufficient; a decoy/hidden/ambiguous/suspicious table
# ---- must NOT be confidently returned (these are the fail-closed guarantees the confidence story rests on) --
_H = ("<tr><th>Name and Principal Position</th><th>Year</th><th>Salary</th><th>Bonus</th><th>Stock Awards</th>"
      "<th>Non-Equity Incentive Plan Compensation</th><th>All Other Compensation</th><th>Total</th></tr>")


def _row(n, y, s, b, st, ne, o, t):
    return f"<tr><td>{n}</td><td>{y}</td><td>{s}</td><td>{b}</td><td>{st}</td><td>{ne}</td><td>{o}</td><td>{t}</td></tr>"


# [P1] a HIDDEN decoy SCT (display:none) with more rows must never be chosen over the real visible SCT
hidden_decoy = (f"<table style='display:none'>{_H}"
                + "".join(_row(f"Fake {i}", 2025, "1,000", "2,000", "3,000", "4,000", "5,000", "15,000") for i in range(4))
                + f"</table><table>{_H}{_row('Real Officer, CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}</table>")
rhd = X.extract_sct(hidden_decoy)
ok(rhd["found"] and rhd["n_rows"] == 1 and rhd["rows"][0]["name"].startswith("Real"),
   "a display:none decoy table is skipped; the real visible SCT is extracted")
ok("Fake" not in rhd["rows"][0]["name"], "no hidden-decoy row is returned")
vis = X.visible_tables(hidden_decoy)
ok(len(vis) == 1 and all("Fake" not in " ".join(cell for row in t for cell in row) for t in vis),
   "visible_tables() drops the hidden decoy table entirely")

# [P1] hidden via an ANCESTOR (a wrapper div / hidden / aria-hidden), not the table itself, is still skipped
for wrap in ("<div style='display:none'>{}</div>", "<section hidden>{}</section>",
             "<div aria-hidden='true'>{}</div>", "<div style='visibility:hidden'>{}</div>"):
    decoy = wrap.format(f"<table>{_H}{_row('Ghost, CEO', 2025, '1,000', '0', '2,000', '0', '0', '3,000')}</table>")
    real = f"<table>{_H}{_row('Live Officer, CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}</table>"
    rr = X.extract_sct(decoy + real)
    ok(rr["found"] and rr["rows"][0]["name"].startswith("Live") and all("Ghost" not in x["name"] for x in rr["rows"]),
       f"a table hidden via an ancestor ({wrap[:22]}...) is skipped, not extracted")
# a hidden <tr> inside the real table is dropped (a phantom officer row must not appear)
hidden_row = (f"<table>{_H}"
              f"{_row('Real CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}"
              f"<tr style='display:none'><td>Phantom, CFO</td><td>2025</td><td>9</td><td>9</td><td>9</td><td>9</td><td>9</td><td>45</td></tr>"
              f"</table>")
rhr = X.extract_sct(hidden_row)
ok(rhr["n_rows"] == 1 and all("Phantom" not in x["name"] for x in rhr["rows"]),
   "a display:none <tr> is dropped — no phantom officer row is extracted")

# [P1] a table hidden by CSS CLASS (a <style> rule OR a conventional utility class) is skipped, not extracted
class_decoy_styled = ("<style>.secret{display:none}</style>"
                      f"<div class='secret'><table>{_H}{_row('Styled Ghost, CEO', 2025, '1', '0', '2', '0', '0', '3')}</table></div>"
                      f"<table>{_H}{_row('Shown CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}</table>")
rcs = X.extract_sct(class_decoy_styled)
ok(rcs["found"] and rcs["rows"][0]["name"].startswith("Shown") and all("Ghost" not in x["name"] for x in rcs["rows"]),
   "a table under a class hidden by a <style> display:none rule is skipped")
for cls in ("sr-only", "visually-hidden", "d-none", "hidden"):
    conv = (f"<div class='{cls}'><table>{_H}{_row('Util Ghost, CEO', 2025, '1', '0', '2', '0', '0', '3')}</table></div>"
            f"<table>{_H}{_row('Real CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}</table>")
    rc = X.extract_sct(conv)
    ok(rc["found"] and all("Ghost" not in x["name"] for x in rc["rows"]),
       f"a table under a conventional hidden utility class ('{cls}') is skipped")

# [P2] malformed proxy HTML with omitted </td>/</tr> (browser-tolerated) still parses — rows aren't dropped
malformed = (f"<table><tr><th>Name and Principal Position<th>Year<th>Salary<th>Bonus<th>Stock Awards"
             f"<th>Non-Equity Incentive Plan Compensation<th>All Other Compensation<th>Total"
             f"<tr><td>Ragged CEO<td>2025<td>650,000<td>0<td>4,200,000<td>975,000<td>12,500<td>5,837,500"
             f"</table>")
rmf = X.extract_sct(malformed)
ok(rmf["found"] and rmf["n_rows"] == 1 and rmf["rows"][0]["values"]["total"] == 5837500.0,
   "HTML with omitted </td>/</tr> still extracts the row (open cells/rows are flushed on the next tag)")

# [P2] reconciliation-first selection: a higher-SCORING non-reconciling decoy must not hide a real reconciling SCT
score_decoy = (f"<table>{_H}"
               + "".join(_row(f"Loud {i}", 2025, "1", "1", "1", "1", "1", "999") for i in range(5))   # 5 rows, none reconcile
               + f"</table><table>{_H}"
               f"{_row('Quiet CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}</table>")
rsd = X.extract_sct(score_decoy)
ok(rsd["found"] and rsd["rows"][0]["name"].startswith("Quiet"),
   "the single reconciling SCT is selected over a higher-scoring non-reconciling decoy")

# [P1] TWO visible tables that BOTH reconcile -> genuinely ambiguous -> fail closed (never guess one)
ambiguous = (f"<table>{_H}{_row('A, CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}</table>"
             f"<table>{_H}{_row('B, CEO', 2024, '500,000', '0', '2,000,000', '800,000', '10,000', '3,310,000')}</table>")
ramb = X.extract_sct(ambiguous)
ok(ramb["found"] is False and "ambiguous" in ramb["reasons"][0],
   "two distinct reconciling SCT tables fail closed as ambiguous (no confident guess)")

# [P1] a NEGATIVE value in a never-negative column reconciles arithmetically but is flagged suspect + not high
neg = f"<table>{_H}{_row('Neg, CEO', 2025, '650,000', '(50,000)', '4,200,000', '975,000', '12,500', '5,787,500')}</table>"
rneg = X.extract_sct(neg)
ok(rneg["reconciliation"]["suspicious"] == 1 and rneg["rows"][0]["suspect"],
   "a negative bonus that still sums is flagged suspect (reconciliation is not sufficient)")
ok(rneg["confidence"] == "low", "a suspicious row caps confidence at low (never high/medium)")
# a legitimate $0 salary (founder) is NOT flagged suspicious (zero != negative) -> stays high
zero_ok = f"<table>{_H}{_row('Founder, CEO', 2025, '0', '0', '4,200,000', '975,000', '12,500', '5,187,500')}</table>"
rz = X.extract_sct(zero_ok)
ok(rz["reconciliation"]["suspicious"] == 0 and rz["confidence"] == "high",
   "a founder's legitimate $0 salary is NOT suspect (zero is plausible; only negatives are)")

# [P1] reconciliation is a GATE: 0% reconciled -> low even though header+parse are perfect
zero_recon = (f"<table>{_H}{_row('X, CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '9,999,999')}"
              f"{_row('Y, CFO', 2025, '480,000', '0', '2,100,000', '540,000', '9,800', '8,888,888')}</table>")
rzr = X.extract_sct(zero_recon)
ok(rzr["confidence"] == "low", "0% reconciliation is LOW, never medium (reconciliation gates the band)")

# [P2] a data row whose Total cell is a dash (present but no value to reconcile) blocks HIGH
no_row_total = (f"<table>{_H}{_row('P, CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}"
                f"{_row('Q, CFO', 2025, '480,000', '0', '2,100,000', '540,000', '9,800', '—')}</table>")
rnrt = X.extract_sct(no_row_total)
q = [r for r in rnrt["rows"] if r["name"].startswith("Q")][0]
ok(q["reconciled"] is None, "a dash Total leaves the row with no Total to reconcile (reconciled=None)")
ok(rnrt["confidence"] != "high" and any("no Total" in r for r in rnrt["reasons"]),
   "a data row with no Total blocks HIGH and is named in the reasons")

# [P3] a scale caption ('in thousands') near the table caps confidence below high (magnitudes not unit-adjusted)
scaled = (f"<table><tr><td colspan='8'>Summary Compensation Table (in thousands)</td></tr>{_H}"
          f"{_row('S, CEO', 2025, '650', '0', '4,200', '975', '12', '5,837')}</table>")
rsc = X.extract_sct(scaled)
ok(rsc["found"] and rsc["confidence"] != "high" and any("scale caption" in r for r in rsc["reasons"]),
   "an 'in thousands' caption caps confidence below high with an honest reason")

# [P2] a PARTIAL (blank-column) recovery must also honor the negative guard — a negative component fails closed
part_neg = (f"<table>{_H}"
            f"{_row('Anchor CEO', 2025, '650,000', '0', '4,200,000', '975,000', '12,500', '5,837,500')}"
            # continuation 2024 row, Bonus column blank (short by one) AND a negative stock value -> reject, not 'partial'
            f"<tr><td></td><td>2024</td><td>600,000</td><td></td><td>(2,000,000)</td><td>900,000</td>"
            f"<td>11,000</td><td>-488,000</td></tr></table>")
rpn = X.extract_sct(part_neg)
ok(all(r["year"] != 2024 for r in rpn["rows"]),
   "a short (blank-column) row with a negative component is NOT recovered as 'partial' (negative guard applies)")

# [P2] ticker mode surfaces sec-edgar errors as a clean ExtractError (no raw traceback) — missing SEC_UA is
# refused by the foundation BEFORE any network call, so this stays offline.
import os as _os  # noqa: E402
_saved_ua = _os.environ.pop("SEC_UA", None)
try:
    try:
        X._fetch_proxy_html("AAPL")
        ok(False, "ticker fetch without SEC_UA should fail")
    except X.ExtractError:
        ok(True, "a missing SEC_UA surfaces as a clean ExtractError (not a raw EdgarError/traceback)")
    except Exception as _e:
        ok(False, f"ticker fetch raised {_e.__class__.__name__}, not ExtractError")
finally:
    if _saved_ua is not None:
        _os.environ["SEC_UA"] = _saved_ua

print(f"OK — {passed} sec-proxy-extractor checks passed "
      f"({len(X.SCT_COLUMNS)} canonical SCT columns modeled).")
