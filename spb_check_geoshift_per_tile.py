#!/usr/bin/env python3
"""
spb_check_geoshift_per_tile.py — проверить, одинаковый ли сдвиг у разных тайлов ArcticDEM.

3 тайла:
- center (32_73_1_1): запад СПб
- east   (32_73_2_1): восток СПб
- north  (32_72_1_2): север СПб

Также проверяет сдвиг по OSM highways (улицы) vs buildings (здания).
"""
import os, sys, json, math, re
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from rasterio.transform import from_origin
from rasterio.features import rasterize
import shapely.geometry as sg
import shapely.ops as so
import pyproj
from scipy.ndimage import distance_transform_edt

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
OUT.mkdir(parents=True, exist_ok=True)
PERSIST = HOME / 'upload' / 'scripts_persistent'
PASSPORT_TXT = HOME / 'upload' / 'Отметки.txt'
BUILDINGS_GJSON = OUT / 'spb_osm_buildings.geojson'

SHIFT_M = 15.673
SPB_BBOX = (29.70, 59.75, 30.55, 60.05)
W_HALF = 30

TILES = [
    {'name': 'center', 'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_1_1_2m_v4.1_dem.tif',
     'bounds_4326': (29.289, 59.575, 30.345, 60.106)},
    {'name': 'east',   'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_2_1_2m_v4.1_dem.tif',
     'bounds_4326': (30.122, 59.687, 31.173, 60.214)},
    {'name': 'north',  'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_72/32_72_1_2_2m_v4.1_dem.tif',
     'bounds_4326': (29.052, 59.990, 30.126, 60.524)},
]

SHIFTS = [(dc, dr) for dr in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]
                   for dc in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]]


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def load_passport():
    lines = PASSPORT_TXT.read_text(encoding='utf-8').strip().split('\n')
    lats, lons, hps = [], [], []
    for line in lines:
        line = line.strip()
        if not line: continue
        m = re.match(r'(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*:\s*(\d+)[,\.](\d*)', line)
        if m:
            lats.append(float(m.group(1))); lons.append(float(m.group(2)))
            hps.append(float(m.group(3) + '.' + (m.group(4) or '0')))
    return pd.DataFrame({'lat': lats, 'lon': lons, 'hp': hps})


def download_highways():
    """Download OSM highways (streets) for SPb."""
    highways_path = OUT / 'spb_osm_highways.geojson'
    if highways_path.exists() and highways_path.stat().st_size > 1000:
        log(f'  ✓ highways cached')
        return highways_path
    log('  Downloading OSM highways...')
    import requests
    W, S, E, N = SPB_BBOX
    q = f'''[out:json][timeout:600];(way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service)$"]({S},{W},{N},{E}););out geom;'''
    r = requests.post('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                      data={'data': q}, timeout=600)
    data = r.json()
    log(f'    {len(data.get("elements", []))} ways')
    feats = []
    for el in data['elements']:
        if el['type'] != 'way' or 'geometry' not in el: continue
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(coords) >= 2:
            feats.append({'type': 'Feature',
                          'geometry': {'type': 'LineString', 'coordinates': coords},
                          'properties': el.get('tags', {})})
    gj = {'type': 'FeatureCollection', 'features': feats,
          'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}}}
    highways_path.write_text(json.dumps(gj, ensure_ascii=False))
    log(f'    ✓ {len(feats)} highways')
    return highways_path


def load_polys(geojson_path, geom_type='Polygon'):
    with open(geojson_path) as fp:
        gj = json.load(fp)
    bbox_poly = sg.box(*SPB_BBOX)
    feats = []
    for ft in gj['features']:
        if ft['geometry']['type'] != geom_type: continue
        geom = sg.shape(ft['geometry'])
        if not geom.is_valid: geom = geom.buffer(0)
        if geom.is_empty: continue
        b = geom.bounds
        if b[2] < SPB_BBOX[0] or b[0] > SPB_BBOX[2]: continue
        if b[3] < SPB_BBOX[1] or b[1] > SPB_BBOX[3]: continue
        try:
            inter = geom.intersection(bbox_poly)
            if not inter.is_empty:
                feats.append(inter)
        except: pass
    return feats


def get_dem_window(tile_handles, b_left, b_bottom, b_right, b_top, shape):
    out = np.full(shape, np.nan, dtype='float32')
    win_tf = from_origin(b_left, b_top, 2, 2)
    inv = ~win_tf
    c_out, r_out = inv * (b_left, b_top)
    c_out = int(round(c_out)); r_out = int(round(r_out))
    for th in tile_handles:
        tb = th['bounds']
        il = max(b_left, tb[0]); ib = max(b_bottom, tb[1])
        ir = min(b_right, tb[2]); it = min(b_top, tb[3])
        if il >= ir or ib >= it: continue
        src = th['handle']
        try:
            tw = from_bounds(il, ib, ir, it, src.transform)
            td = src.read(1, window=tw, boundless=True, fill_value=np.nan).astype('float32')
            if src.nodata is not None:
                td[td == src.nodata] = np.nan
        except: continue
        if td.size == 0: continue
        h_tile, w_tile = td.shape
        r0 = max(0, r_out); c0 = max(0, c_out)
        r1 = min(shape[0], r_out + h_tile)
        c1 = min(shape[1], c_out + w_tile)
        if r1 <= r0 or c1 <= c0: continue
        sr0 = r0 - r_out; sc0 = c0 - c_out
        sr1 = sr0 + (r1 - r0); sc1 = sc0 + (c1 - c0)
        src_slice = td[sr0:sr1, sc0:sc1]
        dst_slice = out[r0:r1, c0:c1]
        mask = np.isnan(dst_slice) & np.isfinite(src_slice)
        dst_slice[mask] = src_slice[mask]
    return out


def rasterize_shifted(polys, shape, win_tf, shift_px=(0, 0), geom_type='Polygon', width_m=4):
    """Rasterize polys/lines with pixel shift. For LineString, buffer by width_m."""
    dc, dr = shift_px
    shifted_tf = rasterio.Affine(win_tf.a, win_tf.b, win_tf.c + dc * 2,
                                  win_tf.d, win_tf.e, win_tf.f - dr * 2)
    if geom_type == 'LineString':
        # Buffer lines to width_m meters in EPSG:3413
        geoms = [(p.buffer(width_m / 2), 1) for p in polys if p is not None and not p.is_empty]
    else:
        geoms = [(p, 1) for p in polys if p is not None and not p.is_empty]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    try:
        arr = rasterize(geoms, out_shape=shape, transform=shifted_tf,
                        fill=0, dtype='uint8', all_touched=False)
        return arr.astype(bool)
    except:
        return np.zeros(shape, dtype=bool)


def find_tile(lon, lat):
    """Which tile covers this point?"""
    for t in TILES:
        b = t['bounds_4326']
        if b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
            return t['name']
    return None


def main():
    log('=' * 60)
    log('Per-tile + streets vs buildings shift check')
    log('=' * 60)
    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    os.environ['VSI_CACHE'] = 'TRUE'
    os.environ['VSI_CACHE_SIZE'] = '500000000'

    pp = load_passport()
    log(f'Passport: {len(pp)} points')
    
    # Tag each point with its tile
    pp['tile'] = pp.apply(lambda r: find_tile(r.lon, r.lat), axis=1)
    log(f'Per-tile counts:')
    print(pp.tile.value_counts())
    
    # Load buildings
    log('Loading buildings...')
    if not BUILDINGS_GJSON.exists():
        log('ERROR: buildings geojson missing — run spb_full_run_v2.py first')
        return
    buildings_4326 = load_polys(BUILDINGS_GJSON, 'Polygon')
    log(f'  buildings: {len(buildings_4326)} polys')
    
    # Load/download highways
    log('Loading highways...')
    highways_path = download_highways()
    highways_4326 = load_polys(highways_path, 'LineString')
    log(f'  highways: {len(highways_4326)} lines')
    
    # Project to EPSG:3413
    log('Projecting to EPSG:3413...')
    proj = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True).transform
    buildings_3413 = [so.transform(proj, p) for p in buildings_4326 if p is not None and not p.is_empty]
    highways_3413 = [so.transform(proj, p) for p in highways_4326 if p is not None and not p.is_empty]
    log(f'  done')
    
    # Open tiles
    log('Opening ArcticDEM tiles...')
    tile_handles = [{'name': t['name'], 'handle': rasterio.open(t['url']),
                     'bounds': rasterio.open(t['url']).bounds} for t in TILES]
    log(f'  {len(tile_handles)} tiles')
    
    # For each passport point: test all shifts against buildings AND highways
    tr_fwd = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True)
    shape = (W_HALF * 2 + 1, W_HALF * 2 + 1)
    
    # Per-tile shift stats (buildings only)
    tile_shifts_build = {t['name']: {s: 0 for s in SHIFTS} for t in TILES}
    # Per-tile shift stats (highways only)
    tile_shifts_road = {t['name']: {s: 0 for s in SHIFTS} for t in TILES}
    # All-tile (buildings)
    all_build = {s: 0 for s in SHIFTS}
    # All-tile (highways)
    all_road = {s: 0 for s in SHIFTS}
    
    log(f'\nTesting {len(SHIFTS)} shifts × {len(pp)} points × (buildings + highways)...')
    
    for i, p in pp.iterrows():
        if i % 20 == 0:
            log(f'  {i}/{len(pp)}...')
        tile_name = p.tile
        if tile_name is None: continue
        
        x_c, y_c = tr_fwd.transform(p.lon, p.lat)
        b_left = x_c - W_HALF * 2
        b_right = x_c + (W_HALF + 1) * 2
        b_top = y_c + W_HALF * 2
        b_bottom = y_c - (W_HALF + 1) * 2
        win_tf = from_origin(b_left, b_top, 2, 2)
        block_box = sg.box(b_left, b_bottom, b_right, b_top)
        
        # Filter buildings in block
        b_local = []
        for bp in buildings_3413:
            if bp.bounds[0] > b_right or bp.bounds[2] < b_left: continue
            if bp.bounds[1] > b_top or bp.bounds[3] < b_bottom: continue
            try:
                inter = bp.intersection(block_box)
                if not inter.is_empty:
                    if inter.geom_type == 'Polygon': b_local.append(inter)
                    elif hasattr(inter, 'geoms'):
                        for g in inter.geoms:
                            if g.geom_type == 'Polygon' and not g.is_empty: b_local.append(g)
            except: pass
        
        # Filter highways in block
        h_local = []
        for hp in highways_3413:
            if hp.bounds[0] > b_right or hp.bounds[2] < b_left: continue
            if hp.bounds[1] > b_top or hp.bounds[3] < b_bottom: continue
            try:
                inter = hp.intersection(block_box)
                if not inter.is_empty:
                    h_local.append(inter)
            except: pass
        
        # Test all shifts for buildings
        for shift in SHIFTS:
            b_mask = rasterize_shifted(b_local, shape, win_tf, shift, 'Polygon')
            if b_mask.any():
                dist_arr = distance_transform_edt(~b_mask) * 2.0
                if dist_arr[W_HALF, W_HALF] <= 6:
                    tile_shifts_build[tile_name][shift] += 1
                    all_build[shift] += 1
        
        # Test all shifts for highways (street width 4m)
        for shift in SHIFTS:
            h_mask = rasterize_shifted(h_local, shape, win_tf, shift, 'LineString', width_m=8)
            if h_mask.any():
                dist_arr = distance_transform_edt(~h_mask) * 2.0
                if dist_arr[W_HALF, W_HALF] <= 4:  # 4m from road centerline
                    tile_shifts_road[tile_name][shift] += 1
                    all_road[shift] += 1
    
    # Print results
    print('\n' + '=' * 100)
    print('=== ALL TILES: buildings (≤6m) ===')
    print_2d_grid(all_build)
    
    print('\n=== ALL TILES: highways (≤4m from 8m-wide street) ===')
    print_2d_grid(all_road)
    
    # Per-tile
    for t in TILES:
        n_points = (pp.tile == t['name']).sum()
        print(f'\n=== TILE {t["name"].upper()} (n={n_points}): buildings (≤6m) ===')
        print_2d_grid(tile_shifts_build[t['name']])
        print(f'\n=== TILE {t["name"].upper()}: highways (≤4m) ===')
        print_2d_grid(tile_shifts_road[t['name']])
    
    # Summary
    print('\n' + '=' * 100)
    print('=== SUMMARY ===')
    best_build = max(SHIFTS, key=lambda s: all_build[s])
    best_road = max(SHIFTS, key=lambda s: all_road[s])
    print(f'Best shift for BUILDINGS (all tiles): {best_build} = ({best_build[0]*2}, {best_build[1]*2}) m, hits={all_build[best_build]}')
    print(f'Best shift for HIGHWAYS  (all tiles): {best_road} = ({best_road[0]*2}, {best_road[1]*2}) m, hits={all_road[best_road]}')
    print(f'Baseline (0,0) buildings: {all_build[(0,0)]}')
    print(f'Baseline (0,0) highways:  {all_road[(0,0)]}')
    
    print(f'\nPer-tile best:')
    for t in TILES:
        bb = max(SHIFTS, key=lambda s: tile_shifts_build[t['name']][s])
        br = max(SHIFTS, key=lambda s: tile_shifts_road[t['name']][s])
        print(f'  {t["name"]:6}  buildings: {bb} ({bb[0]*2},{bb[1]*2})m hits={tile_shifts_build[t["name"]][bb]}  '
              f'highways: {br} ({br[0]*2},{br[1]*2})m hits={tile_shifts_road[t["name"]][br]}')
    
    # Save
    results = {
        'all_buildings': {f'{s[0]},{s[1]}': all_build[s] for s in SHIFTS},
        'all_highways': {f'{s[0]},{s[1]}': all_road[s] for s in SHIFTS},
        'per_tile_buildings': {t['name']: {f'{s[0]},{s[1]}': tile_shifts_build[t['name']][s] for s in SHIFTS} for t in TILES},
        'per_tile_highways': {t['name']: {f'{s[0]},{s[1]}': tile_shifts_road[t['name']][s] for s in SHIFTS} for t in TILES},
    }
    with open(str(OUT / 'spb_geoshift_per_tile.json'), 'w') as f:
        json.dump(results, f, indent=2)
    with open(str(PERSIST / 'spb_geoshift_per_tile.json'), 'w') as f:
        json.dump(results, f, indent=2)
    log(f'\n✓ Saved: spb_geoshift_per_tile.json')
    
    for th in tile_handles:
        try: th['handle'].close()
        except: pass


def print_2d_grid(shift_hits):
    """Print 11x11 grid of shift hits."""
    print(f'{"":>8}', end='')
    for dc in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]:
        print(f'{dc:>+5}', end='')
    print()
    print('-' * 70)
    for dr in [5, 4, 3, 2, 1, 0, -1, -2, -3, -4, -5]:
        print(f'dr={dr:>+3}: ', end='')
        for dc in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]:
            v = shift_hits.get((dc, dr), 0)
            print(f'{v:>5}', end='')
        print()


if __name__ == '__main__':
    main()
