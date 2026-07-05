#!/usr/bin/env python3
"""Illustrative Glass Lewis (GL) pay-for-performance screen — the SECOND proxy advisor, and the ISS-vs-GL
comparison that is the actual product. Runs over the SAME synthetic universe as the ISS screen
(iss_universe.csv + exec_pay_tsr.csv) plus gl_financials.csv, so the two advisors score identical facts.

This reconstructs Glass Lewis's CURRENT (2026) model — a 0–100 SCORECARD across five quantitative tests that
maps to a CONCERN LEVEL (Negligible / Low / Medium / High / Severe). (Glass Lewis RETIRED its old A–F letter
grade with the 2026 model; see governance/glass-lewis-model.md.)

THE HONESTY CONTRACT (component-by-component ledger in governance/glass-lewis-model.md):
- [PUBLIC] GL applies a proprietary quantitative P4P scorecard whose 0–100 composite maps to a concern level;
  Severe/High are more likely to draw a negative say-on-pay recommendation. The five quantitative tests are:
  Granted CEO Pay vs TSR, Granted CEO Pay vs Financial Performance, CEO STI Payouts vs TSR, Total Granted NEO
  Pay vs Financial Performance, and CEO Compensation-Actually-Paid vs TSR; plus a qualitative DOWNWARD modifier.
- [PUBLIC-OUTLINE] pay is granted CEO / NEO-team pay; TSR is a SEPARATE test from the financial tests; the
  financial-performance metric set (for non-financial sectors) is revenue growth, EPS growth, OCF growth,
  ROE, ROA; pay/TSR/STI/CAP use a 5-year weighted window (the financial-GROWTH tests use a 3-year window here
  — a documented simplification vs GL's 5-year, driven by the synthetic data's history depth); GL builds its
  own peer group (~15 firms, min ~10 viable) from disclosed peers + a peers-of-peers network. The STI test
  ranks STI payout AS A % OF TARGET (payout ÷ target, size-neutral — matching GL's description), ranked WITHIN
  the GL peer pool here as an illustrative proxy for GL's broader-market benchmarking. SAY-ON-PAY
  RESPONSIVENESS (GL engages below ~80% prior support, a disclosed policy) is modeled as a RECOMMENDATION-level
  factor, SEPARATE from the quantitative P4P composite — reported beside the score, never subtracted from it.
  NOTE: the CAP test's ">50% above the peer median" threshold is a disclosed rule, its penalty slope
  illustrative; the say-on-pay concern mapping is illustrative (the ~80% threshold is public).
- [ILLUSTRATIVE] the exact TEST WEIGHTS, the qualitative point deductions, and the peer-ranking function are
  PROPRIETARY (Glass Lewis expressly does NOT disclose the WEIGHTS). GL DOES publish test-specific rating
  ranges + overall concern bands; this repo intentionally applies ONE uniform illustrative band across tests
  (a labeled simplification, not GL's per-test tables). Everything in
  `_GL` below is a defensible neutral reconstruction, labeled as such — NOT Glass Lewis output, not
  affiliated with Glass, Lewis & Co., and built only from PUBLIC methodology descriptions (no GL proprietary
  reports or materials).

How this DIFFERS from the ISS screen in this repo (the point of having both): ISS is CEO-only and TSR-centric
(a MOM/RDA/PTA threshold cascade); GL is a broad NEO-team, financials-heavy 5-test scorecard. Both output a
concern level, and they agree at the extremes but can diverge in the middle — where a company's stock lagged
its financials.

Standard library only. Deterministic. Fail-closed. Presentation layers render it; they never decide.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from foundation.compute.iss_screen import _percentile_rank, _median, ISSUniverse  # reuse exact tie + median
from foundation.compute.peers import real_peer_identifiers, name_matches_real

_DATA = Path(__file__).resolve().parents[1] / "data" / "acme"
_TICKER_RE = re.compile(r"^(ACMQ|S\d{3})$")     # structural guard: only the synthetic universe, never a real ticker

_GL_COLS = ("ticker", "neo_other_pay_y1", "neo_other_pay_y2", "neo_other_pay_y3", "neo_other_pay_y4",
            "neo_other_pay_y5", "sti_payout_y1", "sti_payout_y2", "sti_payout_y3", "sti_payout_y4",
            "sti_payout_y5", "cap_y1", "cap_y2", "cap_y3", "cap_y4", "cap_y5",
            "eps_y2", "eps_y3", "eps_y4", "eps_y5", "rev_y2", "rev_y3", "rev_y4", "rev_y5",
            "ocf_y2", "ocf_y3", "ocf_y4", "ocf_y5", "roe_y3", "roe_y4", "roe_y5", "roa_y3", "roa_y4", "roa_y5",
            "sti_target_y1", "sti_target_y2", "sti_target_y3", "sti_target_y4", "sti_target_y5",
            "prior_say_on_pay_support_pct", "say_on_pay_responsiveness")
_RESPONSIVENESS = ("robust", "limited", "none", "n/a")   # board response to a prior low say-on-pay vote
_PEER_COLS = ("ticker", "company_name", "gics_sector", "gics_subindustry", "revenue_usd",
              "market_cap_usd", "employees", "total_assets_usd", "is_subject")
_EXEC_COLS = ("ticker", "self_peers", "pay_y1", "pay_y2", "pay_y3", "pay_y4", "pay_y5",
              "tsrval_y1", "tsrval_y2", "tsrval_y3", "tsrval_y4", "tsrval_y5", "fin_eva")

# ---- ILLUSTRATIVE reconstruction constants (the ONLY invented numbers; each defensible + labeled) ----------
_GL = {
    # illustrative TEST WEIGHTS (Glass Lewis does NOT disclose weights). The financial + NEO tests carry the
    # majority so a broad, aligned program is not dominated by the single TSR-linked CEO test.
    "test_weights": {"granted_ceo_pay_vs_tsr": 0.22, "granted_ceo_pay_vs_financial": 0.22,
                     "sti_vs_tsr": 0.14, "neo_pay_vs_financial": 0.24, "cap_vs_tsr": 0.18},
    # the financial-performance metric SET Glass Lewis discloses for non-financial sectors (EQUAL-weighted
    # here). TSR is a SEPARATE test, NOT blended into the financial performance percentile.
    "financial_metrics": ("rev_growth", "eps_growth", "ocf_growth", "roe", "roa"),
    "year_weights_5": (0.10, 0.15, 0.20, 0.25, 0.30),   # 5-yr recency weights (pay/TSR/STI/CAP)
    "year_weights_3": (0.20, 0.30, 0.50),               # 3-yr weights (financial growth — data-history depth)
    # OVERALL concern bands on the 0–100 composite (higher = better alignment = LESS concern)
    "concern_bands": ((20.0, "Severe"), (40.0, "High"), (60.0, "Medium"), (80.0, "Low"), (math.inf, "Negligible")),
    # per-test score bands — GL publishes test-specific rating ranges, but this repo intentionally applies ONE
    # uniform illustrative band across all five tests (a labeled simplification, not GL's per-test tables)
    "test_bands": ((34.0, "Severe"), (50.0, "High"), (69.0, "Moderate"), (89.0, "Low"), (math.inf, "Negligible")),
    "peer_cap_lo": 0.33, "peer_cap_hi": 3.0, "peer_target": 15, "peer_min_scorable": 10,  # ~15 firms, min ~10
    "cap_penalty_excess": 1.5,   # CAP-vs-TSR: no penalty at/below median; penalties begin >50% above median
    "qual_cap": 20.0,            # the qualitative downward modifier is capped
    # say-on-pay RESPONSIVENESS (a disclosed GL policy): GL engages when prior support fell below ~80%; a weak
    # board response to that low vote deepens the SAY-ON-PAY RECOMMENDATION concern. This is a RECOMMENDATION-
    # level factor, NOT an input to the quantitative P4P composite (GL applies it separately) — so it is
    # reported beside the score, never subtracted from it. Threshold is public; the concern mapping is
    # illustrative.
    "sop_engage_threshold": 80.0,
    "sop_response_concern": {"robust": "low", "limited": "elevated", "none": "high", "n/a": "high"},
    "sti_flag_penalty": 8.0,     # the STI-above-median-while-TSR-lagged P4P alignment flag (a composite modifier)
}
# round ranked measures to this many decimals before percentile-ranking, so a last-ULP float difference
# across platforms (macOS ARM vs Linux x86 in CI) can't flip a near-tie rank -> byte-identical output.
_RANK_ROUND = 9
_TEST_LABELS = {
    "granted_ceo_pay_vs_tsr": "Granted CEO Pay vs TSR",
    "granted_ceo_pay_vs_financial": "Granted CEO Pay vs Financial Performance",
    "sti_vs_tsr": "CEO STI Payouts vs TSR",
    "neo_pay_vs_financial": "Total Granted NEO Pay vs Financial Performance",
    "cap_vs_tsr": "CEO Compensation Actually Paid vs TSR",
}

# Say-on-pay support-erosion bands keyed to COMBINED concern SEVERITY (iss_ord + gl_ord, 0-4) — so a SEVERE
# unilateral flag lands BELOW a Medium one (severity is not flattened onto the verdict label). DIRECTIONAL
# practitioner ranges, wider/lower as concern stacks — NOT a vote forecast or probability. Illustrative.
_SOP_BY_SEVERITY = {0: (90.0, 96.0), 1: (80.0, 92.0), 2: (72.0, 86.0), 3: (62.0, 78.0), 4: (52.0, 74.0)}


class GLDataError(ValueError):
    pass


def _rows(path, cols):
    """Schema-checked CSV load — GL raises GLDataError on any structural fault (its OWN typed error)."""
    if not path.exists():
        raise GLDataError(f"missing data file: {path.name}")
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames is None or list(rd.fieldnames) != list(cols):
            raise GLDataError(f"{path.name}: header {rd.fieldnames} != expected {list(cols)}")
        out = [dict(r) for r in rd]
    if not out:
        raise GLDataError(f"{path.name}: no rows")
    return out


def _num(v, ctx, positive=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise GLDataError(f"{ctx}: not a number ({v!r})")
    if not math.isfinite(f):
        raise GLDataError(f"{ctx}: not finite ({v!r})")
    if positive and f <= 0:
        raise GLDataError(f"{ctx}: must be > 0 ({v!r})")
    return f


def _band(value, bands):
    for hi, label in bands:
        if value <= hi:
            return label
    return bands[-1][1]


def concern_for_score(score):
    """0–100 composite (higher = better) -> concern level."""
    return _band(score, _GL["concern_bands"])


def _align_score(pay_pct, perf_pct):
    """0–100 alignment sub-score: ~100 when performance leads pay, declining as pay outruns performance.
    gap = pay percentile - performance percentile (positive = pay ahead of performance = concern)."""
    gap = pay_pct - perf_pct
    return max(0.0, min(100.0, 85.0 - 1.6 * gap))


def _wavg(values, weights):
    n = len(values)
    w = weights[-n:]
    return sum(v * wi for v, wi in zip(values, w)) / sum(w)


class GLUniverse:
    """Loads + fail-closed validates the synthetic universe, builds a GL-style peer group, and screens the
    subject through the five-test scorecard to a 0–100 composite + concern level. Same roster + facts as ISS."""

    def __init__(self, data_dir=_DATA):
        d = Path(data_dir)
        co = _rows(d / "iss_universe.csv", _PEER_COLS)
        ex = _rows(d / "exec_pay_tsr.csv", _EXEC_COLS)
        gl = _rows(d / "gl_financials.csv", _GL_COLS)
        self._by = {}
        real_tk, real_names = real_peer_identifiers(require=True)   # fail closed if the real-peer roster can't load
        for name, rows in (("iss_universe", co), ("exec_pay_tsr", ex), ("gl_financials", gl)):
            tks = [r["ticker"] for r in rows]
            if len(tks) != len(set(tks)):
                dup = next(t for t in tks if tks.count(t) > 1)
                raise GLDataError(f"{name}.csv has duplicate ticker {dup}")
        rosters = [{r["ticker"] for r in rows} for rows in (co, ex, gl)]
        if rosters[0] != rosters[1] or rosters[0] != rosters[2]:
            raise GLDataError("iss_universe / exec_pay_tsr / gl_financials cover different tickers")
        for c in co:
            tk = c["ticker"]
            if not _TICKER_RE.match(tk):
                raise GLDataError(f"non-synthetic ticker shape {tk!r}")
            if tk in real_tk or name_matches_real(c["company_name"], real_names):
                raise GLDataError(f"real issuer identity leaked into the GL universe: {tk} / {c['company_name']}")
            self._by[tk] = {"co": c}
        for e in ex:
            self._by[e["ticker"]]["ex"] = e
        for g in gl:
            self._by[g["ticker"]]["gl"] = g
        subs = [tk for tk, v in self._by.items() if v["co"]["is_subject"] == "yes"]
        if len(subs) != 1:
            raise GLDataError(f"expected exactly one subject, found {len(subs)}")
        self.subject = subs[0]
        self._validate_numbers()

    def _validate_numbers(self):
        # validate EVERY field the scorecard consumes (not just a subset of years). pay/STI/CAP components and
        # growth bases must be strictly positive; ROE/ROA are ratio levels that may be negative -> finite only.
        pos_gl = ([f"neo_other_pay_y{y}" for y in range(1, 6)] + [f"sti_payout_y{y}" for y in range(1, 6)]
                  + [f"sti_target_y{y}" for y in range(1, 6)]     # STI targets: strictly positive (a denominator)
                  + [f"cap_y{y}" for y in range(1, 6)] + [f"eps_y{y}" for y in range(2, 6)]
                  + [f"rev_y{y}" for y in range(2, 6)] + [f"ocf_y{y}" for y in range(2, 6)])
        fin_gl = [f"roe_y{y}" for y in (3, 4, 5)] + [f"roa_y{y}" for y in (3, 4, 5)]
        for tk, v in self._by.items():
            ex = v["ex"]
            for c in ("pay_y1", "pay_y2", "pay_y3", "pay_y4", "pay_y5",
                      "tsrval_y1", "tsrval_y2", "tsrval_y3", "tsrval_y4", "tsrval_y5"):
                if _num(ex[c], f"{tk}.{c}") <= 0:                 # pay + TSR-index baselines strictly positive
                    raise GLDataError(f"{tk}: non-positive {c}")
            g = v["gl"]
            for c in pos_gl:
                if _num(g[c], f"{tk}.{c}") <= 0:
                    raise GLDataError(f"{tk}: non-positive {c} (pay component / growth base must be > 0)")
            for c in fin_gl:
                _num(g[c], f"{tk}.{c}")                          # ROE/ROA: finite (may be negative)
            sop = _num(g["prior_say_on_pay_support_pct"], f"{tk}.prior_say_on_pay_support_pct")
            if not (0.0 <= sop <= 100.0):                        # a say-on-pay support % is bounded 0–100
                raise GLDataError(f"{tk}: say-on-pay support {sop} out of [0,100]")
            if g["say_on_pay_responsiveness"] not in _RESPONSIVENESS:
                raise GLDataError(f"{tk}: bad responsiveness {g['say_on_pay_responsiveness']!r}")

    # -- per-company measures (each on its own line so the tests are auditable) -----------------------------
    def _ceo_pay(self, tk):
        ex = self._by[tk]["ex"]
        return _wavg([_num(ex[f"pay_y{y}"], "pay") for y in range(1, 6)], _GL["year_weights_5"])

    def _neo_pay(self, tk):
        ex, gl = self._by[tk]["ex"], self._by[tk]["gl"]
        team = [_num(ex[f"pay_y{y}"], "pay") + _num(gl[f"neo_other_pay_y{y}"], "neo") for y in range(1, 6)]
        return _wavg(team, _GL["year_weights_5"])

    def _sti(self, tk):
        # STI PAYOUT AS A % OF TARGET (not raw dollars): a company that paid ABOVE target while performance
        # lagged is what the "STI Payouts vs TSR" test is built to surface — normalizing by target strips the
        # company-size effect so the rank reflects generosity-vs-opportunity, not absolute STI dollars.
        gl = self._by[tk]["gl"]
        ratios = [_num(gl[f"sti_payout_y{y}"], "sti") / _num(gl[f"sti_target_y{y}"], "sti_target", positive=True)
                  for y in range(1, 6)]
        return _wavg(ratios, _GL["year_weights_5"])

    def _tsr_growth(self, tk):
        ex = self._by[tk]["ex"]
        idx = [100.0] + [_num(ex[f"tsrval_y{y}"], "tsr") for y in range(1, 6)]   # $100 base at year 0
        growth = [idx[i] / idx[i - 1] - 1.0 for i in range(1, 6)]
        return _wavg(growth, _GL["year_weights_5"])

    def _cap_ratio(self, tk):
        ex, gl = self._by[tk]["ex"], self._by[tk]["gl"]
        cap5 = sum(_num(gl[f"cap_y{y}"], "cap") for y in range(1, 6))            # 5-yr aggregate CEO CAP
        if not math.isfinite(cap5):                                             # a pathological input can overflow
            raise GLDataError(f"{tk}: CAP aggregate is non-finite")
        tsr_reported = _num(ex["tsrval_y5"], "tsr5")                             # reported cumulative TSR (base 100)
        ratio = cap5 / tsr_reported
        if not math.isfinite(ratio):
            raise GLDataError(f"{tk}: CAP/TSR ratio is non-finite")
        return ratio

    def _financial_metrics(self, tk):
        gl = self._by[tk]["gl"]

        def growth(series):
            g = [(_num(gl[series % y], "v") / _num(gl[series % (y - 1)], "v")) - 1.0 for y in (3, 4, 5)]
            return _wavg(g, _GL["year_weights_3"])

        return {"rev_growth": growth("rev_y%d"), "eps_growth": growth("eps_y%d"),
                "ocf_growth": growth("ocf_y%d"),
                "roe": _wavg([_num(gl[f"roe_y{y}"], "roe") for y in (3, 4, 5)], _GL["year_weights_3"]),
                "roa": _wavg([_num(gl[f"roa_y{y}"], "roa") for y in (3, 4, 5)], _GL["year_weights_3"])}

    # -- GL-style peer group (cap-banded co-citation network; ~15 firms, min ~10 viable) -------------------
    def peer_group(self):
        subj = self._by[self.subject]
        subj_cap = _num(subj["co"]["market_cap_usd"], "cap")
        disclosed = set(subj["ex"]["self_peers"].split(";")) if subj["ex"]["self_peers"] else set()
        namers = {tk for tk, v in self._by.items()
                  if tk != self.subject and self.subject in (v["ex"]["self_peers"].split(";"))}
        seed = (disclosed | namers) & set(self._by)
        pop = seed | {p for tk in seed for p in self._by[tk]["ex"]["self_peers"].split(";") if p in self._by}
        scored = []
        for tk in pop:
            if tk == self.subject:
                continue
            cap = _num(self._by[tk]["co"]["market_cap_usd"], "cap")
            if not (_GL["peer_cap_lo"] * subj_cap <= cap <= _GL["peer_cap_hi"] * subj_cap):
                continue
            cocite = (1 if tk in disclosed else 0) + (1 if tk in namers else 0) \
                + sum(1 for s in seed if tk in self._by[s]["ex"]["self_peers"].split(";"))
            scored.append((tk, cocite, abs(cap - subj_cap)))
        scored.sort(key=lambda x: (-x[1], x[2], x[0]))
        members = [tk for tk, _, _ in scored[:_GL["peer_target"]]]
        return {"members": members, "n": len(members), "scorable": len(members) >= _GL["peer_min_scorable"]}

    def _financial_percentile(self, tk, pool, fin):
        """Average of the disclosed financial metrics' percentiles within the pool (equal-weighted). Values are
        rounded before ranking so a last-ULP float difference across platforms can't flip a near-tie rank."""
        pcts = []
        for m in _GL["financial_metrics"]:
            pcts.append(_percentile_rank(round(fin[tk][m], _RANK_ROUND),
                                         [round(fin[o][m], _RANK_ROUND) for o in pool]))
        return sum(pcts) / len(pcts)

    def screen(self, group=None):
        cg = group or self.peer_group()
        if not cg["scorable"]:
            raise GLDataError(f"GL peer group has only {cg['n']} names (< {_GL['peer_min_scorable']} minimum)")
        pool = cg["members"] + [self.subject]
        s = self.subject
        # rank every measure within the pool
        ceo_pay = {tk: self._ceo_pay(tk) for tk in pool}
        neo_pay = {tk: self._neo_pay(tk) for tk in pool}
        sti = {tk: self._sti(tk) for tk in pool}
        tsr = {tk: self._tsr_growth(tk) for tk in pool}
        fin = {tk: self._financial_metrics(tk) for tk in pool}
        cap_ratio = {tk: self._cap_ratio(tk) for tk in pool}

        def pr(d, tk):    # round before ranking -> platform-stable (no ARM/x86 last-ULP rank flips near ties)
            return _percentile_rank(round(d[tk], _RANK_ROUND), [round(d[o], _RANK_ROUND) for o in pool])

        pay_pct = pr(ceo_pay, s)
        neo_pay_pct = pr(neo_pay, s)
        sti_pct = pr(sti, s)
        tsr_pct = pr(tsr, s)
        fin_pct = self._financial_percentile(s, pool, fin)

        # the five quantitative tests -> 0–100 sub-scores
        cap_med = _median([cap_ratio[o] for o in pool])
        if cap_med <= 0:                                          # fail closed rather than silently neutralize
            raise GLDataError("CAP peer-median ratio is non-positive")
        excess = cap_ratio[s] / cap_med
        cap_sub = 90.0 if excess <= _GL["cap_penalty_excess"] else max(0.0, 90.0 - (excess - _GL["cap_penalty_excess"]) * 80.0)
        subs = {
            "granted_ceo_pay_vs_tsr": _align_score(pay_pct, tsr_pct),
            "granted_ceo_pay_vs_financial": _align_score(pay_pct, fin_pct),
            "sti_vs_tsr": _align_score(sti_pct, tsr_pct),
            "neo_pay_vs_financial": _align_score(neo_pay_pct, fin_pct),
            "cap_vs_tsr": cap_sub,
        }
        w = _GL["test_weights"]
        quant = sum(subs[k] * w[k] for k in subs) / sum(w.values())
        # P4P qualitative DOWNWARD modifier on the composite (the STI-vs-TSR alignment flag). Say-on-pay
        # responsiveness is computed SEPARATELY as a recommendation-level factor — it does NOT touch the score.
        subj_gl = self._by[s]["gl"]
        qual = self._qualitative(sti_pct, tsr_pct)
        say_on_pay = self._say_on_pay_factor(_num(subj_gl["prior_say_on_pay_support_pct"], "sop_support"),
                                             subj_gl["say_on_pay_responsiveness"])
        # round FIRST, then band — so the concern label always matches the DISPLAYED score at a boundary
        composite = round(max(0.0, min(100.0, quant - qual["penalty"])), 1)
        concern = concern_for_score(composite)
        tests = []
        for k in _TEST_LABELS:
            sc = round(subs[k], 1)
            tests.append({"key": k, "label": _TEST_LABELS[k], "score": sc,
                          "band": _band(sc, _GL["test_bands"]), "weight": w[k]})
        # counterfactual reads (NON-additive) — isolate WHY GL diverges from ISS: a pure-TSR read vs a
        # financials-only read. The actual composite sits between them.
        tsr_only = round(subs["granted_ceo_pay_vs_tsr"], 1)
        fin_only = round((subs["granted_ceo_pay_vs_financial"] + subs["neo_pay_vs_financial"]) / 2.0, 1)
        return {
            "subject": s, "peer_group": cg,
            "composite_score": composite, "quant_score": round(quant, 1), "concern": concern,
            "pay_pctile": round(pay_pct, 1), "neo_pay_pctile": round(neo_pay_pct, 1),
            "sti_pctile": round(sti_pct, 1), "tsr_pctile": round(tsr_pct, 1), "fin_pctile": round(fin_pct, 1),
            "cap_excess_vs_median": round(excess, 2),
            "tests": tests, "qualitative": qual, "say_on_pay": say_on_pay,
            "counterfactuals": {
                "tsr_only_score": round(tsr_only, 1), "tsr_only_concern": concern_for_score(tsr_only),
                "financials_only_score": round(fin_only, 1), "financials_only_concern": concern_for_score(fin_only),
            },
        }

    def _qualitative(self, sti_pct, tsr_pct):
        """The P4P qualitative DOWNWARD modifier on the composite — the pay-vs-performance flag derivable from
        the synthetic data (STI paid above the peer median while TSR lagged). GL's full P4P checklist (one-time
        awards, upward discretion, short LTI vesting, excessive LTIP potential, undisclosed goals) needs
        plan-design disclosure beyond this dataset — documented, not faked. Say-on-pay RESPONSIVENESS is a
        SEPARATE recommendation-level factor (see _say_on_pay_factor), not part of this composite modifier."""
        flags, penalty = [], 0.0
        if sti_pct > 60.0 and tsr_pct < 40.0:
            flags.append("STI paid above the peer median while TSR lagged")
            penalty += _GL["sti_flag_penalty"]
        penalty = min(_GL["qual_cap"], penalty)
        return {"flags": flags, "penalty": penalty,
                "note": "partial — full GL P4P qualitative checklist needs plan-design disclosure not modeled here"}

    def _say_on_pay_factor(self, prior_support, responsiveness):
        """Say-on-pay RESPONSIVENESS — a disclosed GL policy applied at the say-on-pay RECOMMENDATION level, NOT
        as an input to the 0–100 P4P composite: GL engages a company whose prior support fell below ~80%, and a
        weak board response to that low vote deepens the recommendation concern. Reported beside the score for
        the committee; it never alters the P4P composite."""
        # round the prior-support % ONCE so the value compared to the threshold is exactly the value stored +
        # rendered — the below_threshold flag can never disagree with the number shown beside it
        prior_support = round(prior_support, 1)
        thr = _GL["sop_engage_threshold"]
        below = prior_support < thr
        concern = _GL["sop_response_concern"].get(responsiveness, "high") if below else "none"
        return {"prior_support_pct": prior_support, "responsiveness": responsiveness,
                "engage_threshold_pct": thr, "below_threshold": below, "recommendation_concern": concern,
                "note": "recommendation-level factor — separate from the quantitative P4P composite"}


# ---------------------------------------------------------------- the ISS-vs-GL "war room"
_GL_ORDINAL = {"Negligible": 0, "Low": 0, "Medium": 1, "High": 2, "Severe": 2}


def advisor_synthesis(iss_result, gl_result):
    """Reconcile the two advisors scoring identical facts. Both are illustrative reconstructions; this reports
    where they AGREE vs DIVERGE and WHY (their lenses differ by construction), plus a directional say-on-pay
    support band. It renders a decision, it does not make one — no probability, no vote forecast."""
    iss_subj = iss_result.get("subject", {}).get("ticker")
    gl_subj = gl_result.get("subject")
    if iss_subj is None or gl_subj is None:      # both advisors must name the issuer they scored
        raise GLDataError("advisor_synthesis requires both ISS and GL subject identifiers")
    if iss_subj != gl_subj:
        raise GLDataError(f"advisor_synthesis subject mismatch: ISS {iss_subj} vs GL {gl_subj}")
    iss_ord = {"Low": 0, "Medium": 1, "High": 2}[iss_result["concern"]]
    gl_concern = gl_result["concern"]
    gl_ord = _GL_ORDINAL[gl_concern]
    if iss_ord == 0 and gl_ord == 0:
        verdict = "CLEAN SWEEP"
    elif iss_ord >= 1 and gl_ord == 0:
        verdict = "ISS-ONLY FLAG"
    elif iss_ord == 0 and gl_ord >= 1:
        verdict = "GL-ONLY FLAG"
    elif iss_ord == 2 or gl_ord == 2:
        verdict = "TWO-FRONT FIGHT"
    else:
        verdict = "DUAL WATCH"
    agree = (iss_ord >= 1) == (gl_ord >= 1)
    sop_lo, sop_hi = _SOP_BY_SEVERITY[iss_ord + gl_ord]     # severity-weighted, not flattened to the verdict
    rda = iss_result["measures"]["rda"]
    contrast = {
        "iss_lens": "CEO-only realized/target pay vs 5-yr relative TSR (MOM/RDA/PTA cascade)",
        "gl_lens": "a 5-test scorecard — granted CEO/NEO pay vs TSR AND vs financial performance",
        "iss_pay_pctile": rda["pay_pctile"], "iss_tsr_pctile": rda["tsr_pctile"],
        "gl_pay_pctile": gl_result["pay_pctile"], "gl_fin_pctile": gl_result["fin_pctile"],
        "gl_tsr_pctile": gl_result["tsr_pctile"], "gl_composite": gl_result["composite_score"],
    }
    driver = ("both advisors align" if agree else
              ("ISS's pure CEO-pay-vs-TSR lens flags what GL's broader financials-weighted scorecard does not"
               if iss_ord > gl_ord else
               "GL's financials-weighted scorecard flags what ISS's CEO-pay-vs-TSR lens does not"))
    return {
        "verdict": verdict, "agree": agree,
        "iss_concern": iss_result["concern"], "iss_ordinal": iss_ord,
        "gl_concern": gl_concern, "gl_ordinal": gl_ord, "gl_composite": gl_result["composite_score"],
        "divergence_driver": driver, "contrast": contrast,
        "say_on_pay_support_band_pct": [sop_lo, sop_hi],
        "band_basis": "directional practitioner range for this combined posture — NOT a vote forecast or probability",
    }


def compute(data_dir=_DATA):
    """The full two-advisor result the glass-lewis agent renders: the GL scorecard, the ISS screen (same
    facts), and the reconciliation. The agent does no scoring of its own."""
    glu = GLUniverse(data_dir)
    gl = glu.screen()
    iss = ISSUniverse(data_dir).screen()
    if iss["subject"]["ticker"] != glu.subject:                  # both advisors MUST score the same issuer
        raise GLDataError(f"subject mismatch: ISS {iss['subject']['ticker']} vs GL {glu.subject}")
    return {"subject": glu.subject, "gl": gl, "iss": iss, "synthesis": advisor_synthesis(iss, gl)}


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2, default=str))
