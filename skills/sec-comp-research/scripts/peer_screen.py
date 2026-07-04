#!/usr/bin/env python3
"""Build a compensation peer group by a transparent, disclosed-market screen. Standard library only.

The screen (the defensible norm a compensation committee uses):
  HARD gates  : same industry group  AND  revenue within 0.5-2.0x  AND  market cap within 0.5-2.0x
  SOFT factor : headcount (shapes the size-fit RANK, never membership)
Then rank the in-band group by a revenue-weighted size-closeness fit (100 = identical size, 0 = band edge).
Membership is defensible on one line: "same industry, within 0.5-2.0x our size."

    python3 peer_screen.py --demo
    python3 peer_screen.py --subject "Acme,852,6400,software" --peers peers.csv
    python3 peer_screen.py --subject "Acme,852,6400,software,2400" --peers peers.csv   # 5th field = employees
      # --subject: name,rev_musd,cap_musd,industry[,employees]  (revenue & market cap in $ MILLIONS)
      # peers.csv columns: ticker,name,revenue_musd,market_cap_musd,industry[,employees]
      # headcount only shapes the fit RANK when BOTH the subject and a peer supply it (never gates membership)
"""
from __future__ import annotations

import csv
import math
import sys

REV_MULT = (0.5, 2.0)
CAP_MULT = (0.5, 2.0)
FIT_W = {"revenue": 0.5, "market_cap": 0.3, "employees": 0.2}   # revenue-weighted size fit
_LN2 = math.log(2.0)
_REQUIRED_PEER_COLS = ("ticker", "name", "revenue_musd", "market_cap_musd", "industry")


class ScreenError(ValueError):
    """A user-facing input error (bad CSV / bad --subject). Printed as one clean line, never a traceback."""


def _pos(v, ctx):
    """A finite POSITIVE number ($millions). Rejects non-numeric, NaN, inf, zero, and negative — a size
    screen on nan/inf/<=0 would silently produce garbage bands or fits, so fail closed instead."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ScreenError(f"{ctx}: must be a number (got {v!r})")
    if not math.isfinite(f) or f <= 0:
        raise ScreenError(f"{ctx}: must be a finite positive $millions value (got {v!r})")
    return f


def _closeness(co_v, subj_v):
    """1.0 == identical size; 0.0 == at/beyond the 0.5x/2.0x band edge. Log-symmetric, clamped [0,1]."""
    if co_v <= 0 or subj_v <= 0:
        return 0.0
    d = abs(math.log(co_v / subj_v)) / _LN2   # 0 at parity, 1 at 2x or 0.5x
    return max(0.0, 1.0 - d)


def _fit(co, subj):
    parts, wsum = 0.0, 0.0
    for k, w in FIT_W.items():
        cv, sv = co.get(k), subj.get(k)
        if cv is None or sv is None:
            continue
        parts += w * _closeness(cv, sv)
        wsum += w
    return round(100.0 * parts / wsum, 1) if wsum else 0.0


def screen(subject, candidates, rev_mult=REV_MULT, cap_mult=CAP_MULT):
    """Return the screen result: each candidate with per-criterion pass/fail, and the fit-ranked peer group.
    A candidate is a peer iff it passes ALL hard gates (industry + revenue band + market-cap band)."""
    sg = str(subject.get("industry", "")).strip().lower()
    if not sg:
        raise ScreenError("subject industry must be non-empty (a blank industry cannot gate a peer group)")
    subj_rev = _pos(subject.get("revenue"), "subject revenue")            # fail closed on nan/inf/<=0
    subj_cap = _pos(subject.get("market_cap"), "subject market cap")
    rlo, rhi = subj_rev * rev_mult[0], subj_rev * rev_mult[1]
    clo, chi = subj_cap * cap_mult[0], subj_cap * cap_mult[1]
    results = []
    for c in candidates:
        cind = str(c.get("industry", "")).strip().lower()
        checks = {
            "industry": bool(cind) and cind == sg,                        # a blank candidate never matches
            "revenue": rlo <= c["revenue"] <= rhi,
            "market_cap": clo <= c["market_cap"] <= chi,
        }
        results.append({"company": c, "checks": checks, "is_peer": all(checks.values()), "fit": _fit(c, subject)})
    peers = sorted((r for r in results if r["is_peer"]), key=lambda r: (-r["fit"], r["company"].get("ticker", "")))
    return {"subject": subject, "results": results, "peers": peers,
            "bands": {"revenue": (rlo, rhi), "market_cap": (clo, chi)}}


def _load_peers(path):
    """Load a peers CSV, failing with a CLEAN ScreenError (never a raw traceback) on a missing file,
    a missing required column, a non-numeric size, or an empty data set."""
    try:
        fh = open(path, newline="", encoding="utf-8")
    except OSError as e:
        raise ScreenError(f"cannot open peers file {path!r}: {e}")
    out = []
    with fh:
        reader = csv.DictReader(fh)
        fnames = reader.fieldnames or []
        # a DUPLICATE header silently collapses in DictReader (last value wins, data lost) — reject it
        dups = sorted({c for c in fnames if fnames.count(c) > 1})
        if dups:
            raise ScreenError(f"peers CSV has duplicate header(s): {', '.join(dups)}")
        missing = [c for c in _REQUIRED_PEER_COLS if c not in fnames]
        if missing:
            raise ScreenError(f"peers CSV is missing column(s): {', '.join(missing)}. Required header: "
                              f"ticker,name,revenue_musd,market_cap_musd,industry[,employees]")
        for i, r in enumerate(reader, start=2):
            rev = _pos(r["revenue_musd"], f"peers CSV line {i} revenue_musd")
            cap = _pos(r["market_cap_musd"], f"peers CSV line {i} market_cap_musd")
            emp = _pos(r["employees"], f"peers CSV line {i} employees") if (r.get("employees") or "").strip() else None
            out.append({"ticker": r.get("ticker", ""), "name": r.get("name", ""),
                        "revenue": rev, "market_cap": cap, "industry": r.get("industry", ""), "employees": emp})
    if not out:
        raise ScreenError(f"peers CSV {path!r} has a header but no data rows")
    return out


_DEMO_SUBJECT = {"ticker": "SUBJ", "name": "Example SaaS Co", "revenue": 852, "market_cap": 6400,
                 "industry": "software", "employees": 2400}
_DEMO_PEERS = [
    {"ticker": "AAA", "name": "Alpha Cloud", "revenue": 995, "market_cap": 6040, "industry": "software", "employees": 1700},
    {"ticker": "BBB", "name": "Beta Systems", "revenue": 729, "market_cap": 5940, "industry": "software", "employees": 2083},
    {"ticker": "CCC", "name": "Gamma Data", "revenue": 1450, "market_cap": 4030, "industry": "software", "employees": 2364},
    {"ticker": "DDD", "name": "Delta Payments", "revenue": 6153, "market_cap": 16710, "industry": "fintech", "employees": 6500},
    {"ticker": "EEE", "name": "Eps Micro", "revenue": 240, "market_cap": 450, "industry": "software", "employees": 966},
]


def _fmt(v):
    return f"${v/1000:.1f}B" if v >= 1000 else f"${v:.0f}M"


def _print(res):
    s = res["subject"]
    rb, cb = res["bands"]["revenue"], res["bands"]["market_cap"]
    print(f"Subject: {s.get('name', s.get('ticker'))} — {_fmt(s['revenue'])} rev · {_fmt(s['market_cap'])} cap · {s.get('industry')}")
    print(f"Screen : same industry · revenue {_fmt(rb[0])}-{_fmt(rb[1])} · market cap {_fmt(cb[0])}-{_fmt(cb[1])}")
    print(f"\nPeer group ({len(res['peers'])} of {len(res['results'])} screened), best size-fit first:")
    for p in res["peers"]:
        c = p["company"]
        print(f"  {c.get('ticker',''):5s} {c.get('name',''):20s} {_fmt(c['revenue']):>8s} rev  {_fmt(c['market_cap']):>8s} cap  fit {p['fit']:.0f}")
    excl = [r for r in res["results"] if not r["is_peer"]]
    if excl:
        print("\nExcluded (failing criterion):")
        for r in excl:
            why = [k for k, v in r["checks"].items() if not v]
            print(f"  {r['company'].get('ticker',''):5s} {r['company'].get('name',''):20s} — fails: {', '.join(why)}")


def _main(argv):
    flags = {a for a in argv if a.startswith("--")}
    if "--help" in flags or (not argv):
        print(__doc__)
        return 0
    if "--demo" in flags:
        _print(screen(_DEMO_SUBJECT, _DEMO_PEERS))
        return 0
    try:
        subj, peers = None, []
        for i, a in enumerate(argv):
            if a == "--subject":
                if i + 1 >= len(argv):
                    raise ScreenError("--subject needs a value: 'name,rev_musd,cap_musd,industry[,employees]'")
                parts = [p.strip() for p in argv[i + 1].split(",")]
                if len(parts) not in (4, 5):
                    raise ScreenError("--subject must be 'name,rev_musd,cap_musd,industry[,employees]' (4 or 5 "
                                      "comma-separated fields; $millions), e.g. 'Acme,852,6400,software,2400' "
                                      f"— got {argv[i + 1]!r}")
                name, rev, cap, ind = parts[:4]
                # headcount only shapes the fit if BOTH subject and a peer supply it — pass a 5th field to use it
                emp = _pos(parts[4], "--subject employees") if len(parts) == 5 and parts[4] else None
                subj = {"ticker": name, "name": name, "employees": emp,
                        "revenue": _pos(rev, "--subject revenue"),
                        "market_cap": _pos(cap, "--subject market cap"), "industry": ind}
            elif a == "--peers":
                if i + 1 >= len(argv):
                    raise ScreenError("--peers needs a file path")
                peers = _load_peers(argv[i + 1])
        if subj is None or not peers:
            raise ScreenError("supply BOTH --subject 'name,rev_musd,cap_musd,industry' and --peers file.csv "
                              "(or --demo for a worked example)")
        _print(screen(subj, peers))
        return 0
    except ScreenError as e:
        print(f"peer_screen: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
