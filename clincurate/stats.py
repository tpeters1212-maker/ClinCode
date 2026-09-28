"""Agreement statistics with patient-clustered bootstrap intervals.

Thresholds and stopping targets are inputs, never constants: they come from
the project's validation plan agreed with EBSD.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Hashable, Sequence


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty rating lists")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def percent_agreement(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    return sum(x == y for x, y in zip(a, b)) / len(a)


@dataclass
class Estimate:
    value: float
    lo: float
    hi: float
    n: int

    @property
    def half_width(self) -> float:
        return (self.hi - self.lo) / 2


def bootstrap(
    a: Sequence[Hashable],
    b: Sequence[Hashable],
    stat: Callable[[Sequence, Sequence], float] = cohen_kappa,
    clusters: Sequence[Hashable] | None = None,
    reps: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Estimate:
    """Percentile bootstrap. Resampling is by cluster (patient) when
    `clusters` is given, since notes from one patient are not independent."""
    clusters = list(clusters) if clusters is not None else list(range(len(a)))
    groups: dict[Hashable, list[int]] = defaultdict(list)
    for i, c in enumerate(clusters):
        groups[c].append(i)
    keys = list(groups)
    rng = random.Random(seed)
    draws = []
    for _ in range(reps):
        idx = [i for k in rng.choices(keys, k=len(keys)) for i in groups[k]]
        try:
            draws.append(stat([a[i] for i in idx], [b[i] for i in idx]))
        except (ValueError, ZeroDivisionError):
            continue
    draws.sort()
    lo = draws[int(alpha / 2 * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return Estimate(stat(a, b), lo, hi, len(a))


def precision_reached(est: Estimate, target_half_width: float) -> bool:
    """Sequential stopping check: is the interval narrow enough?"""
    return est.half_width <= target_half_width
