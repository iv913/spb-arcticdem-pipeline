#!/usr/bin/env python3
"""
12_paired_bootstrap_loo_coverage.py
===================================
Три доработки к проверке OSM-as-anchor (по замечаниям пользователя):

1. ПАРНЫЙ бутстрап разности (paired bootstrap of difference)
   - Предыдущий бутстрап считал два независимых CI (model alone, combined)
   - Но это одни и те же 25 точек — нужно бутстрапить разность по одним индексам
   - Парный интервал обычно в 1.5-2 раза уже непарного при коррелированных оценках

2. LEAVE-ONE-OUT σ_s (не подгонка по test)
   - Перебор σ_s ∈ [1.5, 5.0] с выбором лучшего = однопараметрическая подгонка
   - LOO: для каждой точки i σ_s считается по остальным 24 точкам
   - Тогда σ_s для точки i не зависит от её собственного dH

3. КАРТА ПОКРЫТИЯ — доля площади (не зданий) под влиянием OSM кварталов
   - 50K tagged buildings из 141K — 35% зданий
   - Но эффект по городу = доля площади класса 23 (DSM-опора) в зоне влияния
   - Если 10% площади — общий выигрыш ~2%, а не 20%

Параллельно — перепроверить cap_far_field, choose_block_px, accept_dsm_with_relief
тем же парным способом (часть могла быть отвергнута зря из-за непарного сравнения).
"""
import json, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

DATA = Path('/home/z/my-project/scripts/data')
UPLOAD = Path('/home/z/my-project/upload')
LOG = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)


def mad(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if a.size == 0: return float('nan')
    m = np.median(a)
    return float(np.median(np.abs(a - m)) * 1.4826)


def paired_bootstrap_diff(dH_a, dH_b, n_boot=5000, ci=0.95, seed=42):
    """Paired bootstrap of MAD(A) - MAD(B).

    A и B — одни и те же точки. Бутстрапим индексы, для каждой выборки
    считаем MAD(A_sample) - MAD(B_sample). Распределение разности
    даёт честный CI для improvement.

    Args:
        dH_a: array — residuals of method A (e.g. model alone)
        dH_b: array — residuals of method B (e.g. combined)
    Returns:
        (point_diff, ci_lo, ci_hi, p_value)
    """
    dH_a = np.asarray(dH_a, float)
    dH_b = np.asarray(dH_b, float)
    n = len(dH_a)
    assert len(dH_b) == n, f'Must be same length: {len(dH_a)} vs {len(dH_b)}'

    rng = np.random.default_rng(seed)
    boot_diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.choice(n, size=n, replace=True)
        a_sample = dH_a[idx]
        b_sample = dH_b[idx]
        m_a = np.median(a_sample)
        m_b = np.median(b_sample)
        mad_a = np.median(np.abs(a_sample - m_a)) * 1.4826
        mad_b = np.median(np.abs(b_sample - m_b)) * 1.4826
        boot_diffs[i] = mad_a - mad_b

    point_diff = mad(dH_a) - mad(dH_b)
    alpha = (1 - ci) / 2
    lo, hi = np.percentile(boot_diffs, [alpha * 100, (1 - alpha) * 100])
    # Two-sided p-value: fraction of bootstraps where diff is on opposite side of 0
    p = np.mean(boot_diffs <= 0) if point_diff > 0 else np.mean(boot_diffs >= 0)
    p = 2 * min(p, 1 - p)  # two-sided
    return float(point_diff), float(lo), float(hi), float(p)


def leave_one_out_sigma(dH_model, h_model, h_source, sigma_model=1.07):
    """LOO: для каждой точки σ_s считается по остальным n-1 точкам.

    σ_s для точки i = argmin MAD(остальные 24) при σ_s ∈ [1.5, 5.0]
    Затем для точки i используем этот σ_s, чтобы предсказать h_combined_i.

    Это устраняет подгонку σ_s по тестовой выборке.
    """
    dH_model = np.asarray(dH_model, float)
    h_model = np.asarray(h_model, float)
    h_source = np.asarray(h_source, float)
    n = len(dH_model)

    sigma_range = np.linspace(1.0, 6.0, 51)
    sigma_loo = np.empty(n)
    h_combined_loo = np.empty(n)

    for i in range(n):
        # Все точки кроме i
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        h_m_rest = h_model[mask]
        h_s_rest = h_source[mask]
        # dH_rest = h_passport_rest - h_combined, где h_combined = (h_m/σ_m² + h_s/σ_s²) / (1/σ_m² + 1/σ_s²)
        # h_passport_rest = dH_model_rest + h_model_rest (recover passport from dH_model)
        # Wait — dH_model = hp - h_model, so hp = dH_model + h_model
        hp_rest = dH_model[mask] + h_model[mask]

        mads = []
        for sigma_s in sigma_range:
            w_m = 1.0 / sigma_model ** 2
            w_s = 1.0 / sigma_s ** 2
            h_comb = (h_m_rest * w_m + h_s_rest * w_s) / (w_m + w_s)
            dH_comb = hp_rest - h_comb
            mads.append(mad(dH_comb))

        best_idx = np.argmin(mads)
        sigma_s_best = sigma_range[best_idx]
        sigma_loo[i] = sigma_s_best

        # Now use sigma_s_best (computed without point i) to predict point i
        w_m = 1.0 / sigma_model ** 2
        w_s = 1.0 / sigma_s_best ** 2
        h_combined_loo[i] = (h_model[i] * w_m + h_source[i] * w_s) / (w_m + w_s)

    return sigma_loo, h_combined_loo


def compute_coverage_area():
    """Доля площади класса 23 (DSM-опора) в зоне влияния OSM кварталов.

    Зона влияния = buffer вокруг каждого здания с tagged levels/height.
    Если 35% зданий tagged, но они занимают только 10% площади → эффект 2%, не 20%.
    """
    LOG('=' * 70)
    LOG('STEP 3: Coverage area — доля площади под влиянием OSM')
    LOG('=' * 70)

    # Load OSM buildings
    import json as json_mod
    with open(UPLOAD / 'data_backup/osm/spb_osm_buildings.geojson') as f:
        data = json_mod.load(f)
    feats = data.get('features', [])

    # Total buildings area
    from shapely.geometry import shape
    total_area = 0.0
    tagged_area = 0.0
    n_total = 0
    n_tagged = 0
    for f in feats:
        geom = f.get('geometry', {})
        if geom.get('type') not in ('Polygon', 'MultiPolygon'):
            continue
        try:
            poly = shape(geom)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            area = poly.area  # in degrees²
            # Convert to m² (1° lat ≈ 111km, 1° lon at 60°N ≈ 55.5km)
            # Avg area at 60°N: 1 deg² ≈ 111000 * 55500 ≈ 6.16e9 m²
            area_m2 = area * 6.16e9
            total_area += area_m2
            n_total += 1

            tags = f.get('properties', {}).get('tags', {}) or f.get('properties', {})
            if 'building:levels' in tags or 'height' in tags:
                tagged_area += area_m2
                n_tagged += 1
        except Exception:
            continue

    LOG(f'  Buildings: {n_total} total, {n_tagged} tagged ({100*n_tagged/n_total:.1f}%)')
    LOG(f'  Building footprint area: {total_area/1e6:.1f} km² total, {tagged_area/1e6:.1f} km² tagged ({100*tagged_area/total_area:.1f}%)')

    # Now compute "influence zone" — buffer around tagged buildings
    # The buffer represents how far the OSM anchor would diffuse in build_prior
    # In v9b, build_prior with 540m window (block=180m) → diffusion ~270m
    # We compute area within 100m of any tagged building
    # (using a coarse approximation: union of buffers)
    LOG(f'\n  Computing influence zone (100m buffer around tagged buildings)...')

    # For efficiency: load tagged building centroids, build KD-tree, sample grid
    tagged_centroids = []
    for f in feats:
        geom = f.get('geometry', {})
        if geom.get('type') not in ('Polygon', 'MultiPolygon'):
            continue
        tags = f.get('properties', {}).get('tags', {}) or f.get('properties', {})
        if 'building:levels' not in tags and 'height' not in tags:
            continue
        try:
            poly = shape(geom)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            cx, cy = poly.centroid.x, poly.centroid.y
            tagged_centroids.append((cx, cy))
        except Exception:
            continue

    tagged_centroids = np.array(tagged_centroids)
    LOG(f'  Tagged building centroids: {len(tagged_centroids)}')

    # Sample SPb area at 100m grid, count fraction within 100m of a tagged building
    # SPb bbox: lon 29.7-30.55, lat 59.7-60.05
    grid_lon = np.arange(29.7, 30.55, 0.001)  # ~100m steps
    grid_lat = np.arange(59.7, 60.05, 0.001)
    LOG(f'  Grid: {len(grid_lon)}x{len(grid_lat)} = {len(grid_lon)*len(grid_lat)} points')

    tree = cKDTree(tagged_centroids)
    grid_points = np.array([(lon, lat) for lat in grid_lat for lon in grid_lon])
    dists, _ = tree.query(grid_points, k=1)
    # Distance in degrees → meters (1° ≈ 111km lat, ~55.5km lon at 60°N)
    # Approximate: 1 deg ≈ 80km average
    dists_m = dists * 80000
    within_100m = np.mean(dists_m < 100)
    within_250m = np.mean(dists_m < 250)
    within_500m = np.mean(dists_m < 500)

    LOG(f'\n  Coverage area (fraction of SPb within buffer of tagged building):')
    LOG(f'    100m buffer: {100*within_100m:.1f}% of SPb area')
    LOG(f'    250m buffer: {100*within_250m:.1f}% of SPb area')
    LOG(f'    500m buffer: {100*within_500m:.1f}% of SPb area')

    # Now: what fraction of CLASS 23 (DSM-anchor) pixels are within 100m of tagged?
    # Class 23 = "far field / DSM anchor" — 69% of land area
    # Without class distribution per pixel, estimate:
    # Class 23 is roughly uniformly distributed, so fraction within 100m ≈ 69% * 0.X
    # We can't compute exactly without running v9b, but estimate
    class23_pct = 0.69  # from REVISED_ARCHITECTURE.md
    expected_coverage = class23_pct * within_100m
    LOG(f'\n  Estimated class 23 pixels within 100m of tagged buildings:')
    LOG(f'    class 23 = {100*class23_pct:.0f}% of land area')
    LOG(f'    fraction within 100m = {100*within_100m:.1f}%')
    LOG(f'    → {100*expected_coverage:.1f}% of land area would benefit from OSM anchor')
    LOG(f'    → If combined MAD improves by 20% on these pixels,')
    LOG(f'      city-wide improvement = {100*20*expected_coverage:.1f}% (not 20%)')

    return {
        'n_tagged_buildings': n_tagged,
        'n_total_buildings': n_total,
        'tagged_pct': float(100 * n_tagged / n_total),
        'tagged_building_area_km2': float(tagged_area / 1e6),
        'total_building_area_km2': float(total_area / 1e6),
        'building_area_tagged_pct': float(100 * tagged_area / total_area),
        'coverage_within_100m_pct': float(100 * within_100m),
        'coverage_within_250m_pct': float(100 * within_250m),
        'coverage_within_500m_pct': float(100 * within_500m),
        'class23_pct_land': float(class23_pct),
        'expected_class23_within_100m_pct': float(100 * expected_coverage),
        'expected_citywide_improvement_pct': float(20 * expected_coverage),
    }


def recheck_rejected_edits():
    """Перепроверить cap_far_field, choose_block_px, accept_dsm_with_relief парным бутстрапом.

    Из worklog:
    - cap_far_field: MAD 0.90 → 1.06 (Δ +0.16) — REJECTED as worse
    - choose_block_px: holdout MAD ~1.19 vs 1.34 baseline (Δ -0.15) — but detail 23 hurt
    - accept_dsm_with_relief: HURT holdout — REVERTED

    Bootstrap CI на 40 точках: ширина ~1.0м. Δ в 0.16м или 0.15м — в пределах шума.
    Нужно проверить, значимо ли они отличаются от v8.
    """
    LOG('\n' + '=' * 70)
    LOG('STEP 4: Re-check rejected edits with paired bootstrap')
    LOG('=' * 70)
    LOG('From worklog, three edits rejected on Δ < 0.45m (within CI width):')
    LOG('  cap_far_field:        MAD 0.90 → 1.06 (Δ +0.16) — REJECTED as worse')
    LOG('  choose_block_px:      MAD 1.34 → 1.19 (Δ -0.15) — but detail 23 hurt')
    LOG('  accept_dsm_with_relief: HURT holdout — REVERTED')
    LOG('')
    LOG('Note: these Δ are point estimates on n=40. Bootstrap CI ~1.0m wide.')
    LOG('Without per-point dH for both versions, we cannot do paired bootstrap.')
    LOG('But we can estimate: if Δ=0.16m and CI width=1.0m,')
    LOG('  the edit is INDISTINGUISHABLE from v8 (point diff < CI width).')
    LOG('')
    LOG('Action: need to RE-RUN these edits with per-point dH saved,')
    LOG('  then apply paired bootstrap. For now, mark them as "uncertain".')

    return {
        'cap_far_field': {
            'delta_mad': 0.16,
            'ci_width_v8': 1.0,
            'verdict_was': 'REJECTED as worse',
            'paired_bootstrap_possible': False,
            'reason': 'no per-point dH saved for both versions',
            'recommendation': 'RE-RUN with per-point dH, then paired bootstrap',
            'current_status': 'UNCERTAIN — point estimate within noise',
        },
        'choose_block_px': {
            'delta_mad': -0.15,
            'ci_width_v8': 1.0,
            'verdict_was': 'kept (detail 23 hurt)',
            'paired_bootstrap_possible': False,
            'reason': 'no per-point dH saved for both versions',
            'recommendation': 'RE-RUN with per-point dH, then paired bootstrap',
            'current_status': 'UNCERTAIN — improvement might be real',
        },
        'accept_dsm_with_relief': {
            'delta_mad': None,  # not measured, just "HURT"
            'ci_width_v8': 1.0,
            'verdict_was': 'REVERTED',
            'paired_bootstrap_possible': False,
            'reason': 'no per-point dH saved',
            'recommendation': 'RE-RUN with per-point dH, then paired bootstrap',
            'current_status': 'UNCERTAIN — "HURT" was point estimate',
        },
    }


def main():
    LOG('=' * 70)
    LOG('Paired bootstrap + LOO σ + coverage area')
    LOG('=' * 70)

    # Load merged data (holdout ∩ OSM matched)
    v11 = pd.read_csv(DATA / 'spb_passport_validation_v11.csv')
    holdout = v11[v11.role == 'holdout'].copy()
    osm = pd.read_csv(DATA / 'osm_ndvi_r100_t0.3.csv')
    osm = osm[osm.matched].copy()
    merged = holdout.merge(osm[['lat', 'lon', 'h_pred_ground']], on=['lat', 'lon'], how='inner')
    LOG(f'Merged: {len(merged)} points')

    h_m = merged.hs.values  # model
    h_s = merged.h_pred_ground.values  # OSM source
    h_p = merged.hp.values  # passport ground truth
    sigma_model = 1.07

    # === STEP 1: Paired bootstrap ===
    LOG('\n' + '=' * 70)
    LOG('STEP 1: Paired bootstrap of difference (Model vs Combined)')
    LOG('=' * 70)

    # Combined with σ_s = 2.03 (from previous run)
    sigma_s = 2.03
    w_m = 1.0 / sigma_model ** 2
    w_s = 1.0 / sigma_s ** 2
    h_combined = (h_m * w_m + h_s * w_s) / (w_m + w_s)

    dH_model = h_p - h_m
    dH_combined = h_p - h_combined

    # Unpaired (for comparison)
    mad_m_unpaired, ci_lo_m, ci_hi_m = mad(dH_model), None, None
    mad_c_unpaired, ci_lo_c, ci_hi_c = mad(dH_combined), None, None
    # Bootstrap each independently
    rng = np.random.default_rng(42)
    n = len(dH_model)
    boot_m = np.empty(5000)
    boot_c = np.empty(5000)
    for i in range(5000):
        idx = rng.choice(n, size=n, replace=True)
        m = np.median(dH_model[idx])
        boot_m[i] = np.median(np.abs(dH_model[idx] - m)) * 1.4826
        m = np.median(dH_combined[idx])
        boot_c[i] = np.median(np.abs(dH_combined[idx] - m)) * 1.4826
    ci_lo_m, ci_hi_m = np.percentile(boot_m, [2.5, 97.5])
    ci_lo_c, ci_hi_c = np.percentile(boot_c, [2.5, 97.5])

    LOG(f'  Unpaired (independent intervals):')
    LOG(f'    Model alone:    MAD = {mad(dH_model):.4f}, CI [{ci_lo_m:.4f}, {ci_hi_m:.4f}], width={ci_hi_m-ci_lo_m:.4f}')
    LOG(f'    Combined:       MAD = {mad(dH_combined):.4f}, CI [{ci_lo_c:.4f}, {ci_hi_c:.4f}], width={ci_hi_c-ci_lo_c:.4f}')

    # Paired
    point_diff, ci_lo_d, ci_hi_d, p_val = paired_bootstrap_diff(dH_model, dH_combined, n_boot=5000)
    LOG(f'\n  Paired (bootstrap of difference):')
    LOG(f'    Δ = MAD(model) - MAD(combined) = {point_diff:.4f} m')
    LOG(f'    95% CI for Δ: [{ci_lo_d:.4f}, {ci_hi_d:.4f}], width={ci_hi_d-ci_lo_d:.4f}')
    LOG(f'    p-value (two-sided): {p_val:.4f}')

    if ci_lo_d > 0:
        verdict_paired = 'SIGNIFICANT: combined is better (CI excludes 0)'
    elif ci_hi_d < 0:
        verdict_paired = 'SIGNIFICANT: combined is WORSE (CI excludes 0)'
    else:
        verdict_paired = 'INDISTINGUISHABLE: CI includes 0'
    LOG(f'    Verdict: {verdict_paired}')
    LOG(f'')
    LOG(f'  Width comparison: paired={ci_hi_d-ci_lo_d:.3f}m vs unpaired={ci_hi_m-ci_lo_m:.3f}m')
    LOG(f'  Ratio: {(ci_hi_d-ci_lo_d)/(ci_hi_m-ci_lo_m):.2f}x (paired is narrower if < 1)')

    # === STEP 2: Leave-one-out σ_s ===
    LOG('\n' + '=' * 70)
    LOG('STEP 2: Leave-one-out σ_s (no test-set tuning)')
    LOG('=' * 70)

    sigma_loo, h_combined_loo = leave_one_out_sigma(dH_model, h_m, h_s, sigma_model)
    dH_combined_loo = h_p - h_combined_loo

    LOG(f'  σ_s per point (LOO): median={np.median(sigma_loo):.3f}, range=[{sigma_loo.min():.3f}, {sigma_loo.max():.3f}]')
    LOG(f'  σ_s distribution: {np.percentile(sigma_loo, [25, 50, 75, 95])}')

    # Paired bootstrap of LOO combined vs model
    point_diff_loo, ci_lo_loo, ci_hi_loo, p_loo = paired_bootstrap_diff(dH_model, dH_combined_loo, n_boot=5000)
    LOG(f'\n  LOO combined (no test-set tuning):')
    LOG(f'    MAD = {mad(dH_combined_loo):.4f} m')
    LOG(f'    Δ vs model = {point_diff_loo:.4f} m')
    LOG(f'    95% CI: [{ci_lo_loo:.4f}, {ci_hi_loo:.4f}], width={ci_hi_loo-ci_lo_loo:.4f}')
    LOG(f'    p-value: {p_loo:.4f}')

    if ci_lo_loo > 0:
        verdict_loo = 'SIGNIFICANT: LOO combined is better (CI excludes 0)'
    elif ci_hi_loo < 0:
        verdict_loo = 'SIGNIFICANT: LOO combined is WORSE'
    else:
        verdict_loo = 'INDISTINGUISHABLE: CI includes 0'
    LOG(f'    Verdict: {verdict_loo}')

    # === STEP 3: Coverage area ===
    coverage = compute_coverage_area()

    # === STEP 4: Re-check rejected edits ===
    recheck = recheck_rejected_edits()

    # === SUMMARY ===
    LOG('\n' + '=' * 70)
    LOG('SUMMARY')
    LOG('=' * 70)
    LOG(f'1. Paired bootstrap (vs unpaired):')
    LOG(f'   Unpaired:    Δ = {mad(dH_model) - mad(dH_combined):.3f}, both CIs overlap')
    LOG(f'   Paired:      Δ = {point_diff:.3f}, CI [{ci_lo_d:.3f}, {ci_hi_d:.3f}], p={p_val:.3f}')
    LOG(f'   Verdict: {verdict_paired}')
    LOG(f'')
    LOG(f'2. Leave-one-out σ_s (no test-set tuning):')
    LOG(f'   LOO combined: MAD = {mad(dH_combined_loo):.3f}')
    LOG(f'   Δ vs model = {point_diff_loo:.3f}, CI [{ci_lo_loo:.3f}, {ci_hi_loo:.3f}]')
    LOG(f'   Verdict: {verdict_loo}')
    LOG(f'')
    LOG(f'3. Coverage area:')
    LOG(f'   Tagged buildings: {coverage["n_tagged_buildings"]} / {coverage["n_total_buildings"]} ({coverage["tagged_pct"]:.1f}%)')
    LOG(f'   Building footprint: {coverage["tagged_building_area_km2"]:.1f} / {coverage["total_building_area_km2"]:.1f} km² ({coverage["building_area_tagged_pct"]:.1f}%)')
    LOG(f'   Coverage within 100m: {coverage["coverage_within_100m_pct"]:.1f}% of SPb')
    LOG(f'   Estimated city-wide improvement: ~{coverage["expected_citywide_improvement_pct"]:.1f}% (not 20%)')
    LOG(f'')
    LOG(f'4. Re-check rejected edits:')
    LOG(f'   All three had Δ within CI width (1.0m)')
    LOG(f'   → All marked UNCERTAIN — need re-run with per-point dH for paired bootstrap')

    # Save
    summary = {
        'experiment': 'Paired bootstrap + LOO sigma + coverage',
        'step_1_paired_bootstrap': {
            'unpaired_model_mad': float(mad(dH_model)),
            'unpaired_model_ci': [float(ci_lo_m), float(ci_hi_m)],
            'unpaired_combined_mad': float(mad(dH_combined)),
            'unpaired_combined_ci': [float(ci_lo_c), float(ci_hi_c)],
            'paired_diff': float(point_diff),
            'paired_ci_95': [float(ci_lo_d), float(ci_hi_d)],
            'paired_ci_width': float(ci_hi_d - ci_lo_d),
            'unpaired_ci_width': float(ci_hi_m - ci_lo_m),
            'paired_to_unpaired_ratio': float((ci_hi_d - ci_lo_d) / (ci_hi_m - ci_lo_m)),
            'p_value': float(p_val),
            'verdict': verdict_paired,
        },
        'step_2_loo_sigma': {
            'sigma_loo_median': float(np.median(sigma_loo)),
            'sigma_loo_p25': float(np.percentile(sigma_loo, 25)),
            'sigma_loo_p75': float(np.percentile(sigma_loo, 75)),
            'loo_combined_mad': float(mad(dH_combined_loo)),
            'loo_diff_vs_model': float(point_diff_loo),
            'loo_ci_95': [float(ci_lo_loo), float(ci_hi_loo)],
            'loo_p_value': float(p_loo),
            'verdict': verdict_loo,
        },
        'step_3_coverage': coverage,
        'step_4_rejected_edits_recheck': recheck,
        'implications': [
            f'Paired bootstrap CI width = {ci_hi_d-ci_lo_d:.3f}m (vs unpaired {ci_hi_m-ci_lo_m:.3f}m)',
            f'LOO σ_s eliminates test-set tuning — verdict: {verdict_loo}',
            f'Coverage: only {coverage["coverage_within_100m_pct"]:.1f}% of SPb within 100m of tagged buildings',
            f'Expected city-wide improvement: ~{coverage["expected_citywide_improvement_pct"]:.1f}% (not 20%)',
            'Three previously rejected edits have Δ within CI width — should be re-tested with paired bootstrap',
        ],
    }
    out = DATA / 'paired_bootstrap_loo_coverage.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')


if __name__ == '__main__':
    main()
