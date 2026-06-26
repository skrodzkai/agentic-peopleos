#!/usr/bin/env python3
"""Deterministic, stdlib-only SVG chart toolkit for Agentic PeopleOS dashboards.

Every function returns an inline <svg> string drawn server-side from already-computed values — no
JavaScript, no external libraries, no network. Same inputs -> identical bytes (so a committed
dashboard can be byte-diffed in CI). Dark "skrodzkai" palette baked in to match the arm.

This module draws; it does not compute. Geometry only.
"""
from __future__ import annotations

import hashlib
import html
import re

# --- skrodzkai dark palette (explicit hex so SVG gradients render identically in screenshot + CI) ---
BG = "#000000"
INK = "#eef7ff"
MUTED = "#8db1ce"
SOFT = "#6d8294"
CYAN = "#1ba7ff"
CYAN2 = "#48c7ff"
GREEN = "#43d477"
RED = "#ff4d4f"
AMBER = "#f7b955"
INDIGO = "#7c8cff"
GRID = "rgba(141,177,206,.14)"
TRACK = "rgba(255,255,255,.06)"


def _esc(v) -> str:
    return html.escape(str(v))


# A color goes into an SVG attribute (fill/stroke), where html.escape is NOT enough — a value like
# "#fff' onload='alert(1)" would break out of the attribute. Allow ONLY a #hex literal, an rgba()/var()
# token, or a short bare color word; otherwise fall back to the cyan accent (fail safe).
_HEX = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_RGBA = re.compile(r"^rgba?\([0-9.,\s]+\)$")
_VAR = re.compile(r"^var\(--[a-z0-9-]{1,40}\)$")
_WORD = re.compile(r"^[a-zA-Z]{1,20}$")


def _safe_color(value, default=CYAN) -> str:
    v = str(value).strip()
    return v if (_HEX.match(v) or _RGBA.match(v) or _VAR.match(v) or _WORD.match(v)) else default


def _safe_id(value, default="id") -> str:
    """Sanitize a caller-supplied <defs> id namespace to [A-Za-z0-9_-] (can't break the attribute)."""
    v = re.sub(r"[^A-Za-z0-9_-]", "", str(value))
    return v or default


def _auto_uid(prefix, *parts) -> str:
    """A deterministic, content-derived id namespace: same inputs -> same id, different charts ->
    different ids. So two DEFAULT charts of the same type in one document never collide on <defs> ids."""
    return prefix + hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:8]


def _f(x: float, nd: int = 1) -> str:
    """Deterministic fixed-point string (avoids locale / float-repr drift in committed SVG)."""
    return f"{x:.{nd}f}"


def _scale(d0, d1, r0, r1):
    span = (d1 - d0) or 1
    m = (r1 - r0) / span
    return lambda v: r0 + (v - d0) * m


def _svg(w: int, h: int, body: str, extra: str = "") -> str:
    return (f"<svg viewBox='0 0 {w} {h}' style='width:100%;height:auto;display:block;overflow:visible' "
            f"xmlns='http://www.w3.org/2000/svg'{extra}>{body}</svg>")


# ============================================================ sparkline
def sparkline(series, color: str = CYAN, w: int = 84, h: int = 28) -> str:
    """Word-sized trend line + soft area + end dot (Tufte sparkline)."""
    if not series:
        return _svg(w, h, "")
    color = _safe_color(color)   # caller color goes into an SVG attribute — allowlist it
    p = 3
    lo, hi = min(series), max(series)
    x = _scale(0, len(series) - 1, p, w - p)
    y = _scale(lo, hi, h - p, p)
    pts = [(x(i), y(v)) for i, v in enumerate(series)]
    line = "M" + " L".join(f"{_f(px)} {_f(py)}" for px, py in pts)
    area = (line + f" L {_f(pts[-1][0])} {h} L {_f(pts[0][0])} {h} Z")
    ex, ey = pts[-1]
    return _svg(w, h,
                f"<path d='{area}' fill='{color}' opacity='.12'/>"
                f"<path d='{line}' fill='none' stroke='{color}' stroke-width='1.7' "
                f"stroke-linecap='round' stroke-linejoin='round'/>"
                f"<circle cx='{_f(ex)}' cy='{_f(ey)}' r='2.3' fill='{color}'/>")


# ============================================================ percentile strip (signature)
def percentile_strip(value, lo, hi, ticks, target=None, you_label="You",
                     unit_prefix="$", unit_suffix="K", uid=None) -> str:
    """The marquee signature: a market-position instrument. A gradient track from `lo` to `hi`,
    labelled percentile ticks, an optional dashed target marker, and a glowing 'you' needle.
    ticks: list of (value, label). `uid` namespaces the SVG <defs> ids so two strips can coexist;
    when omitted it is derived deterministically from the chart's content (collision-safe by default)."""
    w, h = 1000, 96
    x0, x1, ty, bar = 22, 978, 46, 18
    X = _scale(lo, hi, x0, x1)
    fmt = lambda v: f"{unit_prefix}{int(round(v))}{unit_suffix}"
    uid = _safe_id(uid) if uid else _auto_uid("ps", value, lo, hi, target, ticks)
    track, glow = f"{uid}_track", f"{uid}_glow"
    b = []
    b.append("<defs>"
             f"<linearGradient id='{track}' x1='0' y1='0' x2='1' y2='0'>"
             f"<stop offset='0' stop-color='#0c2233'/><stop offset='.5' stop-color='#0e5f86'/>"
             f"<stop offset='1' stop-color='{CYAN}'/></linearGradient>"
             f"<filter id='{glow}' x='-50%' y='-50%' width='200%' height='200%'>"
             f"<feGaussianBlur stdDeviation='3.2' result='b'/><feMerge>"
             f"<feMergeNode in='b'/><feMergeNode in='SourceGraphic'/></feMerge></filter></defs>")
    b.append(f"<rect x='{x0}' y='{ty - bar/2}' width='{x1 - x0}' height='{bar}' rx='{bar/2}' "
             f"fill='url(#{track})' opacity='.92'/>")
    # end labels
    b.append(f"<text x='{x0}' y='{ty - bar/2 - 9}' font-family=\"'JetBrains Mono',monospace\" "
             f"font-size='10.5' fill='{SOFT}'>{_esc(fmt(lo))}</text>")
    b.append(f"<text x='{x1}' y='{ty - bar/2 - 9}' text-anchor='end' "
             f"font-family=\"'JetBrains Mono',monospace\" font-size='10.5' fill='{SOFT}'>{_esc(fmt(hi))}</text>")
    # ticks
    for tv, tl in ticks:
        xx = X(tv)
        b.append(f"<line x1='{_f(xx)}' y1='{ty - bar/2 - 5}' x2='{_f(xx)}' y2='{ty + bar/2 + 5}' "
                 f"stroke='{SOFT}' stroke-width='1'/>")
        b.append(f"<text x='{_f(xx)}' y='{ty + bar/2 + 20}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='10' fill='{MUTED}'>{_esc(tl)}</text>")
    # target
    if target is not None:
        tx = X(target)
        b.append(f"<line x1='{_f(tx)}' y1='{ty - bar/2 - 13}' x2='{_f(tx)}' y2='{ty + bar/2 + 8}' "
                 f"stroke='{AMBER}' stroke-width='2' stroke-dasharray='3 3'/>")
        b.append(f"<text x='{_f(tx)}' y='{ty - bar/2 - 17}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='10.5' font-weight='700' "
                 f"fill='{AMBER}'>target {_esc(fmt(target))}</text>")
    # you needle
    yx = X(value)
    fw = 96
    fx = min(max(yx - fw / 2, x0), x1 - fw)
    b.append(f"<g filter='url(#{glow})'>"
             f"<line x1='{_f(yx)}' y1='{ty - bar/2 - 2}' x2='{_f(yx)}' y2='{ty + bar/2 + 2}' "
             f"stroke='#fff' stroke-width='3'/>"
             f"<circle cx='{_f(yx)}' cy='{ty}' r='7.5' fill='#fff' stroke='{CYAN}' stroke-width='3'/></g>")
    b.append(f"<rect x='{_f(fx)}' y='{ty + bar/2 + 8}' width='{fw}' height='23' rx='6' fill='#06222f' "
             f"stroke='{CYAN}' stroke-width='1'/>")
    b.append(f"<text x='{_f(fx + fw/2)}' y='{ty + bar/2 + 23}' text-anchor='middle' "
             f"font-family=\"'JetBrains Mono',monospace\" font-size='11' font-weight='700' "
             f"fill='{CYAN2}'>{_esc(you_label)} · {_esc(fmt(value))}</text>")
    return _svg(w, h, "".join(b))


# ============================================================ waterfall / bridge
def waterfall(steps) -> str:
    """Net-change bridge. steps: list of (label, value, kind) with kind in {total, add, sub}.
    'total' bars are absolute; add/sub float from the running balance."""
    w, h = 560, 240
    if not steps:
        return _svg(w, h, "")
    mL, mR, mT, mB = 42, 12, 20, 36
    plotW, plotH = w - mL - mR, h - mT - mB
    n = len(steps)
    step = plotW / n
    bw = step * 0.58
    vals = []
    run = 0
    for _l, v, k in steps:
        if k == "total":
            run = v
            vals.append(v)
        else:
            run += v
            vals.append(run)
    allv = vals + [s[1] for s in steps if s[2] == "total"]
    vmin, vmax = min(allv), max(allv)
    pad = max((vmax - vmin) * 0.18, 1)
    vmin, vmax = vmin - pad, vmax + pad
    Y = _scale(vmin, vmax, mT + plotH, mT)
    b = []
    gstep = max(1, round((vmax - vmin) / 4))
    g = vmin
    while g <= vmax:
        gv = round(g)
        b.append(f"<line x1='{mL}' y1='{_f(Y(gv))}' x2='{w - mR}' y2='{_f(Y(gv))}' stroke='{GRID}'/>")
        b.append(f"<text x='{mL - 6}' y='{_f(Y(gv) + 3)}' text-anchor='end' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9.5' fill='{SOFT}'>{gv}</text>")
        g += gstep
    run = 0
    for i, (label, v, k) in enumerate(steps):
        cx = mL + step * i + step / 2
        x = cx - bw / 2
        if k == "total":
            top, bot, fill, lab = Y(v), Y(vmin), CYAN, str(v)
            run = v
        else:
            start, end = run, run + v
            top, bot = Y(max(start, end)), Y(min(start, end))
            fill = GREEN if v >= 0 else RED
            lab = ("+" if v >= 0 else "") + str(v)
            run = end
        hgt = max(bot - top, 2)
        b.append(f"<rect x='{_f(x)}' y='{_f(top)}' width='{_f(bw)}' height='{_f(hgt)}' rx='3' fill='{fill}'/>")
        b.append(f"<text x='{_f(cx)}' y='{_f(top - 5)}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9.5' font-weight='700' "
                 f"fill='{INK if k == 'total' else fill}'>{_esc(lab)}</text>")
        if k != "total" and i < n - 1:
            yrun = Y(run)
            nx = mL + step * (i + 1) + step / 2 - bw / 2
            b.append(f"<line x1='{_f(cx + bw/2)}' y1='{_f(yrun)}' x2='{_f(nx)}' y2='{_f(yrun)}' "
                     f"stroke='{SOFT}' stroke-width='1' stroke-dasharray='2 2'/>")
        b.append(f"<text x='{_f(cx)}' y='{h - 12}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9.5' fill='{MUTED}'>{_esc(label)}</text>")
    return _svg(w, h, "".join(b))


# ============================================================ dual-axis line (operating leverage)
def dual_axis_line(labels, left, right, left_fmt=lambda v: f"${v}", right_fmt=lambda v: str(v), uid=None) -> str:
    """Two related series over time. left = filled cyan line (e.g. Rev/FTE); right = dashed indigo
    line (e.g. headcount). The divergence is the story. `uid` namespaces the gradient <defs> id;
    when omitted it is derived deterministically from the series (collision-safe by default)."""
    w, h = 560, 240
    if not labels and not left and not right:
        return _svg(w, h, "")                 # all-empty -> empty SVG
    if not (len(labels) == len(left) == len(right)):   # partial-empty is data-shape corruption -> raise
        raise ValueError(f"dual_axis_line: labels/left/right length mismatch "
                         f"({len(labels)}/{len(left)}/{len(right)})")
    mL, mR, mT, mB = 44, 46, 18, 30
    plotW, plotH = w - mL - mR, h - mT - mB
    X = _scale(0, len(labels) - 1, mL, w - mR)
    llo, lhi = min(left), max(left)
    rlo, rhi = min(right), max(right)
    lpad, rpad = (lhi - llo) * 0.25 or 1, (rhi - rlo) * 0.25 or 1
    Yl = _scale(llo - lpad, lhi + lpad, mT + plotH, mT)
    Yr = _scale(rlo - rpad, rhi + rpad, mT + plotH, mT)
    area_id = f"{_safe_id(uid) if uid else _auto_uid('dal', labels, left, right)}_area"
    b = [f"<defs><linearGradient id='{area_id}' x1='0' y1='0' x2='0' y2='1'>"
         f"<stop offset='0' stop-color='{CYAN}' stop-opacity='.26'/>"
         f"<stop offset='1' stop-color='{CYAN}' stop-opacity='0'/></linearGradient></defs>"]
    # left gridlines + axis
    for t in range(5):
        v = (llo - lpad) + (lhi + lpad - (llo - lpad)) * t / 4
        b.append(f"<line x1='{mL}' y1='{_f(Yl(v))}' x2='{w - mR}' y2='{_f(Yl(v))}' stroke='{GRID}'/>")
        b.append(f"<text x='{mL - 6}' y='{_f(Yl(v) + 3)}' text-anchor='end' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{SOFT}'>{_esc(left_fmt(int(round(v))))}</text>")
    # right axis labels
    for t in range(4):
        v = (rlo - rpad) + (rhi + rpad - (rlo - rpad)) * t / 3
        b.append(f"<text x='{w - mR + 6}' y='{_f(Yr(v) + 3)}' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{INDIGO}'>{_esc(right_fmt(int(round(v))))}</text>")
    # right (headcount) dashed
    dh = "M" + " L".join(f"{_f(X(i))} {_f(Yr(v))}" for i, v in enumerate(right))
    b.append(f"<path d='{dh}' fill='none' stroke='{INDIGO}' stroke-width='2' stroke-dasharray='5 4' opacity='.85'/>")
    # left (rev/fte) area + line
    dl = "M" + " L".join(f"{_f(X(i))} {_f(Yl(v))}" for i, v in enumerate(left))
    area = dl + f" L {_f(X(len(left)-1))} {mT + plotH} L {_f(X(0))} {mT + plotH} Z"
    b.append(f"<path d='{area}' fill='url(#{area_id})'/>")
    b.append(f"<path d='{dl}' fill='none' stroke='{CYAN}' stroke-width='2.4' stroke-linecap='round'/>")
    for i, v in enumerate(left):
        b.append(f"<circle cx='{_f(X(i))}' cy='{_f(Yl(v))}' r='3' fill='{BG}' stroke='{CYAN}' stroke-width='2'/>")
        if i % 2 == 0 or i == len(labels) - 1:
            b.append(f"<text x='{_f(X(i))}' y='{h - 10}' text-anchor='middle' "
                     f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{MUTED}'>{_esc(labels[i])}</text>")
    b.append(f"<text x='{_f(X(len(left)-1))}' y='{_f(Yl(left[-1]) - 9)}' text-anchor='end' "
             f"font-family=\"'JetBrains Mono',monospace\" font-size='10' font-weight='700' "
             f"fill='{CYAN2}'>{_esc(left_fmt(left[-1]))}</text>")
    return _svg(w, h, "".join(b))


# ============================================================ thresholded histogram
def histogram(bins, labels, highlight=None, sub_first=False) -> str:
    """Distribution where the shape + tails are the message. bins: list of numbers; labels: x labels.
    highlight: dict bin_index->color (e.g. first=below-min red, last=above-max amber). sub_first colors
    the first bar red (span 'sub-scale' use)."""
    w, h = 560, 220
    if not bins and not labels:
        return _svg(w, h, "")                 # all-empty -> empty SVG
    if len(bins) != len(labels):              # partial-empty / mismatch is corruption -> raise
        raise ValueError(f"histogram: bins/labels length mismatch ({len(bins)}/{len(labels)})")
    mL, mR, mT, mB = 16, 16, 12, 42
    plotW, plotH = w - mL - mR, h - mT - mB
    n = len(bins)
    gap = 5
    bw = (plotW - gap * (n - 1)) / n
    vmax = max(bins) * 1.14 or 1
    Y = _scale(0, vmax, mT + plotH, mT)
    highlight = highlight or {}
    b = []
    for i, v in enumerate(bins):
        x = mL + i * (bw + gap)
        fill = _safe_color(highlight.get(i, GREEN if not sub_first else CYAN))   # allowlist caller color
        if sub_first and i == 0:
            fill = RED
        yt = Y(v)
        hgt = max(mT + plotH - yt, 1)
        op = ".96" if i in highlight or (sub_first and i == 0) else ".82"
        b.append(f"<rect x='{_f(x)}' y='{_f(yt)}' width='{_f(bw)}' height='{_f(hgt)}' rx='2.5' "
                 f"fill='{fill}' opacity='{op}'/>")
        b.append(f"<text x='{_f(x + bw/2)}' y='{_f(yt - 5)}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{MUTED}'>{_esc(_trim(v))}</text>")
        b.append(f"<text x='{_f(x + bw/2)}' y='{h - 24}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='8.5' fill='{SOFT}'>{_esc(labels[i])}</text>")
    return _svg(w, h, "".join(b))


def _trim(v):
    return int(v) if float(v).is_integer() else v


# ============================================================ forest plot (adjusted pay gap)
def forest_plot(rows, lo=-9, hi=3) -> str:
    """Effect-size-with-uncertainty. rows: list of dict(group, adj, ci_lo, ci_hi, raw).
    A point estimate + 95% CI whisker; a ghost 'raw' marker; red when the CI excludes parity (0)."""
    w = 560
    rows = list(rows)
    if not rows:
        return _svg(w, 56, "")
    h = 56 + 46 * len(rows)
    mL, mR, mT, mB = 138, 60, 20, 30
    plotW, plotH = w - mL - mR, h - mT - mB
    X = _scale(lo, hi, mL, w - mR)
    rh = plotH / len(rows)
    b = []
    # parity line
    b.append(f"<line x1='{_f(X(0))}' y1='{mT - 4}' x2='{_f(X(0))}' y2='{mT + plotH}' "
             f"stroke='{MUTED}' stroke-width='1.4'/>")
    b.append(f"<text x='{_f(X(0))}' y='{mT - 8}' text-anchor='middle' "
             f"font-family=\"'JetBrains Mono',monospace\" font-size='9.5' font-weight='700' "
             f"fill='{MUTED}'>parity (0%)</text>")
    tick = lo
    while tick <= hi:
        b.append(f"<line x1='{_f(X(tick))}' y1='{mT + plotH}' x2='{_f(X(tick))}' y2='{mT + plotH + 4}' stroke='{SOFT}'/>")
        b.append(f"<text x='{_f(X(tick))}' y='{h - 12}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{SOFT}'>{tick}%</text>")
        tick += 2
    for i, d in enumerate(rows):
        cy = mT + rh * i + rh / 2
        sig = d["ci_lo"] > 0 or d["ci_hi"] < 0
        col = RED if sig else CYAN
        b.append(f"<text x='{mL - 12}' y='{_f(cy + 3)}' text-anchor='end' "
                 f"font-family='Inter,sans-serif' font-size='11.5' fill='{INK}'>{_esc(d['group'])}</text>")
        # raw ghost
        b.append(f"<circle cx='{_f(X(d['raw']))}' cy='{_f(cy)}' r='4.5' fill='{SOFT}' opacity='.7'/>")
        b.append(f"<text x='{_f(X(d['raw']))}' y='{_f(cy - 9)}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='8.5' fill='{SOFT}'>raw {_esc(d['raw'])}%</text>")
        # CI whisker
        b.append(f"<line x1='{_f(X(d['ci_lo']))}' y1='{_f(cy)}' x2='{_f(X(d['ci_hi']))}' y2='{_f(cy)}' "
                 f"stroke='{col}' stroke-width='2'/>")
        for e in (d["ci_lo"], d["ci_hi"]):
            b.append(f"<line x1='{_f(X(e))}' y1='{_f(cy - 5)}' x2='{_f(X(e))}' y2='{_f(cy + 5)}' "
                     f"stroke='{col}' stroke-width='2'/>")
        # adjusted point
        b.append(f"<circle cx='{_f(X(d['adj']))}' cy='{_f(cy)}' r='5.5' fill='{col}' stroke='{BG}' stroke-width='1.5'/>")
        b.append(f"<text x='{w - mR + 8}' y='{_f(cy + 3)}' font-family=\"'JetBrains Mono',monospace\" "
                 f"font-size='10.5' font-weight='700' fill='{col}'>{'+' if d['adj'] > 0 else ''}{_esc(d['adj'])}%</text>")
    return _svg(w, h, "".join(b))


# ============================================================ 9-box talent grid
def heatmap_9box(matrix, row_labels=("High", "Med", "Low"), col_labels=("Low", "Med", "High")) -> str:
    """Performance (x) x potential (y) heatmap. matrix: 3x3 counts, row 0 = High potential .. row 2 =
    Low; col 0 = Low performance .. col 2 = High. Cell shade encodes the talent 'value' weight; a
    bubble encodes the count."""
    w, h = 440, 286
    if (len(matrix) != 3
            or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in matrix)):
        raise ValueError("heatmap_9box: matrix must be exactly 3x3 (three rows of length 3)")
    mL, mT, cell, gap = 60, 14, 106, 6
    ch = cell / 1.5
    total = sum(sum(r) for r in matrix) or 1
    mx = max(max(r) for r in matrix) or 1
    weight = [[2, 4, 5], [1, 3, 4], [0, 2, 3]]
    labels = {(0, 2): "STARS", (1, 1): "CORE", (2, 0): "AT RISK"}
    b = []
    for r in range(3):
        for c in range(3):
            x = mL + c * (cell + gap)
            y = mT + r * (ch + gap)
            t = weight[r][c] / 5
            star = (r == 0 and c == 2)
            b.append(f"<rect x='{_f(x)}' y='{_f(y)}' width='{cell}' height='{_f(ch)}' rx='8' "
                     f"fill='rgba(27,167,255,{_f(0.05 + t*0.34, 3)})' "
                     f"stroke='{CYAN if star else 'rgba(141,177,206,.18)'}' stroke-width='{2 if star else 1}'/>")
            cnt = matrix[r][c]
            br = 8 + (cnt / mx) * 19
            b.append(f"<circle cx='{_f(x + cell/2)}' cy='{_f(y + ch/2 - 5)}' r='{_f(br)}' "
                     f"fill='{CYAN}' opacity='{_f(0.18 + t*0.5, 2)}'/>")
            b.append(f"<text x='{_f(x + cell/2)}' y='{_f(y + ch/2)}' text-anchor='middle' "
                     f"font-family='Inter,sans-serif' font-size='16' font-weight='700' fill='{INK}'>{cnt}</text>")
            b.append(f"<text x='{_f(x + cell/2)}' y='{_f(y + ch/2 + 15)}' text-anchor='middle' "
                     f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{MUTED}'>{_f(100*cnt/total,0)}%</text>")
            if (r, c) in labels:
                b.append(f"<text x='{_f(x + 7)}' y='{_f(y + 13)}' font-family=\"'JetBrains Mono',monospace\" "
                         f"font-size='8.5' font-weight='700' fill='{CYAN2 if star else SOFT}'>{_esc(labels[(r,c)])}</text>")
    gw = 3 * cell + 2 * gap
    gh = 3 * ch + 2 * gap
    b.append(f"<text x='{_f(mL + gw/2)}' y='{_f(mT + gh + 24)}' text-anchor='middle' "
             f"font-family=\"'JetBrains Mono',monospace\" font-size='10' font-weight='700' fill='{MUTED}'>Performance →</text>")
    b.append(f"<text x='16' y='{_f(mT + gh/2)}' text-anchor='middle' transform='rotate(-90 16 {_f(mT + gh/2)})' "
             f"font-family=\"'JetBrains Mono',monospace\" font-size='10' font-weight='700' fill='{MUTED}'>Potential →</text>")
    for i, t in enumerate(col_labels):
        b.append(f"<text x='{_f(mL + i*(cell+gap) + cell/2)}' y='{_f(mT + gh + 11)}' text-anchor='middle' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{SOFT}'>{_esc(t)}</text>")
    for i, t in enumerate(row_labels):
        b.append(f"<text x='{mL - 8}' y='{_f(mT + i*(ch+gap) + ch/2)}' text-anchor='end' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9' fill='{SOFT}'>{_esc(t)}</text>")
    return _svg(w, h, "".join(b))


# ============================================================ org diamond (managers vs ICs by level)
def org_diamond(rows, mgr_color=CYAN, ic_color="#345a7d"):
    """Centered org-shape (population-pyramid) bars: one row per level, senior at the top. Each bar is
    centered on a vertical axis with a manager-colored CORE and IC-colored FLANKS; total bar length =
    headcount at that level. A senior-balanced org bulges in the middle (a diamond); a junior-heavy one
    tapers to a triangle. rows: list of (label, managers, ics), ordered senior -> junior."""
    w = 560
    rows = list(rows)
    if not rows:
        return _svg(w, 180, "")
    mgr_color, ic_color = _safe_color(mgr_color), _safe_color(ic_color)
    mL, mR, mT, rowh, gap = 46, 60, 14, 22, 12
    n = len(rows)
    h = mT + n * (rowh + gap) + 6
    plotW = w - mL - mR
    cx = mL + plotW / 2
    maxtot = max((m + i for _l, m, i in rows), default=1) or 1
    px = plotW / maxtot
    b = [f"<line x1='{_f(cx)}' y1='{mT - 4}' x2='{_f(cx)}' y2='{_f(mT + n*(rowh+gap) - gap + 4)}' "
         f"stroke='{GRID}' stroke-dasharray='2 3'/>"]
    for idx, (label, mgr, ic) in enumerate(rows):
        total = mgr + ic
        y = mT + idx * (rowh + gap)
        fh, mh = total * px / 2, mgr * px / 2
        if fh - mh > 0.4:                                  # IC flanks (left + right)
            b.append(f"<rect x='{_f(cx-fh)}' y='{_f(y)}' width='{_f(fh-mh)}' height='{rowh}' rx='2' fill='{ic_color}'/>")
            b.append(f"<rect x='{_f(cx+mh)}' y='{_f(y)}' width='{_f(fh-mh)}' height='{rowh}' rx='2' fill='{ic_color}'/>")
        if mh > 0.4:                                       # manager core
            b.append(f"<rect x='{_f(cx-mh)}' y='{_f(y)}' width='{_f(mh*2)}' height='{rowh}' rx='2' fill='{mgr_color}'/>")
        b.append(f"<text x='{mL-10}' y='{_f(y+rowh/2+3)}' text-anchor='end' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='10' font-weight='700' fill='{MUTED}'>{_esc(label)}</text>")
        b.append(f"<text x='{_f(cx+fh+7)}' y='{_f(y+rowh/2+3)}' "
                 f"font-family=\"'JetBrains Mono',monospace\" font-size='9.5' fill='{SOFT}'>{_esc(total)}</text>")
    return _svg(w, h, "".join(b))
