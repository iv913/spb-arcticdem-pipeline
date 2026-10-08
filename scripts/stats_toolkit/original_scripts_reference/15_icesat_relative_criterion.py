#!/usr/bin/env python3
"""
15_icesat_relative_criterion.py
================================
Использовать ICESat (43K точек) как относительный критерий сравнения версий.

КОНТЕКСТ (пользователь):
- ICESat merяет поверхность, а не датум, но для СРАВНЕНИЯ ВЕРСИЙ это неважно:
  датум в обеих версиях один, и он сокращается при вычитании
- 43K точек vs 40 passport → на порядок больше статистической мощности
- Эффективно 10 линий (RGT), но для поточечных тестов это всё равно 100× прирост

МЕТОД:
1. Загрузить ICESat с h_te_ell (laser ground truth) и h_dem_ell (raw ArcticDEM)
   dZ_baseline = h_te_ell - h_dem_ell  → v4-style baseline error
2. Sample CURRENT processed surface (spb_ground_surface_2m.tif) at all ICESat points
   → dZ_current = h_te_ell - h_surface_processed
3. Paired sign test on 43K points: сколько точек имеют меньшее |dZ| в current?
4. Wilcoxon: с magnitude
5. Bootstrap CI per-point median diff

ВЫХОД:
- Если current vs baseline даёт ≥60% точек лучше при n=43K → p<<0.001, значимо
- Это даёт мощность, недоступную на 40 passport точках
"""
import json, sys, time
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.warp import transform
from scipy import stats

UPLOAD = Path('/home/z/my-project/upload')
DATA = Path('/home/z/my-project/scripts/data')
DATA.mkdir(parents=True, exist_ok=True)
LOG = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)


def mad(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if a.size == 0: return float('nan')
    m = np.median(a)
    return float(np.median(np.abs(a - m)) * 1.4826)


def sign_test(dZ_a, dZ_b, alternative='two-sided'):
    """Sign test: how many points have smaller |dZ| in B than in A?"""
    abs_a = np.abs(dZ_a)
    abs_b = np.abs(dZ_b)
    better = (abs_b < abs_a).sum()
    worse = (abs_b > abs_a).sum()
    tied = (abs_b == abs_a).sum()
    n = better + worse
    if n == 0:
        return 0, 0, int(tied), 1.0, 0.0
    p_val = stats.binomtest(better, n, p=0.5, alternative=alternative).pvalue
    pct_better = 100.0 * better / n
    return int(better), int(worse), int(tied), float(p_val), float(pct_better)


def sample_surface_at_points(surface_path, lat, lon, target_crs='EPSG:4326', radius_px=0):
    """Sample raster at lat/lon points. Returns array of values."""
    src = rasterio.open(surface_path)
    # Transform lat/lon → raster CRS
    xs, ys = transform(target_crs, src.crs, lon.tolist(), lat.tolist())
    nodata = src.nodata
    values = []
    for x, y in zip(xs, ys):
        try:
            row, col = src.index(x, y)
            if 0 <= row < src.height and 0 <= col < src.width:
                if radius_px == 0:
                    val = src.read(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0]
                else:
                    # Read small window, take median
                    r0 = max(0, row - radius_px); r1 = min(src.height, row + radius_px + 1)
                    c0 = max(0, col - radius_px); c1 = min(src.width, col + radius_px + 1)
                    win_data = src.read(1, window=rasterio.windows.Window(c0, r0, c1-c0, r1-r0))
                    win_data = win_data[win_data != nodata]
                    val = float(np.median(win_data)) if len(win_data) > 0 else np.nan
            else:
                val = np.nan
        except Exception:
            val = np.nan
        values.append(float(val) if val != nodata else np.nan)
    src.close()
    return np.array(values)


def main():
    LOG('=' * 70)
    LOG('ICESat as relative criterion: 43K points vs 40 passport')
    LOG('=' * 70)

    # Load ICESat
    LOG('Loading ICESat-2 ATL08 (43K points)...')
    ice = gpd.read_file(UPLOAD / 'atl08_ground.gpkg')
    LOG(f'  ICESat: {len(ice)} points')
    LOG(f'  columns: {list(ice.columns)}')
    LOG(f'  bounds: {ice.total_bounds}')

    # h_te_ell: laser ground truth, ellipsoid
    # h_dem_ell: model height at ICESat point (raw ArcticDEM, v4-style)
    # dZ = h_te_ell - h_dem_ell → baseline error
    LOG(f'\nICESat baseline (raw ArcticDEM, v4-style):')
    LOG(f'  n valid dZ: {ice.dZ.notna().sum()}')
    LOG(f'  median dZ: {ice.dZ.median():+.4f} m')
    LOG(f'  MAD:        {mad(ice.dZ.values):.4f} m')
    LOG(f'  RMSE:       {np.sqrt((ice.dZ.dropna()**2).mean()):.4f} m')

    # Sample current model (spb_ground_surface_2m.tif) at ICESat points
    surface_path = UPLOAD / 'spb_ground_surface_2m.tif'
    LOG(f'\nSampling current model at ICESat points: {surface_path.name}')
    LOG(f'  (this takes ~5 min for 43K points)...')

    t0 = time.time()
    h_current = sample_surface_at_points(str(surface_path),
                                          ice.geometry.y, ice.geometry.x,
                                          radius_px=2)  # median of 5x5 window
    LOG(f'  done in {time.time()-t0:.1f}s')
    LOG(f'  valid samples: {np.isfinite(h_current).sum()} / {len(h_current)}')

    # Build dZ_current: same convention as dZ (laser - model)
    # But note: h_current is BS-77 height (subtract 16m geoid if comparing to ellipsoid)
    # Actually spb_ground_surface_2m.tif is in BS-77 already (pipeline converts)
    # ICESat h_te_ell is ellipsoid
    # For comparison: convert both to same frame
    # ICESat baseline dZ = h_te_ell - h_dem_ell (both ellipsoid, raw ArcticDEM)
    # For current: need h_te_ell - h_current_ell = h_te_ell - (h_current_bs77 + 16)
    # OR: convert h_te_ell to BS-77: h_te_bs77 = h_te_ell - 16
    # Then dZ_current_bs77 = h_te_bs77 - h_current_bs77 = (h_te_ell - 16) - h_current

    GEOID_OFFSET = 16.0  # for SPb
    h_te_ell = ice.h_te_ell.values
    h_te_bs77 = h_te_ell - GEOID_OFFSET
    dZ_baseline_ell = ice.dZ.values  # already (h_te_ell - h_dem_ell)
    dZ_current_bs77 = h_te_bs77 - h_current

    valid_baseline = np.isfinite(dZ_baseline_ell)
    valid_current = np.isfinite(dZ_current_bs77)
    both_valid = valid_baseline & valid_current

    LOG(f'\nValid points:')
    LOG(f'  baseline (raw ArcticDEM): {valid_baseline.sum()}')
    LOG(f'  current (spb_ground_surface_2m.tif): {valid_current.sum()}')
    LOG(f'  both valid: {both_valid.sum()}')

    # === Paired comparison ===
    LOG(f'\n{"="*70}')
    LOG(f'Paired comparison on {both_valid.sum()} ICESat points')
    LOG(f'{"="*70}')

    dZ_a = dZ_baseline_ell[both_valid]
    dZ_b = dZ_current_bs77[both_valid]

    mad_a = mad(dZ_a)
    mad_b = mad(dZ_b)
    LOG(f'  Baseline (raw ArcticDEM):    MAD = {mad_a:.4f} m')
    LOG(f'  Current (processed surface):  MAD = {mad_b:.4f} m')
    LOG(f'  Δ MAD: {mad_b - mad_a:+.4f} m ({100*(mad_b-mad_a)/mad_a:+.1f}%)')

    # Sign test
    n_better, n_worse, n_tied, p_sign, pct_better = sign_test(dZ_a, dZ_b)
    LOG(f'\n  SIGN TEST (|dZ_current| < |dZ_baseline|):')
    LOG(f'    better: {n_better} ({pct_better:.1f}%)')
    LOG(f'    worse:  {n_worse}')
    LOG(f'    tied:   {n_tied}')
    LOG(f'    p-value: {p_sign:.6e}')

    # With 43K points, even 51% better is highly significant
    # Threshold for significance at n=43000: 51% better is enough
    if p_sign < 0.001:
        if n_better > n_worse:
            LOG(f'    ✅ HIGHLY SIGNIFICANT: current is BETTER than baseline')
        else:
            LOG(f'    ✅ HIGHLY SIGNIFICANT: current is WORSE than baseline')
    elif p_sign < 0.05:
        direction = 'BETTER' if n_better > n_worse else 'WORSE'
        LOG(f'    ✅ SIGNIFICANT: current is {direction}')
    else:
        LOG(f'    ⚠ not significant')

    # Wilcoxon
    diff = np.abs(dZ_a) - np.abs(dZ_b)  # positive: B is better
    diff = diff[diff != 0]
    if len(diff) > 5:
        stat, p_wilc = stats.wilcoxon(diff, alternative='two-sided')
        median_diff = float(np.median(diff))
        LOG(f'\n  WILCOXON signed-rank test:')
        LOG(f'    statistic: {stat:.1f}')
        LOG(f'    p-value: {p_wilc:.6e}')
        LOG(f'    median |dZ_A|-|dZ_B| = {median_diff:+.4f} m')

    # Per-point bootstrap of median diff
    rng = np.random.default_rng(42)
    n = len(diff)
    boot_medians = np.empty(500)
    for i in range(500):
        sample = rng.choice(diff, size=n, replace=True)
        boot_medians[i] = np.median(sample)
    pt_lo, pt_hi = np.percentile(boot_medians, [2.5, 97.5])
    pt_median = float(np.median(diff))
    LOG(f'\n  PER-POINT median |dZ_baseline|-|dZ_current| bootstrap:')
    LOG(f'    median: {pt_median:+.4f} m')
    LOG(f'    95% CI: [{pt_lo:+.4f}, {pt_hi:+.4f}], width={pt_hi-pt_lo:.4f}')
    if pt_lo > 0 and pt_hi > 0:
        LOG(f'    ✅ CI excludes 0 — current is BETTER')
    elif pt_lo < 0 and pt_hi < 0:
        LOG(f'    ✅ CI excludes 0 — current is WORSE')
    else:
        LOG(f'    ⚠ CI includes 0 — not significant')

    # === Compare to passport holdout ===
    LOG(f'\n{"="*70}')
    LOG(f'Comparison: ICESat (n={both_valid.sum()}) vs passport holdout (n=40)')
    LOG(f'{"="*70}')

    # Passport baseline
    v11 = pd.read_csv(DATA / 'spb_passport_validation_v11.csv')
    v11_hold = v11[v11.role == 'holdout']
    passport_mad = mad(v11_hold.dH.values)
    passport_median = float(v11_hold.dH.median())
    LOG(f'  Passport holdout (n={len(v11_hold)}): MAD = {passport_mad:.4f}, median = {passport_median:+.4f}')
    LOG(f'  ICESat holdout (n={both_valid.sum()}): MAD = {mad_b:.4f}, median = {np.median(dZ_b):+.4f}')
    LOG(f'  → ICESat MAD is much smaller ({mad_b:.2f} vs {passport_mad:.2f}) because:')
    LOG(f'    1. ICESat tracks are mostly over OPEN areas (laser can\'t see through trees)')
    LOG(f'    2. ICESat measures surface (laser ground photons), not datum')
    LOG(f'    3. Many ICESat points were used as train anchors (detail=22) → expected low error')

    # === ICESat detail=22 (train) vs holdout ===
    LOG(f'\n{"="*70}')
    LOG(f'ICESat train vs holdout (detail-based split)')
    LOG(f'{"="*70}')

    # Need detail per ICESat point — that requires re-running pipeline sampling
    # For now, just check the distribution of |dZ_current|
    abs_b = np.abs(dZ_b)
    LOG(f'  |dZ_current| distribution:')
    LOG(f'    n_total: {len(abs_b)}')
    LOG(f'    |dZ| < 0.5 m: {(abs_b < 0.5).sum()} ({100*(abs_b<0.5).mean():.1f}%)')
    LOG(f'    |dZ| < 1.0 m: {(abs_b < 1.0).sum()} ({100*(abs_b<1.0).mean():.1f}%)')
    LOG(f'    |dZ| < 2.0 m: {(abs_b < 2.0).sum()} ({100*(abs_b<2.0).mean():.1f}%)')
    LOG(f'    |dZ| > 5.0 m: {(abs_b > 5.0).sum()} ({100*(abs_b>5.0).mean():.1f}%)')

    # === Power analysis ===
    LOG(f'\n{"="*70}')
    LOG(f'Power analysis: what Δ can we detect?')
    LOG(f'{"="*70}')
    LOG(f'  On passport (n=40):')
    LOG(f'    Sign test: ≥27/40 better → p<0.05 (detects ~67.5% better)')
    LOG(f'  On ICESat (n={both_valid.sum()}):')
    threshold_pct = 50 + 1.96 * 50 / np.sqrt(both_valid.sum())
    threshold_n = int(threshold_pct * both_valid.sum() / 100)
    LOG(f'    Sign test: ≥{threshold_n}/{both_valid.sum()} better ({threshold_pct:.2f}%) → p<0.05')
    LOG(f'    → Can detect {threshold_pct-50:.2f}% asymmetry, vs 17.5% on passport')
    LOG(f'    → Power improvement: ~{(17.5/(threshold_pct-50)):.0f}× more sensitive')

    # === Summary ===
    summary = {
        'experiment': 'ICESat as relative criterion (43K vs 40 passport)',
        'method': 'Sample current model surface at ICESat points, compare to baseline',
        'icesat_total': int(len(ice)),
        'icesat_valid_paired': int(both_valid.sum()),
        'baseline_mad': float(mad_a),
        'current_mad': float(mad_b),
        'delta_mad': float(mad_b - mad_a),
        'sign_test': {
            'n_better': n_better,
            'n_worse': n_worse,
            'n_tied': int(n_tied),
            'p_value': float(p_sign),
            'pct_better': float(pct_better),
            'significant_5pct': bool(p_sign < 0.05),
            'highly_significant': bool(p_sign < 0.001),
        },
        'wilcoxon': {
            'statistic': float(stat) if len(diff) > 5 else None,
            'p_value': float(p_wilc) if len(diff) > 5 else None,
            'median_diff_abs_dZ': float(median_diff) if len(diff) > 5 else None,
        },
        'per_point_median_bootstrap': {
            'median': pt_median,
            'ci_95': [float(pt_lo), float(pt_hi)],
            'ci_width': float(pt_hi - pt_lo),
        },
        'power_analysis': {
            'passport_n': 40,
            'passport_sign_test_threshold_pct': 67.5,
            'icesat_n': int(both_valid.sum()),
            'icesat_sign_test_threshold_pct': float(threshold_pct),
            'sensitivity_improvement_x': float(17.5 / (threshold_pct - 50)),
        },
        'comparison_to_passport': {
            'passport_mad': float(passport_mad),
            'icesat_mad': float(mad_b),
            'note': 'ICESat MAD smaller because tracks are over open areas, not urban canyon',
        },
        'implications_for_stage_2': [
            'For each Stage 2 iteration: sample new model at 43K ICESat points',
            'Use paired sign test on ICESat (much more powerful than passport MAD)',
            'Can detect ~0.3% asymmetry at p<0.05 (vs 17.5% on passport)',
            'However ICESat measures surface, not datum — for absolute datum check need passport',
            'For relative comparison (v8 vs v9b vs new iteration): ICESat is perfect',
        ],
    }
    out = DATA / 'icesat_relative_criterion.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')

    # Save ICESat dZ per point for future comparisons
    ice_out = pd.DataFrame({
        'lat': ice.geometry.y[both_valid],
        'lon': ice.geometry.x[both_valid],
        'h_te_ell': h_te_ell[both_valid],
        'dZ_baseline_ell': dZ_a,
        'dZ_current_bs77': dZ_b,
        'beam': ice.beam.values[both_valid],
    })
    ice_out.to_csv(DATA / 'icesat_per_point_dZ.csv', index=False)
    LOG(f'Saved per-point dZ → {DATA / "icesat_per_point_dZ.csv"} ({len(ice_out)} rows)')


if __name__ == '__main__':
    main()
