#!/usr/bin/env python3
"""Компактная карта MVP: v1 (сырой) vs v2 (очищенный) + flood levels."""
import json, re
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, calculate_default_transform
import folium
from folium.plugins import MiniMap, Fullscreen, MousePosition
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import base64
import io

HOME = Path('/home/z/my-project')
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
GEOID = 15.9

def reproject_to_4326(src_path, dst_path, target_res=0.00005):
    with rasterio.open(str(src_path)) as src:
        t, w, h = calculate_default_transform(src.crs, 'EPSG:4326', src.width, src.height, *src.bounds)
        arr = np.full((h, w), -9999, dtype='float32')
        reproject(source=rasterio.band(src, 1), destination=arr,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=t, dst_crs='EPSG:4326',
                  resampling=Resampling.bilinear, src_nodata=src.nodata, dst_nodata=-9999)
        profile = src.profile.copy()
        profile.update({'crs':'EPSG:4326','transform':t,'width':w,'height':h,'nodata':-9999})
        with rasterio.open(str(dst_path), 'w', **profile) as dst:
            dst.write(arr, 1)

def arr_to_png_base64(arr_2d, vmin=0, vmax=8, cmap_name='terrain', alpha=180):
    """Convert 2D array to base64 PNG for folium."""
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.get_cmap(cmap_name)
    valid = np.isfinite(arr_2d) & (arr_2d > -1000)
    rgba = np.zeros((*arr_2d.shape, 4), dtype=np.uint8)
    if valid.any():
        colors = cmap(norm(arr_2d[valid]))
        rgba[valid] = (colors * 255).astype(np.uint8)
        rgba[valid, 3] = alpha
    buf = io.BytesIO()
    plt.imsave(buf, rgba, format='png')
    plt.close()
    return base64.b64encode(buf.getvalue()).decode()

def main():
    print('Building compact MVP map...', flush=True)

    raw_dem = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    cleaned_dem = OUT / 'bolshoy_pr_cleaned_dem.tif'

    # Reproject both
    raw_4326 = CACHE / 'raw_4326.tif'
    clean_4326 = CACHE / 'clean_4326.tif'
    if not raw_4326.exists():
        print('  Reproject raw...', flush=True)
        reproject_to_4326(raw_dem, raw_4326)
    if not clean_4326.exists():
        print('  Reproject clean...', flush=True)
        reproject_to_4326(cleaned_dem, clean_4326)

    # Read
    with rasterio.open(str(raw_4326)) as src:
        raw_arr = src.read(1).astype('float32')
        raw_arr[raw_arr == -9999] = np.nan
        raw_arr -= GEOID
        raw_bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]
        print(f'  Raw: {raw_arr.shape}, range [{np.nanmin(raw_arr):.1f}, {np.nanmax(raw_arr):.1f}]')

    with rasterio.open(str(clean_4326)) as src:
        clean_arr = src.read(1).astype('float32')
        clean_arr[clean_arr == -9999] = np.nan
        clean_bounds = [[src.bounds.bottom, src.bounds.left], [src.bounds.top, src.bounds.right]]
        print(f'  Clean: {clean_arr.shape}, range [{np.nanmin(clean_arr):.1f}, {np.nanmax(clean_arr):.1f}]')

    # Convert to base64 PNG (compact)
    print('  Creating overlays...', flush=True)
    raw_b64 = arr_to_png_base64(raw_arr, vmin=0, vmax=8, cmap_name='terrain')
    clean_b64 = arr_to_png_base64(clean_arr, vmin=0, vmax=8, cmap_name='terrain')

    # Flood masks
    flood_b64s = {}
    for level in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        mask = (clean_arr <= level) & np.isfinite(clean_arr)
        if mask.sum() == 0:
            continue
        rgba = np.zeros((*clean_arr.shape, 4), dtype=np.uint8)
        rgba[mask] = [0, 80, 200, 140]
        buf = io.BytesIO()
        plt.imsave(buf, rgba, format='png')
        plt.close()
        flood_b64s[level] = base64.b64encode(buf.getvalue()).decode()

    # Passport points
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
            m2 = re.match(r'^(\d+[.,]?\d*)\s*(.*)$', r.strip())
            if not m2:
                continue
            try:
                h = float(m2.group(1).replace(',', '.'))
            except:
                continue
            pts.append({'lat': lat, 'lon': lon, 'h_pas': h, 'desc': m2.group(2).strip()})

    # Sample both DEMs at passport points
    for p in pts:
        # Raw
        row = int((p['lat'] - raw_bounds[0][0]) / (raw_bounds[1][0] - raw_bounds[0][0]) * raw_arr.shape[0])
        col = int((p['lon'] - raw_bounds[0][1]) / (raw_bounds[1][1] - raw_bounds[0][1]) * raw_arr.shape[1])
        p['h_raw'] = float(raw_arr[row, col]) if (0 <= row < raw_arr.shape[0] and 0 <= col < raw_arr.shape[1] and np.isfinite(raw_arr[row, col])) else None
        # Clean
        row = int((p['lat'] - clean_bounds[0][0]) / (clean_bounds[1][0] - clean_bounds[0][0]) * clean_arr.shape[0])
        col = int((p['lon'] - clean_bounds[0][1]) / (clean_bounds[1][1] - clean_bounds[0][1]) * clean_arr.shape[1])
        p['h_clean'] = float(clean_arr[row, col]) if (0 <= row < clean_arr.shape[0] and 0 <= col < clean_arr.shape[1] and np.isfinite(clean_arr[row, col])) else None

    # OSM buildings (near Bolshoy pr only)
    print('  Loading buildings...', flush=True)
    with open(CACHE / 'osm' / 'center_osm.json') as f:
        osm_data = json.load(f)
    bld_coords = []
    for el in osm_data.get('elements', []):
        if 'building' not in el.get('tags', {}):
            continue
        if el.get('type') != 'way' or 'geometry' not in el:
            continue
        coords = [(p['lat'], p['lon']) for p in el['geometry']]
        if len(coords) >= 3 and any(59.92 <= c[0] <= 59.95 and 30.20 <= c[1] <= 30.32 for c in coords):
            bld_coords.append(coords)
    print(f'  Buildings: {len(bld_coords)}')

    # Create map
    print('  Creating map...', flush=True)
    m = folium.Map(location=[59.935, 30.27], zoom_start=14, control_scale=True, tiles=None)

    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Satellite', max_zoom=19).add_to(m)

    # v1: Raw DSM
    url_raw = f'data:image/png;base64,{raw_b64}'
    folium.raster_layers.ImageOverlay(
        image=url_raw, bounds=raw_bounds, opacity=0.7,
        name='v1: Raw DSM (BS-77)').add_to(m)

    # v2: Cleaned
    url_clean = f'data:image/png;base64,{clean_b64}'
    folium.raster_layers.ImageOverlay(
        image=url_clean, bounds=clean_bounds, opacity=0.7,
        name='v2: Cleaned (BS-77)', show=False).add_to(m)

    # Flood levels
    for level, b64 in flood_b64s.items():
        url = f'data:image/png;base64,{b64}'
        folium.raster_layers.ImageOverlay(
            image=url, bounds=clean_bounds, opacity=0.5,
            name=f'Flood {level:.1f}m', show=False).add_to(m)

    # Passport points
    for p in pts:
        h_raw = p.get('h_raw')
        h_clean = p.get('h_clean')
        dH_raw = h_raw - p['h_pas'] if h_raw is not None else None
        dH_clean = h_clean - p['h_pas'] if h_clean is not None else None
        color = 'green' if (dH_raw is not None and abs(dH_raw) < 1) else 'orange' if (dH_raw is not None and abs(dH_raw) < 3) else 'red' if dH_raw is not None else 'gray'
        popup = f"<b>{p['desc'] or '-'}</b><br>h_pas={p['h_pas']:.2f}m<br>v1: {f'{h_raw:.2f}m dH={dH_raw:+.2f}' if h_raw is not None else 'nodata'}<br>v2: {f'{h_clean:.2f}m dH={dH_clean:+.2f}' if h_clean is not None else 'nodata'}"
        folium.CircleMarker(
            location=[p['lat'], p['lon']], radius=5, color='black', weight=0.5,
            fill=True, fill_color=color, fill_opacity=0.8,
            popup=folium.Popup(popup, max_width=300)).add_to(m)

    # Buildings
    if bld_coords:
        bld_layer = folium.FeatureGroup(name='Buildings (OSM)', show=False)
        for coords in bld_coords[:500]:  # limit for performance
            folium.Polygon(locations=coords, color='#7a0303', weight=0.5,
                          fill=True, fill_color='#7a0303', fill_opacity=0.3).add_to(bld_layer)
        bld_layer.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    MousePosition().add_to(m)
    MiniMap(toggle_display=True).add_to(m)

    # Legend
    legend = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;
    padding:10px;border-radius:5px;box-shadow:0 0 10px rgba(0,0,0,0.3);font-size:11px;">
    <b>Height (m BS-77)</b><br>
    <div style="width:180px;height:12px;margin:4px 0;background:linear-gradient(to right,
    #008080,#00FF00,#FFFF00,#FF0000,#8B4513);"></div>
    <div style="display:flex;justify-content:space-between;width:180px;font-size:10px;">
    <span>0</span><span>2</span><span>4</span><span>6</span><span>8+</span></div>
    <hr><b>Points:</b> green&lt;1m orange&lt;3m red&lt;5m</div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    map_path = OUT / 'mvp_map.html'
    m.save(str(map_path))
    sz = map_path.stat().st_size
    print(f'\nDONE: {map_path} ({sz/1e6:.1f} MB)', flush=True)

if __name__ == '__main__':
    main()
