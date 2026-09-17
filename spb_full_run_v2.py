#!/usr/bin/env python3
"""
spb_full_run_v2.py — SPb pipeline with PMF, FIXED train/holdout separation.

KEY FIX vs v1:
- ICESat points that became anchors (detail=22) are EXCLUDED from holdout
- Per-block metrics saved in progress.json (per user request)
- All outputs to /home/z/my-project/download/, scripts copied to upload/scripts_persistent/

Steps:
1. Download Copernicus 30m + reproject to EPSG:3413
2. Download OSM (buildings, water, green) with relation stitching
3. Backup scripts to upload/scripts_persistent/ (survives session reset)
4. PMF pipeline (anchor_field + build_prior + accept_dsm)
5. Validate with train/holdout split:
   - detail=22 (ICESat anchors) = TRAIN
   - everything else = HOLDOUT
6. Save all results to download/results/
"""
import os, sys, json, gc, math, re, subprocess, shutil
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
import geopandas as gpd
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.windows import Window, from_bounds
from rasterio.merge import merge
import shapely.geometry as sg
import shapely.ops as so
import pyproj
from scipy.ndimage import (uniform_filter, minimum_filter, maximum_filter,
                            distance_transform_edt)
import importlib.util

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
OUT.mkdir(parents=True, exist_ok=True)
PERSIST = HOME / 'upload' / 'scripts_persistent'
PERSIST.mkdir(parents=True, exist_ok=True)
RESULTS = OUT / 'results'
RESULTS.mkdir(parents=True, exist_ok=True)

ICESAT_GPKG = HOME / 'upload' / 'atl08_ground.gpkg'
PASSPORT_TXT = HOME / 'upload' / 'Отметки.txt'
BUILDINGS_GJSON = OUT / 'spb_osm_buildings.geojson'
WATER_GJSON = OUT / 'spb_osm_water.geojson'
GREEN_GJSON = OUT / 'spb_osm_green.geojson'
COP_DIR = HOME / 'scripts' / 'dem_cache' / 'copernicus'
COP_DIR.mkdir(parents=True, exist_ok=True)
COP_TIF = COP_DIR / 'cop_spb_3413_30m.tif'

SHIFT_M = 15.673
PIXEL_M = 2.0
BLOCK = 2048  # reduced from 4096 for C2 weighted build_prior memory

SPB_BBOX = (29.70, 59.75, 30.55, 60.05)

TILES = [
    {'name': 'center', 'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_1_1_2m_v4.1_dem.tif'},
    {'name': 'east',   'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_73/32_73_2_1_2m_v4.1_dem.tif'},
    {'name': 'north',  'url': 'https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32_72/32_72_1_2_2m_v4.1_dem.tif'},
]


def _load_libg():
    spec = importlib.util.spec_from_file_location('libg', HOME / 'scripts' / 'lib_ground.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

libg = _load_libg()


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


# ============================================================
# STEP 1: Copernicus DEM 30m
# ============================================================
def step1_copernicus():
    if COP_TIF.exists() and COP_TIF.stat().st_size > 1_000_000:
        log(f'✓ Copernicus cached: {COP_TIF}')
        return
    log('Step 1: Download Copernicus DEM 30m...')
    for tile in ['N59_00_E029_00', 'N59_00_E030_00', 'N60_00_E029_00', 'N60_00_E030_00']:
        url = f'https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/Copernicus_DSM_COG_10_{tile}_DEM/Copernicus_DSM_COG_10_{tile}_DEM.tif'
        out = COP_DIR / f'cop_{tile}.tif'
        if not out.exists() or out.stat().st_size < 1_000_000:
            subprocess.run(['wget', '-q', '--timeout=300', url, '-O', str(out)], check=True)
        log(f'  ✓ {tile}')

    log('  Merging + reprojecting...')
    srcs = [rasterio.open(str(COP_DIR / f'cop_{t}.tif')) for t in ['N59_00_E029_00', 'N59_00_E030_00', 'N60_00_E029_00', 'N60_00_E030_00']]
    mosaic, tf = merge(srcs)
    spb_3413 = transform_bounds('EPSG:4326', 'EPSG:3413', *SPB_BBOX)
    left = math.floor(spb_3413[0] / 30) * 30
    bottom = math.floor(spb_3413[1] / 30) * 30
    right = math.ceil(spb_3413[2] / 30) * 30
    top = math.ceil(spb_3413[3] / 30) * 30
    w = int((right - left) / 30); h = int((top - bottom) / 30)
    out_tf = from_origin(left, top, 30, 30)
    dst = np.full((h, w), np.nan, dtype='float32')
    reproject(source=mosaic[0], destination=dst,
              src_transform=tf, src_crs=srcs[0].crs,
              dst_transform=out_tf, dst_crs='EPSG:3413',
              resampling=Resampling.bilinear,
              src_nodata=srcs[0].nodata, dst_nodata=np.nan)
    profile = {'driver': 'GTiff', 'dtype': 'float32', 'count': 1,
               'crs': 'EPSG:3413', 'transform': out_tf,
               'width': w, 'height': h, 'compress': 'deflate', 'nodata': np.nan}
    with rasterio.open(str(COP_TIF), 'w', **profile) as d:
        d.write(dst.astype('float32'), 1)
    for s in srcs: s.close()
    log(f'  ✓ {COP_TIF}')


# ============================================================
# STEP 2: OSM download with relation stitching
# ============================================================
def osm_to_geojson(data):
    from shapely.geometry import shape, mapping, Polygon, LineString
    from shapely.ops import linemerge, polygonize
    feats = []
    ways_by_id = {}
    standalone_ways = []
    for el in data['elements']:
        if el['type'] == 'way' and 'geometry' in el:
            ways_by_id[el['id']] = el
            standalone_ways.append(el)
    for el in data['elements']:
        if el['type'] != 'relation' or 'members' not in el: continue
        outers, inners = [], []
        for m in el['members']:
            if m.get('type') != 'way' or m.get('role') not in ('outer', 'inner'): continue
            way = ways_by_id.get(m['ref'])
            if way is None or 'geometry' not in way: continue
            coords = [(p['lon'], p['lat']) for p in way['geometry']]
            if len(coords) < 2: continue
            if m.get('role') == 'outer': outers.append(coords)
            else: inners.append(coords)
        if not outers: continue
        outer_lines = [LineString(r) for r in outers if r]
        if not outer_lines: continue
        merged = linemerge(outer_lines) if len(outer_lines) > 1 else outer_lines[0]
        outer_polys = list(polygonize(merged)) if merged.geom_type != 'Polygon' else [merged]
        if not outer_polys: continue
        inner_lines = [LineString(r) for r in inners if r and len(r) >= 2]
        inner_polys = []
        if inner_lines:
            inner_merged = linemerge(inner_lines) if len(inner_lines) > 1 else inner_lines[0]
            inner_polys = list(polygonize(inner_merged)) if inner_merged.geom_type != 'Polygon' else [inner_merged]
        for op in outer_polys:
            holes = []
            for ip in inner_polys:
                if op.contains(ip.representative_point()):
                    holes.append(list(ip.exterior.coords))
            try:
                poly = Polygon(op.exterior.coords, holes)
                if not poly.is_valid: poly = poly.buffer(0)
                if poly.is_empty: continue
                if poly.geom_type == 'Polygon':
                    feats.append({'type': 'Feature', 'geometry': mapping(poly), 'properties': el.get('tags', {})})
                elif poly.geom_type == 'MultiPolygon':
                    for p in poly.geoms:
                        if not p.is_empty:
                            feats.append({'type': 'Feature', 'geometry': mapping(p), 'properties': el.get('tags', {})})
            except Exception:
                pass
    for el in standalone_ways:
        geom = el.get('geometry')
        if not geom: continue
        coords = [(p['lon'], p['lat']) for p in geom]
        if len(coords) >= 3 and coords[0] == coords[-1]:
            feats.append({'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': [coords]}, 'properties': el.get('tags', {})})
        elif len(coords) >= 2:
            feats.append({'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': coords}, 'properties': el.get('tags', {})})
    return feats


def step2_osm():
    import requests
    OVERPASS_URLS = [
        'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
        'https://overpass.kumi.systems/api/interpreter',
    ]
    W, S, E, N = SPB_BBOX
    queries = [
        ('spb_osm_buildings', f"""[out:json][timeout:600];(way["building"]({S},{W},{N},{E});relation["building"]({S},{W},{N},{E}););out geom;"""),
        ('spb_osm_water', f"""[out:json][timeout:600];(way["natural"="water"]({S},{W},{N},{E});relation["natural"="water"]({S},{W},{N},{E});way["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E});relation["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E}););out geom;"""),
        ('spb_osm_green', f"""[out:json][timeout:600];(way["landuse"~"^(grass|forest|meadow|village_green|flowerbed)$"]({S},{W},{N},{E});way["leisure"~"^(park|garden|playground|common)$"]({S},{W},{N},{E});way["natural"~"^(wood|scrub|tree_row|wetland)$"]({S},{W},{N},{E}););out geom;"""),
    ]
    for name, q in queries:
        out_path = OUT / f'{name}.geojson'
        if out_path.exists() and out_path.stat().st_size > 1000:
            log(f'  ✓ {name} cached')
            continue
        log(f'  Downloading {name}...')
        data = None
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={'data': q}, timeout=600)
                if r.status_code == 200 and r.text.strip():
                    data = r.json()
                    log(f'    OK: {len(data.get("elements", []))} elements')
                    break
            except Exception as e:
                log(f'    ! {e}')
        if data:
            feats = osm_to_geojson(data)
            gj = {'type': 'FeatureCollection', 'features': feats,
                  'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}}}
            out_path.write_text(json.dumps(gj, ensure_ascii=False))
            log(f'  ✓ {out_path.name} ({len(feats)} features)')


# ============================================================
# STEP 3: Backup scripts to upload/scripts_persistent/ (survives reset!)
# ============================================================
def step3_backup():
    log('Step 3: Backup scripts to upload/scripts_persistent/...')
    sources = [
        HOME / 'scripts' / 'lib_ground.py',
        HOME / 'scripts' / 'lib_vertical.py',
        HOME / 'scripts' / 'lib_profile.py',
        Path(__file__),
    ]
    for src in sources:
        if src.exists():
            shutil.copy2(src, PERSIST / src.name)
    log(f'  ✓ {PERSIST} ({len(list(PERSIST.glob("*.py")))} files)')


# ============================================================
# STEP 4: Pipeline with PMF
# ============================================================
def load_icesat():
    g = gpd.read_file(str(ICESAT_GPKG))
    log(f'ICESat-2 ATL08: {len(g)} points')
    g34 = g.to_crs('EPSG:3413')
    df = pd.DataFrame({
        'x': g34.geometry.x.values,
        'y': g34.geometry.y.values,
        'h_ice': g['h_te_ell'].values - SHIFT_M,
        'h_unc': g['h_unc'].values if 'h_unc' in g.columns else np.full(len(g), 0.5),
    })
    df = df[df['h_unc'] <= 5.0].copy()
    log(f'  After h_unc<=5: {len(df)}')
    return df


def load_osm_polys(geojson_path, bbox_4326):
    with open(geojson_path) as fp:
        gj = json.load(fp)
    bbox_poly = sg.box(*bbox_4326)
    polys = []
    for ft in gj['features']:
        geom = sg.shape(ft['geometry'])
        if not geom.is_valid: geom = geom.buffer(0)
        if geom.is_empty: continue
        b = geom.bounds
        if b[2] < bbox_4326[0] or b[0] > bbox_4326[2]: continue
        if b[3] < bbox_4326[1] or b[1] > bbox_4326[3]: continue
        try:
            inter = geom.intersection(bbox_poly)
            if inter.is_empty: continue
            if inter.geom_type == 'Polygon': polys.append(inter)
            elif inter.geom_type == 'MultiPolygon':
                for p in inter.geoms:
                    if not p.is_empty: polys.append(p)
        except Exception:
            pass
    return polys


def project_polys(polys_4326, target_crs='EPSG:3413'):
    proj = pyproj.Transformer.from_crs('EPSG:4326', target_crs, always_xy=True).transform
    return [so.transform(proj, p) for p in polys_4326 if p is not None and not p.is_empty]


def rasterize_polys(polys_proj, shape, transform):
    if polys_proj is None or len(polys_proj) == 0:
        return np.zeros(shape, dtype=bool)
    geoms = [(p, 1) for p in polys_proj if p is not None and not p.is_empty]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    try:
        arr = rasterize(geoms, out_shape=shape, transform=transform,
                        fill=0, dtype='uint8', all_touched=False)
        return arr.astype(bool)
    except Exception:
        return np.zeros(shape, dtype=bool)


def get_dem_for_window(tile_handles, b_left, b_bottom, b_right, b_top, out_shape, block_transform):
    out = np.full(out_shape, np.nan, dtype='float32')
    inv = ~block_transform
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
        c_out, r_out = inv * (il, it)
        c_out = int(round(c_out)); r_out = int(round(r_out))
        r0 = max(0, r_out); c0 = max(0, c_out)
        r1 = min(out_shape[0], r_out + h_tile)
        c1 = min(out_shape[1], c_out + w_tile)
        if r1 <= r0 or c1 <= c0: continue
        sr0 = r0 - r_out; sc0 = c0 - c_out
        sr1 = sr0 + (r1 - r0); sc1 = sc0 + (c1 - c0)
        src_slice = td[sr0:sr1, sc0:sc1]
        dst_slice = out[r0:r1, c0:c1]
        mask = np.isnan(dst_slice) & np.isfinite(src_slice)
        dst_slice[mask] = src_slice[mask]
    return out


def load_cop_on_grid(block_transform, shape):
    with rasterio.open(str(COP_TIF)) as src:
        cop = np.full(shape, np.nan, dtype='float32')
        reproject(source=rasterio.band(src, 1), destination=cop,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=block_transform, dst_crs='EPSG:3413',
                  resampling=Resampling.bilinear,
                  src_nodata=src.nodata, dst_nodata=np.nan)
    return cop


def process_block_pmf(dem, building_mask, water_mask, cop, ice_pts_in_block, passport_pts_in_block, block_transform, shape):
    h_bs = dem - SHIFT_M
    # D FIX: use split_low_pixels to separate real water (connected to gulf)
    # from low-lying land that cop<0.5 captures by mistake
    cop_low = np.isfinite(cop) & (cop < 0.5)
    water_bodies, water_edges = libg.split_low_pixels(
        cop_low, adjacent_to=water_mask, pixel_m=PIXEL_M,
        min_area_m2=5000.0, dsm=cop, body_median_max=0.5,
        overlap_min=0.05)
    water_extended = water_mask | water_bodies
    water_mask = water_extended
    n_cop_low = int(cop_low.sum())
    n_water_bodies = int(water_bodies.sum())
    n_water_edges = int(water_edges.sum())

    try:
        anchors, ground_mask, dist_b, env, info = libg.anchor_field(
            h_bs, building_mask, water_mask, pixel_m=PIXEL_M)  # block_px=None → choose_block_px
    except Exception as e:
        log(f'    PMF ERROR: {e}, fallback')
        fin = np.isfinite(h_bs) & ~building_mask & ~water_mask
        v = np.where(fin, np.nan_to_num(h_bs), 0.0)
        w_arr = fin.astype('float64')
        m1 = uniform_filter(v, size=5, mode='nearest')
        m2 = uniform_filter(v * v, size=5, mode='nearest')
        cnt = uniform_filter(w_arr, size=5, mode='nearest')
        mean = np.divide(m1, cnt, out=np.full_like(m1, np.nan), where=cnt > 0.5)
        msq = np.divide(m2, cnt, out=np.full_like(m2, np.nan), where=cnt > 0.5)
        std = np.sqrt(np.maximum(msq - mean ** 2, 0.0))
        lo = minimum_filter(np.where(fin, h_bs, np.inf), size=5)
        rough = fin & (std > 2.0)
        h_bs_filtered = h_bs.copy()
        h_bs_filtered[rough] = lo[rough]
        anchors = fin & ~rough
        ground_mask = anchors
        env = np.full(shape, np.nan, dtype='float32')
        dist_b = np.full(shape, np.nan, dtype='float32')
        info = {'block_px': 60, 'anchors_px': int(anchors.sum()), 'cap_allow_above_m': 0.50}

    h_known = np.where(anchors, h_bs, np.nan).astype('float32')
    # C2: weights array — ICESat gets 1/σ²=100, DSM anchors get 1/σ²=4
    w_prior = np.where(anchors, 1.0 / 0.50**2, 0.0).astype('float64')  # σ_dsm=0.50
    domain = ~building_mask & ~water_mask & np.isfinite(h_bs)

    # C1 FIX: ICESat points go into h_known BEFORE build_prior, not after
    # This makes prior flow towards ICESat values in their neighborhood
    n_ice_in_prior = 0; n_ice_reject = 0
    inv = ~block_transform
    for p in ice_pts_in_block:
        x, y, h_ice = p['x'], p['y'], p['h_ice']
        c, r = inv * (x, y)
        r_i, c_i = int(round(r)), int(round(c))
        if 0 <= r_i < shape[0] and 0 <= c_i < shape[1]:
            if domain[r_i, c_i]:
                cop_pt = cop[r_i, c_i]
                if np.isfinite(cop_pt) and cop_pt - 5 <= h_ice <= cop_pt + 15:
                    h_known[r_i, c_i] = h_ice
                    w_prior[r_i, c_i] = 1.0 / 0.10**2  # σ_ice=0.10, weight=100
                    n_ice_in_prior += 1
                else:
                    n_ice_reject += 1

    # Этап 1.5: Passport train points as high-weight anchors
    # σ_passport=0.05 m → weight = 1/0.05² = 400 (dominates over ICESat=100, DSM=4)
    n_passport_in_prior = 0
    for p in passport_pts_in_block:
        x, y, hp = p['x'], p['y'], p['hp']
        c, r = inv * (x, y)
        r_i, c_i = int(round(r)), int(round(c))
        if 0 <= r_i < shape[0] and 0 <= c_i < shape[1]:
            if domain[r_i, c_i]:
                h_known[r_i, c_i] = hp  # passport height in BS-77
                w_prior[r_i, c_i] = 1.0 / 0.05**2  # σ_passport=0.05, weight=400
                n_passport_in_prior += 1

    try:
        prior = libg.build_prior(h_known, domain=domain, iters_per_level=30,
                                 weights=w_prior)
    except Exception as e:
        log(f'    build_prior ERROR: {e}, fallback')
        prior = h_bs.copy()
        prior[water_mask] = 0.0
        valid = np.isfinite(prior)
        if valid.any() and not valid.all():
            dist, indices = distance_transform_edt(~valid, return_indices=True)
            prior = prior[indices[0], indices[1]]
        v = np.where(domain, np.nan_to_num(prior), 0.0)
        w = domain.astype('float64')
        m1 = uniform_filter(v, size=15, mode='nearest')
        cnt = uniform_filter(w, size=15, mode='nearest')
        prior = np.where(cnt > 0.1, m1 / np.maximum(cnt, 1e-6), np.nan)

    # C4 (cap_far_field) REMOVED — it's a field correction after сшивка
    # Architecture revision: no field corrections after build_prior
    # cap was overriding passport (w=400) influence, hurting holdout: MAD 1.058→1.515
    n_far_capped = 0

    surface = np.full(shape, np.nan, dtype='float32')
    detail = np.zeros(shape, dtype='uint8')
    stat = np.zeros(shape, dtype='uint8')

    # Build ice_pixel mask for excluding from accept/replace
    ice_pixels = np.zeros(shape, dtype=bool)
    for p in ice_pts_in_block:
        x, y, h_ice = p['x'], p['y'], p['h_ice']
        c, r = inv * (x, y)
        r_i, c_i = int(round(r)), int(round(c))
        if 0 <= r_i < shape[0] and 0 <= c_i < shape[1]:
            if domain[r_i, c_i]:
                cop_pt = cop[r_i, c_i]
                if np.isfinite(cop_pt) and cop_pt - 5 <= h_ice <= cop_pt + 15:
                    ice_pixels[r_i, c_i] = True

    accept = libg.accept_dsm(h_bs, prior, tol_up=0.50, tol_down=1.50) & domain & ground_mask & ~ice_pixels
    replace = domain & ~accept & ~ice_pixels & np.isfinite(prior)
    # ICESat pixels: use prior value (which includes their weighted influence)
    ice_accept = ice_pixels & domain & np.isfinite(prior)
    surface[accept] = h_bs[accept]
    detail[accept] = 21; stat[accept] = 1
    surface[replace] = prior[replace]
    detail[replace] = 23; stat[replace] = 1
    surface[ice_accept] = prior[ice_accept]
    detail[ice_accept] = 22; stat[ice_accept] = 1
    surface[water_mask] = 0.0; detail[water_mask] = 32; stat[water_mask] = 0
    # C6 FIX: don't clamp below-zero, count them for QA
    below = np.isfinite(surface) & (surface < 0) & (stat == 1)
    n_below_zero = int(below.sum())

    # ICESat pixels already handled above (ice_accept → detail=22, surface=prior)
    # Passport pixels: mark as detail=24 (train anchor), surface=prior
    n_ice = n_ice_in_prior
    pp_mask = np.zeros(shape, dtype=bool)
    for p in passport_pts_in_block:
        x, y, hp = p['x'], p['y'], p['hp']
        c, r = inv * (x, y)
        r_i, c_i = int(round(r)), int(round(c))
        if 0 <= r_i < shape[0] and 0 <= c_i < shape[1]:
            if domain[r_i, c_i]:
                pp_mask[r_i, c_i] = True

    pp_accept = pp_mask & domain & np.isfinite(prior) & ~ice_pixels
    surface[pp_accept] = prior[pp_accept]
    detail[pp_accept] = 24; stat[pp_accept] = 1

    return surface, detail, stat, n_ice, n_ice_reject, int(anchors.sum()), int(ground_mask.sum()), n_water_bodies, n_water_edges, n_below_zero, n_passport_in_prior, n_far_capped, info.get('block_px', 60)


def step4_pipeline(ice, buildings_3413, water_3413, pp_train):
    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    os.environ['VSI_CACHE'] = 'TRUE'
    os.environ['VSI_CACHE_SIZE'] = '500000000'

    spb_3413 = transform_bounds('EPSG:4326', 'EPSG:3413', *SPB_BBOX)
    left, bottom, right, top = spb_3413
    left = math.floor(left / 2) * 2
    bottom = math.floor(bottom / 2) * 2
    right = math.ceil(right / 2) * 2
    top = math.ceil(top / 2) * 2
    width = int((right - left) / 2)
    height = int((top - bottom) / 2)
    transform = from_origin(left, top, 2, 2)
    log(f'  Output: {width}x{height} px')

    log('  Opening ArcticDEM tiles...')
    tile_handles = []
    for t in TILES:
        h = rasterio.open(t['url'])
        tile_handles.append({'name': t['name'], 'handle': h, 'bounds': h.bounds})

    BLOCKS_DIR = OUT / 'blocks'
    BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE = OUT / 'spb_progress.json'
    if PROGRESS_FILE.exists():
        progress = json.loads(PROGRESS_FILE.read_text())
        log(f'  Resume: {len(progress.get("completed_blocks", []))} blocks done')
    else:
        progress = {'completed_blocks': [], 'block_metrics': []}

    total_blocks = math.ceil(width / BLOCK) * math.ceil(height / BLOCK)
    log(f'  Processing {total_blocks} blocks...')

    block_idx = 0
    total_ice_used = 0
    total_anchors = 0
    total_ground = 0
    t0 = datetime.now()
    for row0 in range(0, height, BLOCK):
        for col0 in range(0, width, BLOCK):
            block_idx += 1
            block_id = f'{row0}_{col0}'
            if block_id in progress['completed_blocks']:
                log(f'  block {block_idx}/{total_blocks} SKIP')
                continue
            bw = min(BLOCK, width - col0)
            bh = min(BLOCK, height - row0)
            b_left = left + col0 * 2
            b_top = top - row0 * 2
            b_right = b_left + bw * 2
            b_bottom = b_top - bh * 2
            block_transform = from_origin(b_left, b_top, 2, 2)
            shape = (bh, bw)

            elapsed = (datetime.now() - t0).total_seconds()
            eta = elapsed / max(1, len(progress['completed_blocks'])) * max(1, total_blocks - block_idx + 1) if progress['completed_blocks'] else 0
            log(f'  block {block_idx}/{total_blocks} ({bw}x{bh}) ETA {eta/60:.1f}m')

            try:
                dem = get_dem_for_window(tile_handles, b_left-10, b_bottom-10,
                                         b_right+10, b_top+10, shape, block_transform)
            except Exception as e:
                log(f'    DEM ERROR: {e}'); continue
            if np.isfinite(dem).sum() < 100:
                log(f'    DEM empty, skip'); continue

            block_box = sg.box(b_left, b_bottom, b_right, b_top)
            b_local = []
            for p in buildings_3413:
                if p.bounds[0] > b_right or p.bounds[2] < b_left: continue
                if p.bounds[1] > b_top or p.bounds[3] < b_bottom: continue
                try:
                    inter = p.intersection(block_box)
                    if not inter.is_empty:
                        if inter.geom_type == 'Polygon': b_local.append(inter)
                        elif hasattr(inter, 'geoms'):
                            for g in inter.geoms:
                                if g.geom_type == 'Polygon' and not g.is_empty: b_local.append(g)
                except Exception: pass
            w_local = []
            for p in water_3413:
                if p.bounds[0] > b_right or p.bounds[2] < b_left: continue
                if p.bounds[1] > b_top or p.bounds[3] < b_bottom: continue
                try:
                    inter = p.intersection(block_box)
                    if not inter.is_empty:
                        if inter.geom_type == 'Polygon': w_local.append(inter)
                        elif hasattr(inter, 'geoms'):
                            for g in inter.geoms:
                                if g.geom_type == 'Polygon' and not g.is_empty: w_local.append(g)
                except Exception: pass

            building_mask = rasterize_polys(b_local, shape, block_transform)
            water_mask = rasterize_polys(w_local, shape, block_transform)
            cop = load_cop_on_grid(block_transform, shape)
            in_block = ((ice.x >= b_left) & (ice.x <= b_right) &
                        (ice.y >= b_bottom) & (ice.y <= b_top))
            ice_pts_block = ice[in_block].to_dict('records')
            
            # Passport train points in this block
            in_block_pp = ((pp_train.x >= b_left) & (pp_train.x <= b_right) &
                           (pp_train.y >= b_bottom) & (pp_train.y <= b_top))
            pp_pts_block = pp_train[in_block_pp].to_dict('records')

            try:
                surface, detail, stat, n_ice, n_ice_reject, n_anchors, n_ground, n_water_bodies, n_water_edges, n_below_zero, n_pp, n_capped, block_px = process_block_pmf(
                    dem, building_mask, water_mask, cop, ice_pts_block, pp_pts_block, block_transform, shape)
                total_ice_used += n_ice
                total_anchors += n_anchors
                total_ground += n_ground
                n_accept = int(((detail==21) & (stat==1)).sum())
                n_replace = int(((detail==23) & (stat==1)).sum())
                n_pp_px = int(((detail==24) & (stat==1)).sum())
                log(f'    ✓ ice={n_ice} pp={n_pp} blk={block_px} anchors={n_anchors:,} accept={n_accept:,} replace={n_replace:,} pp_px={n_pp_px} capped={n_capped:,}')
                # Save per-block metrics (per user request)
                progress['block_metrics'].append({
                    'block_id': block_id,
                    'n_ice_anchors': n_ice, 'n_ice_reject': n_ice_reject,
                    'n_anchors': n_anchors, 'n_ground': n_ground,
                    'n_accept': n_accept, 'n_replace': n_replace,
                    'copernicus_median_m': float(np.nanmedian(cop)) if np.isfinite(cop).any() else None,
                    'timestamp': datetime.now().isoformat(),
                })
            except Exception as e:
                log(f'    Process ERROR: {e}')
                import traceback; traceback.print_exc()
                continue

            # Save per-block file
            block_file = BLOCKS_DIR / f'block_{row0:06d}_{col0:06d}.tif'
            bprof = {'driver': 'GTiff', 'dtype': 'float32', 'count': 3,
                     'crs': 'EPSG:3413', 'transform': block_transform,
                     'width': bw, 'height': bh,
                     'compress': 'deflate', 'nodata': -9999,
                     'tiled': True, 'blockxsize': 256, 'blockysize': 256}
            surf_out = surface.copy()
            surf_out[~np.isfinite(surf_out)] = -9999
            with rasterio.open(str(block_file), 'w', **bprof) as bd:
                bd.write(surf_out.astype('float32'), 1)
                bd.write(detail.astype('float32'), 2)
                bd.write(stat.astype('float32'), 3)

            progress['completed_blocks'].append(block_id)
            PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2))

            del dem, surface, detail, stat, building_mask, water_mask, cop, b_local, w_local
            gc.collect()

    log(f'\n✓ All {len(progress["completed_blocks"])} blocks done')
    log(f'  Total: ice_anchors={total_ice_used}, anchors_px={total_anchors:,}, ground_px={total_ground:,}')

    # Merge all blocks
    out_path = OUT / 'spb_ground_surface_2m.tif'
    log(f'  Merging blocks into {out_path.name}...')
    block_files = sorted(BLOCKS_DIR.glob('block_*.tif'))
    srcs = [rasterio.open(str(bf)) for bf in block_files]
    mins_x = min(s.bounds[0] for s in srcs)
    mins_y = min(s.bounds[1] for s in srcs)
    maxs_x = max(s.bounds[2] for s in srcs)
    maxs_y = max(s.bounds[3] for s in srcs)
    out_tf = from_origin(mins_x, maxs_y, 2, 2)
    out_w = int((maxs_x - mins_x) / 2)
    out_h = int((maxs_y - mins_y) / 2)
    profile = {'driver': 'GTiff', 'dtype': 'float32', 'count': 3,
               'crs': 'EPSG:3413', 'transform': out_tf,
               'width': out_w, 'height': out_h,
               'compress': 'deflate', 'nodata': -9999,
               'tiled': True, 'blockxsize': 512, 'blockysize': 512,
               'BIGTIFF': 'IF_SAFER'}
    with rasterio.open(str(out_path), 'w', **profile) as dst:
        for i, src in enumerate(srcs):
            b = src.bounds
            col_off = int(round((b.left - out_tf.c) / 2))
            row_off = int(round((out_tf.f - b.top) / 2))
            w = src.width; h = src.height
            win = Window(col_off, row_off, w, h)
            log(f'    [{i+1}/{len(srcs)}] merge {src.name.split("/")[-1]}')
            for band in range(1, 4):
                data = src.read(band)
                dst.write(data.astype('float32'), band, window=win)
            src.close()
    log(f'✓ {out_path} ({out_path.stat().st_size//(1024*1024)}MB)')

    # Save meta
    meta = {
        'pipeline': 'spb_full_run_v2 (PMF + train/holdout fix)',
        'shift_m': SHIFT_M,
        'icesat_anchors_used': total_ice_used,
        'total_anchors_px': total_anchors,
        'total_ground_px': total_ground,
        'block_metrics': progress.get('block_metrics', []),
    }
    (OUT / 'spb_pipeline_meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))

    for th in tile_handles:
        try: th['handle'].close()
        except: pass

    return out_path, out_tf, (out_h, out_w)


# ============================================================
# STEP 5: Validation WITH train/holdout split
# ============================================================
def step5_validate(out_path, transform, shape, ice):
    log('\n=== Validation (train/holdout split) ===')
    inv = ~transform

    lines = PASSPORT_TXT.read_text(encoding='utf-8').strip().split('\n')
    lats, lons, hps = [], [], []
    for line in lines:
        line = line.strip()
        if not line: continue
        m = re.match(r'(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*:\s*(\d+)[,\.](\d*)', line)
        if m:
            lats.append(float(m.group(1))); lons.append(float(m.group(2)))
            hps.append(float(m.group(3) + '.' + (m.group(4) or '0')))
    pp = pd.DataFrame({'lat': lats, 'lon': lons, 'hp': hps})
    log(f'  Passport: {len(pp)} points')

    tr_fwd = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True)
    pp['x'] = pp.apply(lambda r: tr_fwd.transform(r.lon, r.lat)[0], axis=1)
    pp['y'] = pp.apply(lambda r: tr_fwd.transform(r.lon, r.lat)[1], axis=1)

    # Copernicus at each passport point
    with rasterio.open(str(COP_TIF)) as src:
        cop_inv = ~src.transform
        cop_shape = (src.height, src.width)
        cop_arr = src.read(1).astype('float32')
        cop_arr[cop_arr == src.nodata] = np.nan
    pp['cop'] = pp.apply(lambda r: (
        cop_arr[int(round((cop_inv * (r.x, r.y))[1])),
                int(round((cop_inv * (r.x, r.y))[0]))]
        if 0 <= int(round((cop_inv * (r.x, r.y))[1])) < cop_shape[0]
        and 0 <= int(round((cop_inv * (r.x, r.y))[0])) < cop_shape[1] else np.nan), axis=1)

    pp['row'] = pp.apply(lambda r: int(round((inv * (r.x, r.y))[1])), axis=1)
    pp['col'] = pp.apply(lambda r: int(round((inv * (r.x, r.y))[0])), axis=1)
    pp_in = pp[(pp.row >= 0) & (pp.row < shape[0]) & (pp.col >= 0) & (pp.col < shape[1])].copy()
    log(f'  Inside raster: {len(pp_in)}/{len(pp)}')

    h_models = []
    details_at_pts = []
    with rasterio.open(str(out_path)) as src:
        nodata = src.nodata
        for _, p in pp_in.iterrows():
            r, c = int(p.row), int(p.col)
            R = 30
            r0 = max(0, r-R); r1 = min(shape[0], r+R+1)
            c0 = max(0, c-R); c1 = min(shape[1], c+R+1)
            block = src.read(1, window=Window(c0, r0, c1-c0, r1-r0)).astype('float32')
            block[block == nodata] = np.nan
            lr, lc = r-r0, c-c0
            v = block[lr, lc]
            if np.isfinite(v):
                h_models.append(float(v))
            else:
                found = False
                for rad in range(1, R):
                    for dr in range(-rad, rad+1):
                        for dc in range(-rad, rad+1):
                            if dr*dr+dc*dc > rad*rad: continue
                            nr, nc = lr+dr, lc+dc
                            if 0 <= nr < block.shape[0] and 0 <= nc < block.shape[1]:
                                v = block[nr, nc]
                                if np.isfinite(v):
                                    h_models.append(float(v)); found = True; break
                        if found: break
                    if found: break
                if not found: h_models.append(np.nan)
            d_block = src.read(2, window=Window(c, r, 1, 1)).astype('float32').astype('int')
            details_at_pts.append(int(d_block[0, 0]))
    pp_in['hs'] = h_models
    pp_in['dH'] = pp_in.hs - pp_in.hp
    pp_in['detail'] = details_at_pts
    pp_valid = pp_in.dropna(subset=['dH', 'cop']).copy()
    dH = pp_valid.dH.values

    def mad(x):
        x = np.asarray(x); m = np.median(x)
        return float(np.median(np.abs(x - m)) * 1.4826)

    log(f'\n=== ПАСПОРТ ВСЕГО ===')
    if len(dH) > 0:
        log(f'  n={len(dH)}/{len(pp)} (valid/total)')
        log(f'  median={np.median(dH):+.3f}, MAD={mad(dH):.3f}, RMSE={np.sqrt((dH**2).mean()):.3f}')
        log(f'  ±1м: {(np.abs(dH)<=1).sum()}/{len(dH)} ({100*(np.abs(dH)<=1).sum()/len(dH):.0f}%)')
        log(f'  ±2м: {(np.abs(dH)<=2).sum()}/{len(dH)} ({100*(np.abs(dH)<=2).sum()/len(dH):.0f}%)')

    # Detail breakdown — including 0 (no data), 22 (ICESat), 31 (kromka), 32 (water), etc.
    log('\n=== ПАСПОРТ ПО DETAIL ===')
    log(f'{"detail":<25}{"n":>5}  {"median":>8}  {"MAD":>6}  {"RMSE":>7}  {"%>2м":>6}')
    log('-' * 65)
    detail_names = {0: '0: unknown', 21: '21: DSM accept', 22: '22: ICESat anchor',
                    23: '23: replaced by prior', 31: '31: kromka', 32: '32: water',
                    41: '41: no anchor'}
    detail_stats = []
    for code in sorted(pp_in.detail.unique()):
        name = detail_names.get(code, f'{code}: other')
        sub = pp_valid[pp_valid.detail == code]
        n = len(sub)
        if n == 0: continue
        d = sub.dH.values
        med = np.median(d); m = mad(d); rmse = float(np.sqrt((d**2).mean()))
        n2 = int((np.abs(d) > 2).sum())
        log(f'{name:<25}{n:>5}  {med:>+8.3f}  {m:>6.3f}  {rmse:>7.3f}  {100*n2/n:>5.0f}%')
        detail_stats.append({'detail': code, 'name': name, 'n': n,
                             'median_m': round(float(med),3), 'mad_m': round(m,3),
                             'rmse_m': round(rmse,3), 'pct_outside_2m': round(100*n2/n,1)})

    # === ICESat: TRAIN vs HOLDOUT SPLIT ===
    log('\n=== ICESat: TRAIN vs HOLDOUT ===')
    ice_rows = ice.apply(lambda r: int(round((inv * (r.x, r.y))[1])), axis=1).values
    ice_cols = ice.apply(lambda r: int(round((inv * (r.x, r.y))[0])), axis=1).values
    ice_in = (ice_rows >= 0) & (ice_rows < shape[0]) & (ice_cols >= 0) & (ice_cols < shape[1])
    log(f'  ICESat inside raster: {ice_in.sum()}/{len(ice)}')

    ice_hs = []
    ice_details = []
    with rasterio.open(str(out_path)) as src:
        nodata = src.nodata
        for i in np.where(ice_in)[0]:
            r, c = int(ice_rows[i]), int(ice_cols[i])
            R = 15
            r0 = max(0, r-R); r1 = min(shape[0], r+R+1)
            c0 = max(0, c-R); c1 = min(shape[1], c+R+1)
            block = src.read(1, window=Window(c0, r0, c1-c0, r1-r0)).astype('float32')
            block[block == nodata] = np.nan
            lr, lc = r-r0, c-c0
            v = block[lr, lc]
            if np.isfinite(v):
                ice_hs.append(float(v))
            else:
                found = False
                for rad in range(1, R):
                    for dr in range(-rad, rad+1):
                        for dc in range(-rad, rad+1):
                            if dr*dr+dc*dc > rad*rad: continue
                            nr, nc = lr+dr, lc+dc
                            if 0 <= nr < block.shape[0] and 0 <= nc < block.shape[1]:
                                v = block[nr, nc]
                                if np.isfinite(v):
                                    ice_hs.append(float(v)); found = True; break
                        if found: break
                    if found: break
                if not found: ice_hs.append(np.nan)
            # Read detail at this ICESat point
            d_block = src.read(2, window=Window(c, r, 1, 1)).astype('float32').astype('int')
            ice_details.append(int(d_block[0, 0]))

    ice_h_model = np.array(ice_hs)
    ice_dH_all = ice_h_model - ice.h_ice.values[ice_in]
    valid = np.isfinite(ice_dH_all)
    ice_dH_all = ice_dH_all[valid]
    ice_details_arr = np.array(ice_details)[valid]
    ice_h_ice_valid = ice.h_ice.values[ice_in][valid]

    # Split: train (detail=22) vs holdout (everything else)
    train_mask = ice_details_arr == 22
    holdout_mask = ice_details_arr != 22

    log(f'  TRAIN (detail=22, ICESat anchors): {train_mask.sum()} points')
    log(f'  HOLDOUT (detail!=22):              {holdout_mask.sum()} points')
    log(f'  Total valid:                       {len(ice_dH_all)} points')

    if train_mask.sum() > 0:
        train_dH = ice_dH_all[train_mask]
        log(f'  TRAIN:    median={np.median(train_dH):+.4f}, MAD={mad(train_dH):.4f}, RMSE={np.sqrt((train_dH**2).mean()):.4f}')
    if holdout_mask.sum() > 0:
        holdout_dH = ice_dH_all[holdout_mask]
        log(f'  HOLDOUT:  median={np.median(holdout_dH):+.4f}, MAD={mad(holdout_dH):.4f}, RMSE={np.sqrt((holdout_dH**2).mean()):.4f}')

    # Also report by detail class for ICESat
    log(f'\n  ICESat breakdown by detail:')
    for code in sorted(np.unique(ice_details_arr)):
        mask = ice_details_arr == code
        n = int(mask.sum())
        if n == 0: continue
        d = ice_dH_all[mask]
        log(f'    detail={code}: n={n}, median={np.median(d):+.3f}, MAD={mad(d):.3f}')

    # Flood levels — LAND ONLY
    log('\n=== FLOOD LEVELS (LAND ONLY) ===')
    BLOCK_VAL = 2048
    n_land = 0; n_water = 0
    flood_count = {H: 0 for H in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0]}
    with rasterio.open(str(out_path)) as src:
        nodata = src.nodata
        for r0 in range(0, shape[0], BLOCK_VAL):
            for c0 in range(0, shape[1], BLOCK_VAL):
                bh = min(BLOCK_VAL, shape[0]-r0)
                bw = min(BLOCK_VAL, shape[1]-c0)
                win = Window(c0, r0, bw, bh)
                surf = src.read(1, window=win).astype('float32')
                detail = src.read(2, window=win).astype('float32').astype('uint8')
                surf[surf == nodata] = np.nan
                water_mask = (detail == 32)
                land_mask = np.isfinite(surf) & (surf >= 0) & ~water_mask
                n_land += int(land_mask.sum())
                n_water += int(water_mask.sum())
                for H in flood_count:
                    flood_count[H] += int((land_mask & np.isfinite(surf) & (surf < H)).sum())
    log(f'Land: {n_land:,} px = {n_land*4/1e6:.0f} км²')
    log(f'Water: {n_water:,} px = {n_water*4/1e6:.0f} км²')
    flood = {}
    for H in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0]:
        pct = 100 * flood_count[H] / max(1, n_land)
        log(f'  H={H}: {pct:.2f}%')
        flood[H] = round(float(pct), 2)

    # Save QA with train/holdout split
    qa = {
        'pipeline': 'spb_full_run_v2 (PMF + train/holdout split)',
        'shift_m': SHIFT_M,
        'output': {
            'path': str(out_path),
            'width': int(shape[1]), 'height': int(shape[0]),
            'crs': 'EPSG:3413',
            'land_area_km2': round(n_land * 4 / 1e6, 1),
            'water_area_km2': round(n_water * 4 / 1e6, 1),
        },
        'passport_stats': {
            'n_total': int(len(pp)),
            'n_valid': int(len(dH)),
            'n_missing': int(len(pp) - len(dH)),
            'median_m': round(float(np.median(dH)), 3) if len(dH) > 0 else None,
            'mad_m': round(mad(dH), 3) if len(dH) > 0 else None,
            'rmse_m': round(float(np.sqrt((dH ** 2).mean())), 3) if len(dH) > 0 else None,
        },
        'passport_by_detail': detail_stats,
        'icesat_train': {
            'description': 'ICESat points where surface was set FROM ICESat (detail=22)',
            'n': int(train_mask.sum()),
            'median_m': round(float(np.median(train_dH)), 4) if train_mask.sum() > 0 else None,
            'mad_m': round(mad(train_dH), 4) if train_mask.sum() > 0 else None,
            'rmse_m': round(float(np.sqrt((train_dH**2).mean())), 4) if train_mask.sum() > 0 else None,
        },
        'icesat_holdout': {
            'description': 'ICESat points NOT used as anchors (detail!=22)',
            'n': int(holdout_mask.sum()),
            'median_m': round(float(np.median(holdout_dH)), 4) if holdout_mask.sum() > 0 else None,
            'mad_m': round(mad(holdout_dH), 4) if holdout_mask.sum() > 0 else None,
            'rmse_m': round(float(np.sqrt((holdout_dH**2).mean())), 4) if holdout_mask.sum() > 0 else None,
        },
        'flood_levels_pct_land': flood,
    }
    (OUT / 'spb_qa_2m.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    (RESULTS / 'spb_qa_2m.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    pp_valid.to_csv(str(RESULTS / 'spb_passport_validation_v3.csv'), index=False)
    log(f'\n✓ spb_qa_2m.json + results/spb_passport_validation_v3.csv')


# ============================================================
# MAIN
# ============================================================
def main():
    log('=' * 60)
    log('spb_full_run_v2: PMF + train/holdout split')
    log('=' * 60)

    step1_copernicus()
    step2_osm()
    step3_backup()

    log('\nLoading data...')
    ice = load_icesat()
    log('Projecting OSM polys...')
    buildings_4326 = load_osm_polys(BUILDINGS_GJSON, SPB_BBOX)
    water_4326 = load_osm_polys(WATER_GJSON, SPB_BBOX)
    log(f'  buildings: {len(buildings_4326)} polys, water: {len(water_4326)} polys')
    buildings_3413 = project_polys(buildings_4326, 'EPSG:3413')
    water_3413 = project_polys(water_4326, 'EPSG:3413')
    log(f'  projected')

    log('\nStep 4: Pipeline...')
    # Load passport split for train/holdout
    pp_split_path = OUT / 'passport_split.csv'
    pp_split_path_persist = Path('/tmp/my-project/upload/scripts_persistent/passport_split.csv')
    if pp_split_path.exists():
        pp_split = pd.read_csv(str(pp_split_path))
    elif pp_split_path_persist.exists():
        pp_split = pd.read_csv(str(pp_split_path_persist))
    else:
        log('WARNING: passport_split.csv not found, running without passport in prior')
        pp_split = pd.DataFrame(columns=['lat','lon','hp','x','y','dist_to_ice_m','role'])
    pp_train = pp_split[pp_split.role == 'train'].copy()
    log(f'  Passport: {len(pp_train)} train (in prior), {len(pp_split)-len(pp_train)} holdout (validation)')
    
    out_path, transform, shape = step4_pipeline(ice, buildings_3413, water_3413, pp_train)

    log('\nStep 5: Validation...')
    step5_validate(out_path, transform, shape, ice)
    log('\n✓ DONE')


if __name__ == '__main__':
    main()
