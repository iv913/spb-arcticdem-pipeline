"""
Smoke test for stats_toolkit portability. Run with:
    python -m stats_toolkit.test_portability

Should print results without raising any exceptions.
"""
from . import (
    sign_test, wilcoxon_signed_rank, bootstrap_ci_median,
    per_rgt_aggregate, practical_significance, PRACTICAL_THRESHOLD_M,
)


def main():
    print(f"PRACTICAL_THRESHOLD_M = {PRACTICAL_THRESHOLD_M}")
    print()

    # Sample per-RGT data (10 RGTs with median diffs)
    rgt_diffs = [0.21, 0.04, 0.17, 0.31, 0.03, 0.27, -0.08, 0.19, 0.15, 0.08, 0.12]

    print("=== sign_test (per-RGT, n=11) ===")
    r = sign_test(rgt_diffs)
    print(f"  {r}")
    print()

    print("=== wilcoxon_signed_rank ===")
    w = wilcoxon_signed_rank(rgt_diffs)
    print(f"  {w}")
    print()

    print("=== bootstrap_ci_median ===")
    b = bootstrap_ci_median(rgt_diffs, n_boot=1000)
    print(f"  {b}")
    print()

    print("=== per_rgt_aggregate (from points list) ===")
    # Build a sample points list
    points = []
    for i, d in enumerate(rgt_diffs):
        # Each "RGT" has a few points
        for j in range(5):
            points.append({"rgt": f"rgt_{i:03d}", "diff": d + (j - 2) * 0.02})
    agg = per_rgt_aggregate(points)
    print(f"  n_groups: {agg['n_groups']}")
    print(f"  n_points: {agg['n_points']}")
    print(f"  sign_test_per_group: {agg['sign_test_per_group']}")
    print()

    print("=== practical_significance ===")
    p = practical_significance(rgt_diffs)
    print(f"  {p}")
    print()

    print("ALL TESTS PASSED.")


if __name__ == "__main__":
    main()
