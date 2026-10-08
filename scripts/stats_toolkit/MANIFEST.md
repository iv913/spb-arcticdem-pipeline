# stats_toolkit — Portable Statistical Methods

**Origin**: SPb pipeline v5.4 audit (2026-10-07 14:44 UTC)
**Purpose**: Drop-in package for repo `spb-arcticdem-pipeline-5.3` (and any
future pipeline repo) so the next statistical verdict doesn't come out as
a "точечная оценка" (point estimate) again.

## What's inside

| File | Purpose |
|------|---------|
| `__init__.py` | Public API entry point |
| `core.py` | Implementations: sign_test, wilcoxon_signed_rank, bootstrap_ci_median, per_rgt_aggregate, practical_significance |
| `test_portability.py` | Smoke test — runs toolkit on sample data |

## Source scripts (in this repo)

These scripts were the original implementations (project-specific, with
data loading and pipeline coupling). The `stats_toolkit/` package is the
distilled, portable version of their core methods.

| Original script | Method extracted |
|---|---|
| `scripts_persistent/14_sign_test_wilcoxon_recheck.py` | `sign_test()`, `wilcoxon_signed_rank()` |
| `scripts_persistent/15_icesat_relative_criterion.py` | ICESat-relative criterion |
| `scripts_persistent/19_icesat_per_rgt_test.py` | `per_rgt_aggregate()`, `PRACTICAL_THRESHOLD_M` |
| `scripts_persistent/12_paired_bootstrap_loo_coverage.py` | `bootstrap_ci_median()` |
| `scripts_persistent/16_stage2_protocol.py` | Stage 2 protocol (paired tests) |

## How to copy to 5.3

```bash
# From this repo (spb-arcticdem-pipeline, 5.67 line):
cd /home/z/my-project/upload/deliverables

# To 5.3 repo (replace path):
TARGET=/path/to/spb-arcticdem-pipeline-5.3
cp -r stats_toolkit/ "$TARGET/scripts/"

# Verify it imports:
cd "$TARGET"
python -m scripts.stats_toolkit.test_portability
```

## Usage in 5.3

```python
from scripts.stats_toolkit import (
    sign_test, wilcoxon_signed_rank, bootstrap_ci_median,
    per_rgt_aggregate, practical_significance, PRACTICAL_THRESHOLD_M,
)

# Aggregate per-point ICESat diffs by RGT
points = [{"rgt": "025", "diff": 0.21}, ...]  # your data
result = per_rgt_aggregate(points, value_key="diff", group_key="rgt")
print(result["sign_test_per_group"])  # p-value with honest n_eff=10-12
```

## Why this matters (audit findings)

The audit identified a critical regression: per-point statistics inflate
significance when points within one group are not independent. ICESat-2
points within one RGT diverge by 0.82m across 3 epochs while within-track
MAD is 0.02m — they are highly correlated. Per-point n=37K gives
p=10⁻¹⁰⁰; per-RGT n=10-12 gives p=0.109 (MARGINAL).

| Test | per-point p | per-RGT p | Inflation |
|---|---|---|---|
| `part_2_icesat_near_tagged` (this audit) | 9.3e-46 | 0.0312 | 3e44x |
| `test_1_pipeline_vs_baseline` (earlier) | 10⁻¹⁰⁰ | 0.109 | ~10⁹⁹x |

Without this toolkit, 5.3's verdicts will keep coming out as falsely
precise point estimates.
