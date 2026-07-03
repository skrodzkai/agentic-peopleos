#!/usr/bin/env python3
"""Executive-comp peer-group screening. The SUBJECT is synthetic Acme; the candidate PEERS are REAL public
companies (a peer screen benchmarks against real comps) with as-disclosed public financials — a dated,
illustrative snapshot sourced in governance/real-peer-data.md. (The ISS pay screen + rTSR valuation run on a
separate, clearly-synthetic universe, so no real name ever carries a fabricated pay/TSR figure.)

Pure, stdlib-only, deterministic. Two clearly-separated steps, the way a compensation committee
actually works:

  1. SCREEN (the gate) — a transparent per-criterion pass/fail: size bands as multiples of the subject
     (0.5x-2.0x on revenue and market cap) plus a same-GICS-sub-industry match. Headcount is a SOFT fit
     factor, not a hard gate (disclosed market practice). Membership in the peer group is decided HERE and
     only here — defensible to a board on one line ("same sub-industry, within 0.5-2.0x our size"). The
     fit score below NEVER gates membership.
  2. FIT-RANK (the order) — within that already-defensible group, rank peers by a pure SIZE-CLOSENESS
     score (revenue-weighted): 100 == identical size to the subject, 0 == at a band edge. No opaque
     qualitative weights — the ranking survives the same "who set these weights?" scrutiny as the gate.

It SCREENS, RANKS, and RECOMMENDS; it never finalizes the group or sets pay (that is the committee's call).

    from foundation.compute.peers import PeerUniverse
    u = PeerUniverse()
    result = u.screen()                     # default screen
    result["peers"]                         # companies that pass every active criterion, fit-ranked
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "foundation" / "data" / "acme"

# The exact schema the peer universe must have (loader fails closed on any drift).
REQUIRED_COLS = ("ticker", "company_name", "gics_sector", "gics_subindustry",
                 "revenue_usd", "market_cap_usd", "employees", "total_assets_usd", "is_subject",
                 "revenue_period", "market_cap_as_of", "confidence", "source_id")
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")

# Real, recognizable tickers used by the SYNTHETIC arms as a public-safety backstop. NOTE: the peer-builder
# universe (this loader) now INTENTIONALLY carries real tickers — its peers are real public companies. This
# set is consumed where the data must stay synthetic: the ISS pay screen + rTSR valuation loaders (which also
# enforce a synthetic ticker SHAPE) and tools/ticker_scan.py (which scans every non-real-peer artifact). It is
# a defense-in-depth deny-list, not the primary guard (the shape checks are).
REAL_TICKERS = frozenset({
    "AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "NVDA", "TSLA", "BRK", "JPM", "V", "MA", "UNH",
    "HD", "PG", "JNJ", "XOM", "CVX", "KO", "PEP", "WMT", "DIS", "NFLX", "CRM", "ORCL", "ADBE", "INTC",
    "AMD", "CSCO", "IBM", "QCOM", "TXN", "AVGO", "NOW", "SHOP", "UBER", "ABNB", "SNOW", "PLTR", "COIN",
    "SQ", "PYPL", "BAC", "WFC", "GS", "MS", "C", "T", "VZ", "CMCSA", "PFE", "MRK", "ABBV", "LLY",
    "NKE", "MCD", "SBUX", "COST", "TGT", "LOW", "CAT", "LUMN", "GRAB", "DRIP", "MERC", "VRTX", "PWR",
    "AUR", "SLAB", "FORM", "HELE", "ONON", "SPY", "QQQ", "VOO", "VTI", "IWM", "DIA", "GLD", "SLV",
    "TLT", "ARKK",
    # rTSR sample collision regressions: real/listed/recognizable symbols that must never appear in
    # synthetic exec-comp examples.
    "NOVA", "MTRX", "PULS", "JUNO", "KITE", "FLUX", "LUMA", "HUBX", "RIVR", "NSTR",
})


_NAME_SUFFIX_RE = re.compile(
    r"\b(the|inc|incorporated|corp|corporation|company|co|ltd|limited|holdings|holding|group|"
    r"plc|lp|llp|llc|sa|nv|ag|se)\b")


def _canon_name(s):
    """Canonical key for real-company-NAME matching: casefold, drop the leading article + punctuation + common
    corporate suffixes, collapse whitespace. So 'GitLab, Inc.' / 'GitLab Inc' / 'GITLAB  INC.' / 'GitLab
    Holdings' / 'The GitLab Group' all reduce to 'gitlab' — a punctuation/article/suffix variant can't slip a
    real name past the guards."""
    s = _NAME_SUFFIX_RE.sub(" ", str(s).casefold())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def name_matches_real(candidate, real_keys):
    """True if `candidate` is (a variant of) a real company name in `real_keys` (a set of canonical keys from
    real_peer_identifiers). Matching is TOKEN-SET SUBSET in BOTH directions, not exact-key equality: a
    recognizable SHORT FORM ('Descartes Systems Group', 'ZoomInfo') must be caught even though its canonical
    key differs from the stored full name ('The Descartes Systems Group Inc.', 'ZoomInfo Technologies Inc.').
    A real name is matched when the candidate's token set is a subset of, or a superset of, a stored name's
    token set — so a fabricated pay/TSR figure can never attach to a real company via a trimmed name."""
    ctoks = frozenset(_canon_name(candidate).split())
    if not ctoks:
        return False
    for r in real_keys:
        rtoks = frozenset(r.split())
        if rtoks and (ctoks <= rtoks or rtoks <= ctoks):
            return True
    return False


# Real companies that RENAMED — their FORMER names must still be rejected in synthetic artifacts even though
# only the current name is in peer_universe.csv (canonicalized form).
_FORMER_REAL_NAMES = frozenset(_canon_name(n) for n in (
    "BigCommerce Holdings, Inc.",      # -> Commerce.com, Inc. (Aug 2025)
    "ZoomInfo Technologies Inc.",      # ticker ZI -> GTM (2025); name retained but keep for safety
))


def real_peer_identifiers(data_dir=DATA, require=False):
    """(tickers, canonical-names) of the REAL public peers in peer_universe.csv (the non-subject rows). This is
    the ONLY place real tickers/names may legitimately appear; every SYNTHETIC artifact (ISS/rTSR universes,
    the ticker scanner) loads this set and REJECTS these identifiers so a real name can never carry a
    fabricated figure. Tickers are upper-cased; names are canonicalized via `_canon_name` (former names of
    renamed companies are folded in) so a punctuation/suffix variant can't evade the guard.

    `require=True` FAILS CLOSED (raises PeerDataError) if the roster is missing, schema-drifted, or has zero
    real peers — used by the rtsr/ISS name+ticker guards, so an unloadable roster can NEVER silently turn a
    public-safety guard into a no-op. `require=False` (default) is best-effort: it returns empty sets when the
    file is absent (used by tools/ticker_scan.py, which keeps the static deny-list as a backstop)."""
    path = Path(data_dir) / "peer_universe.csv"
    if not path.exists():
        if require:
            raise PeerDataError(f"peer_universe.csv not found at {path} — cannot verify real-peer identifiers")
        return set(), set(_FORMER_REAL_NAMES)
    tickers, names = set(), set(_FORMER_REAL_NAMES)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = set(reader.fieldnames or ())
        if not {"ticker", "company_name", "is_subject"} <= cols:
            if require:
                raise PeerDataError("peer_universe.csv missing required columns (ticker/company_name/is_subject)")
            return set(), set(_FORMER_REAL_NAMES)
        n_peer = 0
        for r in reader:
            if r.get("is_subject") == "no":
                n_peer += 1
                if r.get("ticker"):
                    tickers.add(r["ticker"].strip().upper())
                if r.get("company_name"):
                    names.add(_canon_name(r["company_name"]))
    if require and (not tickers or n_peer == 0):
        raise PeerDataError("peer_universe.csv has zero real peers — the real-name/ticker guard would be a no-op")
    return tickers, names


# The documented software/SaaS INDUSTRY GROUP: the set of GICS Level-4 sub-industries a compensation
# committee treats as software/SaaS peers. GICS deliberately FRAGMENTS software businesses across sectors —
# HCM SaaS (Paycom/Paylocity) lands in Industrials "Human Resource & Employment Services" and payments SaaS
# (Toast/Marqeta) in Financials "Transaction & Payment Processing Services" — so gating on a single Level-4
# code ("Application Software") would wrongly drop real software peers. Gating on this GROUP mirrors how
# committees actually build a software peer set and, crucially, does NOT depend on a candidate's exact
# Level-4 being verifiable from a public index table — only that it is a software/SaaS business in one of
# these sub-industries. Tunable + disclosed.
SOFTWARE_PEER_GROUP = frozenset({
    "Application Software",
    "Systems Software",
    "Internet Services & Infrastructure",
    "Human Resource & Employment Services",       # HCM SaaS (Paycom/Paylocity)
    "Transaction & Payment Processing Services",  # payments SaaS (Toast/Marqeta)
    # NB: "Interactive Media & Services" is deliberately EXCLUDED — it is too broad for a software peer
    # group (it also contains local-advertising/reviews names like Yelp that are not SaaS comps). The one
    # data/martech name it would admit (ZoomInfo) is size-excluded anyway; a firm outside these
    # sub-industries is not treated as a software peer.
})

# Default screen — the disclosed-market norm for exec-comp peer construction: the HARD gates are revenue and
# market cap (each 0.5x-2.0x of the subject) plus membership in the software/SaaS industry GROUP above.
# HEADCOUNT is deliberately NOT a hard gate — in practice it is a *secondary/soft* factor (e.g. Datadog files
# it under "secondary factors"), so it only feeds the size-fit RANK below, never membership. Every band is
# tunable; set employees_mult to a (lo, hi) pair to bring headcount back in as a hard gate if desired.
DEFAULT_CRITERIA = {
    "revenue_mult": (0.5, 2.0),
    "market_cap_mult": (0.5, 2.0),
    "employees_mult": None,    # headcount is a SOFT fit factor by default, not a hard gate
    "gics": "group",           # "group" (software/SaaS peer group) | "sector" | "subindustry" | None
    "min_criteria": None,      # None => must pass ALL active criteria; or an int N => pass >= N
}

_NUM = ("revenue_usd", "market_cap_usd", "employees", "total_assets_usd")

# Fit-rank weights — size-closeness only, revenue-weighted (revenue is the primary size anchor in
# exec comp). These ORDER the already-screened group; they never decide membership. Tunable + disclosed.
FIT_WEIGHTS = {"revenue_usd": 0.5, "market_cap_usd": 0.3, "employees": 0.2}
_LN2 = math.log(2.0)


def _closeness(co_v, subj_v):
    """1.0 == identical size to the subject; 0.0 == at the 0.5x/2.0x band edge (or beyond). Log-symmetric
    so 2x-up and 0.5x-down score the same. Clamped to [0, 1]."""
    if co_v <= 0 or subj_v <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(math.log(co_v / subj_v)) / _LN2)


def _fit(co, subj, weights=FIT_WEIGHTS):
    """Revenue-weighted size-closeness on [0, 100]. Pure ranking signal — does NOT gate membership."""
    return round(100.0 * sum(weights[k] * _closeness(co[k], subj[k]) for k in weights), 1)


class PeerDataError(ValueError):
    """The peer universe is missing or has no subject company (fail closed)."""


class PeerUniverse:
    def __init__(self, data_dir=DATA):
        path = Path(data_dir) / "peer_universe.csv"
        if not path.exists():
            raise PeerDataError(f"peer universe not found: {path}")
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None or set(reader.fieldnames) != set(REQUIRED_COLS):
                raise PeerDataError(
                    f"peer universe schema mismatch: expected columns {sorted(REQUIRED_COLS)}, "
                    f"got {sorted(reader.fieldnames or [])}")
            rows = list(reader)
        if not rows:
            raise PeerDataError("peer universe has no rows")
        try:
            for r in rows:
                for k in _NUM:
                    r[k] = int(r[k])
        except (KeyError, ValueError, TypeError) as e:
            raise PeerDataError(f"peer universe has a missing or non-numeric field: {e}") from e
        # ticker integrity: well-formed + unique. NOTE: the PEERS are intentionally REAL public companies —
        # a peer-group screen benchmarks against real comps — so real tickers/names are EXPECTED here; the
        # subject (Acme / ACMQ) is the only synthetic issuer. Provenance + as-of date for every peer figure
        # is documented in governance/real-peer-data.md. (The ISS pay screen + rTSR valuation run on a
        # separate, clearly-synthetic universe so no real name ever carries a fabricated pay/TSR number.)
        tickers = [r["ticker"] for r in rows]
        if len(tickers) != len(set(tickers)):
            dupes = sorted({t for t in tickers if tickers.count(t) > 1})
            raise PeerDataError(f"peer universe has duplicate tickers: {dupes[:5]}")
        malformed = [t for t in tickers if not _TICKER_RE.fullmatch(t)]
        if malformed:
            raise PeerDataError(f"peer universe has malformed tickers: {malformed[:5]}")
        subjects = [r for r in rows if r.get("is_subject") == "yes"]
        if len(subjects) != 1:
            raise PeerDataError(
                f"peer universe must have exactly one subject company (is_subject=yes); found {len(subjects)}")
        self.subject = subjects[0]
        # the subject's size fields anchor every band — degenerate (<=0) values would make the screen
        # meaningless, so fail closed rather than emit a nonsense peer group
        for k in ("revenue_usd", "market_cap_usd", "employees"):
            if self.subject[k] <= 0:
                raise PeerDataError(f"subject company has non-positive {k}={self.subject[k]}")
        self.candidates = [r for r in rows if r is not self.subject]

    def screen(self, criteria=None):
        """Evaluate every candidate against the screen. Returns the subject, the resolved criteria,
        every candidate with its per-criterion checks, and the peer group (passes the screen)."""
        c = {**DEFAULT_CRITERIA, **(criteria or {})}
        subj = self.subject
        active = []                                  # the criteria actually applied (for honest counts)
        if c.get("revenue_mult"):
            active.append("revenue")
        if c.get("market_cap_mult"):
            active.append("market_cap")
        if c.get("employees_mult"):
            active.append("employees")
        if c.get("gics") in ("group", "sector", "subindustry"):
            active.append("gics")
            # in GROUP mode the subject itself must be a software/SaaS business, else gating peers on a group
            # the subject isn't in is incoherent — fail closed rather than return a nonsense group
            if c["gics"] == "group" and subj["gics_subindustry"] not in SOFTWARE_PEER_GROUP:
                raise PeerDataError(
                    f"subject sub-industry {subj['gics_subindustry']!r} is not in the software/SaaS peer group")
        elif c.get("gics") is not None:
            raise PeerDataError(f"gics mode must be 'group' | 'sector' | 'subindustry' | None (got {c['gics']!r})")
        # refuse to return a meaningless "everyone is a peer" result from an unconfigured screen
        if not active:
            raise PeerDataError("screen has no active criteria — refusing to return every company as a peer")
        min_n = c.get("min_criteria")
        # strict int only — bool is a subclass of int (True==1) and floats/strings compare too, any of
        # which would silently blow membership wide open; reject anything that isn't a plain int
        if min_n is not None and (type(min_n) is not int or not (1 <= min_n <= len(active))):
            raise PeerDataError(f"min_criteria must be an int between 1 and {len(active)} (got {min_n!r})")
        # validate the size bands of the ACTIVE criteria — a malformed or inverted band would silently
        # admit nobody (or everybody); fail closed instead
        for crit, key in (("revenue", "revenue_mult"), ("market_cap", "market_cap_mult"),
                          ("employees", "employees_mult")):
            if crit not in active:
                continue
            m = c.get(key)
            if not (isinstance(m, (tuple, list)) and len(m) == 2):
                raise PeerDataError(f"{key} must be a (lo, hi) pair (got {m!r})")
            lo, hi = m
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                       for x in (lo, hi)) or lo <= 0 or hi < lo:
                # finiteness matters: (0.5, inf) would silently remove the upper size bound, and (nan, 2.0)
                # would make every comparison False — both defeat the fail-closed intent, so reject them
                raise PeerDataError(f"{key} must be finite and satisfy 0 < lo <= hi (got {m!r})")

        def band(field, mult):
            lo, hi = mult
            return subj[field] * lo, subj[field] * hi

        results = []
        for co in self.candidates:
            checks = {}
            if "revenue" in active:
                lo, hi = band("revenue_usd", c["revenue_mult"])
                checks["revenue"] = lo <= co["revenue_usd"] <= hi
            if "market_cap" in active:
                lo, hi = band("market_cap_usd", c["market_cap_mult"])
                checks["market_cap"] = lo <= co["market_cap_usd"] <= hi
            if "employees" in active:
                lo, hi = band("employees", c["employees_mult"])
                checks["employees"] = lo <= co["employees"] <= hi
            if "gics" in active:
                if c["gics"] == "group":
                    checks["gics"] = co["gics_subindustry"] in SOFTWARE_PEER_GROUP
                else:
                    key = "gics_sector" if c["gics"] == "sector" else "gics_subindustry"
                    checks["gics"] = co[key] == subj[key]
            n_pass = sum(1 for v in checks.values() if v)
            need = min_n if min_n is not None else len(active)
            is_peer = n_pass >= need
            # in GROUP mode, software/SaaS membership is a HARD prerequisite regardless of min_criteria — a
            # relaxed size gate must never admit an out-of-group company as a "software peer" (which would also
            # drive run.py's "outside size" funnel bar negative). Sector/sub-industry modes keep the uniform
            # min_criteria semantics.
            if c.get("gics") == "group" and "gics" in checks and not checks["gics"]:
                is_peer = False
            results.append({"company": co, "checks": checks, "pass_count": n_pass,
                            "is_peer": is_peer, "fit": _fit(co, subj, FIT_WEIGHTS)})

        # Two clearly-separated orderings: PEERS rank by fit (size-closeness, the recommended order);
        # non-peers fall back to revenue-closeness for the exclusions view. Ticker is the final
        # deterministic tie-break in both, so the full ordering is specified.
        peers = sorted((r for r in results if r["is_peer"]),
                       key=lambda r: (-r["fit"], r["company"]["ticker"]))
        others = sorted((r for r in results if not r["is_peer"]),
                        key=lambda r: (abs(r["company"]["revenue_usd"] - subj["revenue_usd"]),
                                       r["company"]["ticker"]))
        ordered = peers + others
        return {"subject": subj, "criteria": c, "active_criteria": active, "fit_weights": dict(FIT_WEIGHTS),
                "results": ordered, "peers": peers, "n_peers": len(peers),
                "n_candidates": len(self.candidates)}
