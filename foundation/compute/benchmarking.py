#!/usr/bin/env python3
"""Executive-compensation benchmarking — position a subject's NEOs against the peer group's REAL,
publicly-disclosed proxy pay.

The peer figures are actual SCT (Summary Compensation Table) amounts from each company's latest SEC DEF 14A
(or the SEC-furnished proxy circular for a foreign private issuer) — provenance in
governance/proxy-comp-data.md. Only the SUBJECT (Acme, ticker ACMQ) is synthetic. This module is pure,
stdlib-only, deterministic math: it loads the committed proxy_comp.csv, builds a per-role peer distribution
(one INCUMBENT per company), and reports where each subject NEO's pay ELEMENT sits vs that distribution,
against the committee's target-percentile policy. It never sets pay — it produces a positioning a
Compensation Committee reviews.

Honesty note: proxy SCT pay is ACTUAL/as-disclosed (equity at grant-date fair value), NOT target
opportunity. Positioning the subject vs peers' disclosed pay at each percentile is the standard proxy-read
(how Equilar/ISS use a proxy); the target-percentile bands below are the committee's policy targets.

    from foundation.compute.benchmarking import benchmark
    result = benchmark()          # positions the committed subject vs the peer proxy data
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "foundation" / "data" / "acme"
PROXY_PATH = DATA / "proxy_comp.csv"

# exact schema the proxy dataset must have (fail closed on drift)
REQUIRED_COLS = ("ticker", "company_name", "role_bucket", "title", "salary", "bonus", "stock_awards",
                 "option_awards", "non_equity_incentive", "other_comp", "total", "fiscal_year",
                 "disclosure", "is_subject")
_MONEY_COLS = ("salary", "bonus", "stock_awards", "option_awards", "non_equity_incentive", "other_comp", "total")
ROLES = ("CEO", "CFO", "COO", "CLO", "CHRO")     # the subject's benchmarked roles, in committee-report order
MIN_PEER_N = 6                                    # suppress a role with fewer than this many peer observations
_NON_INCUMBENT = ("former", "outgoing", "interim", "retired")   # title markers of a non-incumbent officer


class BenchmarkError(ValueError):
    """Raised when benchmarking inputs are structurally invalid (fail closed)."""


def _money(v, ctx):
    """A non-negative finite dollar amount. Rejects bool/str/None/NaN/negative — never coerces silently."""
    if isinstance(v, bool):
        raise BenchmarkError(f"{ctx}: a bool is not a dollar amount")
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"{ctx}: non-numeric amount {v!r}") from exc
    if not math.isfinite(f) or f < 0:
        raise BenchmarkError(f"{ctx}: amount must be finite and non-negative (got {v!r})")
    return f


# ---- pay ELEMENTS: SCT columns -> the committee's policy pay elements, each with its target-percentile band
def _base(r):        return r["salary"]
def _sti(r):         return r["bonus"] + r["non_equity_incentive"]                  # annual cash incentive
def _total_cash(r):  return r["salary"] + r["bonus"] + r["non_equity_incentive"]
def _ltie(r):        return r["stock_awards"] + r["option_awards"]                  # long-term (equity)
def _tdc(r):         return r["total"]                                              # total direct comp (SCT Total)

# (key, label, value-fn, target band lo, target band hi) — bands mirror the peer-builder's carried policy.
# Every element VALUE is ACTUAL/as-disclosed SCT pay (never a target opportunity), so labels say so and
# never read "target" — the target-percentile POLICY lives in the band, not in the pay figure. Conflating
# realized SCT cash with target STI opportunity is the first thing a compensation committee would object to.
ELEMENTS = (
    ("base",       "Base salary",           _base,       45, 55),
    ("sti",        "Annual cash incentive", _sti,        50, 60),
    ("total_cash", "Total cash (actual)",   _total_cash, 50, 60),
    ("ltie",       "LTI / equity",          _ltie,       50, 65),
    ("tdc",        "Total direct comp",     _tdc,        50, 65),
)


def load_proxy_comp(path: Path = PROXY_PATH):
    """Load + fail-closed-validate the proxy-comp dataset. Returns (peers, subject) lists of typed row dicts
    (money columns parsed to float). Raises BenchmarkError on any schema/type violation."""
    path = Path(path)
    if not path.exists():
        raise BenchmarkError(f"proxy comp dataset not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or set(reader.fieldnames) != set(REQUIRED_COLS):
            raise BenchmarkError(f"proxy_comp schema mismatch: expected {sorted(REQUIRED_COLS)}, "
                                 f"got {sorted(reader.fieldnames or [])}")
        peers, subject = [], []
        for i, r in enumerate(reader, start=2):
            for c in _MONEY_COLS:
                r[c] = _money(r[c], f"line {i} {c}")
            rb = (r.get("role_bucket") or "").strip()
            if not rb:
                raise BenchmarkError(f"line {i}: empty role_bucket")
            r["role_bucket"] = rb
            r["title"] = (r.get("title") or "").strip()
            # component sum must reconcile to the reported SCT Total (catches a units error / a dropped column)
            comp_sum = r["salary"] + r["bonus"] + r["stock_awards"] + r["option_awards"] + \
                r["non_equity_incentive"] + r["other_comp"]
            if r["total"] > 0 and abs(comp_sum - r["total"]) > max(2.0, 0.005 * r["total"]):
                raise BenchmarkError(f"line {i} ({r['ticker']}/{rb}): components ${comp_sum:,.0f} do not "
                                     f"reconcile to SCT Total ${r['total']:,.0f}")
            (subject if r.get("is_subject") == "yes" else peers).append(r)
    if not peers:
        raise BenchmarkError("proxy_comp has no peer rows")
    if not subject:
        raise BenchmarkError("proxy_comp has no subject (is_subject=yes) rows")
    return peers, subject


def _incumbents(rows):
    """One INCUMBENT peer observation per (ticker, role): in a CEO/CFO transition year the SCT lists an
    outgoing + incoming officer; keep the incumbent (a title without former/interim/outgoing/retired; if that
    is ambiguous, the higher Total, which is the full-year officer). Deterministic."""
    best = {}
    for r in rows:
        key = (r["ticker"], r["role_bucket"])
        cur = best.get(key)
        is_inc = not any(m in r["title"].lower() for m in _NON_INCUMBENT)
        if cur is None:
            best[key] = (r, is_inc)
            continue
        cur_r, cur_inc = cur
        # prefer an incumbent title; among equals, the higher Total (full-year officer); then a stable
        # tertiary key (title) so genuinely-tied duplicate rows resolve deterministically REGARDLESS of CSV
        # row order (never rely on first-seen / implicit list order for a tie).
        if (is_inc, r["total"], r["title"]) > (cur_inc, cur_r["total"], cur_r["title"]):
            best[key] = (r, is_inc)
    return [v[0] for v in best.values()]


def _sorted(values):
    """Validate + sort a distribution. Every value must be a finite non-negative amount (rejects bool/str/
    NaN/negative via _money) so a bad direct-call input fails closed instead of yielding a fabricated
    percentile. Empty raises BenchmarkError, never a downstream ZeroDivisionError."""
    vals = sorted(_money(v, "distribution value") for v in values)
    if not vals:
        raise BenchmarkError("empty distribution")
    return vals


def _quantile(sorted_vals, q):
    """Linear-interpolated quantile (q in [0,1]) of a pre-sorted non-empty list."""
    if not (0.0 <= q <= 1.0):
        raise BenchmarkError(f"quantile q must be in [0,1] (got {q})")
    n = len(sorted_vals)
    if n == 0:
        raise BenchmarkError("quantile of an empty distribution")
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    if lo + 1 >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + (sorted_vals[lo + 1] - sorted_vals[lo]) * (pos - lo)


def percentile_rank(sorted_vals, x):
    """The subject's percentile position in the peer distribution: 100 * (below + 0.5*equal) / n — the
    standard mid-rank ('your pay is at the Nth percentile of the market'). 0..100. Fail-closed: validates
    both x and every distribution value (rejects bool/str/NaN/negative), and rejects an empty distribution
    with BenchmarkError rather than dividing by zero."""
    x = _money(x, "percentile_rank value")
    vals = [_money(v, "percentile_rank distribution value") for v in sorted_vals]
    n = len(vals)
    if n == 0:
        raise BenchmarkError("percentile_rank of an empty distribution")
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return 100.0 * (below + 0.5 * equal) / n


def position(subject_value, peer_values, band_lo, band_hi):
    """Position one subject pay element vs the peer distribution. Returns percentile, P25/median/P75, the
    target band, a below/within/above status, and the gap (percentile points outside the band, 0 if within)."""
    if not (isinstance(band_lo, int) and isinstance(band_hi, int) and 0 <= band_lo <= band_hi <= 100):
        raise BenchmarkError(f"target band must be ints 0<=lo<=hi<=100 (got {band_lo},{band_hi})")
    sv = _money(subject_value, "subject_value")
    peers = _sorted(peer_values)
    pr = percentile_rank(peers, sv)
    if pr < band_lo:
        status, gap = "below", round(band_lo - pr, 1)
    elif pr > band_hi:
        status, gap = "above", round(pr - band_hi, 1)
    else:
        status, gap = "within", 0.0
    return {"subject_value": sv, "peer_n": len(peers), "peer_p25": _quantile(peers, 0.25),
            "peer_median": _quantile(peers, 0.50), "peer_p75": _quantile(peers, 0.75),
            "percentile": round(pr, 1), "target_lo": band_lo, "target_hi": band_hi,
            "status": status, "gap": gap}


def benchmark(path: Path = PROXY_PATH):
    """Full positioning of every subject NEO vs the peer group, per pay element, against the policy bands.
    Roles with fewer than MIN_PEER_N peer observations are SUPPRESSED (reported, never a spurious percentile).
    Deterministic + fail-closed."""
    peers, subject = load_proxy_comp(path)
    inc = _incumbents(peers)
    subj_by_role = {}
    for s in subject:
        rb = s["role_bucket"]
        if rb in subj_by_role:
            raise BenchmarkError(f"subject has more than one {rb} NEO — expected one per role")
        subj_by_role[rb] = s

    rows, suppressed = [], []
    for role in ROLES:
        subj = subj_by_role.get(role)
        peer_rows = [r for r in inc if r["role_bucket"] == role]
        if subj is None:
            continue                                     # subject doesn't have this role
        if len(peer_rows) < MIN_PEER_N:
            suppressed.append({"role": role, "peer_n": len(peer_rows), "reason": "insufficient peer disclosure"})
            continue
        for key, label, fn, lo, hi in ELEMENTS:
            pos = position(fn(subj), [fn(r) for r in peer_rows], lo, hi)
            rows.append({"role": role, "element": key, "element_label": label, **pos})

    below = [r for r in rows if r["status"] == "below"]
    return {
        "subject_company": subject[0]["company_name"],
        "n_peers_total": len({r["ticker"] for r in peers}),
        "roles_benchmarked": sorted({r["role"] for r in rows}, key=lambda x: ROLES.index(x)),
        "roles_suppressed": suppressed,
        "positions": rows,
        "n_positions": len(rows),
        "n_below_target": len(below),
        "elements": [{"key": k, "label": lab, "band": [lo, hi]} for k, lab, _f, lo, hi in ELEMENTS],
        "disclosure_note": "peer figures are actual SCT-disclosed proxy pay (not target); subject is synthetic",
    }


def _fmt_money(v):
    v = float(v)
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.0f}K"


def main(argv=None):
    import sys
    r = benchmark()
    print(f"Exec-comp benchmarking — {r['subject_company']} vs {r['n_peers_total']} peers "
          f"({r['n_below_target']}/{r['n_positions']} positions below target)")
    for role in r["roles_benchmarked"]:
        print(f"\n{role}")
        for p in [x for x in r["positions"] if x["role"] == role]:
            print(f"  {p['element_label']:20s} {_fmt_money(p['subject_value']):>9s}  "
                  f"P{p['percentile']:>4.0f}  (peer median {_fmt_money(p['peer_median'])}; "
                  f"target P{p['target_lo']}-{p['target_hi']})  -> {p['status']}"
                  + (f" by {p['gap']:.0f}pts" if p["gap"] else ""))
    for s in r["roles_suppressed"]:
        print(f"\n{s['role']}: SUPPRESSED — {s['reason']} (peer n={s['peer_n']} < {MIN_PEER_N})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
