#!/usr/bin/env python3
"""Known-answer evals for the stdlib OLS used by the adjusted pay gap.
Run: python foundation/compute/tests/test_regression.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute.regression import ols, _inverse, SingularMatrixError  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


# ---- exact linear fit: y = 2 + 3x recovers beta = [2, 3] with ~0 residual ----
X = [[1.0, x] for x in range(1, 11)]
y = [2 + 3 * x for x in range(1, 11)]
beta, se, info = ols(X, y)
ok(abs(beta[0] - 2) < 1e-9 and abs(beta[1] - 3) < 1e-9, "OLS recovers the exact slope/intercept")
ok(info["r2"] > 0.999999, "a perfect linear fit has r2 == 1")
ok(se[1] < 1e-6, "a zero-residual fit has ~0 standard error")

# ---- multiple regression: y = 1 + 2*a - 1*b ----
import itertools  # noqa: E402
rows, ys = [], []
for a, b in itertools.product(range(5), range(5)):
    rows.append([1.0, float(a), float(b)])
    ys.append(1 + 2 * a - 1 * b)
beta, se, info = ols(rows, ys)
ok(abs(beta[0] - 1) < 1e-9 and abs(beta[1] - 2) < 1e-9 and abs(beta[2] + 1) < 1e-9,
   "OLS recovers multi-coefficient exact fit")

# ---- noisy fit: slope is recovered approximately, SE is positive and finite ----
Xn = [[1.0, float(i)] for i in range(50)]
yn = [3.0 + 1.5 * i + (1 if i % 2 else -1) for i in range(50)]   # deterministic +/-1 wobble
b2, s2, info2 = ols(Xn, yn)
ok(abs(b2[1] - 1.5) < 0.05 and 0 < s2[1] < 1, "noisy fit recovers slope with a finite positive SE")
ok(0 < info2["r2"] <= 1, "r2 is in (0, 1] for a noisy fit")

# ---- inverse round-trips (A · A⁻¹ ≈ I) ----
A = [[4.0, 7.0], [2.0, 6.0]]
inv = _inverse(A)
prod = [[sum(A[i][k] * inv[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
ok(abs(prod[0][0] - 1) < 1e-9 and abs(prod[1][1] - 1) < 1e-9
   and abs(prod[0][1]) < 1e-9 and abs(prod[1][0]) < 1e-9, "matrix inverse round-trips to identity")

# ---- fail closed on a singular design (collinear column) ----
try:
    ols([[1.0, 1.0], [1.0, 1.0], [1.0, 1.0]], [1.0, 2.0, 3.0])
    ok(False, "a singular/collinear design should raise")
except (SingularMatrixError, ValueError):
    ok(True, "OLS fails closed on a singular design")

# ---- guards: too few observations, ragged matrix ----
try:
    ols([[1.0, 2.0]], [1.0])
    ok(False, "n<=p should raise")
except ValueError:
    ok(True, "OLS rejects n <= p")
try:
    ols([[1.0, 2.0], [1.0]], [1.0, 2.0])
    ok(False, "ragged matrix should raise")
except ValueError:
    ok(True, "OLS rejects a ragged design matrix")

print(f"OK — {passed} regression checks passed.")
