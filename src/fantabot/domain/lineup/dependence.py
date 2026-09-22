"""Same-team dependence: a Gaussian copula over per-role residual marginals. Pure.

Teammates' scores move together: a clean sheet lifts the keeper and every defender, and a
rout sinks them all. Measured on the lega's history (SPEC A11) the coupling is small but
real: +0.10 pooled over every same-team pair, and +0.21 between a keeper and his defenders.
Players of different clubs are independent; the negative coupling with *opponents* in the
same match is a recorded bias of v1 (A11).

**The fit** (SPEC A25). A residual is `z = (y - μ)/sigma_tilde`, one per appearance (`residuals`).
Two players are paired when they played for the same club on the same day. A role-pair
class — 15 over the Mantra macro roles, the projection's — gets a **target**: the Pearson
correlation of its pairs' residuals, shrunk toward 0 as `r·n/(n + n0)`, n its pair count and
n0 a declared prior of 100 pseudo-pairs, which leaves a class of thousands of pairs alone
and pulls a rare one (two keepers in one match: 16 pairs) to near 0.

The copula rho is then **moment-matched**: the rho whose *output* Pearson, through the class's
two marginals, equals the target — because the variance of a team's total is made of those
Pearson covariances. Neither obvious estimator does that. The residuals are skewed (+1.6 to
+2.4 for outfield roles, from +3 goal jumps) and their coupling is stronger in the body than
in the goal tail, so on the lega's data the raw Pearson fed in as rho comes out ~20% low, and
the normal-scores rho (the textbook copula fit) ~50% high; matched, every class lands within
±0.003. The output Pearson at rho is the Hermite series `Σ rho^k·aₖ·bₖ` of the two marginals, and
for a step marginal each coefficient is exact, since `∫Heₖφ = -[Heₖ₋₁φ]`; the series is
monotone in rho, so bisection solves it. No randomness in the fit. These are slow-moving; the
backtest fits them on strictly earlier seasons.

**The marginals** are each role's residuals, standardized to mean 0 and variance 1, so a
draw scaled by a player's sigma_tilde and shifted by his μ has exactly his mean and spread, while
the shape — the skew — is the role's (A17(2)). A role with no residuals draws from the pool
of every role.

**The draw.** R has 1 on its diagonal, the class correlation between two players of the
same club, and 0 otherwise. A matrix built from class numbers need not be a correlation
matrix, so it is repaired: negative eigenvalues clipped, then rescaled to a unit diagonal.
Normals are drawn with R, mapped through Φ to uniforms, then to each role's marginal by its
empirical quantile. The `Generator` is injected; the module owns no randomness and reads no
clock.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations

import numpy as np
import numpy.typing as npt
from scipy.special import ndtr, ndtri

from fantabot.domain.lineup.projection import Observation, Projection, Target

Floats = npt.NDArray[np.float64]
Pair = tuple[str, str]

#: The pool of every role, for a role with no residuals of its own.
POOLED = "*"
#: An eigenvalue this small is 0: it keeps a correlation of exactly 1 exact.
_EIGEN_FLOOR = 1e-12
#: Terms of the Hermite series. The tail after K is below |rho|^(K+1): 1e-9 at rho = 0.6.
HERMITE_TERMS = 40


@dataclass(frozen=True, slots=True)
class DependenceConfig:
    #: n0: pseudo-pairs of zero correlation each class is shrunk toward.
    shrink_pairs: float = 100.0

    def __post_init__(self) -> None:
        if math.isnan(self.shrink_pairs) or self.shrink_pairs < 0:
            raise ValueError(f"shrink_pairs must be >= 0, got {self.shrink_pairs}")


@dataclass(frozen=True, slots=True)
class Residual:
    """One appearance's standardized residual `(y - μ)/sigma_tilde`, in its club's match."""

    macro: str
    club: str
    played_on: date
    z: float


@dataclass(frozen=True, slots=True)
class Member:
    """One player in a draw."""

    macro: str
    #: `None` when his club is unknown: he is then independent of everyone.
    club: str | None
    mu: float
    sigma_tilde: float


@dataclass(frozen=True)
class Dependence:
    #: The copula rho per class, keyed by the sorted role pair: it reproduces `pearson`.
    rho: Mapping[Pair, float]
    #: The target per class: the observed Pearson of the pairs' residuals, shrunk.
    pearson: Mapping[Pair, float]
    pairs: Mapping[Pair, int]
    #: Per role, and under `POOLED`: residuals standardized to mean 0, variance 1, sorted.
    marginals: Mapping[str, Floats]
    #: The observed Pearson over every same-team pair, unshrunk.
    pooled: float

    def correlation(self, a: str, b: str) -> float:
        return self.rho.get(_pair(a, b), 0.0)

    def marginal(self, macro: str) -> Floats:
        return self.marginals.get(macro, self.marginals[POOLED])


def residuals(
    history: Iterable[Observation],
    projections: Mapping[int, Projection],
    players: Mapping[int, Target],
) -> list[Residual]:
    """`(y - μ)/sigma_tilde` for every appearance of a projected player whose club resolved."""
    out: list[Residual] = []
    for o in history:
        projection = projections.get(o.player_id)
        if projection is None or o.club is None:
            continue
        z = (o.score - projection.mu) / math.sqrt(projection.sigma_tilde2)
        out.append(Residual(players[o.player_id].macro, o.club, o.played_on, z))
    return out


def fit(
    history: Iterable[Residual], *, config: DependenceConfig = DependenceConfig()
) -> Dependence:
    """Each class's shrunk Pearson, the copula rho that reproduces it, and the per-role
    marginals. Raises when there are not two residuals to standardize."""
    rows = list(history)
    marginals = _marginals(rows)
    matches: dict[tuple[str, date], list[Residual]] = defaultdict(list)
    for r in rows:
        matches[r.club, r.played_on].append(r)

    by_class: dict[Pair, list[tuple[float, float]]] = defaultdict(list)
    pairs: dict[Pair, int] = defaultdict(int)
    every: list[tuple[float, float]] = []
    for members in matches.values():
        for one, other in combinations(members, 2):
            forward, backward = (one.z, other.z), (other.z, one.z)
            cls = _pair(one.macro, other.macro)
            pairs[cls] += 1
            # Both orders where the class is one role, so its two columns are symmetric.
            if one.macro == other.macro:
                by_class[cls] += [forward, backward]
            else:
                by_class[cls].append(forward if one.macro < other.macro else backward)
            every += [forward, backward]

    coefficients = {role: _hermite(grid) for role, grid in marginals.items()}
    pearson: dict[Pair, float] = {}
    rho: dict[Pair, float] = {}
    for (a, b), xy in by_class.items():
        n = pairs[a, b]
        pearson[a, b] = _pearson(xy) * n / (n + config.shrink_pairs)
        rho[a, b] = _match(
            pearson[a, b],
            coefficients.get(a, coefficients[POOLED]),
            coefficients.get(b, coefficients[POOLED]),
        )
    return Dependence(
        rho=rho, pearson=pearson, pairs=dict(pairs), marginals=marginals,
        pooled=_pearson(every),
    )


def output_correlation(rho: float, a: Floats, b: Floats) -> float:
    """The Pearson correlation a Gaussian copula at `rho` produces through the sorted
    marginals `a` and `b`, each drawn as `draw` draws it."""
    return _series(rho, _hermite(a), _hermite(b))


def correlation_matrix(members: Sequence[Member], dep: Dependence) -> Floats:
    """R: 1 on the diagonal, the class correlation within a club, 0 across clubs, then
    repaired to a correlation matrix."""
    k = len(members)
    r = np.eye(k)
    for i, j in combinations(range(k), 2):
        a, b = members[i], members[j]
        if a.club is not None and a.club == b.club:
            r[i, j] = r[j, i] = dep.correlation(a.macro, b.macro)
    return repair(r)


def repair(matrix: Floats) -> Floats:
    """The matrix itself when it is positive semi-definite; otherwise its negative
    eigenvalues clipped to 0 and the result rescaled to a unit diagonal."""
    values, vectors = np.linalg.eigh(matrix)
    if values.min() >= -_EIGEN_FLOOR:
        return matrix
    clipped = (vectors * np.clip(values, 0.0, None)) @ vectors.T
    scale = np.sqrt(np.diag(clipped))
    out: Floats = clipped / np.outer(scale, scale)
    np.fill_diagonal(out, 1.0)
    return (out + out.T) / 2.0


def draw(
    members: Sequence[Member], dep: Dependence, *, rng: np.random.Generator, n: int
) -> Floats:
    """`n` joint draws of every member's score, shape `(n, len(members))`."""
    r = correlation_matrix(members, dep)
    values, vectors = np.linalg.eigh(r)
    factor = vectors * np.sqrt(np.where(values > _EIGEN_FLOOR, values, 0.0))
    u = ndtr(rng.standard_normal((n, len(members))) @ factor.T)
    out = np.empty((n, len(members)))
    for j, m in enumerate(members):
        grid = dep.marginal(m.macro)
        index = np.minimum((u[:, j] * len(grid)).astype(np.intp), len(grid) - 1)
        out[:, j] = m.mu + m.sigma_tilde * grid[index]
    return out


def _pair(a: str, b: str) -> Pair:
    return (a, b) if a <= b else (b, a)


def _marginals(rows: Sequence[Residual]) -> dict[str, Floats]:
    by_role: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_role[r.macro].append(r.z)
    pooled = _standardized([r.z for r in rows])
    if pooled is None:
        raise ValueError("history too thin to fit the dependence: it needs two residuals")
    out = {POOLED: pooled}
    for role, zs in by_role.items():
        if (own := _standardized(zs)) is not None:
            out[role] = own
    return out


def _standardized(zs: Sequence[float]) -> Floats | None:
    values = np.sort(np.asarray(zs, dtype=np.float64))
    sd = values.std()
    if len(values) < 2 or sd == 0:
        return None
    standardized: Floats = (values - values.mean()) / sd
    return standardized


def _hermite(grid: Floats) -> Floats:
    """`E[g(Z)·Heₖ(Z)]/√k!` for k = 1..K, over the marginal's standard deviation, where g
    maps Z's m equal-probability bins to the sorted grid — the step `draw` samples.

    Exact per bin: `∫ĥₖφ = (ĥₖ₋₁φ at the left edge - at the right)/√k` for the normalized
    `ĥₖ = Heₖ/√k!`, with φ = 0 at ±∞.
    """
    m = len(grid)
    edges = ndtri(np.arange(m + 1) / m)
    finite = np.isfinite(edges)
    z = np.where(finite, edges, 0.0)
    phi = np.where(finite, np.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi), 0.0)
    out = np.empty(HERMITE_TERMS)
    previous, current = np.zeros(m + 1), np.ones(m + 1)
    for k in range(1, HERMITE_TERMS + 1):
        edge = current * phi
        out[k - 1] = float(grid @ (edge[:-1] - edge[1:])) / math.sqrt(k)
        previous, current = current, (z * current - math.sqrt(k - 1) * previous) / math.sqrt(k)
    scaled: Floats = out / float(grid.std())
    return scaled


def _series(rho: float, a: Floats, b: Floats) -> float:
    powers = rho ** np.arange(1, HERMITE_TERMS + 1)
    return float(np.sum(powers * a * b))


def _match(target: float, a: Floats, b: Floats) -> float:
    """The rho in [-1, 1] whose output correlation is `target`; the nearer end when none is.
    The output correlation is increasing in rho, so bisection finds it."""
    low, high = -1.0, 1.0
    for _ in range(64):
        middle = (low + high) / 2.0
        if _series(middle, a, b) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _pearson(xy: Sequence[tuple[float, float]]) -> float:
    """The correlation of the pairs; 0 with fewer than two or no spread in either column."""
    if len(xy) < 2:
        return 0.0
    x, y = np.asarray(xy, dtype=np.float64).T
    x, y = x - x.mean(), y - y.mean()
    denominator = math.sqrt(float((x * x).sum() * (y * y).sum()))
    return float((x * y).sum()) / denominator if denominator > 0 else 0.0
