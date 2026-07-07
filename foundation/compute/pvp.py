#!/usr/bin/env python3
"""pvp.py — Pay-versus-Performance / Compensation Actually Paid (SEC Item 402(v)).

Deterministic, stdlib-only math for the mandatory Pay-versus-Performance disclosure every US public
filer must publish. The load-bearing piece is the **CAP reconciliation bridge**: turning each named
executive officer's Summary Compensation Table (SCT) Total into Compensation Actually Paid (CAP) by
the equity fair-value roll-forward the SEC rule prescribes (Reg. S-K 402(v)(2)(iii)).

CAP(NEO, fiscal year Y) = SCT Total(Y)
  - (Stock Awards + Option Awards grant-date fair value reported in the SCT for Y)
  + year-end fair value of awards granted in Y that are outstanding and unvested at Y-end
  + change in fair value (Y-end vs prior year-end) of awards granted in prior years, unvested at Y-end
  + fair value at vesting of awards granted in Y that vested during Y
  + change in fair value (vesting date vs prior year-end) of prior-year awards that vested during Y
  - prior-year-end fair value of prior-year awards that were forfeited during Y
  + dividends/dividend-equivalents paid on unvested awards during Y not otherwise reflected
  +/- pension adjustments (service cost + prior-service cost - reported change in actuarial present value)

This module RE-MEASURES the fair values rather than trusting pre-supplied figures: restricted stock at
the share price, stock options by Black-Scholes, and relative-TSR market-condition PSUs by the shared
Monte Carlo model (`foundation.compute.rtsr.monte_carlo_valuation`) — the same estimator the rTSR PSU
arm ships. One committed synthetic subject stock-price path drives both the executives' equity fair
values AND the company's own Total Shareholder Return column, so the pay side and the performance side
of the table reconcile to a single price series.

This is an illustrative reconstruction of the 402(v) methodology on synthetic data. It is not
accounting, legal, tax, or investment advice, and not an auditor-approved ASC 718 valuation. Award
fair values a company files are produced by its valuation provider under audited assumptions; this
engine re-derives comparable figures from transparent inputs to make the reconciliation legible.
"""
from __future__ import annotations

from datetime import date
import math

from foundation.compute.rtsr import PayoutCurve, monte_carlo_valuation

TARGET_ROUND = 2                 # dollars are reported to the cent; fair values round to 2 dp for byte-stable output
AWARD_TYPES = ("rsu", "option", "psu_rtsr")


class PVPError(ValueError):
    """Raised when Pay-versus-Performance inputs are structurally invalid (the engine fails closed)."""


# --------------------------------------------------------------------------- small validators

def _num(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise PVPError(f"{name} must be numeric") from exc
    if not math.isfinite(out):
        raise PVPError(f"{name} must be finite")
    return out


def _pos(value, name):
    out = _num(value, name)
    if out <= 0:
        raise PVPError(f"{name} must be positive")
    return out


def _date(value, name):
    if not isinstance(value, str):
        raise PVPError(f"{name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PVPError(f"{name} must be a real ISO date (YYYY-MM-DD): {value!r}") from exc


def _fy_end(fy, mmdd):
    try:
        m, d = (int(x) for x in str(mmdd).split("-"))
        return date(int(fy), m, d)
    except (ValueError, TypeError) as exc:
        raise PVPError(f"fiscal_year_end must be MM-DD, got {mmdd!r}") from exc


# --------------------------------------------------------------------------- Black-Scholes (options)

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(spot, strike, years, rate, vol, div_yield=0.0):
    """Black-Scholes-Merton European call value per share. At/after expiry (years<=0) the value is the
    intrinsic max(spot-strike, 0); a zero-vol option is its discounted intrinsic. Deterministic."""
    s = _pos(spot, "spot")
    k = _pos(strike, "strike")
    t = _num(years, "years")
    r = _num(rate, "rate")
    sig = _num(vol, "vol")
    q = _num(div_yield, "div_yield")
    if sig < 0:
        raise PVPError("vol must be non-negative")
    if q < 0:
        raise PVPError("div_yield must be non-negative")
    if t <= 0:
        return round(max(s - k, 0.0), 6)
    if sig == 0.0:
        return round(max(s * math.exp(-q * t) - k * math.exp(-r * t), 0.0), 6)
    d1 = (math.log(s / k) + (r - q + 0.5 * sig * sig) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    return round(s * math.exp(-q * t) * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2), 6)


# --------------------------------------------------------------------------- input model

class PayVersusPerformance:
    """Validated Pay-versus-Performance inputs: a synthetic subject, one committed stock-price path, an
    equity-award book per NEO, and a per-year financials series (peer TSR, net income, company-selected
    measure). Fails closed on any structural contradiction so a malformed panel can never be scored."""

    def __init__(self, awards: dict, financials: dict):
        if not isinstance(awards, dict) or not isinstance(financials, dict):
            raise PVPError("awards and financials must be objects")
        subj = awards.get("subject") or {}
        self.ticker = str(subj.get("ticker", "")).strip().upper()
        self.company_name = str(subj.get("name", "")).strip()
        if not self.ticker or not self.company_name:
            raise PVPError("subject.ticker and subject.name are required")
        self.fiscal_years = [int(y) for y in awards.get("fiscal_years", [])]
        if len(self.fiscal_years) < 3:
            raise PVPError("Pay-versus-Performance requires at least three covered fiscal years")
        if self.fiscal_years != sorted(self.fiscal_years) or len(set(self.fiscal_years)) != len(self.fiscal_years):
            raise PVPError("fiscal_years must be strictly increasing and unique")
        self.fy_mmdd = str(awards.get("fiscal_year_end", "12-31"))
        self.fy_end = {y: _fy_end(y, self.fy_mmdd) for y in self.fiscal_years}
        self.prior_fy_end = {y: _fy_end(y - 1, self.fy_mmdd) for y in self.fiscal_years}

        self.prices = {}
        for k, v in (awards.get("price_by_date") or {}).items():
            self.prices[_date(k, "price_by_date key").isoformat()] = _pos(v, f"price {k}")
        if not self.prices:
            raise PVPError("price_by_date is required (the subject stock-price path)")

        self.market = awards.get("market") or {}
        self._mc_cache = {}

        self.base_date = _date(financials.get("base_date", ""), "financials.base_date")
        self.csm_label = str(financials.get("csm_label", "Company-Selected Measure")).strip() or "Company-Selected Measure"
        self.fin = {}
        for row in financials.get("years", []):
            fy = int(row.get("fy"))
            self.fin[fy] = {
                "peer_tsr_value": _pos(row.get("peer_tsr_value"), f"peer_tsr_value {fy}"),
                "net_income_usd": _num(row.get("net_income_usd"), f"net_income_usd {fy}"),
                "csm_usd": _num(row.get("csm_usd"), f"csm_usd {fy}"),
            }
        missing_fin = [y for y in self.fiscal_years if y not in self.fin]
        if missing_fin:
            raise PVPError(f"financials missing covered years: {missing_fin}")

        self.neos = [self._load_neo(n) for n in awards.get("neos", [])]
        if not self.neos:
            raise PVPError("at least one NEO is required")
        peos = [n for n in self.neos if n["is_peo"]]
        if len(peos) != 1:
            raise PVPError("exactly one principal executive officer (is_peo) is required")
        self.peo = peos[0]
        self.non_peo = [n for n in self.neos if not n["is_peo"]]
        if not self.non_peo:
            raise PVPError("at least one non-PEO NEO is required for the average column")

    def _load_neo(self, n):
        nid = str(n.get("id", "")).strip()
        role = str(n.get("role", "")).strip()
        if not nid or not role:
            raise PVPError("each NEO needs an id and role")
        sct = {}
        for fy in self.fiscal_years:
            row = (n.get("sct") or {}).get(str(fy)) or (n.get("sct") or {}).get(fy)
            if row is None:
                raise PVPError(f"NEO {nid} missing SCT row for FY{fy}")
            sct[fy] = {
                "total": _num(row.get("total"), f"{nid} SCT total {fy}"),
                "stock_awards": _num(row.get("stock_awards", 0.0), f"{nid} stock_awards {fy}"),
                "option_awards": _num(row.get("option_awards", 0.0), f"{nid} option_awards {fy}"),
            }
        tranches = [self._load_tranche(nid, t) for t in (n.get("tranches") or [])]
        return {
            "id": nid, "role": role, "is_peo": bool(n.get("is_peo", False)),
            "sct": sct, "tranches": tranches,
            "pension_by_fy": {int(k): _num(v, f"{nid} pension {k}")
                              for k, v in (n.get("pension_adjustment_by_fy") or {}).items()},
        }

    def _load_tranche(self, nid, t):
        ttype = str(t.get("type", "")).strip()
        if ttype not in AWARD_TYPES:
            raise PVPError(f"{nid}: tranche type must be one of {AWARD_TYPES}, got {ttype!r}")
        tr = {
            "id": str(t.get("id", "")).strip(),
            "type": ttype,
            "grant_fy": int(t.get("grant_fy")),
            "grant_date": _date(t.get("grant_date", ""), f"{nid} grant_date"),
            "vest_date": _date(t.get("vest_date", ""), f"{nid} vest_date"),
            "forfeited": bool(t.get("forfeited", False)),
            "dividends_paid_unvested": _num(t.get("dividends_paid_unvested", 0.0), f"{nid} dividends"),
        }
        if not tr["id"]:
            raise PVPError(f"{nid}: every tranche needs an id")
        if tr["grant_fy"] not in self.fiscal_years and tr["grant_fy"] > min(self.fiscal_years):
            # a prior-to-window grant year is fine; a grant_fy AFTER the window opens must be a covered year
            if tr["grant_fy"] > max(self.fiscal_years):
                raise PVPError(f"{tr['id']}: grant_fy {tr['grant_fy']} is after the covered window")
        if tr["forfeited"]:
            tr["forfeit_date"] = _date(t.get("forfeit_date", ""), f"{tr['id']} forfeit_date")
            if tr["forfeit_date"] < tr["grant_date"]:
                raise PVPError(f"{tr['id']}: forfeit_date precedes grant_date")
        else:
            tr["forfeit_date"] = None
        if tr["vest_date"] < tr["grant_date"]:
            raise PVPError(f"{tr['id']}: vest_date precedes grant_date")
        if ttype == "option":
            tr["shares"] = _pos(t.get("shares"), f"{tr['id']} shares")
            tr["strike"] = _pos(t.get("strike"), f"{tr['id']} strike")
            tr["expiry"] = _date(t.get("expiry", ""), f"{tr['id']} expiry")
            if tr["expiry"] <= tr["vest_date"]:
                raise PVPError(f"{tr['id']}: option expiry must be after vest_date")
        elif ttype == "rsu":
            tr["shares"] = _pos(t.get("shares"), f"{tr['id']} shares")
        else:  # psu_rtsr
            tr["target_shares"] = _pos(t.get("target_shares"), f"{tr['id']} target_shares")
            tr["performance_end"] = _date(t.get("performance_end", t.get("vest_date")), f"{tr['id']} performance_end")
        return tr

    # ---------------------------------------------------------------- price + fair value
    def price_on(self, d: date) -> float:
        iso = d.isoformat()
        if iso not in self.prices:
            raise PVPError(f"price_by_date is missing a required measurement date: {iso}")
        return self.prices[iso]

    def _mc_context(self, spot, years):
        m = self.market.get("rtsr") or {}
        peers = [str(t).strip().upper() for t in m.get("peer_tickers", [])]
        if len(peers) < 1:
            raise PVPError("market.rtsr.peer_tickers is required for PSU re-measurement")
        tickers = [self.ticker] + peers
        peer_spots = m.get("peer_spots") or {}
        spots = {self.ticker: spot}
        for p in peers:
            spots[p] = _pos(peer_spots.get(p), f"peer_spot {p}")
        vol = _num(m.get("volatility", self.market.get("volatility")), "rtsr volatility")
        rf = _num(m.get("risk_free_rate", self.market.get("risk_free_rate")), "rtsr risk_free_rate")
        q = _num(m.get("dividend_yield", self.market.get("dividend_yield", 0.0)), "rtsr dividend_yield")
        comp = m.get("correlation") or {"diagonal": 1.0, "off_diagonal": 0.4}
        diag = float(comp.get("diagonal", 1.0))
        off = float(comp.get("off_diagonal", 0.4))
        corr = {a: {b: (diag if a == b else off) for b in tickers} for a in tickers}
        return {
            "tickers": tickers, "issuer": self.ticker,
            "spot_prices": spots,
            "volatilities": {t: vol for t in tickers},
            "dividend_yields": {t: q for t in tickers},
            "correlations": corr,
            "risk_free_rate": rf,
            "performance_years": max(years, 1e-6),
            "paths": int(m.get("paths", 3000)),
            "seed": int(m.get("seed", 30414)),
        }

    def _psu_curve(self):
        m = self.market.get("rtsr") or {}
        pts = [(p["percentile"], p["payout_percent"]) for p in m.get("payout_curve", [])]
        if not pts:
            raise PVPError("market.rtsr.payout_curve is required for PSU re-measurement")
        return PayoutCurve(pts)

    def fair_value(self, tr, at: date) -> float:
        """Fair value of a whole tranche at date `at`, re-measured with the model appropriate to its type.

        Deterministic. PSU Monte Carlo values are memoized on (tranche id, measurement date) so the same
        award re-measured for two adjacent covered years is valued identically and only once."""
        px = self.price_on(at)
        if tr["type"] == "rsu":
            return round(px * tr["shares"], 6)
        if tr["type"] == "option":
            years = (tr["expiry"].toordinal() - at.toordinal()) / 365.0
            per = bs_call(px, tr["strike"], years,
                          _num((self.market.get("rtsr") or {}).get("risk_free_rate", self.market.get("risk_free_rate")), "rf"),
                          _num((self.market.get("rtsr") or {}).get("volatility", self.market.get("volatility")), "vol"),
                          _num((self.market.get("rtsr") or {}).get("dividend_yield", self.market.get("dividend_yield", 0.0)), "q"))
            return round(per * tr["shares"], 6)
        # psu_rtsr — remaining-period market-condition re-measurement via the shared Monte Carlo estimator
        key = (tr["id"], at.isoformat())
        if key not in self._mc_cache:
            years = max((tr["performance_end"].toordinal() - at.toordinal()) / 365.0, 0.0)
            if years <= 0.0:
                per_share = px          # performance period closed: value settles at the current share price basis
            else:
                val = monte_carlo_valuation(self._mc_context(px, years), self._psu_curve())
                per_share = val["fair_value_per_target_share"]
            self._mc_cache[key] = round(per_share * tr["target_shares"], 6)
        return self._mc_cache[key]


# --------------------------------------------------------------------------- CAP reconciliation

def _resolved_before(tr, d: date) -> bool:
    """True if the tranche vested or was forfeited strictly before date `d` (so it is out of the roll-forward)."""
    if not tr["forfeited"] and tr["vest_date"] < d:
        return True
    if tr["forfeited"] and tr["forfeit_date"] < d:
        return True
    return False


def cap_for_neo_year(pvp: PayVersusPerformance, neo, fy: int):
    """The full SCT-Total -> CAP reconciliation for one NEO and one covered fiscal year.

    Returns {sct_total, cap, equity_adjustment, pension_adjustment, bridge:[(label, usd, kind)]} where
    `bridge` is a signed waterfall from SCT Total to CAP. Every equity term is computed by re-measuring
    the affected tranches at the exact date the rule prescribes."""
    ye = pvp.fy_end[fy]
    pye = pvp.prior_fy_end[fy]
    y_start = date(fy, 1, 1)
    sct = neo["sct"][fy]
    sct_total = sct["total"]
    sct_equity = sct["stock_awards"] + sct["option_awards"]

    add_ye_new = 0.0            # granted this year, unvested at year-end -> + year-end FV
    chg_prior_unvested = 0.0    # granted prior, unvested at year-end -> + (YE - prior YE)
    vest_new = 0.0             # granted this year, vested this year -> + FV at vesting
    chg_prior_vested = 0.0      # granted prior, vested this year -> + (vest FV - prior YE)
    less_forfeited = 0.0        # granted prior, forfeited this year -> - prior YE FV
    dividends = 0.0

    for tr in neo["tranches"]:
        if tr["grant_date"] > ye:
            continue                                   # not yet granted within this covered year
        if _resolved_before(tr, y_start):
            continue                                   # already vested/forfeited before the year -> out of the bridge
        granted_this_year = (tr["grant_fy"] == fy)
        vested_this_year = (not tr["forfeited"]) and (y_start <= tr["vest_date"] <= ye)
        forfeited_this_year = tr["forfeited"] and (y_start <= tr["forfeit_date"] <= ye)
        unvested_at_ye = (not vested_this_year) and (not forfeited_this_year) and (tr["vest_date"] > ye)

        if forfeited_this_year:
            if not granted_this_year:
                less_forfeited += pvp.fair_value(tr, pye)   # remove value carried at the prior year-end
            # a same-year grant that also forfeits in-year carried no prior-year value and no SCT-add to remove
            dividends += tr["dividends_paid_unvested"] if vested_this_year else 0.0
            continue
        if vested_this_year:
            if granted_this_year:
                vest_new += pvp.fair_value(tr, tr["vest_date"])
            else:
                chg_prior_vested += pvp.fair_value(tr, tr["vest_date"]) - pvp.fair_value(tr, pye)
        elif unvested_at_ye:
            if granted_this_year:
                add_ye_new += pvp.fair_value(tr, ye)
            else:
                chg_prior_unvested += pvp.fair_value(tr, ye) - pvp.fair_value(tr, pye)
        dividends += tr["dividends_paid_unvested"]

    pension = neo["pension_by_fy"].get(fy, 0.0)
    equity_adj = (-sct_equity + add_ye_new + chg_prior_unvested + vest_new
                  + chg_prior_vested - less_forfeited + dividends)
    cap = sct_total + equity_adj + pension

    bridge = [("SCT total", round(sct_total, TARGET_ROUND), "total")]
    bridge.append(("less SCT equity FV", round(-sct_equity, TARGET_ROUND), "sub"))
    bridge.append(("+ YE FV new grants", round(add_ye_new, TARGET_ROUND), "add"))
    bridge.append(("+ Δ FV prior unvested", round(chg_prior_unvested, TARGET_ROUND), "add"))
    bridge.append(("+ vest FV new grants", round(vest_new, TARGET_ROUND), "add"))
    bridge.append(("+ Δ to vest, prior", round(chg_prior_vested, TARGET_ROUND), "add"))
    bridge.append(("less forfeited FV", round(-less_forfeited, TARGET_ROUND), "sub"))
    if abs(dividends) > 0:
        bridge.append(("+ dividends", round(dividends, TARGET_ROUND), "add"))
    if abs(pension) > 0:
        bridge.append(("pension adj", round(pension, TARGET_ROUND), "add"))
    bridge.append(("CAP", round(cap, TARGET_ROUND), "total"))

    return {
        "fy": fy,
        "sct_total": round(sct_total, TARGET_ROUND),
        "equity_adjustment": round(equity_adj, TARGET_ROUND),
        "pension_adjustment": round(pension, TARGET_ROUND),
        "cap": round(cap, TARGET_ROUND),
        "components": {
            "less_sct_equity_fv": round(-sct_equity, TARGET_ROUND),
            "ye_fv_new_grants": round(add_ye_new, TARGET_ROUND),
            "change_fv_prior_unvested": round(chg_prior_unvested, TARGET_ROUND),
            "vest_fv_new_grants": round(vest_new, TARGET_ROUND),
            "change_fv_to_vest_prior": round(chg_prior_vested, TARGET_ROUND),
            "less_forfeited_prior_ye_fv": round(-less_forfeited, TARGET_ROUND),
            "dividends": round(dividends, TARGET_ROUND),
        },
        "bridge": bridge,
    }


def company_tsr_value(pvp: PayVersusPerformance, fy: int) -> float:
    """Value at FY-end of a fixed $100 invested at the base date, on the subject's own committed price path
    (ex-dividend; the synthetic subject pays no dividend). Ties the performance column to the same price
    series that drives every executive equity fair value above."""
    base = pvp.price_on(pvp.base_date)
    return round(100.0 * pvp.price_on(pvp.fy_end[fy]) / base, TARGET_ROUND)


def pvp_table(pvp: PayVersusPerformance):
    """The Item 402(v) table, one row per covered fiscal year: PEO SCT + CAP, average non-PEO SCT + CAP,
    company TSR ($100 indexed), peer-group TSR ($100 indexed), net income, and the company-selected
    measure. Every CAP figure is the reconciliation above; the two TSR columns share one $100 base."""
    rows = []
    for fy in pvp.fiscal_years:
        peo = cap_for_neo_year(pvp, pvp.peo, fy)
        others = [cap_for_neo_year(pvp, n, fy) for n in pvp.non_peo]
        avg_sct = sum(o["sct_total"] for o in others) / len(others)
        avg_cap = sum(o["cap"] for o in others) / len(others)
        rows.append({
            "fy": fy,
            "peo_sct_total": peo["sct_total"],
            "peo_cap": peo["cap"],
            "avg_nonpeo_sct_total": round(avg_sct, TARGET_ROUND),
            "avg_nonpeo_cap": round(avg_cap, TARGET_ROUND),
            "company_tsr_value": company_tsr_value(pvp, fy),
            "peer_tsr_value": round(pvp.fin[fy]["peer_tsr_value"], TARGET_ROUND),
            "net_income_usd": round(pvp.fin[fy]["net_income_usd"], TARGET_ROUND),
            "csm_usd": round(pvp.fin[fy]["csm_usd"], TARGET_ROUND),
        })
    return {"csm_label": pvp.csm_label, "base_date": pvp.base_date.isoformat(), "rows": rows}


def relationship_series(table):
    """The three required 402(v) relationship graphics as parallel arrays over the covered years:
    PEO CAP versus company TSR, versus net income, and versus the company-selected measure."""
    rows = table["rows"]
    return {
        "years": [r["fy"] for r in rows],
        "peo_cap": [r["peo_cap"] for r in rows],
        "avg_nonpeo_cap": [r["avg_nonpeo_cap"] for r in rows],
        "company_tsr_value": [r["company_tsr_value"] for r in rows],
        "peer_tsr_value": [r["peer_tsr_value"] for r in rows],
        "net_income_usd": [r["net_income_usd"] for r in rows],
        "csm_usd": [r["csm_usd"] for r in rows],
        "csm_label": table["csm_label"],
    }


def alignment(table):
    """Directional pay-for-performance read used for the honest headline: does PEO CAP move WITH company
    TSR across the window? Reports the first-to-last direction of each and whether they agree. This is a
    legibility signal, never a say-on-pay vote forecast or a proxy-advisor concern level."""
    rows = table["rows"]
    if len(rows) < 2:
        return {"cap_direction": "flat", "tsr_direction": "flat", "aligned": True, "note": "single-year window"}

    def _dir(a, b):
        if b > a * 1.02:
            return "up"
        if b < a * 0.98:
            return "down"
        return "flat"

    cap_dir = _dir(rows[0]["peo_cap"], rows[-1]["peo_cap"])
    tsr_dir = _dir(rows[0]["company_tsr_value"], rows[-1]["company_tsr_value"])
    aligned = (cap_dir == tsr_dir) or cap_dir == "flat" or tsr_dir == "flat"
    return {
        "cap_direction": cap_dir,
        "tsr_direction": tsr_dir,
        "aligned": aligned,
        "peo_cap_first": rows[0]["peo_cap"],
        "peo_cap_last": rows[-1]["peo_cap"],
        "tsr_first": rows[0]["company_tsr_value"],
        "tsr_last": rows[-1]["company_tsr_value"],
    }
