"""
stats_toolkit — portable statistical methods for ground-surface validation.

Origin: SPb pipeline v5.4 audit (2026-10). Designed to be drop-in portable
across SPB pipeline repos (5.3 and later). No project-specific imports.

Public API:
  sign_test(values, alternative="two-sided") -> SignTestResult
  wilcoxon_signed_rank(diffs) -> WilcoxonResult
  bootstrap_ci_median(values, n_boot=10000, ci=0.95) -> BootstrapResult
  per_rgt_aggregate(points, group_col="rgt") -> dict
  practical_significance(values, threshold_m=0.10) -> PracticalResult

Reference: 19_icesat_per_rgt_test.py (per-RGT, the right way) vs
           17_osm_anchor_powerful_retest.py:part_2 (per-point, inflated).
"""
from .core import (
    sign_test,
    wilcoxon_signed_rank,
    bootstrap_ci_median,
    per_rgt_aggregate,
    practical_significance,
    PRACTICAL_THRESHOLD_M,
)

__all__ = [
    "sign_test",
    "wilcoxon_signed_rank",
    "bootstrap_ci_median",
    "per_rgt_aggregate",
    "practical_significance",
    "PRACTICAL_THRESHOLD_M",
]
__version__ = "1.0.0"
