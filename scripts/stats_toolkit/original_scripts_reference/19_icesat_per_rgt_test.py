#!/usr/bin/env python3
"""
19_icesat_per_rgt_test.py
========================
Пересчёт ICESat-тестов по RGT (10-12 линий), а не по 37K точек.

КОНТЕКСТ (пользователь):
- p≈10⁻¹⁰⁰ на 37K точек — артефакт большого n
- ICESat не независимы: 3 эпохи одного RGT разошлись на 0.82м при
  внутритрековом MAD 0.02
- Эффективное n ≈ 10 (число RGT), а не 37 322
- SE занижена в sqrt(37322/10) = 61×
- Честный CI: не [+0.078, +0.086], а ~[-0.16, +0.32] (включает 0)

МЕТОД:
1. Для каждой ICESat точки — уже есть dZ_baseline, dZ_current, dH_combined
2. Сгруппировать по RGT, взять медиану per-point улучшения
3. Sign test по 12 RGT-значениям (threshold 10/12 better → p<0.05)
4. Wilcoxon по 12 значениям
5. Bootstrap CI с честным n=12

Также: применить ту же логику к OSM-as-anchor тесту (5.27):
- Группировать 813 точек по RGT
- Sign test по линиям, а не по точкам
"""
import json, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import geopandas as gpd
import re
from scipy import stats

DATA = Path('/home/z/my-project/scripts/data')
UPLOAD = Path('/home/z/my-project/upload')
LOG = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)

# Practical significance threshold (user-suggested)
PRACTICAL_THRESHOLD_M = 0.10


def mad(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if a.size == 0: return float('nan')
    m = np.median(a)
    return float(np.median(np.abs(a - m)) * 1.4826)


def sign_test(values, alternative='two-sided'):
    """Sign test on a list of values (already aggregated per RGT).
    Tests: P(value > 0) = 0.5?
    """
    pos = (np.array(values) > 0).sum()
    neg = (np.array(values) < 0).sum()
    n = pos + neg
    if n == 0:
        return 0, 0, 0, 1.0
    p = stats.binomtest(pos, n, 0.5, alternative=alternative).pvalue
    return int(pos), int(neg), 0, float(p)


def wilcoxon_test(values):
    """Wilcoxon signed-rank test on values."""
    values = np.array(values)
    values = values[values != 0]
    if len(values) < 5:
        return None
    stat, p = stats.wilcoxon(values, alternative='two-sided')
    return float(stat), float(p), float(np.median(values))


def bootstrap_ci(values, n_boot=2000, ci=0.95, seed=42):
    """Bootstrap CI for median of values."""
    rng = np.random.default_rng(seed)
    n = len(values)
    if n < 5:
        return float('nan'), float('nan'), float('nan')
    boot = np.empty(n_boot)
    for i in range(n_boot):
        s = rng.choice(values, size=n, replace=True)
        boot[i] = np.median(s)
    pt_med = float(np.median(values))
    alpha = (1 - ci) / 2
    lo, hi = np.percentile(boot, [alpha * 100, (1 - alpha) * 100])
    return pt_med, float(lo), float(hi)


def extract_rgt(src_str):
    """Extract RGT from ATL08 filename: ATL08_<timestamp>_<RRRTTT...>_007_01.h5"""
    if not isinstance(src_str, str):
        return None
    parts = src_str.split('_')
    if len(parts) < 3:
        return None
    return parts[2][:3]


def test_per_rgt(dH_a, dH_b, rgt, name_a, name_b, description):
    """Aggregate per-point diffs by RGT, then test.

    dH_a, dH_b: per-point residuals (e.g., dZ_baseline, dZ_current)
    rgt: per-point RGT (10-12 unique values)
    """
    n_points = len(dH_a)
    n_rgt = len(set(rgt))

    LOG(f'\n{"="*70}')
    LOG(f'{description}')
    LOG(f'  Comparing: {name_b} vs {name_a}')
    LOG(f'  n_points={n_points}, n_RGTs={n_rgt}')
    LOG(f'{"="*70}')

    # Per-point improvement (positive: B is better)
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)
    per_point_diff = abs_a - abs_b  # positive: B is better

    # Aggregate by RGT: median per-point diff for each RGT
    df = pd.DataFrame({'rgt': rgt, 'diff': per_point_diff, 'abs_a': abs_a, 'abs_b': abs_b})
    rgt_agg = df.groupby('rgt').agg(
        median_diff=('diff', 'median'),
        n_points=('diff', 'count'),
        mad_a=('abs_a', lambda x: mad(x)),
        mad_b=('abs_b', lambda x: mad(x)),
    ).reset_index()

    LOG(f'\nPer-RGT medians (n={n_rgt}):')
    LOG(f'{"RGT":<6s}  {"n_pts":>6s}  {"median_diff":>14s}  {"MAD_a":>7s}  {"MAD_b":>7s}  {"direction":>10s}')
    for _, row in rgt_agg.iterrows():
        direction = 'BETTER' if row.median_diff > 0 else 'WORSE'
        LOG(f'{row.rgt:<6s}  {row.n_points:>6d}  {row.median_diff:>+14.4f}  {row.mad_a:>7.3f}  {row.mad_b:>7.3f}  {direction:>10s}')

    # Sign test on RGT-level medians
    rgt_diffs = rgt_agg.median_diff.values
    pos, neg, tied, p_sign = sign_test(rgt_diffs)
    pct_better = 100 * pos / (pos + neg) if (pos + neg) > 0 else 0
    LOG(f'\nSIGN TEST on {n_rgt} RGT medians:')
    LOG(f'  better (median > 0): {pos}')
    LOG(f'  worse (median < 0):  {neg}')
    LOG(f'  pct better: {pct_better:.1f}%')
    LOG(f'  binomial p-value: {p_sign:.4f}')
    LOG(f'  threshold for p<0.05 at n={n_rgt}: {np.ceil(0.6 * n_rgt)}/{n_rgt} better (need ≥{int(np.ceil(0.6 * n_rgt))})')
    # For n=10: 9/10 → p=0.021, 10/10 → p=0.002
    # For n=12: 10/12 → p=0.039, 11/12 → p=0.006, 12/12 → p=0.0005

    # Wilcoxon
    wilc = wilcoxon_test(rgt_diffs)
    if wilc:
        w_stat, p_wilc, median_diff = wilc
        LOG(f'\nWILCOXON signed-rank test on RGT medians:')
        LOG(f'  statistic: {w_stat:.1f}')
        LOG(f'  p-value: {p_wilc:.4f}')
        LOG(f'  median of medians: {median_diff:+.4f} m')

    # Bootstrap CI with honest n
    pt_med, pt_lo, pt_hi = bootstrap_ci(rgt_diffs.tolist(), n_boot=2000)
    LOG(f'\nPER-RGT median bootstrap (honest n={n_rgt}):')
    LOG(f'  median: {pt_med:+.4f} m')
    LOG(f'  95% CI: [{pt_lo:+.4f}, {pt_hi:+.4f}], width={pt_hi-pt_lo:.4f}')

    # Compare to per-point (wrong) CI
    pt_med_pp, pt_lo_pp, pt_hi_pp = bootstrap_ci(per_point_diff.tolist(), n_boot=500)
    LOG(f'\n[FOR COMPARISON] Per-point bootstrap (WRONG, n={n_points}):')
    LOG(f'  median: {pt_med_pp:+.4f} m')
    LOG(f'  95% CI: [{pt_lo_pp:+.4f}, {pt_hi_pp:+.4f}], width={pt_hi_pp-pt_lo_pp:.4f}')
    LOG(f'  → SE ratio: per-point CI {(pt_hi_pp-pt_lo_pp):.3f} vs per-RGT CI {(pt_hi-pt_lo):.3f}')
    LOG(f'  → SE inflation: {(pt_hi-pt_lo)/(pt_hi_pp-pt_lo_pp):.1f}× (should be ~sqrt(n_per_rgt)={np.sqrt(n_points/n_rgt):.1f}×)')

    # Practical significance
    LOG(f'\nPRACTICAL SIGNIFICANCE (threshold {PRACTICAL_THRESHOLD_M} m):')
    abs_med = abs(pt_med)
    if abs_med < PRACTICAL_THRESHOLD_M:
        LOG(f'  ⚠ Effect {pt_med:+.4f}m < {PRACTICAL_THRESHOLD_M}m threshold')
        LOG(f'  → NOT practically significant')
        practical = False
    else:
        LOG(f'  ✅ Effect {pt_med:+.4f}m ≥ {PRACTICAL_THRESHOLD_M}m threshold')
        practical = True

    # Combined verdict
    tests_significant = sum([
        p_sign < 0.05,
        wilc[1] < 0.05 if wilc else False,
        pt_lo > 0 or pt_hi < 0,
    ])
    direction = 'BETTER' if pt_med > 0 else 'WORSE' if pt_med < 0 else 'NONE'

    LOG(f'\nVERDICT (per-RGT, n={n_rgt}):')
    LOG(f'  Tests significant: {tests_significant}/3')
    LOG(f'  Direction: {direction}')
    LOG(f'  Practical: {"YES" if practical else "NO"} (effect ≥ {PRACTICAL_THRESHOLD_M}m)')

    if tests_significant >= 2 and direction == 'BETTER' and practical:
        verdict = f'SIGNIFICANT + PRACTICAL: {name_b} BETTER than {name_a}'
    elif tests_significant >= 2 and direction == 'WORSE' and practical:
        verdict = f'SIGNIFICANT + PRACTICAL: {name_b} WORSE than {name_a}'
    elif tests_significant >= 2 and not practical:
        verdict = f'SIGNIFICANT but NOT PRACTICAL (effect {pt_med:+.3f}m < {PRACTICAL_THRESHOLD_M}m)'
    elif tests_significant == 0:
        verdict = f'NOT SIGNIFICANT on per-RGT (n={n_rgt}) — previous "p≈0" was artifact'
    else:
        verdict = f'MARGINAL — {tests_significant}/3 tests significant'

    LOG(f'  → {verdict}')

    return {
        'description': description,
        'n_points': int(n_points),
        'n_rgt': int(n_rgt),
        'rgt_aggregates': rgt_agg.to_dict('records'),
        'sign_test_per_rgt': {
            'n_better': pos, 'n_worse': neg, 'p_value': p_sign,
        },
        'wilcoxon_per_rgt': {
            'statistic': w_stat if wilc else None,
            'p_value': p_wilc if wilc else None,
            'median_diff': median_diff if wilc else None,
        },
        'per_rgt_bootstrap': {
            'median': pt_med,
            'ci_95': [pt_lo, pt_hi],
            'ci_width': pt_hi - pt_lo,
        },
        'per_point_bootstrap_wrong': {
            'median': pt_med_pp,
            'ci_95': [pt_lo_pp, pt_hi_pp],
            'ci_width': pt_hi_pp - pt_lo_pp,
        },
        'practical_significant': practical,
        'practical_threshold_m': PRACTICAL_THRESHOLD_M,
        'verdict': verdict,
    }


def main():
    LOG('=' * 70)
    LOG('ICESat per-RGT re-test (honest n=10-12, not 37K)')
    LOG('=' * 70)
    LOG('Two comparisons:')
    LOG('  1. Current pipeline vs raw ArcticDEM (was p≈10⁻¹⁰⁰ on 37K — artifact)')
    LOG('  2. OSM-as-anchor vs baseline (was p≈10⁻²² WORSE on 813 — also artifact?)')
    LOG(f'Practical significance threshold: {PRACTICAL_THRESHOLD_M} m')

    # Load ICESat per-point dZ (from 5.25) — has dZ_baseline_ell, dZ_current_bs77
    ice = pd.read_csv(DATA / 'icesat_per_point_dZ.csv')
    LOG(f'\nLoaded ICESat: {len(ice)} points')
    LOG(f'  columns: {list(ice.columns)}')

    # Load full ICESat to get src (for RGT extraction)
    ice_full = gpd.read_file(UPLOAD / 'atl08_ground.gpkg')
    LOG(f'Full ICESat: {len(ice_full)} points')

    # Extract RGT
    ice_full['rgt'] = ice_full.src.apply(extract_rgt)
    LOG(f'RGTs: {ice_full.rgt.nunique()} unique')

    # Match RGT to the per-point subset (which is from 5.25, sampled at surface)
    # 5.25 used ice.geometry.y/x → merge on lat/lon
    ice_full['lat'] = ice_full.geometry.y
    ice_full['lon'] = ice_full.geometry.x
    merged = ice.merge(ice_full[['lat', 'lon', 'rgt']], on=['lat', 'lon'], how='left')
    LOG(f'Merged: {len(merged)}, with RGT: {merged.rgt.notna().sum()}')
    LOG(f'RGT distribution in merged:')
    print(merged.rgt.value_counts())

    # === TEST 1: Current pipeline vs raw ArcticDEM ===
    r1 = test_per_rgt(
        dH_a=merged.dZ_baseline_ell.values,
        dH_b=merged.dZ_current_bs77.values,
        rgt=merged.rgt.values,
        name_a='raw ArcticDEM',
        name_b='current pipeline',
        description='TEST 1: Current pipeline vs raw ArcticDEM (was p≈10⁻¹⁰⁰ on 37K)',
    )

    # === TEST 2: OSM-as-anchor vs baseline ===
    # Load OSM-as-anchor per-point CSV (from 5.27)
    osm = pd.read_csv(DATA / 'osm_anchor_icesat_per_point.csv')
    LOG(f'\nOSM-as-anchor data: {len(osm)} points')

    # Match RGT
    osm_merged = osm.merge(ice_full[['lat', 'lon', 'rgt']], on=['lat', 'lon'], how='left')
    LOG(f'OSM with RGT: {osm_merged.rgt.notna().sum()}')

    r2 = test_per_rgt(
        dH_a=osm_merged.dH_baseline.values,
        dH_b=osm_merged.dH_combined.values,
        rgt=osm_merged.rgt.values,
        name_a='baseline (pipeline alone)',
        name_b='combined (pipeline + OSM weighted)',
        description='TEST 2: OSM-as-anchor vs baseline (was p≈10⁻²² WORSE on 813 — recheck per-RGT)',
    )

    # === SUMMARY ===
    LOG('\n' + '=' * 70)
    LOG('SUMMARY')
    LOG('=' * 70)
    LOG(f'\n{"Test":<60s} {"verdict":<50s}')
    LOG(f'{"-"*60} {"-"*50}')
    for r in [r1, r2]:
        LOG(f'{r["description"][:60]:<60s} {r["verdict"][:50]:<50s}')

    # Save
    summary = {
        'experiment': 'ICESat per-RGT re-test (honest n=10-12)',
        'methodology': 'Aggregate per-point diffs by RGT (lines), then sign test on n_lines',
        'practical_threshold_m': PRACTICAL_THRESHOLD_M,
        'rationale': (
            'Per-point ICESat tests inflate significance because points within '
            'one RGT are not independent (3 epochs of one RGT diverged by 0.82m). '
            'Effective n is number of RGTs (~10-12), not points (37K).'
        ),
        'test_1_pipeline_vs_baseline': r1,
        'test_2_osm_anchor_vs_baseline': r2,
    }
    out = DATA / 'icesat_per_rgt_retest.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')


if __name__ == '__main__':
    main()
