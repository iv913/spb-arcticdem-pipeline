#!/usr/bin/env python3
"""
Карта MVP: тепловой слой высот с переключением v1 (сырой DSM) / v2 (очищенный).
+ уровни затопления + паспортные точки + здания OSM.
"""
import json, re, subprocess
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform, reproject, Resampling, calculate_default_transform
from rasterio.features import geometry_mask
from shapely.geometry import Polygon, Point, box
import geopandas as gpd
import folium
from folium.plugins import MiniMap, Fullscreen, MousePosition
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

HOME = Path('/home/z/my-project')
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
GEOID = 15.9

def reproject_to_4326(src_path, dst_path, target_res=0.00003):
    """Reproject raster to EPSG:4326."""
    with rasterio.open(str(src_path)) as src:
        t, w, h = calculate_default_transform(src.crs, 'EPSG:4326', src.width, src.height, *src.bounds)
        arr = np.full((h, w), -9999, dtype='float32')
        reproject(
            source=rasterio.band(src, 1), destination=arr,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=t, dst_crs='EPSG:4326',
            resampling=Resampling.bilinear, src_nodata=src.nodata, dst_nodata=-9999)
        profile = src.profile.copy()
        profile.update({'crs':'EPSG:4326','transform':t,'width':w,'height':h,'nodata':-9999})
        with rasterio.open(str(dst_path), 'w', **profile) as dst:
            dst.write(arr, 1)
    return dst_path

def make_rgba(arr, vmin, vmax, cmap_name='terrain', alpha=170):
    """Создать RGBA из массива высот."""
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.get_cmap(cmap_name)
    valid = np.isfinite(arr) & (arr > -1000)
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    if valid.any():
        colors = cmap(norm(arr[valid]))
        rgba[valid] = (colors * 255).astype(np.uint8)
        rgba[valid, 3] = alpha
    return rgba

def main():
    print('Building MVP comparison map...', flush=True)

    # --- 1. Reproject both DEMs to EPSG:4326 ---
    raw_dem = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    cleaned_dem = OUT / 'bolshoy_pr_cleaned_dem.tif'

    raw_4326 = CACHE / 'raw_dem_4326.tif'
    cleaned_4326 = CACHE / 'cleaned_dem_4326.tif'

    if not raw_4326.exists():
        print('  Reprojecting raw DEM...', flush=True)
        reproject_to_4326(raw_dem, raw_4326)
    if not cleaned_4326.exists():
        print('  Reprojecting cleaned DEM...', flush=True)
        reproject_to_4326(cleaned_dem, cleaned_4326)

    # --- 2. Read both DEMs ---
    with rasterio.open(str(raw_4326)) as src:
        raw_arr = src.read(1).astype('float32')
        raw_arr[raw_arr == -9999] = np.nan
        raw_arr -= GEOID  # → BS-77
        raw_bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]
        print(f'  Raw DEM: {raw_arr.shape}, BS-77 range [{np.nanmin(raw_arr):.1f}, {np.nanmax(raw_arr):.1f}]', flush=True)

    with rasterio.open(str(cleaned_4326)) as src:
        clean_arr = src.read(1).astype('float32')
        clean_arr[clean_arr == -9999] = np.nan
        clean_bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]
        print(f'  Clean DEM: {clean_arr.shape}, BS-77 range [{np.nanmin(clean_arr):.1f}, {np.nanmax(clean_arr):.1f}]', flush=True)

    # --- 3. Create RGBA overlays ---
    vmin, vmax = 0, 8  # BS-77 range for flood-relevant area
    rgba_raw = make_rgba(raw_arr, vmin, vmax, 'terrain', alpha=170)
    rgba_clean = make_rgba(clean_arr, vmin, vmax, 'terrain', alpha=170)

    # Flood masks (use cleaned DEM)
    FLOOD_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    flood_overlays = {}
    for level in FLOOD_LEVELS:
        flood_mask = (clean_arr <= level) & np.isfinite(clean_arr)
        if flood_mask.sum() == 0:
            continue
        rgba_f = np.zeros((*clean_arr.shape, 4), dtype=np.uint8)
        rgba_f[flood_mask] = [0, 80, 200, 140]
        flood_overlays[level] = rgba_f

    # --- 4. Load passport points ---
    pts = []
    with open(HOME / 'upload' / 'Отметки.txt', 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or ':' not in line:
                continue
            l, r = line.split(':', 1)
            m = re.match(r'^(\d+\.\d+)\s*,\s*(\d+\.\d+)$', l.strip())
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
            m = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m:
                continue
            try:
                h = float(m.group(1).replace(',', '.'))
            except:
                continue
            pts.append({'lat': lat, 'lon': lon, 'h_pas': h, 'desc': m.group(2).strip()})

    # Sample both DEMs at passport points
    for dem_arr, dem_bounds, label in [(raw_arr, raw_bounds, 'h_raw'),
                                        (clean_arr, clean_bounds, 'h_clean')]:
        b = dem_bounds
        for p in pts:
            row = int((p['lat'] - b[0][0]) / (b[1][0] - b[0][0]) * dem_arr.shape[0])
            col = int((p['lon'] - b[0][1]) / (b[1][1] - b[0][1]) * dem_arr.shape[1])
            if 0 <= row < dem_arr.shape[0] and 0 <= col < dem_arr.shape[1]:
                v = dem_arr[row, col]
                p[label] = float(v) if np.isfinite(v) else None
            else:
                p[label] = None

    # --- 5. Load OSM buildings (Bolshoy pr area only) ---
    print('  Loading OSM buildings...', flush=True)
    with open(CACHE / 'osm' / 'center_osm.json') as f:
        osm_data = json.load(f)

    bld_coords = []
    for el in osm_data.get('elements', []):
        if 'building' not in el.get('tags', {}):
            continue
        if el.get('type') != 'way' or 'geometry' not in el:
            continue
        coords = [(p['lat'], p['lon']) for p in el['geometry']]
        if len(coords) >= 3:
            # Filter: only near Bolshoy pr V.O. (~59.93-59.94, 30.23-30.30)
            if any(59.92 <= c[0] <= 59.95 and 30.20 <= c[1] <= 30.32 for c in coords):
                bld_coords.append(coords)
    print(f'  Buildings near MVP: {len(bld_coords)}', flush=True)

    # --- 6. Create map ---
    print('  Creating map...', flush=True)
    cl = 59.935
    clo = 30.27
    m = folium.Map(location=[cl, clo], zoom_start=14, control_scale=True, tiles=None)

    # Base layers
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Спутник', max_zoom=19).add_to(m)

    # v1: Raw DSM (BS-77)
    folium.raster_layers.ImageOverlay(
        image=rgba_raw, bounds=raw_bounds, opacity=0.7,
        name='🌡️ v1: Сырой DSM (БС-77)', show=True).add_to(m)

    # v2: Cleaned surface (BS-77)
    folium.raster_layers.ImageOverlay(
        image=rgba_clean, bounds=clean_bounds, opacity=0.7,
        name='🌡️ v2: Очищенный (БС-77)', show=False).add_to(m)

    # Flood levels
    for level, rgba_f in flood_overlays.items():
        folium.raster_layers.ImageOverlay(
            image=rgba_f, bounds=clean_bounds, opacity=0.5,
            name=f'💧 Затопление {level:.1f}м', show=False).add_to(m)

    # Passport points
    for p in pts:
        h_raw = p.get('h_raw')
        h_clean = p.get('h_clean')
        if h_raw is not None:
            dH_raw = h_raw - p['h_pas']
            c_raw = 'green' if abs(dH_raw) < 1 else 'orange' if abs(dH_raw) < 3 else 'red'
        else:
            dH_raw = None
            c_raw = 'gray'

        if h_clean is not None:
            dH_clean = h_clean - p['h_pas']
            c_clean = 'green' if abs(dH_clean) < 1 else 'orange' if abs(dH_clean) < 3 else 'red'
        else:
            dH_clean = None
            c_clean = 'gray'

        popup = f"""<b>{p['desc'] or '—'}</b><br>
        h_паспорт: <b>{p['h_pas']:.2f}м БС-77</b><br>
        <hr>
        v1 (сырой): {f'{h_raw:.2f}м, dH={dH_raw:+.2f}м' if h_raw is not None else 'nodata'}<br>
        v2 (очищ.): {f'{h_clean:.2f}м, dH={dH_clean:+.2f}м' if h_clean is not None else 'nodata'}"""

        # Use raw DEM color for the marker
        folium.CircleMarker(
            location=[p['lat'], p['lon']], radius=5, color='black', weight=0.5,
            fill=True, fill_color=c_raw, fill_opacity=0.8,
            popup=folium.Popup(popup, max_width=350)).add_to(m)

    # OSM buildings
    if bld_coords:
        bld_layer = folium.FeatureGroup(name='🏢 Здания OSM', show=False)
        for coords in bld_coords:
            folium.Polygon(
                locations=coords, color='#7a0303', weight=0.5,
                fill=True, fill_color='#7a0303', fill_opacity=0.3).add_to(bld_layer)
        bld_layer.add_to(m)

    # Controls
    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    MousePosition().add_to(m)
    MiniMap(toggle_display=True).add_to(m)

    # Color legend (as HTML)
    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 1000;
                background: white; padding: 10px; border-radius: 5px;
                box-shadow: 0 0 10px rgba(0,0,0,0.3); font-size: 12px;">
        <b>Шкала высот (м БС-77)</b><br>
        <div style="display: flex; width: 200px; height: 15px; margin: 5px 0;
                    background: linear-gradient(to right,
                    #008080, #00FF00, #FFFF00, #FF0000, #8B4513);"></div>
        <div style="display: flex; justify-content: space-between; width: 200px;">
            <span>0</span><span>2</span><span>4</span><span>6</span><span>8+</span>
        </div>
        <hr>
        <b>Точки:</b><br>
        🟢 |dH| &lt; 1м &nbsp; 🟠 &lt; 3м &nbsp; 🔴 &lt; 5м &nbsp; ⚫ &ge; 5м
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    map_path = OUT / 'mvp_comparison_map.html'
    m.save(str(map_path))
    print(f'\nDONE: {map_path} ({map_path.stat().st_size/1e6:.1f} MB)', flush=True)

if __name__ == '__main__':
    main()
