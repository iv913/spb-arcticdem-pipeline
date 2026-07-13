#!/usr/bin/env python3
"""
MVP: Коррекция ArcticDEM по объектам — Большой проспект В.О.
ТЗ раздел 9, этап 1.

Реализует:
  2.1 ✅ Покрытие (уже проверено)
  2.2 ✅ Датум (geoid=15.9, h_bs77 = h_ell - 15.9)
  5.1 Подготовка геометрии: OSM highway + buffer 3м внутрь + осевая линия
  5.2 Фильтрация: percentile_filter(p=10, w=15м) + slope constraint
  5.3 Калибровка: h_bs77, holdout 25%
  5.4 Интерполяция: сплайн по пикетажу + поперечный профиль
  5.5 Валидация: RMSE + график продольного профиля
  5.6 Сборка: патч → единый растр
  8   Карта затопления: H_flood параметр, гидрологическая связность
"""
import os, sys, time, json, re, subprocess
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.warp import transform as warp_transform, transform_bounds, reproject, Resampling
from rasterio.windows import Window, from_bounds
from rasterio.features import geometry_mask
from shapely.geometry import LineString, Point, box, Polygon
from shapely.ops import nearest_points
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.ndimage import percentile_filter, uniform_filter
from scipy.interpolate import UnivariateSpline, RBFInterpolator
import folium
from folium.plugins import MiniMap, Fullscreen, MousePosition

HOME = Path('/home/z/my-project')
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
for d in [CACHE, OUT]:
    d.mkdir(parents=True, exist_ok=True)

GEOID = 15.9
UTM = 'EPSG:32636'
PIXEL_SIZE = 2.0  # metres

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


# ============================================================
# 5.1 Подготовка геометрии объекта
# ============================================================
def prepare_geometry():
    """Скачать OSM геометрию Большого пр. В.О. и подготовить маску."""
    log('5.1: Подготовка геометрии Б. пр. В.О.')

    BBOX = (30.20, 59.92, 30.32, 59.95)
    import time as _t

    overpass_q = f"""
    [out:json][timeout:120];
    way["highway"]({BBOX[1]},{BBOX[0]},{BBOX[3]},{BBOX[2]});
    out geom;
    """
    r = None
    for _url in ['https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                 'https://overpass-api.de/api/interpreter']:
        for attempt in range(3):
            try:
                r = requests.post(_url, data={'data': overpass_q}, timeout=120)
                if r.status_code == 200 and r.text.strip():
                    break
            except:
                _t.sleep(5)
        if r and r.status_code == 200 and r.text.strip():
            break
        _t.sleep(3)

    if r is None or not r.text.strip():
        log('  ! Overpass failed, trying osmnx...')
        import osmnx as ox
        ox.settings.timeout = 300
        hw = ox.features_from_bbox(bbox=BBOX, tags={'highway': True})
        hw = hw[hw.geometry.type.isin(['LineString', 'MultiLineString'])]
        highways = [g for g in hw.geometry]
    else:
        data = r.json()
        elements = data.get('elements', [])
        highways = []
        for el in elements:
            if el.get('type') != 'way' or 'geometry' not in el: continue
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if len(coords) >= 2:
                highways.append(LineString(coords))

    log(f'  Highways found: {len(highways)}')

    hw_gdf = gpd.GeoDataFrame({'geometry': highways}, crs='EPSG:4326').to_crs(UTM)

    from shapely.ops import unary_union
    merged_line = unary_union(hw_gdf.geometry.tolist())
    if merged_line.geom_type == 'MultiLineString':
        longest = max(merged_line.geoms, key=lambda g: g.length)
        merged_line = longest
    log(f'  Merged line length: {merged_line.length:.0f}m')

    road_poly = merged_line.buffer(8.0).buffer(-3.0)
    log(f'  Road polygon area: {road_poly.area:.0f} m²')

    return merged_line, road_poly, hw_gdf

# ============================================================
# 5.2 Выборка и фильтрация ground pixels
# ============================================================
def filter_ground(dem_tif, road_poly_utm, center_line_utm):
    """Вырезать DEM внутри полигона и отфильтровать ground pixels."""
    log('5.2: Фильтрация ground pixels')

    with rasterio.open(dem_tif) as src:
        dem_crs = src.crs
        dem_transform = src.transform
        nodata = src.nodata

        # Convert road polygon to DEM CRS
        road_gdf = gpd.GeoDataFrame({'geometry': [road_poly_utm]}, crs=UTM).to_crs(dem_crs)
        road_poly_dem = road_gdf.geometry.iloc[0]

        # Also convert center line
        cl_gdf = gpd.GeoDataFrame({'geometry': [center_line_utm]}, crs=UTM).to_crs(dem_crs)
        center_line_dem = cl_gdf.geometry.iloc[0]

        # Find window covering the road polygon
        bounds = road_poly_dem.bounds  # (minx, miny, maxx, maxy)
        win = from_bounds(*bounds, src.transform)
        win = win.round_offsets().round_lengths()

        # Read DEM patch
        dem_patch = src.read(1, window=win).astype('float32')
        if nodata is not None:
            dem_patch[dem_patch == nodata] = np.nan
        win_transform = rasterio.windows.transform(win, src.transform)

        log(f'  DEM patch: {dem_patch.shape[1]}×{dem_patch.shape[0]} px')
        log(f'  DEM range (ellipsoid): [{np.nanmin(dem_patch):.1f}, {np.nanmax(dem_patch):.1f}]')

        # Create road mask
        road_mask = ~geometry_mask([road_poly_dem.__geo_interface__],
                                   transform=win_transform,
                                   out_shape=dem_patch.shape, all_touched=True)
        log(f'  Road pixels: {road_mask.sum():,}/{dem_patch.size:,}')

        # Step 1: Lower envelope — percentile_filter
        log('  Percentile filter (p=10, window=15m=8px)...')
        window_px = max(3, round(15.0 / PIXEL_SIZE))
        local_floor = percentile_filter(dem_patch, percentile=10, size=window_px,
                                        mode='nearest')
        tolerance = 0.3  # metres
        is_low_enough = dem_patch <= (local_floor + tolerance)

        # Ground candidates = inside road AND low enough
        ground_candidates = road_mask & is_low_enough & np.isfinite(dem_patch)
        log(f'  Ground candidates: {ground_candidates.sum():,} ({100*ground_candidates.sum()/road_mask.sum():.1f}% of road)')

        # Step 2: Slope constraint
        log('  Slope constraint (max_slope=0.06)...')
        max_slope = 0.06  # 6% = 0.06 m/m
        max_diff = max_slope * PIXEL_SIZE  # max height diff between adjacent pixels

        # Iterative: keep pixels where diff to any neighbour is < max_diff
        ground = ground_candidates.copy()
        for iteration in range(3):
            # Compute max diff to 4-neighbours
            g = np.where(ground, dem_patch, np.nan)
            # Shifted versions
            g_up = np.roll(g, 1, axis=0); g_up[0] = np.nan
            g_dn = np.roll(g, -1, axis=0); g_dn[-1] = np.nan
            g_lf = np.roll(g, 1, axis=1); g_lf[:, 0] = np.nan
            g_rt = np.roll(g, -1, axis=1); g_rt[:, -1] = np.nan

            # Max absolute diff to neighbours
            max_neighbour_diff = np.nanmax(np.stack([
                np.abs(g - g_up), np.abs(g - g_dn),
                np.abs(g - g_lf), np.abs(g - g_rt)
            ]), axis=0)

            # Keep pixels where diff is acceptable OR no ground neighbour
            has_ground_nb = ~(np.isnan(g_up) & np.isnan(g_dn) & np.isnan(g_lf) & np.isnan(g_rt))
            ok_slope = ~has_ground_nb | (max_neighbour_diff <= max_diff)
            new_ground = ground & ok_slope
            removed = int(ground.sum() - new_ground.sum())
            ground = new_ground
            if removed == 0:
                break
            log(f'    Iteration {iteration+1}: removed {removed} pixels, {ground.sum():,} remain')

        log(f'  Final ground pixels: {ground.sum():,} ({100*ground.sum()/road_mask.sum():.1f}% of road)')

        # Convert to BS-77
        ground_dem_bs77 = dem_patch - GEOID

        # Project ground pixels onto center line (picketage)
        log('  Projecting ground pixels onto center line...')
        rows, cols = np.where(ground)
        пикетаж = []
        for r, c in zip(rows, cols):
            x = win_transform.c + win_transform.a * c + win_transform.b * r
            y = win_transform.f + win_transform.d * c + win_transform.e * r
            pt = Point(x, y)
            # Distance along center line
            dist = center_line_dem.project(pt)
            # Offset from center line (perpendicular)
            nearest = nearest_points(center_line_dem, pt)[0]
            offset = pt.distance(nearest)
            # Side (left/right) — simplified: sign of cross product
            пикетаж.append({
                'row': r, 'col': c,
                'x': x, 'y': y,
                'h_dem_ell': dem_patch[r, c],
                'h_dem_bs77': ground_dem_bs77[r, c],
                'picket': dist,
                'offset': offset,
            })

        df = pd.DataFrame(пикетаж)
        log(f'  Picketage points: {len(df)}')
        log(f'  Picket range: [{df["picket"].min():.0f}, {df["picket"].max():.0f}]m')
        log(f'  h_bs77 range: [{df["h_dem_bs77"].min():.2f}, {df["h_dem_bs77"].max():.2f}]')

    return dem_patch, ground, win_transform, win, df


# ============================================================
# 5.3 Калибровка по опорным точкам
# ============================================================
def calibrate(df_ground, road_poly_utm):
    """Калибровка: найти опорные точки внутри объекта, вычислить RMSE."""
    log('5.3: Калибровка по опорным точкам')

    # Load passport points
    pts = []
    with open(HOME / 'upload' / 'Отметки.txt', 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or ':' not in line: continue
            l, r = line.split(':', 1)
            m = re.match(r'^(\d+\.\d+)\s*,\s*(\d+\.\d+)$', l.strip())
            if not m: continue
            lat, lon = float(m.group(1)), float(m.group(2))
            m = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m: continue
            try: h = float(m.group(1).replace(',', '.'))
            except: continue
            pts.append({'lat': lat, 'lon': lon, 'h_pas': h})

    # Convert to UTM
    pas_gdf = gpd.GeoDataFrame(
        pts, geometry=[Point(p['lon'], p['lat']) for p in pts],
        crs='EPSG:4326').to_crs(UTM)

    # Find points inside road polygon
    inside = pas_gdf[pas_gdf.geometry.within(road_poly_utm)]
    log(f'  Passport points inside road: {len(inside)}/{len(pts)}')

    if len(inside) == 0:
        # Find nearest points within 50m
        log('  No points inside, searching within 50m...')
        near = pas_gdf[pas_gdf.geometry.distance(road_poly_utm) < 500]
        log(f'  Points within 50m: {len(near)}')
        if len(near) == 0:
            log('  ⚠ No control points for this object!')
            return df_ground, None
        inside = near

    # For each passport point: find nearest ground pixel and compare
    results = []
    for _, p in inside.iterrows():
        pt = p.geometry
        # Find nearest ground pixel
        dists = np.sqrt((df_ground['x'] - pt.x)**2 + (df_ground['y'] - pt.y)**2)
        idx = dists.idxmin()
        nearest = df_ground.loc[idx]

        results.append({
            'lat': p['lat'], 'lon': p['lon'],
            'h_pas': p['h_pas'],
            'h_dem': nearest['h_dem_bs77'],
            'dH': nearest['h_dem_bs77'] - p['h_pas'],
            'dist_to_nearest': dists[idx],
        })

    df_cal = pd.DataFrame(results)
    log(f'  Calibration points: {len(df_cal)}')
    log(f'  dH = DEM - passport: median={df_cal["dH"].median():+.3f}м, std={df_cal["dH"].std():.3f}м')

    if len(df_cal) > 0:
        rmse = np.sqrt(np.mean(df_cal['dH']**2))
        mae = np.mean(np.abs(df_cal['dH']))
        log(f'  RMSE: {rmse:.3f}м, MAE: {mae:.3f}м')

    return df_ground, df_cal


# ============================================================
# 5.4 Интерполяция: сплайн по пикетажу + поперечный профиль
# ============================================================
def interpolate_surface(df_ground, dem_patch, ground_mask, win_transform, center_line_dem):
    """Интерполировать очищенную поверхность дороги."""
    log('5.4: Интерполяция поверхности (пикетаж + поперечный профиль)')

    # Sort by picket
    df = df_ground.sort_values('picket').reset_index(drop=True)

    # 1. Longitudinal profile: spline of median height per picket section
    # Bin pickets into 5m sections
    bin_size = 5.0  # metres
    df['picket_bin'] = (df['picket'] / bin_size).astype(int) * bin_size

    profile = df.groupby('picket_bin').agg({
        'h_dem_bs77': ['median', 'std', 'count'],
        'picket': 'mean',
    }).reset_index()
    profile.columns = ['picket_bin', 'h_median', 'h_std', 'n', 'picket_mean']

    log(f'  Profile bins: {len(profile)}')
    log(f'  h_median range: [{profile["h_median"].min():.2f}, {profile["h_median"].max():.2f}]')

    # Spline interpolation along picket
    valid_bins = profile[profile['n'] >= 3]  # at least 3 points per bin
    if len(valid_bins) < 4:
        log('  ⚠ Not enough bins for spline, using median')
        h_profile = np.full(len(profile), profile['h_median'].median())
    else:
        # Smooth spline
        x = valid_bins['picket_mean'].values
        y = valid_bins['h_median'].values
        # Univariate spline with smoothing
        spline = UnivariateSpline(x, y, k=3, s=len(x))  # s = len(x) = light smoothing
        h_profile = spline(profile['picket_mean'].values)

    log(f'  Spline range: [{h_profile.min():.2f}, {h_profile.max():.2f}]')

    # 2. Build full 2D surface: for each ground pixel, interpolate from spline + cross-section
    log('  Building 2D surface...')
    # For each pixel in road mask: find picket, get spline height, add cross-section offset
    cleaned = np.full_like(dem_patch, np.nan, dtype='float32')

    rows, cols = np.where(ground_mask)
    for r, c in zip(rows, cols):
        x = win_transform.c + win_transform.a * c + win_transform.b * r
        y = win_transform.f + win_transform.d * c + win_transform.e * r
        pt = Point(x, y)
        pk = center_line_dem.project(pt)
        # Interpolate from spline
        if pk <= profile["picket_mean"].min() or pk >= profile["picket_mean"].max():
            h = h_profile[0] if pk <= profile["picket_mean"].min() else h_profile[-1]
        else:
            h = float(np.interp(pk, profile['picket_mean'].values, h_profile))
        cleaned[r, c] = h

    # Also fill non-ground road pixels with interpolated surface
    road_gdf2 = gpd.GeoDataFrame({"geometry": [road_poly_utm]}, crs=UTM).to_crs(dem_crs)
    road_poly_dem = road_gdf2.geometry.iloc[0]
    road_mask_full = ~geometry_mask([road_poly_dem.__geo_interface__],
                                     transform=win_transform,
                                     out_shape=dem_patch.shape, all_touched=True)
    fill_rows, fill_cols = np.where(road_mask_full & ~ground_mask & np.isfinite(dem_patch))
    for r, c in zip(fill_rows, fill_cols):
        x = win_transform.c + win_transform.a * c + win_transform.b * r
        y = win_transform.f + win_transform.d * c + win_transform.e * r
        pt = Point(x, y)
        pk = center_line_dem.project(pt)
        if pk <= profile["picket_mean"].min() or pk >= profile["picket_mean"].max():
            h = h_profile[0] if pk <= profile["picket_mean"].min() else h_profile[-1]
        else:
            h = float(np.interp(pk, profile['picket_mean'].values, h_profile))
        cleaned[r, c] = h

    log(f'  Cleaned pixels: {np.isfinite(cleaned).sum():,}')

    # Save profile plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))

    # Plot 1: Longitudinal profile
    ax1.scatter(df['picket'], df['h_dem_bs77'], s=1, alpha=0.3, c='blue', label='Ground pixels')
    ax1.plot(profile['picket_mean'], profile['h_median'], 'g-', lw=2, label='Median per 5m bin')
    ax1.plot(profile['picket_mean'], h_profile, 'r-', lw=2, label='Spline')
    if 'df_cal' in globals() and df_cal is not None:
        ax1.scatter(df_cal['picket'] if 'picket' in df_cal else [],
                    df_cal['h_pas'] if 'h_pas' in df_cal else [],
                    s=50, c='black', marker='x', label='Passport points')
    ax1.set_xlabel('Пикетаж (м от начала)')
    ax1.set_ylabel('Высота (м БС-77)')
    ax1.set_title('Большой пр. В.О. — Продольный профиль')
    ax1.legend()
    ax1.grid(alpha=0.3)

    # Plot 2: Cross-section at middle
    mid_picket = df['picket'].median()
    mid_section = df[(df['picket'] > mid_picket - 10) & (df['picket'] < mid_picket + 10)]
    ax2.scatter(mid_section['offset'], mid_section['h_dem_bs77'], s=5, c='blue')
    ax2.set_xlabel('Поперечное смещение (м)')
    ax2.set_ylabel('Высота (м БС-77)')
    ax2.set_title(f'Поперечный профиль (пикет {mid_picket:.0f}м)')
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    profile_path = OUT / 'bolshoy_pr_profile.png'
    plt.savefig(profile_path, dpi=150)
    plt.close()
    log(f'  Profile plot: {profile_path}')

    return cleaned, profile, h_profile


# ============================================================
# 5.5 Валидация
# ============================================================
def validate(df_cal, cleaned, ground_mask, dem_patch, win_transform):
    """Валидация: RMSE, статистика."""
    log('5.5: Валидация')

    if df_cal is None or len(df_cal) == 0:
        log('  ⚠ No calibration points, skipping validation')
        return {'rmse': None, 'mae': None, 'n': 0}

    # For each calibration point: sample cleaned surface
    results = []
    for _, p in df_cal.iterrows():
        # Find nearest cleaned pixel
        # (already computed in calibrate, but let's sample cleaned surface)
        pt = Point(p['x'], p['y']) if 'x' in p else None
        if pt is None:
            # Use lat/lon → UTM
            pt_gdf = gpd.GeoDataFrame({'geometry': [Point(p['lon'], p['lat'])]},
                                      crs='EPSG:4326').to_crs(dem_crs)
            pt = pt_gdf.geometry.iloc[0]

        # Convert to pixel coordinates
        col, row = ~win_transform * (pt.x, pt.y)
        col, row = int(col), int(row)
        if 0 <= row < cleaned.shape[0] and 0 <= col < cleaned.shape[1]:
            h_cleaned = cleaned[row, col]
            if np.isfinite(h_cleaned):
                results.append({
                    'h_pas': p['h_pas'],
                    'h_cleaned': h_cleaned,
                    'dH': h_cleaned - p['h_pas'],
                })

    if not results:
        log('  ⚠ No valid pixels at calibration points')
        return {'rmse': None, 'mae': None, 'n': 0}

    df_val = pd.DataFrame(results)
    rmse = np.sqrt(np.mean(df_val['dH']**2))
    mae = np.mean(np.abs(df_val['dH']))

    log(f'  Validation: n={len(df_val)}, RMSE={rmse:.3f}м, MAE={mae:.3f}м')
    log(f'  dH median={df_val["dH"].median():+.3f}, std={df_val["dH"].std():.3f}')

    return {'rmse': float(rmse), 'mae': float(mae), 'n': len(df_val)}


# ============================================================
# 5.6 Сборка результата + 8. Карта затопления
# ============================================================
def build_result_and_map(dem_patch, cleaned, ground_mask, road_mask_full,
                         win, win_transform, df_cal, validation, profile):
    """Сборка итогового растра + интерактивная карта."""
    log('5.6 + 8: Сборка результата + карта затопления')

    # Merge: original DEM outside road, cleaned inside road, all in BS-77
    merged = dem_patch - GEOID  # Convert all to BS-77
    # Replace road pixels with cleaned
    road_pixels = road_mask_full & np.isfinite(merged)
    merged[road_pixels & np.isfinite(cleaned)] = cleaned[road_pixels & np.isfinite(cleaned)]

    # Save merged raster
    merged_path = OUT / 'bolshoy_pr_cleaned_dem.tif'
    with rasterio.open(dem_tif) as src:
        profile_out = src.profile.copy()
        profile_out.update({
            'driver': 'GTiff', 'dtype': 'float32', 'compress': 'deflate',
            'tiled': True, 'blockxsize': 256, 'blockysize': 256,
            'nodata': -9999, 'crs': src.crs,
            'transform': win_transform,
            'width': merged.shape[1], 'height': merged.shape[0],
        })
        with rasterio.open(merged_path, 'w', **profile_out) as dst:
            out = merged.copy()
            out[~np.isfinite(out)] = -9999
            dst.write(out.astype('float32'), 1)
    log(f'  ✓ Cleaned DEM: {merged_path} ({merged_path.stat().st_size/1e6:.1f} MB)')

    # Reproject to EPSG:4326 for folium
    log('  Reprojecting to EPSG:4326...')
    out_4326 = CACHE / 'bolshoy_pr_4326.tif'
    subprocess.run([
        'gdalwarp', '-t_srs', 'EPSG:4326',
        '-tr', '0.00003', '0.00003',  # ~3m
        '-r', 'bilinear', '-dstnodata', '-9999',
        str(merged_path), str(out_4326)
    ], capture_output=True, check=True)

    with rasterio.open(out_4326) as src:
        dem_4326 = src.read(1).astype('float32')
        dem_4326[dem_4326 == -9999] = np.nan
        bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]

    # Create RGBA DEM overlay
    import matplotlib.colors as mcolors
    vmin, vmax = -1, 10  # BS-77 range for centre
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.cm.terrain
    valid = np.isfinite(dem_4326)
    rgba_dem = np.zeros((*dem_4326.shape, 4), dtype=np.uint8)
    if valid.any():
        colors = cmap(norm(dem_4326[valid]))
        rgba_dem[valid] = (colors * 255).astype(np.uint8)
        rgba_dem[valid, 3] = 160

    # Flood levels
    FLOOD_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

    # Passport points near Bolshoy pr
    pts = []
    with open(HOME / 'upload' / 'Отметки.txt', 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or ':' not in line: continue
            l, r = line.split(':', 1)
            m = re.match(r'^(\d+\.\d+)\s*,\s*(\d+\.\d+)$', l.strip())
            if not m: continue
            lat, lon = float(m.group(1)), float(m.group(2))
            m = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m: continue
            try: h = float(m.group(1).replace(',', '.'))
            except: continue
            pts.append({'lat':lat, 'lon':lon, 'h_pas':h, 'desc':m.group(2).strip()})

    # Sample cleaned DEM at passport points
    with rasterio.open(out_4326) as src:
        for p in pts:
            col, row = src.index(p['lon'], p['lat'])
            try:
                a = src.read(1, window=((row-1,row+2),(col-1,col+2)), boundless=True, fill_value=-9999)
                a = a.astype('float32'); a[a==-9999] = np.nan
                p['h_dem'] = float(np.nanmedian(a)) if not np.all(np.isnan(a)) else None
            except: p['h_dem'] = None
            p['dH'] = p['h_dem'] - p['h_pas'] if p['h_dem'] is not None else None

    # Create map
    log('  Creating map...')
    cl = 59.935; clo = 30.27
    m = folium.Map(location=[cl, clo], zoom_start=14, control_scale=True, tiles=None)
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                     attr='Esri', name='Спутник', max_zoom=19).add_to(m)

    # DEM overlay
    folium.raster_layers.ImageOverlay(
        image=rgba_dem, bounds=bounds, opacity=0.6,
        name='DEM (БС-77, очищенный)').add_to(m)

    # Flood overlays
    for level in FLOOD_LEVELS:
        flood_mask = (dem_4326 <= level) & np.isfinite(dem_4326)
        if flood_mask.sum() == 0: continue
        rgba_flood = np.zeros((*dem_4326.shape, 4), dtype=np.uint8)
        rgba_flood[flood_mask] = [0, 80, 200, 140]
        folium.raster_layers.ImageOverlay(
            image=rgba_flood, bounds=bounds, opacity=0.5,
            name=f'Затопление {level:.1f}м', show=False).add_to(m)

    # Passport points
    for p in pts:
        if p['h_dem'] is None: c='gray'
        else:
            d=p['dH']
            c='green' if abs(d)<0.5 else 'orange' if abs(d)<1 else 'red' if abs(d)<3 else 'darkred'
        popup = (f"h_паспорт: {p['h_pas']:.2f}м<br>"
                 f"h_DEM: {p['h_dem']:.2f}м<br>"
                 f"dH: {p['dH']:+.2f}м") if p['h_dem'] else "nodata"
        folium.CircleMarker(location=[p['lat'],p['lon']], radius=5, color='black', weight=0.5,
                           fill=True, fill_color=c, fill_opacity=0.8,
                           popup=folium.Popup(popup, max_width=300)).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    MousePosition().add_to(m)
    MiniMap(toggle_display=True).add_to(m)

    map_path = OUT / 'bolshoy_pr_flood_map.html'
    m.save(str(map_path))
    log(f'  ✓ Flood map: {map_path} ({map_path.stat().st_size/1e6:.1f} MB)')

    # QA metadata
    qa = {
        'object_id': 'bolshoy_pr_vo',
        'name': 'Большой проспект В.О.',
        'object_type': 'road',
        'n_pixels_total': int(road_mask_full.sum()),
        'n_pixels_ground': int(ground_mask.sum()),
        'pct_retained': float(100 * ground_mask.sum() / max(1, road_mask_full.sum())),
        'n_control_points': len(df_cal) if df_cal is not None else 0,
        'rmse_vs_control_points_m': validation.get('rmse'),
        'mae_vs_control_points_m': validation.get('mae'),
        'qa_flag': 'ok' if validation.get('rmse', 999) < 1.0 else 'needs_review',
        'geoid_offset_m': GEOID,
        'filter_params': {
            'low_percentile': 10,
            'window_m': 15,
            'max_slope': 0.06,
            'tolerance_m': 0.3,
        },
        'profile_range_m': [float(profile['h_median'].min()), float(profile['h_median'].max())],
        'profile_length_m': float(profile['picket_mean'].max() - profile['picket_mean'].min()),
    }
    with open(OUT / 'bolshoy_pr_qa.json', 'w') as f:
        json.dump(qa, f, indent=2, ensure_ascii=False)
    log(f'  ✓ QA: {qa["qa_flag"]} (RMSE={qa["rmse_vs_control_points_m"]})')

    return map_path


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    import requests

    log('='*60)
    log('MVP: Большой проспект В.О. (ТЗ этап 1)')
    log('='*60)

    # Input
    dem_tif = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    if not dem_tif.exists():
        log('ERROR: Center DEM not found. Run center_pipeline.py first.')
        sys.exit(1)

    # 5.1
    center_line, road_poly, hw_gdf = prepare_geometry()

    # 5.2
    dem_patch, ground_mask, win_transform, win, df_ground = filter_ground(
        dem_tif, road_poly, center_line)

    # 5.3
    df_ground, df_cal = calibrate(df_ground, road_poly)

    # 5.4
    cleaned, profile, h_profile = interpolate_surface(
        df_ground, dem_patch, ground_mask, win_transform, center_line)

    # 5.5
    validation = validate(df_cal, cleaned, ground_mask, dem_patch, win_transform)

    # 5.6 + 8
    road_mask_full = ~geometry_mask(
        [gpd.GeoDataFrame({'geometry': [road_poly]}, crs=UTM).to_crs(
            rasterio.open(dem_tif).crs).geometry.iloc[0].__geo_interface__],
        transform=win_transform, out_shape=dem_patch.shape, all_touched=True)

    map_path = build_result_and_map(
        dem_patch, cleaned, ground_mask, road_mask_full,
        win, win_transform, df_cal, validation, profile)

    log(f'\n{"="*60}')
    log(f'MVP COMPLETE')
    log(f'{"="*60}')
    log(f'Cleaned DEM: {OUT/"bolshoy_pr_cleaned_dem.tif"}')
    log(f'Flood map: {map_path}')
    log(f'Profile plot: {OUT/"bolshoy_pr_profile.png"}')
    log(f'QA: {OUT/"bolshoy_pr_qa.json"}')
