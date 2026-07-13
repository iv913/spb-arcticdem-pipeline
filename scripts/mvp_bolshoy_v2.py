#!/usr/bin/env python3
"""
MVP v2: Большой пр. В.О. с Sentinel-2 SCL + NDVI фильтрацией.

Улучшения v2:
  - SCL mask: исключить water(6), cloud shadow(3), clouds(8-10)
  - NDVI mask: исключить деревья (NDVI > 0.4)
  - Эти маски применяются ДО percentile_filter → меньше шума → точнее ground
  - Исправлены баги v1 (road_poly_dem, h_profile_x_min)
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
from shapely.ops import unary_union, nearest_points
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.ndimage import percentile_filter
from scipy.interpolate import UnivariateSpline
import folium
from folium.plugins import MiniMap, Fullscreen, MousePosition
import matplotlib.colors as mcolors
import requests

HOME = Path('/home/z/my-project')
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
for d in [CACHE, OUT]:
    d.mkdir(parents=True, exist_ok=True)

GEOID = 15.9
UTM = 'EPSG:32636'
PIXEL = 2.0  # metres

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


def download_osm_highways(bbox):
    """Скачать OSM highways через Overpass."""
    W, S, E, N = bbox
    q = f'[out:json][timeout:120];way["highway"]({S},{W},{N},{E});out geom;'
    for url in ['https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                'https://overpass-api.de/api/interpreter']:
        try:
            r = requests.post(url, data={'data': q}, timeout=120)
            if r.status_code == 200 and r.text.strip():
                return r.json()
        except:
            pass
    return {'elements': []}


def sample_raster_at_points(raster_path, lats, lons, crs=None):
    """Сэмплировать растр в точках (lat, lon)."""
    with rasterio.open(raster_path) as src:
        rc = crs or src.crs
        xs, ys = warp_transform('EPSG:4326', rc, lons, lats)
        vals = []
        for i in range(len(lats)):
            col, row = src.index(xs[i], ys[i])
            try:
                a = src.read(1, window=((row-1,row+2),(col-1,col+2)),
                           boundless=True, fill_value=src.nodata)
                a = a.astype('float32')
                if src.nodata is not None: a[a==src.nodata] = np.nan
                vals.append(float(np.nanmedian(a)) if not np.all(np.isnan(a)) else np.nan)
            except:
                vals.append(np.nan)
        return np.array(vals)


# ============================================================
# MAIN PIPELINE
# ============================================================
def main():
    log('='*60)
    log('MVP v2: Большой пр. В.О. + Sentinel-2 фильтрация')
    log('='*60)

    dem_tif = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    ndvi_tif = CACHE / 'sentinel2' / 'ndvi_100m.tif'
    scl_tif = CACHE / 'sentinel2' / 'scl_100m.tif'
    osm_path = CACHE / 'osm' / 'center_osm.json'

    # --- 5.1: Geometry ---
    log('\n5.1: Геометрия Б. пр. В.О.')
    BBOX = (30.20, 59.92, 30.32, 59.95)

    # Get highways from OSM
    if osm_path.exists():
        with open(osm_path) as f:
            osm_data = json.load(f)
    else:
        osm_data = download_osm_highways(BBOX)

    highways = []
    for el in osm_data.get('elements', []):
        if el.get('type') != 'way' or 'geometry' not in el: continue
        tags = el.get('tags', {})
        name = tags.get('name', '')
        # Filter: only Bolshoy pr (В.О.)
        if 'ольшой' in name and ('асиль' in name or 'В.О' in name or 'В О' in name):
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if len(coords) >= 2:
                highways.append(LineString(coords))
    # Fallback: if no Bolshoy found, use all
    if not highways:
        for el in osm_data.get('elements', []):
            if el.get('type') != 'way' or 'geometry' not in el: continue
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if len(coords) >= 2:
                highways.append(LineString(coords))
    log(f'  Highways: {len(highways)}')

    hw_gdf = gpd.GeoDataFrame({'geometry': highways}, crs='EPSG:4326').to_crs(UTM)
    merged_line = unary_union(hw_gdf.geometry.tolist())
    if merged_line.geom_type == 'MultiLineString':
        merged_line = max(merged_line.geoms, key=lambda g: g.length)
    log(f'  Line length: {merged_line.length:.0f}m')

    road_poly = merged_line.buffer(8.0).buffer(-3.0)
    log(f'  Road area: {road_poly.area:.0f} m²')

    # --- 5.2: Filter ground pixels ---
    log('\n5.2: Фильтрация ground pixels (percentile + slope + SCL + NDVI)')

    with rasterio.open(dem_tif) as src:
        dem_crs = src.crs
        dem_transform = src.transform
        nodata = src.nodata

        # Convert to DEM CRS
        road_gdf = gpd.GeoDataFrame({'geometry': [road_poly]}, crs=UTM).to_crs(dem_crs)
        road_poly_dem = road_gdf.geometry.iloc[0]
        cl_gdf = gpd.GeoDataFrame({'geometry': [merged_line]}, crs=UTM).to_crs(dem_crs)
        center_line_dem = cl_gdf.geometry.iloc[0]

        # Window for road polygon
        bounds = road_poly_dem.bounds
        win = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
        dem_patch = src.read(1, window=win).astype('float32')
        if nodata is not None: dem_patch[dem_patch==nodata] = np.nan
        win_transform = rasterio.windows.transform(win, src.transform)
        log(f'  DEM patch: {dem_patch.shape[1]}×{dem_patch.shape[0]} px')
        log(f'  DEM range (ell): [{np.nanmin(dem_patch):.1f}, {np.nanmax(dem_patch):.1f}]')

        # Road mask
        road_mask = ~geometry_mask([road_poly_dem.__geo_interface__],
                                   transform=win_transform, out_shape=dem_patch.shape, all_touched=True)
        log(f'  Road pixels: {road_mask.sum():,}')

        # --- SCL + NDVI masks (NEW in v2) ---
        # Reproject SCL and NDVI to DEM patch grid
        scl_mask = np.ones_like(dem_patch, dtype=bool)  # default: all pass
        ndvi_mask = np.ones_like(dem_patch, dtype=bool)

        if scl_tif.exists():
            log('  Reprojecting SCL to DEM grid...')
            with rasterio.open(scl_tif) as scl_src:
                scl_patch = np.full(dem_patch.shape, 0, dtype='float32')
                reproject(
                    source=rasterio.band(scl_src, 1),
                    destination=scl_patch,
                    src_transform=scl_src.transform, src_crs=scl_src.crs,
                    dst_transform=win_transform, dst_crs=dem_crs,
                    resampling=Resampling.nearest,
                    src_nodata=0, dst_nodata=0)
                # Exclude: water(6), cloud shadow(3), clouds(8,9,10), saturated(1)
                bad_scl = {1, 3, 6, 8, 9, 10}
                scl_bad = np.zeros_like(scl_patch, dtype=bool)
                for s in bad_scl:
                    scl_bad |= (scl_patch == s)
                scl_mask = ~scl_bad
                log(f'  SCL: {scl_mask.sum()}/{scl_mask.size} pass ({100*scl_mask.mean():.0f}%)')

        if ndvi_tif.exists():
            log('  Reprojecting NDVI to DEM grid...')
            with rasterio.open(ndvi_tif) as ndvi_src:
                ndvi_patch = np.full(dem_patch.shape, np.nan, dtype='float32')
                reproject(
                    source=rasterio.band(ndvi_src, 1),
                    destination=ndvi_patch,
                    src_transform=ndvi_src.transform, src_crs=ndvi_src.crs,
                    dst_transform=win_transform, dst_crs=dem_crs,
                    resampling=Resampling.bilinear,
                    src_nodata=np.nan, dst_nodata=np.nan)
                # Exclude trees: NDVI > 0.4
                ndvi_mask = ndvi_patch < 0.4
                log(f'  NDVI < 0.4: {ndvi_mask.sum()}/{ndvi_mask.size} pass ({100*ndvi_mask.mean():.0f}%)')

        # OSM buildings mask (exclude roofs)
        bld_mask = np.ones_like(dem_patch, dtype=bool)
        bld_polys = []
        for el in osm_data.get('elements', []):
            if 'building' not in el.get('tags', {}): continue
            if el.get('type') != 'way' or 'geometry' not in el: continue
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                bld_polys.append(Polygon(coords))
        if bld_polys:
            bld_gdf2 = gpd.GeoDataFrame({'geometry': bld_polys}, crs='EPSG:4326').to_crs(dem_crs)
            road_box = box(*road_poly_dem.bounds)
            nearby_b = bld_gdf2[bld_gdf2.intersects(road_box)]
            if len(nearby_b) > 0:
                bld_mask_raw = geometry_mask(
                    [g.__geo_interface__ for g in nearby_b.geometry],
                    transform=win_transform, out_shape=dem_patch.shape, all_touched=False)
                bld_mask = bld_mask_raw  # True = outside building (NOT building)
            log(f'  Buildings near road: {len(nearby_b)}')

        # Combined mask: road + SCL + NDVI + NOT building
        combined_mask = road_mask & scl_mask & ndvi_mask & bld_mask & np.isfinite(dem_patch)
        log(f'  Combined mask: {combined_mask.sum():,} ({100*combined_mask.sum()/road_mask.sum():.1f}% of road)')

        # Percentile filter (lower envelope)
        log('  Percentile filter (p=10, w=15m=8px)...')
        wpx = max(3, round(15.0 / PIXEL))
        local_floor = percentile_filter(dem_patch, percentile=10, size=wpx, mode='nearest')
        is_low = dem_patch <= (local_floor + 0.3)
        ground_candidates = combined_mask & is_low
        log(f'  Ground candidates: {ground_candidates.sum():,}')

        # Slope constraint
        log('  Slope constraint (max=0.06)...')
        max_diff = 0.06 * PIXEL
        ground = ground_candidates.copy()
        for it in range(3):
            g = np.where(ground, dem_patch, np.nan)
            diffs = []
            for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
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
            if removed == 0: break
            log(f'    Iter {it+1}: -{removed}, {ground.sum():,} remain')

        log(f'  Final ground: {ground.sum():,} ({100*ground.sum()/road_mask.sum():.1f}% of road)')

        # Convert to BS-77
        ground_bs77 = dem_patch - GEOID

        # Picketage
        log('  Picketage...')
        rows, cols = np.where(ground)
        pk_data = []
        for r, c in zip(rows, cols):
            x = win_transform.c + win_transform.a * c + win_transform.b * r
            y = win_transform.f + win_transform.d * c + win_transform.e * r
            pt = Point(x, y)
            dist = center_line_dem.project(pt)
            nearest = nearest_points(center_line_dem, pt)[0]
            offset = pt.distance(nearest)
            pk_data.append({'row':r, 'col':c, 'x':x, 'y':y,
                           'h_ell':dem_patch[r,c], 'h_bs77':ground_bs77[r,c],
                           'picket':dist, 'offset':offset})

        df_ground = pd.DataFrame(pk_data).sort_values('picket').reset_index(drop=True)
        log(f'  Picket: [{df_ground["picket"].min():.0f}, {df_ground["picket"].max():.0f}]m')
        log(f'  h_bs77: [{df_ground["h_bs77"].min():.2f}, {df_ground["h_bs77"].max():.2f}]')

    # --- 5.3: Calibration ---
    log('\n5.3: Калибровка')
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
            pts.append({'lat':lat, 'lon':lon, 'h_pas':h})

    pas_gdf = gpd.GeoDataFrame(pts, geometry=[Point(p['lon'],p['lat']) for p in pts], crs='EPSG:4326').to_crs(UTM)
    near = pas_gdf[pas_gdf.geometry.distance(road_poly) < 500]
    log(f'  Passport within 500m: {len(near)}')

    cal_results = []
    for _, p in near.iterrows():
        pt = p.geometry
        dists = np.sqrt((df_ground['x']-pt.x)**2 + (df_ground['y']-pt.y)**2)
        idx = dists.idxmin()
        cal_results.append({
            'lat':p['lat'], 'lon':p['lon'], 'h_pas':p['h_pas'],
            'h_dem':df_ground.loc[idx,'h_bs77'],
            'dH':df_ground.loc[idx,'h_bs77'] - p['h_pas'],
            'dist':dists[idx],
        })
    df_cal = pd.DataFrame(cal_results)
    if len(df_cal) > 0:
        rmse = np.sqrt(np.mean(df_cal['dH']**2))
        log(f'  n={len(df_cal)}, dH median={df_cal["dH"].median():+.3f}, RMSE={rmse:.3f}м')

    # --- 5.4: Interpolation ---
    log('\n5.4: Интерполяция (пикетаж + сплайн)')
    bin_size = 5.0
    df_ground['pk_bin'] = (df_ground['picket'] / bin_size).astype(int) * bin_size
    profile = df_ground.groupby('pk_bin').agg(
        h_median=('h_bs77','median'), h_std=('h_bs77','std'),
        n=('h_bs77','count'), pk_mean=('picket','mean')).reset_index()
    log(f'  Bins: {len(profile)}, h_median: [{profile["h_median"].min():.2f}, {profile["h_median"].max():.2f}]')

    valid = profile[profile['n'] >= 3]
    if len(valid) >= 4:
        spline = UnivariateSpline(valid['pk_mean'].values, valid['h_median'].values, k=3, s=len(valid))
        h_profile = spline(profile['pk_mean'].values)
    else:
        h_profile = np.full(len(profile), df_ground['h_bs77'].median())
    log(f'  Spline: [{h_profile.min():.2f}, {h_profile.max():.2f}]')

    # Build 2D surface
    log('  Building 2D surface...')
    cleaned = np.full_like(dem_patch, np.nan, dtype='float32')
    pk_min = profile['pk_mean'].min()
    pk_max = profile['pk_mean'].max()
    pk_arr = profile['pk_mean'].values

    # Fill all road pixels
    all_road_rows, all_road_cols = np.where(road_mask & np.isfinite(dem_patch))
    for r, c in zip(all_road_rows, all_road_cols):
        x = win_transform.c + win_transform.a * c + win_transform.b * r
        y = win_transform.f + win_transform.d * c + win_transform.e * r
        pt = Point(x, y)
        pk = center_line_dem.project(pt)
        if pk <= pk_min: h = h_profile[0]
        elif pk >= pk_max: h = h_profile[-1]
        else: h = float(np.interp(pk, pk_arr, h_profile))
        cleaned[r, c] = h
    log(f'  Cleaned: {np.isfinite(cleaned).sum():,} px')

    # --- 5.5: Profile plot ---
    log('\n5.5: Профиль + валидация')
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))

    ax1 = axes[0]
    ax1.scatter(df_ground['picket'], df_ground['h_bs77'], s=1, alpha=0.3, c='blue', label='Ground pixels')
    ax1.plot(profile['pk_mean'], profile['h_median'], 'g-', lw=2, label='Median per 5m')
    ax1.plot(profile['pk_mean'], h_profile, 'r-', lw=2, label='Spline')
    if len(df_cal) > 0:
        # Project passport points onto center line
        for _, p in df_cal.iterrows():
            pt_gdf = gpd.GeoDataFrame({'geometry':[Point(p['lon'],p['lat'])]}, crs='EPSG:4326').to_crs(dem_crs)
            pk = center_line_dem.project(pt_gdf.geometry.iloc[0])
            ax1.scatter(pk, p['h_pas'], s=50, c='black', marker='x', zorder=5)
    ax1.set_xlabel('Пикетаж (м)')
    ax1.set_ylabel('Высота (м БС-77)')
    ax1.set_title('Большой пр. В.О. — Продольный профиль')
    ax1.legend()
    ax1.grid(alpha=0.3)

    # Cross-section
    mid_pk = df_ground['picket'].median()
    mid = df_ground[(df_ground['picket'] > mid_pk-10) & (df_ground['picket'] < mid_pk+10)]
    ax2 = axes[1]
    ax2.scatter(mid['offset'], mid['h_bs77'], s=5, c='blue')
    ax2.set_xlabel('Поперечное смещение (м)')
    ax2.set_ylabel('Высота (м БС-77)')
    ax2.set_title(f'Поперечный профиль (пикет ~{mid_pk:.0f}м)')
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    profile_path = OUT / 'bolshoy_pr_profile.png'
    plt.savefig(profile_path, dpi=150)
    plt.close()
    log(f'  Profile: {profile_path}')

    # --- 5.6: Merge + map ---
    log('\n5.6: Сборка + карта')
    merged = dem_patch - GEOID
    road_px = road_mask & np.isfinite(merged)
    merged[road_px & np.isfinite(cleaned)] = cleaned[road_px & np.isfinite(cleaned)]

    # Save
    merged_path = OUT / 'bolshoy_pr_cleaned_dem.tif'
    profile_out = {
        'driver':'GTiff','dtype':'float32','compress':'deflate',
        'tiled':True,'blockxsize':256,'blockysize':256,
        'nodata':-9999,'crs':dem_crs,'count':1,
        'transform':win_transform,
        'width':merged.shape[1],'height':merged.shape[0],
    }
    with rasterio.open(merged_path, 'w', **profile_out) as dst:
        out = merged.copy(); out[~np.isfinite(out)] = -9999
        dst.write(out.astype('float32'), 1)
    log(f'  DEM: {merged_path} ({merged_path.stat().st_size/1e6:.1f} MB)')

    # Reproject to 4326 for folium
    out_4326 = CACHE / 'bolshoy_pr_4326.tif'
    with rasterio.open(str(merged_path)) as s:
        from rasterio.warp import calculate_default_transform
        t, w, h = calculate_default_transform(s.crs, 'EPSG:4326', s.width, s.height, *s.bounds)
        arr4 = np.full((h, w), -9999, dtype='float32')
        reproject(source=rasterio.band(s, 1), destination=arr4,
                 src_transform=s.transform, src_crs=s.crs,
                 dst_transform=t, dst_crs='EPSG:4326',
                 resampling=Resampling.bilinear, src_nodata=-9999, dst_nodata=-9999)
        p4 = s.profile.copy()
        p4.update({'crs':'EPSG:4326','transform':t,'width':w,'height':h})
        with rasterio.open(str(out_4326), 'w', **p4) as d: d.write(arr4, 1)

    with rasterio.open(out_4326) as src:
        dem_4326 = src.read(1).astype('float32')
        dem_4326[dem_4326==-9999] = np.nan
        bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]

    # RGBA overlay
    vmin, vmax = -1, 8
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.cm.terrain
    valid_px = np.isfinite(dem_4326)
    rgba_dem = np.zeros((*dem_4326.shape, 4), dtype=np.uint8)
    if valid_px.any():
        colors = cmap(norm(dem_4326[valid_px]))
        rgba_dem[valid_px] = (colors * 255).astype(np.uint8)
        rgba_dem[valid_px, 3] = 160

    # Map
    m = folium.Map(location=[59.935, 30.27], zoom_start=14, control_scale=True, tiles=None)
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                     attr='Esri', name='Спутник', max_zoom=19).add_to(m)

    folium.raster_layers.ImageOverlay(image=rgba_dem, bounds=bounds, opacity=0.6,
                                      name='DEM (БС-77, очищенный)').add_to(m)

    for level in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        flood = (dem_4326 <= level) & np.isfinite(dem_4326)
        if flood.sum() == 0: continue
        rgba_f = np.zeros((*dem_4326.shape, 4), dtype=np.uint8)
        rgba_f[flood] = [0, 80, 200, 140]
        folium.raster_layers.ImageOverlay(image=rgba_f, bounds=bounds, opacity=0.5,
                                          name=f'Затопление {level:.1f}м', show=False).add_to(m)

    # Passport points near road
    for _, p in near.iterrows():
        # Sample DEM at passport location
        row_px = int((p['lat'] - bounds[0][0]) / (bounds[1][0] - bounds[0][0]) * dem_4326.shape[0])
        col_px = int((p['lon'] - bounds[0][1]) / (bounds[1][1] - bounds[0][1]) * dem_4326.shape[1])
        h_dem = None
        if 0 <= row_px < dem_4326.shape[0] and 0 <= col_px < dem_4326.shape[1]:
            v = dem_4326[row_px, col_px]
            if np.isfinite(v): h_dem = float(v)
        if h_dem is not None:
            dH = h_dem - p['h_pas']
            color = 'green' if abs(dH) < 1 else 'orange' if abs(dH) < 3 else 'red'
            popup = f"h_pas={p['h_pas']:.2f}<br>h_dem={h_dem:.2f}<br>dH={dH:+.2f}"
        else:
            color = 'gray'
            popup = f"h_pas={p['h_pas']:.2f} (nodata)"
        folium.CircleMarker(
            location=[p['lat'], p['lon']], radius=5, color='black', weight=0.5,
            fill=True, fill_color=color, fill_opacity=0.8,
            popup=folium.Popup(popup, max_width=200)).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    MousePosition().add_to(m)
    MiniMap(toggle_display=True).add_to(m)

    map_path = OUT / 'bolshoy_pr_flood_map.html'
    m.save(str(map_path))
    log(f'  Map: {map_path} ({map_path.stat().st_size/1e6:.1f} MB)')

    # QA
    rmse_val = float(np.sqrt(np.mean(df_cal['dH']**2))) if len(df_cal) > 0 else None
    qa = {
        'object_id': 'bolshoy_pr_vo',
        'name': 'Большой проспект В.О.',
        'object_type': 'road',
        'n_pixels_road': int(road_mask.sum()),
        'n_pixels_ground': int(ground.sum()),
        'pct_retained': float(100*ground.sum()/max(1,road_mask.sum())),
        'n_control_points': len(df_cal),
        'rmse_m': rmse_val,
        'qa_flag': 'ok' if rmse_val and rmse_val < 1.0 else 'needs_review',
        'scl_filter': scl_tif.exists(),
        'ndvi_filter': ndvi_tif.exists(),
        'filter_params': {'percentile':10, 'window_m':15, 'max_slope':0.06, 'tolerance':0.3, 'ndvi_max':0.4},
        'geoid_m': GEOID,
        'h_profile_range': [float(h_profile.min()), float(h_profile.max())],
    }
    with open(OUT / 'bolshoy_pr_qa.json', 'w') as f:
        json.dump(qa, f, indent=2, ensure_ascii=False)

    log(f'\n{"="*60}')
    log(f'MVP v2 COMPLETE')
    log(f'{"="*60}')
    log(f'Ground pixels: {ground.sum():,} ({100*ground.sum()/road_mask.sum():.1f}% of road)')
    log(f'RMSE: {rmse_val:.3f}м' if rmse_val else 'RMSE: N/A')
    log(f'h_bs77 range: [{df_ground["h_bs77"].min():.2f}, {df_ground["h_bs77"].max():.2f}]')
    log(f'Profile: {profile_path}')
    log(f'Map: {map_path}')
    log(f'DEM: {merged_path}')
    log(f'QA: {OUT/"bolshoy_pr_qa.json"}')

if __name__ == '__main__':
    main()
