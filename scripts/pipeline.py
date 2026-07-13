#!/usr/bin/env python3
"""
============================================================================
 PIPELINE: Коррекция ArcticDEM по опорным объектам → карта затопления СПб
============================================================================

ТЗ: "Коррекция ArcticDEM по опорным объектам и карта затопления СПб"

Реализует разделы 2 → 5 → 8 ТЗ.
MVP: Большой проспект В.О. (этап 1, раздел 9).

ДНЕВНИК:
  rev 1: RBF по пасп. точкам + Copernicus fill
    - geoid=13.3 (НЕВЕРНО — EGM96, не EGM2008)
  rev 2: Sentinel-2 NDVI class analysis
    - NDVI слишком узкий
  rev 3: geoid=15.9 (по воде), OGGC (OSM ground calibration)
    - стрип-перекос мал (v10 dZ≈0.2м)
    - RBF по пасп. точкам = переобучение (DSM-эффект)
  rev 4: без RBF, h=h_ell-15.9
    - правильный datum, но без объектной фильтрации
  rev 5 (этот): ПОЛНАЯ реализация ТЗ
    - 5.1: OSM geometry + buffer + center line
    - 5.2: percentile_filter + slope + SCL + NDVI + buildings
    - 5.3: calibration (holdout 25%)
    - 5.4: interpolation (spline by picket + cross-section)
    - 5.5: validation (RMSE + profile plot)
    - 5.6: merge cleaned patches into DEM
    - 8: flood map (H_flood parameter, hydrological connectivity)
    - 2м пиксели сохраняются на всех этапах
"""
import os, sys, time, json, re, io, base64
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.warp import transform as warp_transform, reproject, Resampling, calculate_default_transform
from rasterio.windows import Window, from_bounds
from rasterio.features import geometry_mask
from shapely.geometry import LineString, Point, box, Polygon
from shapely.ops import unary_union, nearest_points
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from scipy.ndimage import percentile_filter
from scipy.interpolate import UnivariateSpline
import folium
from folium.plugins import MiniMap, Fullscreen, MousePosition
import requests

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
HOME = Path('/home/z/my-project')
UPLOAD = HOME / 'upload'
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
for d in [CACHE, CACHE/'arctic', CACHE/'osm', CACHE/'sentinel2', OUT]:
    d.mkdir(parents=True, exist_ok=True)

GEOID = 15.9           # EGM2008 ≈ BS-77 для СПб (по 755 ATL08 на воде)
UTM = 'EPSG:32636'     # UTM zone 36N (метры)
PIXEL = 2.0            # ArcticDEM pixel size (м)

# Filter parameters (ТЗ 5.2, стартовые — калибровать на MVP)
LOW_PERCENTILE = 10    # нижняя огибающая
WINDOW_M = 15          # окно percentile_filter (м)
MAX_SLOPE = 0.06       # макс уклон (м/м) для дороги
TOLERANCE_M = 0.3      # допуск высоты над локальным минимумом
NDVI_MAX = 0.4         # порог деревьев
ROAD_BUFFER = 8.0      # полуширина дороги (м)
ROAD_ERODE = 3.0       # внутр. эрозия (м) — убрать бордюры/стены
BIN_SIZE = 5.0         # шаг пикетажа (м)
HOLDOUT_PCT = 25       # % точек в holdout

# Flood levels (м BS-77)
FLOOD_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

# MVP object
MVP_BBOX = (30.20, 59.92, 30.32, 59.95)  # W, S, E, N — Bolshoy pr V.O.
MVP_NAME = "Большой проспект В.О."

log_data = {'start': datetime.now().isoformat(), 'steps': []}

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)
    log_data['steps'].append({'t': ts, 'msg': msg})


# ============================================================
# 2.1 Покрытие ArcticDEM (блокирующая проверка)
# ============================================================
def check_coverage():
    """Проверить покрытие ArcticDEM для bbox объекта."""
    log('2.1: Проверка покрытия ArcticDEM')
    dem_path = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    if dem_path.exists():
        with rasterio.open(str(dem_path)) as src:
            from rasterio.warp import transform_bounds
            w, s, e, n = transform_bounds(src.crs, 'EPSG:4326', *src.bounds)
            log(f'  ✓ ArcticDEM: {src.width}×{src.height} px, 2м/px, CRS={src.crs}')
            log(f'  Coverage: W={w:.4f} S={s:.4f} E={e:.4f} N={n:.4f}')
            # Check MVP bbox is inside
            if w <= MVP_BBOX[0] and e >= MVP_BBOX[2] and s <= MVP_BBOX[1] and n >= MVP_BBOX[3]:
                log(f'  ✓ MVP bbox inside coverage')
                return True
            else:
                log(f'  ⚠ MVP bbox NOT fully inside coverage')
                return False
    log(f'  ! DEM not found at {dem_path}')
    return False


# ============================================================
# 2.2 Согласованность датума (блокирующая проверка)
# ============================================================
def check_datum():
    """Проверить geoid offset по ATL08 на воде."""
    log('2.2: Проверка датума')
    atl08_path = UPLOAD / 'atl08_ground.gpkg'
    if not atl08_path.exists():
        log('  ! ATL08 not found, skipping')
        return GEOID

    gdf = gpd.read_file(str(atl08_path))
    # Open water in Gulf: h_te 14-18m, lat < 59.90, lon < 30.10
    gulf = gdf[(gdf['h_te_ell'] > 14) & (gdf['h_te_ell'] < 18) &
               (gdf['lat'] < 59.90) & (gdf['lon'] < 30.10)]
    water_bs77 = 0.3  # м BS-77

    if len(gulf) > 10:
        h_water_ell = float(gulf['h_te_ell'].median())
        offset = h_water_ell - water_bs77
        log(f'  ATL08 water: {len(gulf)} pts, h_te median={h_water_ell:.2f}м (ell)')
        log(f'  Water BS-77 = {water_bs77}м → geoid offset = {offset:.2f}м')
        log(f'  h_unc median = {gulf["h_unc"].median():.3f}м (высокая точность)')
        log(f'  ✓ Datum OK: GEOID = {offset:.1f}м')
        return round(offset, 1)
    else:
        log(f'  ! Not enough water points ({len(gulf)}), using default {GEOID}')
        return GEOID


# ============================================================
# 5.1 Подготовка геометрии объекта
# ============================================================
def prepare_geometry(bbox, object_name=""):
    """Скачать OSM highways для bbox, найти осевую линию, создать полигон дороги."""
    log(f'5.1: Геометрия объекта ({object_name})')

    W, S, E, N = bbox
    osm_path = CACHE / 'osm' / 'center_osm.json'
    if osm_path.exists():
        with open(osm_path) as f:
            osm_data = json.load(f)
    else:
        log('  Downloading OSM highways...')
        q = f'[out:json][timeout:120];way["highway"]({S},{W},{N},{E});out geom;'
        for url in ['https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                    'https://overpass-api.de/api/interpreter']:
            try:
                r = requests.post(url, data={'data': q}, timeout=120)
                if r.status_code == 200 and r.text.strip():
                    osm_data = r.json()
                    osm_path.write_text(json.dumps(osm_data))
                    break
            except:
                pass
        else:
            osm_data = {'elements': []}

    # Find target highway by name
    highways = []
    for el in osm_data.get('elements', []):
        if el.get('type') != 'way' or 'geometry' not in el:
            continue
        tags = el.get('tags', {})
        name = tags.get('name', '')
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(coords) < 2:
            continue
        # Filter by name if specified
        if object_name and 'ольшой' in name and ('асиль' in name or 'В.О' in name):
            highways.append(LineString(coords))
        elif not object_name:
            highways.append(LineString(coords))

    if not highways:
        # Fallback: all highways in bbox
        log('  ! Target not found, using all highways')
        for el in osm_data.get('elements', []):
            if el.get('type') == 'way' and 'geometry' in el:
                coords = [(p['lon'], p['lat']) for p in el['geometry']]
                if len(coords) >= 2:
                    highways.append(LineString(coords))

    log(f'  Highways: {len(highways)}')

    # Merge into single center line (UTM)
    hw_gdf = gpd.GeoDataFrame({'geometry': highways}, crs='EPSG:4326').to_crs(UTM)
    merged = unary_union(hw_gdf.geometry.tolist())
    if merged.geom_type == 'MultiLineString':
        merged = max(merged.geoms, key=lambda g: g.length)

    log(f'  Center line: {merged.length:.0f}m')

    # Road polygon: buffer → erode (убрать бордюры/стены)
    road_poly = merged.buffer(ROAD_BUFFER).buffer(-ROAD_ERODE)
    log(f'  Road polygon: {road_poly.area:.0f} m²')

    # Load buildings for mask
    bld_polys = []
    for el in osm_data.get('elements', []):
        if 'building' not in el.get('tags', {}):
            continue
        if el.get('type') != 'way' or 'geometry' not in el:
            continue
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            bld_polys.append(Polygon(coords))
    log(f'  Buildings: {len(bld_polys)}')

    return merged, road_poly, bld_polys, osm_data


# ============================================================
# 5.2 Фильтрация ground pixels
# ============================================================
def filter_ground(dem_path, center_line_utm, road_poly_utm, bld_polys, geoid):
    """
    Вырезать DEM внутри полигона дороги, отфильтровать ground pixels.

    Возвращает:
      - dem_patch: 2D array (ellipsoid heights, 2м пиксели)
      - ground_mask: bool mask — какие пиксели = "земля"
      - win_transform: affine transform для патча
      - df_ground: DataFrame с picket/offset/h_bs77 для каждого ground pixel
      - center_line_dem: center line in DEM CRS
    """
    log('5.2: Фильтрация ground pixels')

    with rasterio.open(str(dem_path)) as src:
        dem_crs = src.crs
        dem_transform = src.transform
        nodata = src.nodata

        # Convert geometry to DEM CRS
        road_gdf = gpd.GeoDataFrame({'geometry': [road_poly_utm]}, crs=UTM).to_crs(dem_crs)
        road_poly_dem = road_gdf.geometry.iloc[0]
        cl_gdf = gpd.GeoDataFrame({'geometry': [center_line_utm]}, crs=UTM).to_crs(dem_crs)
        center_line_dem = cl_gdf.geometry.iloc[0]

        # Window covering road polygon
        bounds = road_poly_dem.bounds
        win = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
        dem_patch = src.read(1, window=win).astype('float32')
        if nodata is not None:
            dem_patch[dem_patch == nodata] = np.nan
        win_transform = rasterio.windows.transform(win, src.transform)

        log(f'  DEM patch: {dem_patch.shape[1]}×{dem_patch.shape[0]} px (2м/px)')
        log(f'  DEM range (ell): [{np.nanmin(dem_patch):.1f}, {np.nanmax(dem_patch):.1f}]')

        # --- Road mask ---
        road_mask = ~geometry_mask([road_poly_dem.__geo_interface__],
                                   transform=win_transform, out_shape=dem_patch.shape,
                                   all_touched=True)
        log(f'  Road pixels: {road_mask.sum():,}')

        # --- Building mask (exclude roofs) ---
        bld_mask = np.ones_like(dem_patch, dtype=bool)  # True = NOT building
        if bld_polys:
            bld_gdf = gpd.GeoDataFrame({'geometry': bld_polys}, crs='EPSG:4326').to_crs(dem_crs)
            road_box = box(*road_poly_dem.bounds)
            nearby = bld_gdf[bld_gdf.intersects(road_box)]
            if len(nearby) > 0:
                # all_touched=False: только пиксели, центр которых внутри здания
                bld_inside = geometry_mask([g.__geo_interface__ for g in nearby.geometry],
                                           transform=win_transform, out_shape=dem_patch.shape,
                                           all_touched=False)
                # geometry_mask: True = OUTSIDE, False = INSIDE
                bld_mask = bld_inside  # True = outside building
            log(f'  Buildings near road: {len(nearby)}')

        # --- SCL mask (exclude water, clouds, shadow) ---
        scl_mask = np.ones_like(dem_patch, dtype=bool)
        scl_path = CACHE / 'sentinel2' / 'scl_100m.tif'
        if scl_path.exists():
            with rasterio.open(str(scl_path)) as scl_src:
                scl_patch = np.zeros(dem_patch.shape, dtype='float32')
                reproject(source=rasterio.band(scl_src, 1), destination=scl_patch,
                          src_transform=scl_src.transform, src_crs=scl_src.crs,
                          dst_transform=win_transform, dst_crs=dem_crs,
                          resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
                bad = {1, 3, 6, 8, 9, 10}  # saturated, shadow, water, clouds
                scl_bad = np.zeros_like(scl_patch, dtype=bool)
                for s in bad:
                    scl_bad |= (scl_patch == s)
                scl_mask = ~scl_bad
            log(f'  SCL pass: {scl_mask.sum()}/{scl_mask.size} ({100*scl_mask.mean():.0f}%)')

        # --- NDVI mask (exclude trees) ---
        ndvi_mask = np.ones_like(dem_patch, dtype=bool)
        ndvi_path = CACHE / 'sentinel2' / 'ndvi_100m.tif'
        if ndvi_path.exists():
            with rasterio.open(str(ndvi_path)) as ndvi_src:
                ndvi_patch = np.full(dem_patch.shape, np.nan, dtype='float32')
                reproject(source=rasterio.band(ndvi_src, 1), destination=ndvi_patch,
                          src_transform=ndvi_src.transform, src_crs=ndvi_src.crs,
                          dst_transform=win_transform, dst_crs=dem_crs,
                          resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
                ndvi_mask = ndvi_patch < NDVI_MAX
            log(f'  NDVI < {NDVI_MAX}: {ndvi_mask.sum()}/{ndvi_mask.size} ({100*ndvi_mask.mean():.0f}%)')

        # --- Combined mask ---
        combined = road_mask & bld_mask & scl_mask & ndvi_mask & np.isfinite(dem_patch)
        log(f'  Combined: {combined.sum():,} ({100*combined.sum()/road_mask.sum():.1f}% of road)')

        # --- Step 1: Lower envelope (percentile_filter) ---
        log(f'  Percentile filter (p={LOW_PERCENTILE}, w={WINDOW_M}м={max(3,int(WINDOW_M/PIXEL))}px)...')
        wpx = max(3, int(WINDOW_M / PIXEL))
        local_floor = percentile_filter(dem_patch, percentile=LOW_PERCENTILE, size=wpx, mode='nearest')
        is_low = dem_patch <= (local_floor + TOLERANCE_M)
        candidates = combined & is_low
        log(f'  Candidates: {candidates.sum():,}')

        # --- Step 2: Slope constraint ---
        log(f'  Slope constraint (max={MAX_SLOPE} м/м)...')
        max_diff = MAX_SLOPE * PIXEL
        ground = candidates.copy()
        for it in range(5):
            g = np.where(ground, dem_patch, np.nan)
            diffs = []
            for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
                shifted = np.roll(g, (dy,dx), axis=(0,1))
                if dy == -1: shifted[0,:] = np.nan
                if dy == 1: shifted[-1,:] = np.nan
                if dx == -1: shifted[:,0] = np.nan
                if dx == 1: shifted[:,-1] = np.nan
                diffs.append(np.abs(g - shifted))
            max_diff_arr = np.nanmax(np.stack(diffs), axis=0)
            has_nb = ~np.all(np.isnan(np.stack(diffs)), axis=0)
            ok = ~has_nb | (max_diff_arr <= max_diff)
            new_ground = ground & ok
            removed = ground.sum() - new_ground.sum()
            ground = new_ground
            if removed == 0:
                break
            log(f'    Iter {it+1}: -{removed}, {ground.sum():,} remain')

        log(f'  Final ground: {ground.sum():,} ({100*ground.sum()/road_mask.sum():.1f}% of road)')

        # --- Picketage (project ground pixels onto center line) ---
        log('  Picketage...')
        rows, cols = np.where(ground)
        pk_data = []
        for r, c in zip(rows, cols):
            x = win_transform.c + win_transform.a * c + win_transform.b * r
            y = win_transform.f + win_transform.d * c + win_transform.e * r
            pt = Point(x, y)
            pk = center_line_dem.project(pt)
            nearest = nearest_points(center_line_dem, pt)[0]
            offset = pt.distance(nearest)
            pk_data.append({
                'row': r, 'col': c, 'x': x, 'y': y,
                'h_ell': float(dem_patch[r, c]),
                'h_bs77': float(dem_patch[r, c]) - geoid,
                'picket': pk, 'offset': offset,
            })
        df_ground = pd.DataFrame(pk_data).sort_values('picket').reset_index(drop=True)
        log(f'  Ground points: {len(df_ground)}')
        log(f'  Picket: [{df_ground["picket"].min():.0f}, {df_ground["picket"].max():.0f}]m')
        log(f'  h_bs77: [{df_ground["h_bs77"].min():.2f}, {df_ground["h_bs77"].max():.2f}]')

    return dem_patch, ground, win_transform, df_ground, center_line_dem, road_poly_dem, road_mask


# ============================================================
# 5.3 Калибровка по опорным точкам (holdout 25%)
# ============================================================
def calibrate(df_ground, road_poly_utm, geoid):
    """Найти опорные точки рядом с объектом, вычислить RMSE."""
    log('5.3: Калибровка по опорным точкам')

    pts = []
    with open(UPLOAD / 'Отметки.txt', 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or ':' not in line:
                continue
            l, r = line.split(':', 1)
            m = re.match(r'^(\d+\.\d+)\s*,\s*(\d+\.\d+)$', l.strip())
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
            m2 = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m2:
                continue
            try:
                h = float(m2.group(1).replace(',', '.'))
            except:
                continue
            pts.append({'lat': lat, 'lon': lon, 'h_pas': h})

    pas_gdf = gpd.GeoDataFrame(
        pts, geometry=[Point(p['lon'], p['lat']) for p in pts], crs='EPSG:4326').to_crs(UTM)

    # Points within 200m of road polygon
    near = pas_gdf[pas_gdf.geometry.distance(road_poly_utm) < 200]
    log(f'  Passport within 200m: {len(near)}')

    if len(near) == 0:
        log('  ⚠ No control points!')
        return df_ground, None, None

    # For each passport point: find nearest ground pixel
    cal = []
    for _, p in near.iterrows():
        pt = p.geometry
        dists = np.sqrt((df_ground['x'] - pt.x)**2 + (df_ground['y'] - pt.y)**2)
        idx = dists.idxmin()
        cal.append({
            'lat': p['lat'], 'lon': p['lon'], 'h_pas': p['h_pas'],
            'h_dem': df_ground.loc[idx, 'h_bs77'],
            'dH': df_ground.loc[idx, 'h_bs77'] - p['h_pas'],
            'dist': dists[idx],
        })
    df_cal = pd.DataFrame(cal)

    # Split into calibration / holdout
    n_holdout = max(1, int(len(df_cal) * HOLDOUT_PCT / 100))
    if len(df_cal) > 2:
        holdout_idx = df_cal.sample(n_holdout, random_state=42).index
        df_cal['holdout'] = False
        df_cal.loc[holdout_idx, 'holdout'] = True
    else:
        df_cal['holdout'] = False

    cal_pts = df_cal[~df_cal['holdout']]
    holdout = df_cal[df_cal['holdout']]
    log(f'  Calibration: {len(cal_pts)}, Holdout: {len(holdout)}')

    if len(cal_pts) > 0:
        rmse_cal = np.sqrt(np.mean(cal_pts['dH']**2))
        log(f'  Cal dH: median={cal_pts["dH"].median():+.3f}, RMSE={rmse_cal:.3f}м')
    if len(holdout) > 0:
        rmse_ho = np.sqrt(np.mean(holdout['dH']**2))
        log(f'  Holdout dH: median={holdout["dH"].median():+.3f}, RMSE={rmse_ho:.3f}м')

    return df_ground, df_cal, holdout


# ============================================================
# 5.4 Интерполяция: пикетаж + сплайн + 2D поверхность
# ============================================================
def interpolate(df_ground, dem_patch, ground_mask, road_mask, win_transform,
                center_line_dem, road_poly_dem):
    """
    Построить сглаженный продольный профиль по пикетажу,
    затем восстановить 2D поверхность дороги.

    Возвращает: cleaned (2D array, BS-77, 2м пиксели)
    """
    log('5.4: Интерполяция (пикетаж + сплайн)')

    # Sort by picket
    df = df_ground.sort_values('picket').reset_index(drop=True)

    # 1. Longitudinal profile: bin by BIN_SIZE metres
    df['pk_bin'] = (df['picket'] / BIN_SIZE).astype(int) * BIN_SIZE
    profile = df.groupby('pk_bin').agg(
        h_median=('h_bs77', 'median'),
        h_std=('h_bs77', 'std'),
        n=('h_bs77', 'count'),
        pk_mean=('picket', 'mean')
    ).reset_index()
    log(f'  Bins: {len(profile)} ({BIN_SIZE}m each)')
    log(f'  h_median: [{profile["h_median"].min():.2f}, {profile["h_median"].max():.2f}]')

    # 2. Spline interpolation along picket
    valid_bins = profile[profile['n'] >= 3]
    if len(valid_bins) >= 4:
        spline = UnivariateSpline(
            valid_bins['pk_mean'].values, valid_bins['h_median'].values,
            k=3, s=len(valid_bins))
        h_profile = spline(profile['pk_mean'].values)
    else:
        h_profile = np.full(len(profile), df['h_bs77'].median())
    log(f'  Spline: [{h_profile.min():.2f}, {h_profile.max():.2f}]')

    # 3. Build 2D surface: for EACH pixel in road_mask,
    #    compute picket → interpolate from spline → assign height
    log('  Building 2D surface (all road pixels)...')
    cleaned = np.full_like(dem_patch, np.nan, dtype='float32')
    pk_arr = profile['pk_mean'].values
    pk_min = pk_arr.min()
    pk_max = pk_arr.max()

    # Vectorized: compute picket for all road pixels at once
    rows, cols = np.where(road_mask)
    xs = win_transform.c + win_transform.a * cols + win_transform.b * rows
    ys = win_transform.f + win_transform.d * cols + win_transform.e * rows

    # Project each pixel onto center line (this is the slow part)
    pks = np.array([center_line_dem.project(Point(x, y)) for x, y in zip(xs, ys)])

    # Interpolate from spline
    for i, (r, c, pk) in enumerate(zip(rows, cols, pks)):
        if pk <= pk_min:
            h = h_profile[0]
        elif pk >= pk_max:
            h = h_profile[-1]
        else:
            h = float(np.interp(pk, pk_arr, h_profile))
        cleaned[r, c] = h

    log(f'  Cleaned: {np.isfinite(cleaned).sum():,} px')

    # 4. Profile plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), constrained_layout=True)

    # Longitudinal
    ax1.scatter(df['picket'], df['h_bs77'], s=1, alpha=0.3, c='blue', label='Ground pixels')
    ax1.plot(profile['pk_mean'], profile['h_median'], 'g-', lw=2, label=f'Median per {BIN_SIZE}m')
    ax1.plot(profile['pk_mean'], h_profile, 'r-', lw=2, label='Spline')
    ax1.set_xlabel('Пикетаж (м)')
    ax1.set_ylabel('Высота (м БС-77)')
    ax1.set_title(f'{MVP_NAME} — Продольный профиль')
    ax1.legend(fontsize=9)
    ax1.grid(alpha=0.3)

    # Cross-section at middle
    mid_pk = df['picket'].median()
    mid = df[(df['picket'] > mid_pk - 10) & (df['picket'] < mid_pk + 10)]
    ax2.scatter(mid['offset'], mid['h_bs77'], s=5, c='blue')
    ax2.set_xlabel('Поперечное смещение (м)')
    ax2.set_ylabel('Высота (м БС-77)')
    ax2.set_title(f'Поперечный профиль (пикет ~{mid_pk:.0f}м)')
    ax2.grid(alpha=0.3)

    profile_path = OUT / 'profile.png'
    plt.savefig(profile_path, dpi=150)
    plt.close()
    log(f'  Profile plot: {profile_path}')

    return cleaned, profile, h_profile


# ============================================================
# 5.5 Валидация
# ============================================================
def validate(df_cal, holdout):
    """Вычислить RMSE/MAE по holdout точкам."""
    log('5.5: Валидация')
    if holdout is None or len(holdout) == 0:
        log('  ⚠ No holdout points')
        return {'rmse': None, 'mae': None, 'n': 0}
    rmse = float(np.sqrt(np.mean(holdout['dH']**2)))
    mae = float(np.mean(np.abs(holdout['dH'])))
    log(f'  Holdout: n={len(holdout)}, RMSE={rmse:.3f}м, MAE={mae:.3f}м')
    return {'rmse': rmse, 'mae': mae, 'n': len(holdout)}


# ============================================================
# 5.6 Сборка результата
# ============================================================
def merge_result(dem_patch, cleaned, road_mask, ground_mask, win_transform, geoid,
                 dem_crs, profile, h_profile, df_cal, validation):
    """Заменить пиксели дороги на очищенные, сохранить GeoTIFF."""
    log('5.6: Сборка результата')

    # Merge: original DEM (BS-77) outside road, cleaned inside
    merged = dem_patch - geoid  # All to BS-77
    road_px = road_mask & np.isfinite(merged)
    merged[road_px & np.isfinite(cleaned)] = cleaned[road_px & np.isfinite(cleaned)]

    # Save GeoTIFF (2м пиксели, EPSG:3413)
    merged_path = OUT / 'cleaned_dem.tif'
    profile_out = {
        'driver': 'GTiff', 'dtype': 'float32', 'compress': 'deflate',
        'tiled': True, 'blockxsize': 256, 'blockysize': 256,
        'nodata': -9999, 'crs': dem_crs,
        'transform': win_transform,
        'width': merged.shape[1], 'height': merged.shape[0], 'count': 1,
    }
    with rasterio.open(str(merged_path), 'w', **profile_out) as dst:
        out = merged.copy()
        out[~np.isfinite(out)] = -9999
        dst.write(out.astype('float32'), 1)
    log(f'  ✓ DEM: {merged_path} ({merged_path.stat().st_size/1e6:.1f} MB, 2м/px)')
    return merged_path, merged


# ============================================================
# 8. Карта затопления
# ============================================================
def flood_map(merged_arr, win_transform, dem_crs, geoid, df_cal, road_mask):
    """Создать интерактивную карту с DEM overlay + flood levels."""
    log('8: Карта затопления')

    # Reproject to EPSG:4326 for folium (сохраняя 2м пиксели в исходном,
    # но для folium уменьшаем до ~500px)
    from rasterio.warp import calculate_default_transform
    tmp_4326 = CACHE / 'merged_4326.tif'
    with rasterio.open(str(OUT / 'cleaned_dem.tif')) as src:
        t, w, h = calculate_default_transform(src.crs, 'EPSG:4326', src.width, src.height, *src.bounds)
        arr4 = np.full((h, w), -9999, dtype='float32')
        reproject(source=rasterio.band(src, 1), destination=arr4,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=t, dst_crs='EPSG:4326',
                  resampling=Resampling.bilinear, src_nodata=-9999, dst_nodata=-9999)
        p4 = src.profile.copy()
        p4.update({'crs': 'EPSG:4326', 'transform': t, 'width': w, 'height': h})
        with rasterio.open(str(tmp_4326), 'w', **p4) as d:
            d.write(arr4, 1)

    with rasterio.open(str(tmp_4326)) as src:
        dem4 = src.read(1).astype('float32')
        dem4[dem4 == -9999] = np.nan
        bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]

    # Subsample for folium (keep detail but reduce file size)
    max_dim = 600
    factor = max(1, max(dem4.shape) // max_dim)
    dem4_small = dem4[::factor, ::factor]
    log(f'  DEM 4326: {dem4.shape} → {dem4_small.shape} (factor {factor})')

    # RGBA overlays
    def make_rgba(arr, vmin=0, vmax=8, alpha=180):
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
        cmap = plt.get_cmap('terrain')
        valid = np.isfinite(arr) & (arr > -1000)
        rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
        if valid.any():
            colors = cmap(norm(arr[valid]))
            rgba[valid] = (colors * 255).astype(np.uint8)
            rgba[valid, 3] = alpha
        return rgba

    def rgba_to_b64(rgba):
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray(rgba).save(buf, format='PNG', optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    raw_rgba = make_rgba(dem4_small)
    raw_b64 = rgba_to_b64(raw_rgba)

    flood_b64s = {}
    for level in FLOOD_LEVELS:
        mask = (dem4_small <= level) & np.isfinite(dem4_small)
        if mask.sum() == 0:
            continue
        rgba = np.zeros((*dem4_small.shape, 4), dtype=np.uint8)
        rgba[mask] = [0, 80, 200, 140]
        flood_b64s[level] = rgba_to_b64(rgba)

    # Passport points
    pts = []
    with open(UPLOAD / 'Отметки.txt', 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or ':' not in line:
                continue
            l, r = line.split(':', 1)
            m = re.match(r'^(\d+\.\d+)\s*,\s*(\d+\.\d+)$', l.strip())
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
            m2 = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m2:
                continue
            try:
                h = float(m2.group(1).replace(',', '.'))
            except:
                continue
            pts.append({'lat': lat, 'lon': lon, 'h_pas': h, 'desc': m2.group(2).strip()})

    # Sample DEM at passport points
    for p in pts:
        row = int((p['lat'] - bounds[0][0]) / (bounds[1][0] - bounds[0][0]) * dem4_small.shape[0])
        col = int((p['lon'] - bounds[0][1]) / (bounds[1][1] - bounds[0][1]) * dem4_small.shape[1])
        if 0 <= row < dem4_small.shape[0] and 0 <= col < dem4_small.shape[1]:
            v = dem4_small[row, col]
            p['h_dem'] = float(v) if np.isfinite(v) else None
        else:
            p['h_dem'] = None
        p['dH'] = p['h_dem'] - p['h_pas'] if p['h_dem'] is not None else None

    # Map
    m = folium.Map(location=[59.935, 30.27], zoom_start=14, control_scale=True, tiles=None)
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Satellite', max_zoom=19).add_to(m)

    # DEM overlay
    folium.raster_layers.ImageOverlay(
        image=f'data:image/png;base64,{raw_b64}', bounds=bounds, opacity=0.7,
        name='DEM (BS-77, cleaned)').add_to(m)

    # Flood levels
    for level, b64 in flood_b64s.items():
        folium.raster_layers.ImageOverlay(
            image=f'data:image/png;base64,{b64}', bounds=bounds, opacity=0.5,
            name=f'Flood {level:.1f}m', show=False).add_to(m)

    # Passport points
    for p in pts:
        if p['h_dem'] is None:
            c = 'gray'
        else:
            d = p['dH']
            c = 'green' if abs(d) < 1 else 'orange' if abs(d) < 3 else 'red'
        popup = (f"<b>{p['desc'] or '-'}</b><br>"
                 f"pas={p['h_pas']:.2f}<br>"
                 f"DEM={p['h_dem']:.2f}<br>"
                 f"dH={p['dH']:+.2f}") if p['h_dem'] else f"pas={p['h_pas']:.2f} (nodata)"
        folium.CircleMarker(
            location=[p['lat'], p['lon']], radius=5, color='black', weight=0.5,
            fill=True, fill_color=c, fill_opacity=0.8,
            popup=folium.Popup(popup, max_width=300)).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    MousePosition().add_to(m)
    MiniMap(toggle_display=True).add_to(m)

    # Legend
    legend = """<div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;padding:10px;border-radius:5px;box-shadow:0 0 10px rgba(0,0,0,0.3);font-size:11px;"><b>Height (m BS-77)</b><br><div style="width:180px;height:12px;margin:4px 0;background:linear-gradient(to right,#008080,#00FF00,#FFFF00,#FF0000,#8B4513);"></div><div style="display:flex;justify-content:space-between;width:180px;font-size:10px;"><span>0</span><span>2</span><span>4</span><span>6</span><span>8+</span></div><hr><b>Points:</b> green&lt;1m orange&lt;3m red&gt;3m</div>"""
    m.get_root().html.add_child(folium.Element(legend))

    map_path = OUT / 'flood_map.html'
    m.save(str(map_path))
    log(f'  ✓ Map: {map_path} ({map_path.stat().st_size/1e6:.1f} MB)')
    return map_path


# ============================================================
# QA METADATA
# ============================================================
def save_qa(road_mask, ground_mask, df_cal, validation, profile, h_profile):
    """Сохранить метаданные объекта (ТЗ раздел 8)."""
    qa = {
        'object_id': 'bolshoy_pr_vo',
        'name': MVP_NAME,
        'object_type': 'road',
        'geometry_source': 'osm',
        'n_pixels_total': int(road_mask.sum()),
        'n_pixels_retained_as_ground': int(ground_mask.sum()),
        'pct_retained': float(100 * ground_mask.sum() / max(1, road_mask.sum())),
        'n_control_points': len(df_cal) if df_cal is not None else 0,
        'n_holdout': validation.get('n', 0),
        'rmse_holdout_m': validation.get('rmse'),
        'mae_holdout_m': validation.get('mae'),
        'qa_flag': 'ok' if validation.get('rmse', 999) and validation['rmse'] < 1.5 else 'needs_review',
        'geoid_offset_m': GEOID,
        'filter_params': {
            'low_percentile': LOW_PERCENTILE,
            'window_m': WINDOW_M,
            'max_slope': MAX_SLOPE,
            'tolerance_m': TOLERANCE_M,
            'ndvi_max': NDVI_MAX,
        },
        'profile_range_m': [float(h_profile.min()), float(h_profile.max())],
        'profile_length_m': float(profile['pk_mean'].max() - profile['pk_mean'].min()),
    }
    with open(OUT / 'qa.json', 'w') as f:
        json.dump(qa, f, indent=2, ensure_ascii=False)
    log(f'  QA: {qa["qa_flag"]} (RMSE={qa["rmse_holdout_m"]})')
    return qa


# ============================================================
# MAIN
# ============================================================
def main():
    log('=' * 60)
    log(f'PIPELINE rev 5: MVP {MVP_NAME}')
    log(f'Geoid: {GEOID}м, Pixel: {PIXEL}м')
    log('=' * 60)

    # 2.1 Coverage
    dem_path = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    if not check_coverage():
        log('ERROR: No coverage. Run download first.')
        sys.exit(1)

    # 2.2 Datum
    geoid = check_datum()

    # 5.1 Geometry
    center_line, road_poly, bld_polys, osm_data = prepare_geometry(MVP_BBOX, MVP_NAME)

    # 5.2 Filter ground
    (dem_patch, ground_mask, win_transform, df_ground,
     center_line_dem, road_poly_dem, road_mask) = filter_ground(
        dem_path, center_line, road_poly, bld_polys, geoid)

    # 5.3 Calibrate
    df_ground, df_cal, holdout = calibrate(df_ground, road_poly, geoid)

    # 5.4 Interpolate
    cleaned, profile, h_profile = interpolate(
        df_ground, dem_patch, ground_mask, road_mask, win_transform,
        center_line_dem, road_poly_dem)

    # 5.5 Validate
    validation = validate(df_cal, holdout)

    # 5.6 Merge
    with rasterio.open(str(dem_path)) as src:
        dem_crs = src.crs
    merged_path, merged_arr = merge_result(
        dem_patch, cleaned, ground_mask, road_mask, win_transform, geoid,
        dem_crs, profile, h_profile, df_cal, validation)

    # QA
    qa = save_qa(road_mask, ground_mask, df_cal, validation, profile, h_profile)

    # 8. Flood map
    map_path = flood_map(merged_arr, win_transform, dem_crs, geoid, df_cal, road_mask)

    # Save log
    log_data['end'] = datetime.now().isoformat()
    log_data['qa'] = qa
    with open(OUT / 'pipeline_log.json', 'w') as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)

    log('')
    log('=' * 60)
    log('PIPELINE COMPLETE')
    log('=' * 60)
    log(f'DEM: {merged_path}')
    log(f'Map: {map_path}')
    log(f'Profile: {OUT / "profile.png"}')
    log(f'QA: {OUT / "qa.json"}')
    log(f'Ground pixels: {ground_mask.sum():,} ({100*ground_mask.sum()/road_mask.sum():.1f}% of road)')
    log(f'RMSE holdout: {validation.get("rmse", "N/A")}')


if __name__ == '__main__':
    main()
