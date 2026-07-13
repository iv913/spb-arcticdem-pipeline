#!/usr/bin/env python3
"""
Технологический стенд: исторический центр СПб
1. Скачать ArcticDEM (windowed — только центр, ~200 МБ)
2. Скачать OSM buildings + highways для центра
3. OSM-объект за объектом: DEM stats внутри ground polygons
4. Сопоставить с паспортными точками → стрип-offset
5. Интерактивная карта:
   - Прозрачный DEM overlay на OSM (цветовая шкала высот)
   - Переключение уровней затопления (0.5-5м)
   - Паспортные точки с dH
   - OSM buildings (полупрозрачные)
"""
import os, sys, time, json, re, subprocess, pickle
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import requests
import rasterio
from rasterio.warp import transform as warp_transform, reproject, Resampling, calculate_default_transform
from rasterio.windows import Window, from_bounds
from rasterio.features import geometry_mask
from shapely.geometry import Polygon, box, Point
import geopandas as gpd
import folium
from folium.plugins import MiniMap, Fullscreen, MousePosition
from scipy.interpolate import RBFInterpolator
from scipy.spatial import Delaunay
from scipy.ndimage import uniform_filter
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

HOME = Path('/home/z/my-project')
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
for d in [CACHE, CACHE/'arctic', CACHE/'osm', OUT]:
    d.mkdir(parents=True, exist_ok=True)

GEOID = 15.9
CENTER_BBOX = (30.20, 59.90, 30.40, 59.98)  # W, S, E, N — исторический центр

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)

# ============================================================
# 1. Скачать ArcticDEM (windowed — только центр)
# ============================================================
def download_arcticdem_center():
    log('STEP 1: ArcticDEM (center only)')
    arctic_tif = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    if arctic_tif.exists() and arctic_tif.stat().st_size > 1e7:
        log(f'  ✓ exists ({arctic_tif.stat().st_size/1e6:.0f} MB)')
        return arctic_tif
    
    # Сначала VRT
    vrt_path = CACHE / 'arctic' / 'arcticdem_2m.vrt'
    if not vrt_path.exists() or vrt_path.stat().st_size < 1e6:
        log('  Downloading VRT...')
        r = requests.get('https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m_dem_tiles.vrt', timeout=60)
        vrt_path.write_bytes(r.content)
    
    # Скачать только нужные тайлы (32_73_1_1 покрывает центр)
    tile_path = CACHE / 'arctic' / '32_73_1_1_2m_v4.1_dem.tif'
    if not tile_path.exists() or tile_path.stat().st_size < 1e8:
        log('  Downloading tile 32_73_1_1 (955 MB, 4 parallel)...')
        import concurrent.futures
        size = 954938317
        chunk = size // 4
        BASE = 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73'
        def dlc(i):
            s, e = i*chunk, (i+1)*chunk-1 if i<3 else size-1
            p = CACHE/'arctic'/f'_p_{i}.bin'
            if p.exists() and p.stat().st_size == e-s+1: return
            r = requests.get(f'{BASE}/32_73_1_1_2m_v4.1_dem.tif', headers={'Range':f'bytes={s}-{e}'}, stream=True, timeout=600)
            with open(p, 'wb') as f:
                for c in r.iter_content(1<<16): f.write(c)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(dlc, range(4)))
        with open(tile_path, 'wb') as o:
            for i in range(4):
                p = CACHE/'arctic'/f'_p_{i}.bin'; o.write(p.read_bytes()); p.unlink()
        log(f'  Tile downloaded')
    
    # Вырезать центр из тайла
    log('  Cropping center...')
    with rasterio.open(tile_path) as src:
        # src is EPSG:3413, need to find window for CENTER_BBOX in lat/lon
        from rasterio.warp import transform_bounds
        w, s, e, n = transform_bounds('EPSG:4326', src.crs, *CENTER_BBOX)
        win = from_bounds(w, s, e, n, src.transform)
        win = win.round_offsets().round_lengths()
        
        data = src.read(1, window=win)
        win_transform = rasterio.windows.transform(win, src.transform)
        
        profile = src.profile.copy()
        profile.update({
            'height': data.shape[0], 'width': data.shape[1],
            'transform': win_transform, 'compress': 'deflate',
            'tiled': True, 'blockxsize': 256, 'blockysize': 256,
        })
        with rasterio.open(arctic_tif, 'w', **profile) as dst:
            dst.write(data, 1)
    
    log(f'  ✓ Center DEM: {arctic_tif.stat().st_size/1e6:.0f} MB, {data.shape[1]}×{data.shape[0]} px')
    
    # Удалить полный тайл (экономия места)
    tile_path.unlink()
    log(f'  Deleted full tile')
    
    return arctic_tif

# ============================================================
# 2. Скачать OSM для центра
# ============================================================
def download_osm_center():
    log('STEP 2: OSM (center)')
    osm_path = CACHE / 'osm' / 'center_osm.json'
    if osm_path.exists() and osm_path.stat().st_size > 1000:
        log('  ✓ exists')
        return osm_path
    
    W, S, E, N = CENTER_BBOX
    bbox_str = f'{S},{W},{N},{E}'
    
    # Buildings + highways одним запросом
    log('  Downloading buildings + highways...')
    q = f'''[out:json][timeout:180];
    (
      way["building"]({bbox_str});
      way["highway"]({bbox_str});
    );
    out geom;'''
    
    for url in ['https://overpass-api.de/api/interpreter',
                'https://maps.mail.ru/osm/tools/overpass/api/interpreter']:
        try:
            r = requests.post(url, data={'data': q}, timeout=180)
            if r.status_code == 200:
                data = r.json()
                log(f'  ✓ {len(data["elements"])} elements')
                osm_path.write_text(json.dumps(data))
                return osm_path
            else:
                log(f'  {url}: HTTP {r.status_code}')
        except Exception as e:
            log(f'  {url}: {e}')
    
    log('  ! Overpass failed, trying osmnx...')
    try:
        import osmnx as ox
        ox.settings.timeout = 300
        bld = ox.features_from_bbox(bbox=CENTER_BBOX, tags={'building': True})
        hw = ox.features_from_bbox(bbox=CENTER_BBOX, tags={'highway': True})
        # Save as JSON-like
        elements = []
        for _, row in bld.iterrows():
            geom = row.geometry
            if geom.geom_type == 'Polygon':
                coords = [[{'lat': y, 'lon': x} for x, y in geom.exterior.coords]]
                elements.append({'type': 'way', 'geometry': coords[0], 'tags': {'building': 'yes'}})
        for _, row in hw.iterrows():
            geom = row.geometry
            if geom.geom_type == 'LineString':
                coords = [{'lat': y, 'lon': x} for x, y in geom.coords]
                elements.append({'type': 'way', 'geometry': coords, 'tags': {'highway': str(row.get('highway', 'road'))}})
        data = {'elements': elements}
        osm_path.write_text(json.dumps(data))
        log(f'  ✓ osmnx: {len(elements)} elements')
        return osm_path
    except Exception as e:
        log(f'  osmnx failed: {e}')
        return None

# ============================================================
# 3. OSM-объект за объектом: DEM stats
# ============================================================
def osm_ground_calibration(arctic_tif, osm_path):
    log('STEP 3: OSM ground calibration (объект за объектом)')
    
    # Load ArcticDEM
    with rasterio.open(arctic_tif) as src:
        dem_crs = src.crs
        dem_transform = src.transform
        dem_nodata = src.nodata
        dem_shape = (src.height, src.width)
        log(f'  DEM: {src.width}×{src.height}, CRS={dem_crs}')
    
    # Load OSM
    with open(osm_path) as f:
        osm_data = json.load(f)
    
    buildings = []
    highways = []
    for el in osm_data['elements']:
        if el.get('type') != 'way' or 'geometry' not in el: continue
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        tags = el.get('tags', {})
        if 'building' in tags:
            if len(coords) >= 4 and coords[0] == coords[-1]:
                buildings.append(Polygon(coords))
        elif 'highway' in tags:
            from shapely.geometry import LineString
            highways.append(LineString(coords))
    
    log(f'  Buildings: {len(buildings):,}')
    log(f'  Highways: {len(highways):,}')
    
    # Convert to DEM CRS
    UTM = 'EPSG:32636'
    bld_gdf = gpd.GeoDataFrame({'geometry': buildings}, crs='EPSG:4326').to_crs(UTM)
    hw_gdf = gpd.GeoDataFrame({'geometry': highways}, crs='EPSG:4326').to_crs(UTM)
    
    # Buffer highways (3m)
    hw_buf = hw_gdf.copy()
    hw_buf['geometry'] = hw_buf.buffer(3.0)
    hw_buf = hw_buf.to_crs(dem_crs)
    bld_proj = bld_gdf.to_crs(dem_crs)
    
    # Load passport points
    pas_pts = []
    with open(HOME / 'upload' / 'Отметки.txt', 'r', encoding='utf-8') as f:
        for ln, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or ':' not in line: continue
            l, r = line.split(':', 1)
            m = re.match(r'^(\d+\.\d+)\s*,\s*(\d+\.\d+)$', l.strip())
            if not m: continue
            lat, lon = float(m.group(1)), float(m.group(2))
            # Only center points
            if not (CENTER_BBOX[1] <= lat <= CENTER_BBOX[3] and CENTER_BBOX[0] <= lon <= CENTER_BBOX[2]):
                continue
            m = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m: continue
            try: h = float(m.group(1).replace(',', '.'))
            except: continue
            pas_pts.append({'lat': lat, 'lon': lon, 'h_pas': h, 'line': ln})
    
    log(f'  Passport points in center: {len(pas_pts)}')
    
    # For each passport point:
    # 1. Find nearby road polygon (buffered highway)
    # 2. Sample DEM inside road polygon, EXCLUDING buildings
    # 3. Compare with passport height
    
    # Convert passport to DEM CRS
    pas_lats = np.array([p['lat'] for p in pas_pts])
    pas_lons = np.array([p['lon'] for p in pas_pts])
    pas_xs, pas_ys = warp_transform('EPSG:4326', dem_crs, pas_lons, pas_lats)
    
    # Build spatial index for highways
    from scipy.spatial import cKDTree
    hw_centroids = np.array([[g.centroid.x, g.centroid.y] for g in hw_buf.geometry])
    hw_tree = cKDTree(hw_centroids) if len(hw_centroids) > 0 else None
    
    # Build spatial index for buildings
    bld_centroids = np.array([[g.centroid.x, g.centroid.y] for g in bld_proj.geometry]) if len(bld_proj) > 0 else None
    bld_tree = cKDTree(bld_centroids) if bld_centroids is not None and len(bld_centroids) > 0 else None
    
    results = []
    with rasterio.open(arctic_tif) as src:
        for i, p in enumerate(pas_pts):
            x, y = pas_xs[i], pas_ys[i]
            col, row = src.index(x, y)
            
            # Read 15×15 window (~30m radius)
            w = 7
            try:
                arr = src.read(1, window=((row-w, row+w+1), (col-w, col+w+1)),
                              boundless=True, fill_value=dem_nodata)
                arr = arr.astype('float32')
                if dem_nodata is not None: arr[arr == dem_nodata] = np.nan
            except: continue
            
            win_transform = rasterio.windows.transform(
                Window(col-w, row-w, 2*w+1, 2*w+1), src.transform)
            
            # Building mask
            win_box = box(
                win_transform.c, win_transform.f + win_transform.e * (2*w+1),
                win_transform.c + win_transform.a * (2*w+1), win_transform.f)
            
            if bld_tree is not None:
                # Find nearby buildings (within 50m)
                _, idxs = bld_tree.query([x, y], k=20, distance_upper_bound=50)
                idxs = [i for i in idxs if i < len(bld_proj)]
                nearby_blds = [bld_proj.geometry.iloc[i] for i in idxs if bld_proj.geometry.iloc[i].intersects(win_box)]
            else:
                nearby_blds = []
            
            if nearby_blds:
                bmask = geometry_mask([g.__geo_interface__ for g in nearby_blds],
                                      transform=win_transform, out_shape=arr.shape, all_touched=True)
            else:
                bmask = np.zeros_like(arr, dtype=bool)
            
            # Ground pixels = valid DEM AND NOT building
            ground_px = np.isfinite(arr) & ~bmask
            if ground_px.sum() < 5: continue
            
            ground_vals = arr[ground_px]
            
            # Use lowest 25% (road surface = lowest)
            ground_vals_sorted = np.sort(ground_vals)
            n25 = max(5, len(ground_vals_sorted) // 4)
            ground_h = float(np.median(ground_vals_sorted[:n25]))
            
            # Also: find nearest road polygon and sample inside it
            road_h = np.nan
            if hw_tree is not None:
                dist, idx = hw_tree.query([x, y], k=1)
                if dist < 30:  # within 30m of a road
                    road_geom = hw_buf.geometry.iloc[idx]
                    road_mask = geometry_mask([road_geom.__geo_interface__],
                                              transform=win_transform, out_shape=arr.shape, all_touched=True)
                    road_px = np.isfinite(arr) & ~road_mask & ~bmask
                    if road_px.sum() >= 3:
                        road_vals = arr[road_px]
                        road_h = float(np.median(road_vals))
            
            # Offset = DEM_ground - passport - geoid (in ellipsoid)
            offset_ground = ground_h - p['h_pas'] - GEOID
            offset_road = road_h - p['h_pas'] - GEOID if np.isfinite(road_h) else np.nan
            
            results.append({
                'line': p['line'], 'lat': p['lat'], 'lon': p['lon'],
                'h_pas': p['h_pas'],
                'h_dem_ground': ground_h, 'h_dem_road': road_h,
                'offset_ground': offset_ground, 'offset_road': offset_road,
                'n_ground': int(ground_px.sum()), 'n_building': int(bmask.sum()),
                'x': x, 'y': y,
            })
    
    log(f'  Results: {len(results)} points')
    
    df = pd.DataFrame(results)
    df.to_csv(OUT / 'center_calibration.csv', index=False)
    
    og = df['offset_ground']
    or_ = df['offset_road'].dropna()
    log(f'  Offset (ground, lowest 25%): median={og.median():+.3f}, std={og.std():.3f}')
    if len(or_) > 0:
        log(f'  Offset (road polygon):       median={or_.median():+.3f}, std={or_.std():.3f}')
    
    return df

# ============================================================
# 4. Интерактивная карта: DEM overlay + flood levels + points
# ============================================================
def create_map(arctic_tif, df_cal, osm_path=None):
    log('STEP 4: Interactive map (DEM overlay + flood levels)')
    
    with rasterio.open(arctic_tif) as src:
        dem_crs = src.crs
        dem_transform = src.transform
        dem_nodata = src.nodata
        
        # Read full DEM (center is small ~5000×4000)
        dem = src.read(1).astype('float32')
        if dem_nodata is not None:
            dem[dem == dem_nodata] = np.nan
        
        # Convert to BS-77
        dem_bs77 = dem - GEOID
        
        # Reproject to EPSG:4326 for folium
        dst_transform, dst_w, dst_h = calculate_default_transform(
            dem_crs, 'EPSG:4326', src.width, src.height, *src.bounds)
        
        dem_4326 = np.full((dst_h, dst_w), np.nan, dtype='float32')
        reproject(
            source=dem_bs77, destination=dem_4326,
            src_transform=dem_transform, src_crs=dem_crs,
            dst_transform=dst_transform, dst_crs='EPSG:4326',
            resampling=Resampling.bilinear,
            src_nodata=np.nan, dst_nodata=np.nan,
        )
        
        bounds_4326 = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]
        # Actually use the 4326 bounds
        from rasterio.warp import transform_bounds
        w4326, s4326, e4326, n4326 = transform_bounds(dem_crs, 'EPSG:4326', *src.bounds)
        bounds = [[s4326, w4326], [n4326, e4326]]
        
        log(f'  DEM bounds (4326): W={w4326:.4f} S={s4326:.4f} E={e4326:.4f} N={n4326:.4f}')
        log(f'  DEM shape (4326): {dst_w}×{dst_h}')
    
    # Create RGBA DEM overlay
    # Color scale: 0-20m BS-77 (blue → green → yellow → red)
    valid = np.isfinite(dem_4326)
    log(f'  Valid pixels: {valid.sum():,}/{dem_4326.size:,}')
    
    # Normalize to 0-1 for colormap
    vmin, vmax = 0, 20  # BS-77 range
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.cm.terrain
    
    rgba_dem = np.zeros((*dem_4326.shape, 4), dtype=np.uint8)
    if valid.any():
        vals = norm(dem_4326[valid])
        colors = cmap(vals)
        rgba_dem[valid] = (colors * 255).astype(np.uint8)
        rgba_dem[valid, 3] = 150  # semi-transparent
    
    # Downsample if too large for folium
    max_size = 1000
    if max(dem_4326.shape) > max_size:
        from scipy.ndimage import zoom
        factor = max_size / max(dem_4326.shape)
        rgba_dem = zoom(rgba_dem, (factor, factor, 1), order=0)
        dem_4326 = zoom(dem_4326, (factor, factor), order=0)
        log(f'  Downsampled to {rgba_dem.shape[1]}×{rgba_dem.shape[0]}')
    
    # Create flood masks
    FLOOD_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    
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
            if not (CENTER_BBOX[1] <= lat <= CENTER_BBOX[3] and CENTER_BBOX[0] <= lon <= CENTER_BBOX[2]):
                continue
            m = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m: continue
            try: h = float(m.group(1).replace(',', '.'))
            except: continue
            pts.append({'lat':lat, 'lon':lon, 'h_pas':h, 'desc':m.group(2).strip()})
    
    # Sample BS-77 DEM at passport points
    for p in pts:
        col = int((p['lon'] - w4326) / (e4326 - w4326) * dem_4326.shape[1])
        row = int((p['lat'] - n4326) / (s4326 - n4326) * dem_4326.shape[0])
        if 0 <= row < dem_4326.shape[0] and 0 <= col < dem_4326.shape[1]:
            v = dem_4326[row, col]
            p['h_dem'] = float(v) if np.isfinite(v) else None
        else:
            p['h_dem'] = None
        p['dH'] = p['h_dem'] - p['h_pas'] if p['h_dem'] is not None else None
    
    # Create map
    cl = float(np.mean([p['lat'] for p in pts]))
    clo = float(np.mean([p['lon'] for p in pts]))
    m = folium.Map(location=[cl, clo], zoom_start=13, control_scale=True, tiles=None)
    
    # Base layers
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Спутник', max_zoom=19).add_to(m)
    
    # DEM overlay (semi-transparent)
    folium.raster_layers.ImageOverlay(
        image=rgba_dem, bounds=bounds, opacity=0.6,
        name='DEM (БС-77, цвет=высота)', show=True).add_to(m)
    
    # Flood level overlays
    for level in FLOOD_LEVELS:
        flood_mask = (dem_4326 <= level) & np.isfinite(dem_4326)
        if flood_mask.sum() == 0: continue
        rgba_flood = np.zeros((*dem_4326.shape, 4), dtype=np.uint8)
        rgba_flood[flood_mask, 0] = 0
        rgba_flood[flood_mask, 1] = 80
        rgba_flood[flood_mask, 2] = 200
        rgba_flood[flood_mask, 3] = 140
        folium.raster_layers.ImageOverlay(
            image=rgba_flood, bounds=bounds, opacity=0.5,
            name=f'💧 Затопление {level:.1f}м', show=False).add_to(m)
    
    # Passport points
    for p in pts:
        if p['h_dem'] is None: c = 'gray'
        else:
            d = p['dH']
            c = 'green' if abs(d) < 2 else 'orange' if abs(d) < 5 else 'red' if abs(d) < 10 else 'darkred'
        popup = (f"<b>L{p.get('line','?')}</b><br>"
                 f"h_паспорт: <b>{p['h_pas']:.2f}м БС-77</b><br>"
                 f"h_DEM: <b>{p['h_dem']:.2f}м</b><br>"
                 f"dH: <b>{p['dH']:+.2f}м</b>") if p['h_dem'] else f"L{p.get('line','?')} (nodata)"
        folium.CircleMarker(
            location=[p['lat'], p['lon']], radius=5, color='black', weight=0.5,
            fill=True, fill_color=c, fill_opacity=0.8,
            popup=folium.Popup(popup, max_width=300)).add_to(m)
    
    # OSM buildings (from calibration data)
    if osm_path and Path(osm_path).exists():
        with open(osm_path) as f:
            osm_data = json.load(f)
        bld_layer = folium.FeatureGroup(name='🏢 Здания (OSM)', show=False)
        for el in osm_data['elements']:
            if el.get('type') == 'way' and 'geometry' in el:
                tags = el.get('tags', {})
                if 'building' in tags:
                    coords = [(p['lat'], p['lon']) for p in el['geometry']]
                    if len(coords) >= 3:
                        folium.Polygon(locations=coords, color='#7a0303', weight=0.5,
                                      fill=True, fill_color='#7a0303', fill_opacity=0.3).add_to(bld_layer)
        bld_layer.add_to(m)
    
    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    MousePosition().add_to(m)
    MiniMap(toggle_display=True).add_to(m)
    
    map_path = OUT / 'center_dem_flood_map.html'
    m.save(str(map_path))
    log(f'  ✓ Map: {map_path} ({map_path.stat().st_size/1e6:.1f} MB)')
    return map_path


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    log('=== Технологический стенд: исторический центр СПб ===')
    log(f'Center bbox: {CENTER_BBOX}')
    log(f'Geoid: {GEOID}м')
    
    arctic_tif = download_arcticdem_center()
    osm_path = download_osm_center()
    
    if osm_path:
        df_cal = osm_ground_calibration(arctic_tif, osm_path)
    
    map_path = create_map(arctic_tif, df_cal if osm_path else None)
    
    log(f'\n=== DONE ===')
    log(f'Map: {map_path}')
    log(f'Calibration: {OUT/"center_calibration.csv"}')
