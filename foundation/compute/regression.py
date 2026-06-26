#!/usr/bin/env python3
"""Minimal, dependency-free Ordinary Least Squares — just what the adjusted pay-gap needs.

Pure stdlib + deterministic (same inputs -> same coefficients). This is NOT a general statistics
library; it solves the normal equations (XᵀX)β = Xᵀy by an explicit Gauss-Jordan inverse and returns
each coefficient with its standard error, so the engine can report a regression-adjusted pay gap with
a confidence interval instead of a raw mean difference. Raises on a singular design (fail closed —
better than emitting a coefficient the data can't support).
"""
from __future__ import annotations

import math


class SingularMatrixError(ValueError):
    """The design matrix is rank-deficient (e.g. a control with no variation) — no unique fit."""


def _inverse(matrix):
    """Gauss-Jordan inverse with partial pivoting. Raises SingularMatrixError if singular."""
    n = len(matrix)
    aug = [list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-10:
            raise SingularMatrixError(f"singular at column {col}")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pv = aug[col][col]
        aug[col] = [v / pv for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                f = aug[r][col]
                aug[r] = [a - f * b for a, b in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


def ols(X, y):
    """Fit y = Xβ + ε by least squares.

    X: list of n rows, each a list of p floats (include an intercept column yourself).
    y: list of n floats.
    Returns (beta, se, info) where beta[j]/se[j] are the coefficient and its standard error, and
    info has n, p, dof, r2. Standard errors use the classical σ²(XᵀX)⁻¹ (homoskedastic) estimator.
    """
    n = len(X)
    if n == 0:
        raise ValueError("no observations")
    p = len(X[0])
    if any(len(row) != p for row in X):
        raise ValueError("ragged design matrix")
    if len(y) != n:
        raise ValueError("X and y length mismatch")
    if n <= p:
        raise ValueError(f"not enough observations ({n}) for {p} parameters")

    XtX = [[0.0] * p for _ in range(p)]
    Xty = [0.0] * p
    for i in range(n):
        xi, yi = X[i], y[i]
        for a in range(p):
            Xty[a] += xi[a] * yi
            xa = xi[a]
            for b in range(a, p):
                XtX[a][b] += xa * xi[b]
    for a in range(p):                       # mirror the symmetric lower triangle
        for b in range(a):
            XtX[a][b] = XtX[b][a]

    inv = _inverse(XtX)
    beta = [sum(inv[a][b] * Xty[b] for b in range(p)) for a in range(p)]

    rss = 0.0
    ybar = sum(y) / n
    tss = sum((v - ybar) ** 2 for v in y)
    for i in range(n):
        pred = sum(X[i][a] * beta[a] for a in range(p))
        rss += (y[i] - pred) ** 2
    dof = n - p
    sigma2 = rss / dof
    se = [math.sqrt(sigma2 * inv[a][a]) if inv[a][a] > 0 else float("inf") for a in range(p)]
    r2 = 1.0 - rss / tss if tss > 0 else 0.0
    return beta, se, {"n": n, "p": p, "dof": dof, "r2": r2}
