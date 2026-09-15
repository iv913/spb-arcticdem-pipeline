#!/usr/bin/env python3
"""
spb_check_geoshift.py — проверить гипотезу о смещении геопривязки ArcticDEM.

Для каждой из 56 detail=23 точек:
- Прочитать 41×41 окно ArcticDEM из /vsicurl/
- Растеризовать OSM buildings с разными сдвигами (0, ±2, ±4, ±6 px)
- Найти, при каком сдвиге доля точек "у здания" (внутри или <6м) максимальна

Если найдётся сдвиг, который ловит больше точек, чем 0 — гипотеза подтверждена.
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

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
OUT.mkdir(parents=True, exist_ok=True)
PERSIST = HOME / 'upload' / 'scripts_persistent'
PASSPORT_TXT = HOME / 'upload' / 'Отметки.txt'
BUILDINGS_GJSON = OUT / 'spb_osm_buildings.geojson'
COP_TIF = HOME / 'scripts' / 'dem_cache' / 'copernicus' / 'cop_spb_3413_30m.tif'

SHIFT_M = 15.673
SPB_BBOX = (29.70, 59.75, 30.55, 60.05)
W_HALF = 30  # 61x61 window = 120m

TILES = [
    {'name': 'center', 'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_1_1_2m_v4.1_dem.tif'},
    {'name': 'east',   'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_2_1_2m_v4.1_dem.tif'},
    {'name': 'north',  'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_72/32_72_1_2_2m_v4.1_dem.tif'},
]

SHIFTS_TO_TEST = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
                  (2, 0), (-2, 0), (0, 2), (0, -2),
                  (3, 0), (-3, 0), (0, 3), (0, -3),
                  (4, 0), (-4, 0), (0, 4), (0, -4)]


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


def load_buildings():
    if not BUILDINGS_GJSON.exists():
        log('Downloading OSM buildings...')
        import requests
        W, S, E, N = SPB_BBOX
        q = f'''[out:json][timeout:600];(way["building"]({S},{W},{N},{E});relation["building"]({S},{W},{N},{E}););out geom;'''
        r = requests.post('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                          data={'data': q}, timeout=600)
        data = r.json()
        log(f'  {len(data.get("elements", []))} elements')
        # Convert to geojson
        import importlib.util
        spec = importlib.util.spec_from_file_location('spf', PERSIST / 'spb_full_run_v2.py')
        spf = importlib.util.module_from_spec(spec); spec.loader.exec_module(spf)
        feats = spf.osm_to_geojson(data)
        gj = {'type': 'FeatureCollection', 'features': feats,
              'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}}}
        BUILDINGS_GJSON.write_text(json.dumps(gj, ensure_ascii=False))
        log(f'  ✓ {len(feats)} features')
    
    with open(BUILDINGS_GJSON) as fp:
        gj = json.load(fp)
    bbox_poly = sg.box(*SPB_BBOX)
    polys = []
    for ft in gj['features']:
        geom = sg.shape(ft['geometry'])
        if not geom.is_valid: geom = geom.buffer(0)
        if geom.is_empty: continue
        b = geom.bounds
        if b[2] < SPB_BBOX[0] or b[0] > SPB_BBOX[2]: continue
        if b[3] < SPB_BBOX[1] or b[1] > SPB_BBOX[3]: continue
        try:
            inter = geom.intersection(bbox_poly)
            if inter.is_empty: continue
            if inter.geom_type == 'Polygon': polys.append(inter)
            elif inter.geom_type == 'MultiPolygon':
                for p in inter.geoms:
                    if not p.is_empty: polys.append(p)
        except Exception:
            pass
    log(f'Buildings: {len(polys)} polys in bbox')
    return polys


def get_dem_window(tile_handles, b_left, b_bottom, b_right, b_top, shape):
    """Read DEM for window from tiles."""
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
        except Exception:
            continue
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


def rasterize_buildings_shifted(buildings_local, shape, win_tf, shift_px=(0, 0)):
    """Rasterize buildings with pixel shift applied to transform."""
    dc, dr = shift_px
    # Shift transform: c += dc*2, f -= dr*2 (note: row goes down = -y)
    shifted_tf = rasterio.Affine(win_tf.a, win_tf.b, win_tf.c + dc * 2,
                                  win_tf.d, win_tf.e, win_tf.f - dr * 2)
    if not buildings_local:
        return np.zeros(shape, dtype=bool)
    geoms = [(p, 1) for p in buildings_local if p is not None and not p.is_empty]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    try:
        arr = rasterize(geoms, out_shape=shape, transform=shifted_tf,
                        fill=0, dtype='uint8', all_touched=False)
        return arr.astype(bool)
    except Exception:
        return np.zeros(shape, dtype=bool)


def main():
    log('=' * 60)
    log('Geo-shift check: does ArcticDEM have a few-pixel offset?')
    log('=' * 60)
    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    os.environ['VSI_CACHE'] = 'TRUE'
    os.environ['VSI_CACHE_SIZE'] = '500000000'

    # Load passport
    pp = load_passport()
    log(f'Passport: {len(pp)} points')

    # Load buildings (download if missing)
    buildings_4326 = load_buildings()
    
    # Load d23 verification CSV (from persistent backup)
    d23_csv = PERSIST / 'spb_d23_verification.csv'
    if not d23_csv.exists():
        # Fallback: download/ CSV
        d23_csv = OUT / 'spb_d23_verification.csv'
    if d23_csv.exists():
        d23_df = pd.read_csv(d23_csv)
        d23_df = d23_df[d23_df.detail == 23].reset_index(drop=True)
        log(f'detail=23 points from CSV: {len(d23_df)}')
    else:
        log('No d23 CSV found, will compute for all 202 passport points')
        d23_df = pp.copy()
        d23_df['detail'] = 23  # placeholder

    # Project buildings to EPSG:3413
    log('Projecting buildings to EPSG:3413...')
    proj = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True).transform
    buildings_3413 = [so.transform(proj, p) for p in buildings_4326 if p is not None and not p.is_empty]
    log(f'  projected: {len(buildings_3413)} polys')

    # Open ArcticDEM tiles
    log('Opening ArcticDEM tiles...')
    tile_handles = []
    for t in TILES:
        h = rasterio.open(t['url'])
        tile_handles.append({'name': t['name'], 'handle': h, 'bounds': h.bounds})
    log(f'  {len(tile_handles)} tiles opened')

    # For each passport point: read DEM window, rasterize buildings with each shift
    log(f'\nTesting {len(SHIFTS_TO_TEST)} shifts × {len(pp)} points...')
    
    # Results: for each shift, count how many points are "inside building" or "within 6m"
    # Also count for d23 subset
    shift_stats = {s: {'inside': 0, 'within_6m': 0, 'within_10m': 0,
                       'd23_inside': 0, 'd23_within_6m': 0, 'd23_within_10m': 0}
                   for s in SHIFTS_TO_TEST}
    
    # Track per-point detail
    all_results = []
    
    tr_fwd = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True)
    
    for i, p in pp.iterrows():
        if i % 20 == 0:
            log(f'  {i}/{len(pp)}...')
        x_c, y_c = tr_fwd.transform(p.lon, p.lat)
        # Window 61x61 = 120m
        b_left = x_c - W_HALF * 2
        b_right = x_c + (W_HALF + 1) * 2
        b_top = y_c + W_HALF * 2
        b_bottom = y_c - (W_HALF + 1) * 2
        shape = (W_HALF * 2 + 1, W_HALF * 2 + 1)
        
        # Get DEM window
        dem = get_dem_window(tile_handles, b_left, b_bottom, b_right, b_top, shape)
        h_dsm = dem[W_HALF, W_HALF] - SHIFT_M if np.isfinite(dem[W_HALF, W_HALF]) else np.nan
        
        # Find buildings in this block
        block_box = sg.box(b_left, b_bottom, b_right, b_top)
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
            except Exception: pass
        
        # Reference transform (no shift)
        ref_tf = from_origin(b_left, b_top, 2, 2)
        center_r, center_c = W_HALF, W_HALF
        
        is_d23 = (i in d23_df.index) if len(d23_df) < len(pp) else True
        
        for shift in SHIFTS_TO_TEST:
            b_mask = rasterize_buildings_shifted(b_local, shape, ref_tf, shift_px=shift)
            # Distance transform from building
            from scipy.ndimage import distance_transform_edt
            if b_mask.any():
                dist_arr = distance_transform_edt(~b_mask) * 2.0  # meters
                dist_at_center = dist_arr[center_r, center_c]
                inside = b_mask[center_r, center_c]
            else:
                dist_at_center = 9999
                inside = False
            
            shift_stats[shift]['inside'] += int(inside)
            if dist_at_center <= 6:
                shift_stats[shift]['within_6m'] += 1
            if dist_at_center <= 10:
                shift_stats[shift]['within_10m'] += 1
            
            if is_d23:
                shift_stats[shift]['d23_inside'] += int(inside)
                if dist_at_center <= 6:
                    shift_stats[shift]['d23_within_6m'] += 1
                if dist_at_center <= 10:
                    shift_stats[shift]['d23_within_10m'] += 1
        
        # For shift=0 baseline, record h_dsm and dist_to_building
        b_mask_0 = rasterize_buildings_shifted(b_local, shape, ref_tf, shift_px=(0, 0))
        if b_mask_0.any():
            dist_arr_0 = distance_transform_edt(~b_mask_0) * 2.0
            dist_at_0 = float(dist_arr_0[center_r, center_c])
        else:
            dist_at_0 = 9999.0
        
        all_results.append({
            'lat': float(p.lat), 'lon': float(p.lon), 'hp': float(p.hp),
            'h_dsm': float(h_dsm) if np.isfinite(h_dsm) else None,
            'dist_to_building_m_shift0': round(dist_at_0, 1) if dist_at_0 < 9999 else None,
        })

    # Print results
    print('\n' + '=' * 90)
    print(f'{"shift (col,row)":<20}{"inside":>8}{"≤6m":>8}{"≤10m":>8}{"d23_in":>8}{"d23_≤6m":>10}{"d23_≤10m":>10}')
    print('-' * 90)
    for shift in SHIFTS_TO_TEST:
        s = shift_stats[shift]
        print(f'({shift[0]:>+3},{shift[1]:>+3}) px    '
              f'{s["inside"]:>8}{s["within_6m"]:>8}{s["within_10m"]:>8}'
              f'{s["d23_inside"]:>8}{s["d23_within_6m"]:>10}{s["d23_within_10m"]:>10}')
    
    # Find best shift
    best_shift = max(SHIFTS_TO_TEST, key=lambda s: shift_stats[s]['within_6m'])
    best_s = shift_stats[best_shift]
    baseline = shift_stats[(0, 0)]
    print(f'\nBaseline (0,0): inside={baseline["inside"]}, ≤6m={baseline["within_6m"]}')
    print(f'Best shift {best_shift}: inside={best_s["inside"]}, ≤6m={best_s["within_6m"]}')
    
    if best_shift != (0, 0) and best_s['within_6m'] > baseline['within_6m'] + 3:
        print(f'\n→ СДВИГ ПОДТВЕРЖДЕН: ArcticDEM смещён на {best_shift[0]} px по X, {best_shift[1]} px по Y')
        print(f'  Это {best_shift[0]*2} м по X, {best_shift[1]*2} м по Y')
        print(f'  Лечится одной поправкой геотрансформа: transform.c += {best_shift[0]*2}, transform.f -= {best_shift[1]*2}')
    else:
        print(f'\n→ СДВИГ НЕ ПОДТВЕРЖДЕН: нет значимого сдвига (max improvement = {best_s["within_6m"] - baseline["within_6m"]})')
        print(f'  Гипотеза о геопривязке не объясняет +2.79 м на точках вдали от зданий')
    
    # Save CSV
    df = pd.DataFrame(all_results)
    df.to_csv(str(OUT / 'spb_geoshift_check.csv'), index=False)
    # Also save to persistent
    df.to_csv(str(PERSIST / 'spb_geoshift_check.csv'), index=False)
    log(f'\n✓ Saved: spb_geoshift_check.csv')
    
    # Save shift stats
    shift_data = [{'shift_col': s[0], 'shift_row': s[1], **shift_stats[s]} for s in SHIFTS_TO_TEST]
    with open(str(OUT / 'spb_geoshift_stats.json'), 'w') as f:
        json.dump(shift_data, f, indent=2)
    with open(str(PERSIST / 'spb_geoshift_stats.json'), 'w') as f:
        json.dump(shift_data, f, indent=2)
    log(f'✓ Saved: spb_geoshift_stats.json')
    
    for th in tile_handles:
        try: th['handle'].close()
        except: pass


if __name__ == '__main__':
    main()
