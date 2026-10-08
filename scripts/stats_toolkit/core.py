"""
stats_toolkit.core — implementations.

All functions are pure (no I/O, no global state). Designed for both
interactive use and pipeline integration.
"""
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Iterable, Optional


# Per audit: "порог практической значимости practical_threshold_m = 0.1"
PRACTICAL_THRESHOLD_M = 0.10


@dataclass
class SignTestResult:
    n_better: int
    n_worse: int
    n_tied: int
    n_total: int
    p_value: float
    pct_better: float
    direction: str  # "better", "worse", "tied"

    def as_dict(self) -> dict:
        return asdict(self)


def _binom_two_sided_p(k: int, n: int) -> float:
    """Two-sided p-value for binomial test H0: p = 0.5."""
    if n == 0:
        return 1.0
    # Use exact for small n, normal approx for large n
    if n <= 25:
        from math import comb
        # Sum probabilities at the tails
        # Two-sided: 2 * P(X <= min(k, n-k))
        m = min(k, n - k)
        p = 0.0
        for i in range(0, m + 1):
            p += comb(n, i) * (0.5 ** n)
        # Avoid double-count when k == n - k (symmetric)
        if k == n - k:
            p -= comb(n, k) * (0.5 ** n) * 0.5  # don't double-count the middle
        return min(1.0, max(0.0, 2 * p))
    # Normal approx with continuity correction
    z = (abs(k - n / 2) - 0.5) / math.sqrt(n / 4)
    return math.erfc(z / math.sqrt(2))


def sign_test(
    values: Iterable[float],
    threshold: float = 0.0,
    alternative: str = "two-sided",
) -> SignTestResult:
    """Sign test on a list of values.

    Tests H0: P(value > threshold) = 0.5
    Useful for per-RGT aggregated diffs where each RGT contributes one value.

    Args:
        values: per-group (e.g. per-RGT) signed differences
        threshold: zero by default; can be set to practical_threshold_m
        alternative: "two-sided" (default), "greater", "less"

    Returns:
        SignTestResult with n_better, n_worse, p_value, etc.
    """
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    n_better = sum(1 for v in vals if v > threshold)
    n_worse = sum(1 for v in vals if v < threshold)
    n_tied = sum(1 for v in vals if v == threshold)
    n_total = n_better + n_worse  # exclude ties

    if n_total == 0:
        return SignTestResult(0, 0, int(n_tied), 0, 1.0, 0.0, "tied")

    if alternative == "two-sided":
        p = _binom_two_sided_p(n_better, n_total)
    elif alternative == "greater":
        # P(X >= n_better)
        from math import comb
        p = sum(comb(n_total, i) * (0.5 ** n_total) for i in range(n_better, n_total + 1))
    elif alternative == "less":
        from math import comb
        p = sum(comb(n_total, i) * (0.5 ** n_total) for i in range(0, n_better + 1))
    else:
        raise ValueError(f"alternative must be 'two-sided', 'greater', or 'less'")

    pct = 100.0 * n_better / n_total if n_total > 0 else 0.0
    direction = "better" if n_better > n_worse else ("worse" if n_better < n_worse else "tied")
    return SignTestResult(n_better, n_worse, int(n_tied), n_total, p, pct, direction)


@dataclass
class WilcoxonResult:
    W: float  # smaller of positive/negative rank sums
    z: float
    p_value: float
    n_nonzero: int
    median_diff: float

    def as_dict(self) -> dict:
        return asdict(self)


def wilcoxon_signed_rank(diffs: Iterable[float]) -> Optional[WilcoxonResult]:
    """Two-sided Wilcoxon signed-rank test with normal approximation.

    Args:
        diffs: signed differences (paired)

    Returns:
        WilcoxonResult, or None if too few non-zero diffs (n<10 for normal approx)
    """
    nonzero = [d for d in diffs if d != 0 and not (isinstance(d, float) and math.isnan(d))]
    n = len(nonzero)
    if n < 10:
        return None  # too small for normal approx

    abs_sorted = sorted(abs(d) for d in nonzero)
    pos_sum = 0.0
    neg_sum = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs_sorted[j + 1] == abs_sorted[i]:
            j += 1
        # Ranks i+1 .. j+1 share the average
        avg_rank = (i + 1 + j + 1) / 2.0
        # Apply sign based on original diff
        for k in range(i, j + 1):
            # find the original diff with this |value| (could be tied)
            # since we sorted by |value|, walk all nonzero with same |value|
            pass
        # Re-walk nonzero to apply ranks (handles ties correctly)
        for idx, d in enumerate(nonzero):
            if abs(d) == abs_sorted[i]:  # matches this tie group
                if d > 0:
                    pos_sum += avg_rank
                else:
                    neg_sum += avg_rank
        i = j + 1

    W = min(pos_sum, neg_sum)
    mu = n * (n + 1) / 4
    # Tie correction
    from collections import Counter
    tie_counts = Counter(abs_sorted)
    tie_correction = sum(t * t * t - t for t in tie_counts.values()) / 48.0
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24 - tie_correction)
    if sigma == 0:
        return None
    z = (W - mu) / sigma
    p = math.erfc(abs(z) / math.sqrt(2))
    median_diff = statistics.median(nonzero) if nonzero else 0.0
    return WilcoxonResult(W, round(z, 4), p, n, round(median_diff, 4))


@dataclass
class BootstrapResult:
    median: float
    mean: float
    ci_95: list  # [low, high]
    n_boot: int
    width: float  # ci_high - ci_low

    def as_dict(self) -> dict:
        return asdict(self)


def bootstrap_ci_median(
    values: Iterable[float],
    n_boot: int = 10000,
    ci: float = 0.95,
    seed: int = 42,
) -> Optional[BootstrapResult]:
    """Bootstrap confidence interval for the median.

    Args:
        values: sample to bootstrap
        n_boot: number of bootstrap iterations
        ci: confidence level (0.95 = 95% CI)
        seed: random seed for reproducibility

    Returns:
        BootstrapResult, or None if values is empty
    """
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    n = len(vals)
    if n == 0:
        return None
    if n == 1:
        return BootstrapResult(vals[0], vals[0], [vals[0], vals[0]], n_boot, 0.0)

    random.seed(seed)
    medians = []
    means = []
    for _ in range(n_boot):
        sample = [random.choice(vals) for _ in range(n)]
        medians.append(statistics.median(sample))
        means.append(statistics.mean(sample))
    medians.sort()
    means.sort()
    alpha = (1 - ci) / 2
    lo_idx = int(alpha * n_boot)
    hi_idx = int((1 - alpha) * n_boot)
    return BootstrapResult(
        median=round(statistics.median(vals), 4),
        mean=round(statistics.mean(vals), 4),
        ci_95=[round(medians[lo_idx], 4), round(medians[hi_idx], 4)],
        n_boot=n_boot,
        width=round(medians[hi_idx] - medians[lo_idx], 4),
    )


def per_rgt_aggregate(
    points: list,
    value_key: str = "diff",
    group_key: str = "rgt",
) -> dict:
    """Aggregate per-point diffs by RGT (or any group), then compute per-group
    statistics. Returns the "honest" per-group view that should be used
    instead of per-point tests when points within a group are not independent.

    Args:
        points: list of dicts, each with `value_key` and `group_key`
        value_key: name of the diff field (default "diff")
        group_key: name of the group field (default "rgt")

    Returns:
        {
            "n_groups": int,
            "n_points": int,
            "per_group": [{group: str, n: int, median: float, mad: float}, ...],
            "sign_test_per_group": SignTestResult.as_dict(),
            "wilcoxon_per_group": WilcoxonResult.as_dict() | None,
            "bootstrap_per_group": BootstrapResult.as_dict(),
        }
    """
    groups = defaultdict(list)
    for p in points:
        if group_key in p and value_key in p:
            try:
                groups[p[group_key]].append(float(p[value_key]))
            except (ValueError, TypeError):
                continue

    per_group = []
    for g, vals in sorted(groups.items(), key=lambda x: str(x[0])):
        if not vals:
            continue
        m = statistics.median(vals)
        mad = statistics.median([abs(v - m) for v in vals])
        per_group.append({
            "group": g,
            "n": len(vals),
            "median": round(m, 4),
            "mad": round(mad, 4),
        })

    group_medians = [g["median"] for g in per_group]
    sign = sign_test(group_medians)
    wilcoxon = wilcoxon_signed_rank(group_medians)
    boot = bootstrap_ci_median(group_medians)

    return {
        "n_groups": len(per_group),
        "n_points": sum(g["n"] for g in per_group),
        "per_group": per_group,
        "sign_test_per_group": sign.as_dict(),
        "wilcoxon_per_group": wilcoxon.as_dict() if wilcoxon else None,
        "bootstrap_per_group": boot.as_dict() if boot else None,
    }


@dataclass
class PracticalResult:
    median_abs: float
    threshold: float
    practically_significant: bool
    margin_above_threshold: float
    n: int

    def as_dict(self) -> dict:
        return asdict(self)


def practical_significance(
    values: Iterable[float],
    threshold_m: float = PRACTICAL_THRESHOLD_M,
) -> PracticalResult:
    """Check if the median absolute value exceeds the practical threshold.

    Per audit: "practical_threshold_m = 0.1" -- differences below 0.1m
    are practically insignificant regardless of statistical p-value.

    Args:
        values: signed diffs
        threshold_m: practical significance threshold (default 0.10m)

    Returns:
        PracticalResult
    """
    vals = [abs(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return PracticalResult(float("nan"), threshold_m, False, float("nan"), 0)
    med = statistics.median(vals)
    return PracticalResult(
        median_abs=round(med, 4),
        threshold=threshold_m,
        practically_significant=med > threshold_m,
        margin_above_threshold=round(med - threshold_m, 4),
        n=len(vals),
    )
