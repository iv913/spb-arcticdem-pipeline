#!/usr/bin/env python3
"""
18_osm_anchor_icesat_test.py
============================
Тест OSM-as-anchor на ICESat (37K точек) БЕЗ перезапуска pipeline.

КОНТЕКСТ:
- 17_osm_anchor_powerful_retest.py: на 25 passport точках OSM-as-anchor НЕ подтвердился
  (0/3 тестов значимы, weak test совпал с мощным)
- НО 25 точек — слишком мало для sign test (нужно 17+/25)
- ICESat даёт 37K paired points → power 34× больше
- Здесь: для каждой ICESat точки рядом с tagged building, построить OSM-prediction
  и сравнить combined vs baseline-alone через sign test + Wilcoxon

МЕТОД:
1. Для каждой ICESat точки рядом с tagged building (25m buffer):
   a. Найти ближайшее здание с levels
   b. Sample max(h_dsm) внутри полигона = h_roof_ell (raw ArcticDEM)
   c. h_pred_ground_bs77 = h_roof_ell - 16 (geoid) - levels * 3.5 (floor_h)
   d. h_baseline_bs77 = current pipeline (spb_ground_surface_2m.tif)
   e. Combined (inverse-variance weighted):
      h_combined_bs77 = (h_baseline/σ_m² + h_pred_osm/σ_s²) / (1/σ_m² + 1/σ_s²)
2. dH_baseline = h_te_bs77 - h_baseline_bs77
   dH_combined = h_te_bs77 - h_combined_bs77
3. Sign test + Wilcoxon + per-point bootstrap

ВЫХОД:
- Если ≥2/3 тестов значимы → OSM-as-anchor действительно добавляет информацию
- Если 0/3 → направление закрыто
"""
import json, sys, time
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds, Window
from rasterio.warp import transform, transform_bounds
from scipy import stats
from scipy.spatial import cKDTree
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from pyproj import Transformer

DATA = Path('/home/z/my-project/scripts/data')
UPLOAD = Path('/home/z/my-project/upload')
CACHE = Path('/home/z/my-project/scripts/dem_cache/sentinel2')
LOG = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)


def mad(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if a.size == 0: return float('nan')
    m = np.median(a)
    return float(np.median(np.abs(a - m)) * 1.4826)


def sign_test(dH_a, dH_b):
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)
    better = (abs_b < abs_a).sum()
    worse = (abs_b > abs_a).sum()
    tied = (abs_b == abs_a).sum()
    n = better + worse
    if n == 0:
        return 0, 0, 0, 1.0, 0.0
    p_val = stats.binomtest(better, n, p=0.5, alternative='two-sided').pvalue
    return int(better), int(worse), int(tied), float(p_val), float(100.0 * better / n)


def wilcoxon_test(dH_a, dH_b):
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)
    diff = abs_a - abs_b
    diff = diff[diff != 0]
    if len(diff) < 5:
        return None
    stat, p = stats.wilcoxon(diff, alternative='two-sided')
    return float(stat), float(p), float(np.median(diff))


def load_tagged_buildings():
    """Load OSM buildings with levels/height — return list of (lon, lat, levels, height, polygon)."""
    LOG('Loading OSM buildings...')
    with open(UPLOAD / 'data_backup/osm/spb_osm_buildings.geojson') as f:
        data = json.load(f)
    feats = data.get('features', [])

    buildings = []
    for feat in feats:
        geom = feat.get('geometry', {})
        if geom.get('type') not in ('Polygon', 'MultiPolygon'):
            continue
        tags = feat.get('properties', {}).get('tags', {}) or feat.get('properties', {})
        levels = tags.get('building:levels')
        height = tags.get('height')
        if not levels and not height:
            continue
        try:
            poly = shape(geom)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            cx, cy = poly.centroid.x, poly.centroid.y
            levels_int = int(float(levels)) if levels else None
            height_float = float(height) if height else None
            buildings.append({
                'lon': cx, 'lat': cy,
                'levels': levels_int,
                'height_m': height_float,
                'poly': poly,
            })
        except Exception:
            continue
    LOG(f'  tagged buildings: {len(buildings)}')
    return buildings


def load_icesat():
    """Load ICESat with current pipeline dZ."""
    ice = pd.read_csv(DATA / 'icesat_per_point_dZ.csv')
    LOG(f'ICESat: {len(ice)} paired points (with current pipeline dZ)')
    return ice


def load_arctic_dem_srcs():
    """Open raw ArcticDEM tiles via vsicurl."""
    urls = [
        ('center', '/vsicurl/https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_1_1_2m_v4.1_dem.tif'),
        ('east',   '/vsicurl/https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_2_1_2m_v4.1_dem.tif'),
        ('north',  '/vsicurl/https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_72/32_72_1_2_2m_v4.1_dem.tif'),
    ]
    LOG('Opening RAW ArcticDEM tiles...')
    srcs = []
    for name, url in urls:
        try:
            src = rasterio.open(url)
            srcs.append((name, src))
        except Exception as e:
            LOG(f'  {name} failed: {e}')
    return srcs


def sample_h_roof_in_polygon(poly_wgs84, dsm_srcs):
    """Sample max(h_dsm) inside building polygon. Returns h_roof_ell or None."""
    bounds = poly_wgs84.bounds  # lon/lat

    for tile_name, src in dsm_srcs:
        try:
            # Transform polygon to tile CRS
            t_dsm = Transformer.from_crs('EPSG:4326', src.crs, always_xy=True)
            poly_dsm = shp_transform(lambda x, y: t_dsm.transform(x, y), poly_wgs84)
            win_left, win_bot, win_right, win_top = poly_dsm.bounds

            tile_b = src.bounds
            if win_right < tile_b.left or win_left > tile_b.right or \
               win_top < tile_b.bottom or win_bot > tile_b.top:
                continue

            buf = 2.0
            win = rasterio.windows.from_bounds(
                win_left - buf, win_bot - buf, win_right + buf, win_top + buf,
                src.transform
            )
            dsm_data = src.read(1, window=win, masked=True)
            if dsm_data.size == 0 or not np.isfinite(dsm_data).any():
                continue

            win_transform = rasterio.windows.transform(win, src.transform)
            mask_inside = geometry_mask(
                [poly_dsm],
                out_shape=dsm_data.shape,
                transform=win_transform,
                invert=True,
                all_touched=True,
            )
            in_poly = dsm_data[mask_inside]
            in_poly = in_poly[np.isfinite(in_poly)]
            if in_poly.size < 5:
                continue

            # 90th percentile as roof height (robust to a few stray high pixels)
            return float(np.percentile(in_poly, 90))
        except Exception:
            continue
    return None


def main():
    LOG('=' * 70)
    LOG('OSM-as-anchor TEST on ICESat (37K points, near tagged buildings)')
    LOG('=' * 70)
    LOG('For each ICESat point near a tagged building:')
    LOG('  1. Find nearest tagged building with levels/height')
    LOG('  2. Sample h_roof_ell from raw ArcticDEM (max in polygon)')
    LOG('  3. h_pred_ground_bs77 = h_roof_ell - 16 (geoid) - levels * 3.5 (floor_h)')
    LOG('  4. h_baseline_bs77 from spb_ground_surface_2m.tif')
    LOG('  5. Combined = weighted (1/σ_m² + 1/σ_s²)')
    LOG('  6. Sign test + Wilcoxon + bootstrap')

    ice = load_icesat()
    buildings = load_tagged_buildings()
    dsm_srcs = load_arctic_dem_srcs()

    # Open current pipeline surface
    surface_path = UPLOAD / 'spb_ground_surface_2m.tif'
    LOG(f'Opening pipeline surface: {surface_path.name}')
    surf_src = rasterio.open(str(surface_path))

    # Build KD-tree on tagged building centroids
    bxy = np.array([[b['lon'], b['lat']] for b in buildings])
    tree = cKDTree(bxy)

    # For each ICESat point, find nearest tagged building within 25m
    ice_xy = np.column_stack([ice.lon.values, ice.lat.values])
    dists_deg, idxs = tree.query(ice_xy, k=1)
    dists_m = dists_deg * 80000  # ~80km per degree at 60°N

    near_mask = dists_m < 25
    LOG(f'ICESat points within 25m of tagged building: {near_mask.sum()}')
    if near_mask.sum() < 100:
        LOG('  too few, extending to 50m')
        near_mask = dists_m < 50
        LOG(f'  ICESat points within 50m: {near_mask.sum()}')

    # Process each near ICESat point
    near_indices = np.where(near_mask)[0]
    n_total = len(near_indices)
    LOG(f'\nProcessing {n_total} ICESat points...')

    sigma_m = 1.07  # pipeline σ
    sigma_s = 2.03  # OSM σ (from 5.22 LOO)
    GEOID_OFFSET = 16.0
    FLOOR_H = 3.5  # default; could be improved

    results = []
    t0 = time.time()
    for i, ice_idx in enumerate(near_indices):
        if i % 100 == 0 and i > 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (n_total - i)
            LOG(f'  {i}/{n_total} ({100*i/n_total:.1f}%), ETA {eta/60:.1f}m')

        ice_point = ice.iloc[ice_idx]
        building_idx = idxs[ice_idx]
        b = buildings[building_idx]

        # Skip if no levels/height
        if b['levels'] is None and b['height_m'] is None:
            continue

        # Sample h_roof from raw ArcticDEM at this building
        h_roof_ell = sample_h_roof_in_polygon(b['poly'], dsm_srcs)
        if h_roof_ell is None:
            continue

        # Sanity check for tall buildings
        if b['levels'] is not None and b['levels'] >= 5:
            expected_min_ell = b['levels'] * 2.5 + 12.0
            if h_roof_ell < expected_min_ell:
                continue  # skip — polygon probably wrong

        # Compute OSM prediction (BS-77)
        if b['levels'] is not None:
            h_pred_ground_bs77 = h_roof_ell - GEOID_OFFSET - b['levels'] * FLOOR_H
        else:
            h_pred_ground_bs77 = h_roof_ell - GEOID_OFFSET - b['height_m']

        # Get baseline surface at ICESat point (BS-77)
        xs, ys = transform('EPSG:4326', surf_src.crs, [ice_point.lon], [ice_point.lat])
        x, y = xs[0], ys[0]
        try:
            row, col = surf_src.index(x, y)
            if 0 <= row < surf_src.height and 0 <= col < surf_src.width:
                win_data = surf_src.read(1, window=Window(col, row, 1, 1))
                h_baseline_bs77 = float(win_data[0, 0])
                if h_baseline_bs77 == surf_src.nodata or not np.isfinite(h_baseline_bs77):
                    continue
            else:
                continue
        except Exception:
            continue

        # Combined: inverse-variance weighted
        w_m = 1.0 / sigma_m ** 2
        w_s = 1.0 / sigma_s ** 2
        h_combined_bs77 = (h_baseline_bs77 * w_m + h_pred_ground_bs77 * w_s) / (w_m + w_s)

        # Compare to ICESat ground truth
        h_te_bs77 = ice_point.h_te_ell - GEOID_OFFSET

        dH_baseline = h_te_bs77 - h_baseline_bs77
        dH_combined = h_te_bs77 - h_combined_bs77
        dH_osm_alone = h_te_bs77 - h_pred_ground_bs77

        results.append({
            'lat': ice_point.lat,
            'lon': ice_point.lon,
            'h_te_bs77': h_te_bs77,
            'h_baseline_bs77': h_baseline_bs77,
            'h_pred_osm_bs77': h_pred_ground_bs77,
            'h_combined_bs77': h_combined_bs77,
            'dH_baseline': dH_baseline,
            'dH_combined': dH_combined,
            'dH_osm_alone': dH_osm_alone,
            'levels': b['levels'] if b['levels'] is not None else -1,
            'dist_to_building_m': dists_m[ice_idx],
        })

    LOG(f'\nProcessed: {len(results)} valid pairs (out of {n_total})')

    if len(results) < 10:
        LOG('ERROR: too few valid pairs')
        return

    df = pd.DataFrame(results)
    df.to_csv(DATA / 'osm_anchor_icesat_per_point.csv', index=False)
    LOG(f'Saved → {DATA / "osm_anchor_icesat_per_point.csv"}')

    # === TESTS ===
    LOG('\n' + '=' * 70)
    LOG('Tests: Combined vs Baseline-alone on ICESat near tagged buildings')
    LOG('=' * 70)

    dH_a = df.dH_baseline.values
    dH_b = df.dH_combined.values

    mad_a = mad(dH_a)
    mad_b = mad(dH_b)
    LOG(f'  n={len(df)}')
    LOG(f'  Baseline (pipeline alone): MAD = {mad_a:.4f} m')
    LOG(f'  Combined (1/σ² weighted): MAD = {mad_b:.4f} m')
    LOG(f'  Δ MAD = {mad_b - mad_a:+.4f} m ({100*(mad_b-mad_a)/mad_a:+.1f}%)')

    # Also OSM alone for reference
    mad_osm = mad(df.dH_osm_alone.values)
    LOG(f'  OSM alone: MAD = {mad_osm:.4f} m (for reference)')

    # 1. Sign test
    n_b, n_w, n_t, p_sign, pct = sign_test(dH_a, dH_b)
    LOG(f'\n1. SIGN TEST (|dH_combined| < |dH_baseline|):')
    LOG(f'   better={n_b}, worse={n_w}, tied={n_t} (out of {n_b+n_w})')
    LOG(f'   pct better: {pct:.2f}%')
    LOG(f'   p-value: {p_sign:.6e}')
    threshold_pct = 50 + 1.96 * 50 / np.sqrt(n_b + n_w)
    LOG(f'   threshold for p<0.05: {threshold_pct:.2f}%')
    if p_sign < 0.001:
        direction = 'BETTER' if n_b > n_w else 'WORSE'
        LOG(f'   ✅ HIGHLY SIGNIFICANT: combined IS {direction} than baseline')
    elif p_sign < 0.05:
        direction = 'BETTER' if n_b > n_w else 'WORSE'
        LOG(f'   ✅ SIGNIFICANT: combined IS {direction}')
    else:
        LOG(f'   ⚠ not significant')

    # 2. Wilcoxon
    wilc = wilcoxon_test(dH_a, dH_b)
    LOG(f'\n2. WILCOXON signed-rank test:')
    if wilc:
        w_stat, p_wilc, median_diff = wilc
        LOG(f'   statistic: {w_stat:.1f}')
        LOG(f'   p-value: {p_wilc:.6e}')
        LOG(f'   median |dH_baseline|-|dH_combined| = {median_diff:+.4f} m')
        if p_wilc < 0.001:
            direction = 'BETTER' if median_diff > 0 else 'WORSE'
            LOG(f'   ✅ HIGHLY SIGNIFICANT ({direction})')
        elif p_wilc < 0.05:
            direction = 'BETTER' if median_diff > 0 else 'WORSE'
            LOG(f'   ✅ SIGNIFICANT ({direction})')
        else:
            LOG(f'   ⚠ not significant')

    # 3. Per-point bootstrap
    rng = np.random.default_rng(42)
    diff = np.abs(dH_a) - np.abs(dH_b)
    n = len(diff)
    boot = np.empty(500)
    for i in range(500):
        s = rng.choice(diff, size=n, replace=True)
        boot[i] = np.median(s)
    pt_lo, pt_hi = np.percentile(boot, [2.5, 97.5])
    pt_med = float(np.median(diff))
    LOG(f'\n3. PER-POINT median |dH_baseline|-|dH_combined| bootstrap:')
    LOG(f'   median: {pt_med:+.4f} m')
    LOG(f'   95% CI: [{pt_lo:+.4f}, {pt_hi:+.4f}], width={pt_hi-pt_lo:.4f}')
    if pt_lo > 0:
        LOG(f'   ✅ CI excludes 0 — combined BETTER')
    elif pt_hi < 0:
        LOG(f'   ✅ CI excludes 0 — combined WORSE')
    else:
        LOG(f'   ⚠ CI includes 0 — not significant')

    # === Combined verdict ===
    # Determine direction: positive median_diff = BETTER, negative = WORSE
    if wilc:
        direction_better = wilc[2] > 0  # median |dH_baseline|-|dH_combined| > 0 → combined better
    else:
        direction_better = pt_med > 0

    tests = [
        ('sign_test', p_sign < 0.05, n_b > n_w),  # True if BETTER direction
        ('wilcoxon', wilc[1] < 0.05 if wilc else False, wilc[2] > 0 if wilc else False),
        ('per_point_bootstrap', pt_lo > 0 or pt_hi < 0, pt_med > 0),
    ]
    n_pass = sum(1 for _, p, _ in tests if p)
    n_better = sum(1 for _, p, d in tests if p and d)
    n_worse = sum(1 for _, p, d in tests if p and not d)

    LOG(f'\n{"="*70}')
    LOG(f'FINAL VERDICT')
    LOG(f'{"="*70}')
    LOG(f'  Tests significant: {n_pass}/3')
    LOG(f'  Tests showing BETTER: {n_better}')
    LOG(f'  Tests showing WORSE:  {n_worse}')
    for name, passed, better in tests:
        if not passed:
            marker = '⚠'
            direction = 'not significant'
        elif better:
            marker = '✅'
            direction = 'BETTER'
        else:
            marker = '❌'
            direction = 'WORSE'
        LOG(f'  {marker} {name}: {direction}')

    if n_better >= 2:
        verdict = 'OSM-as-anchor ADDS INFORMATION (BETTER)'
        LOG(f'  → {verdict}')
    elif n_worse >= 2:
        verdict = 'OSM-as-anchor HURTS model (WORSE) — close direction'
        LOG(f'  → {verdict}')
    elif n_pass == 0:
        verdict = 'OSM-as-anchor NOT CONFIRMED (no significant effect)'
        LOG(f'  → {verdict}')
    else:
        verdict = f'MIXED — {n_better} better, {n_worse} worse, {3-n_pass} not significant'
        LOG(f'  → {verdict}')

    # Save
    summary = {
        'experiment': 'OSM-as-anchor test on ICESat (no pipeline re-run)',
        'method': 'For each ICESat near tagged building: OSM-pred + weighted combine + sign test',
        'n_paired_points': int(len(df)),
        'sigma_model': sigma_m,
        'sigma_source': sigma_s,
        'floor_h': FLOOR_H,
        'mad_baseline': float(mad_a),
        'mad_combined': float(mad_b),
        'mad_osm_alone': float(mad_osm),
        'delta_mad': float(mad_b - mad_a),
        'sign_test': {
            'n_better': n_b, 'n_worse': n_w, 'n_tied': n_t,
            'p_value': p_sign, 'pct_better': pct,
            'significant': bool(p_sign < 0.05),
        },
        'wilcoxon': {
            'statistic': w_stat if wilc else None,
            'p_value': p_wilc if wilc else None,
            'median_diff': median_diff if wilc else None,
            'significant': bool(wilc[1] < 0.05) if wilc else False,
        },
        'per_point_median_bootstrap': {
            'median': pt_med,
            'ci_95': [float(pt_lo), float(pt_hi)],
            'significant': bool(pt_lo > 0 or pt_hi < 0),
        },
        'tests_passing': n_pass,
        'verdict': verdict,
    }
    out = DATA / 'osm_anchor_icesat_test.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')


if __name__ == '__main__':
    main()
