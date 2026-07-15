#!/usr/bin/env python3
"""multi_dem_profile.py — профиль дороги по всем DEM из manifest."""
import json, sys, types
from pathlib import Path
from datetime import datetime
import numpy as np, pandas as pd, geopandas as gpd, rasterio, shapely
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
import city_local_ground_v3 as clg_v3

OUT = m01.OUT; UTM = m01.UTM
def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

_MANIFEST = Path('/home/z/my-project/scripts/dem_cache/arctic/strips/manifest.json')
DEM_SOURCES = {}
if _MANIFEST.exists():
    for entry in json.loads(_MANIFEST.read_text()):
        date = entry['date']; label = f'ArcticDEM mosaic v4.1 (~2019)' if entry.get('type')=='mosaic' else f'ArcticDEM strip {date}'
        cp = Path('/home/z/my-project') / entry['clip_path']
        try:
            with rasterio.open(str(cp)) as s: res = abs(s.transform.a)
            if s.crs and s.crs.is_geographic: res = res * 111000
        except: res = 2.0
        DEM_SOURCES[label] = {'path':cp,'resolution_m':res,'date':date,'type':entry.get('type','strip')}

def profile_along_axis(src, axis_utm, dem_crs, n_pickets=200, anchor_ways_utm=None):
    res_m = abs(src.transform.a)
    if src.crs.is_geographic:
        from rasterio.warp import transform_bounds
        b = transform_bounds(src.crs, 'EPSG:3857', *src.bounds); res_m = abs((b[2]-b[0])/src.width)
    log(f'  DEM resolution: {res_m:.1f}м')
    if res_m > 10: return _profile_coarse(src, axis_utm, dem_crs, n_pickets, res_m, anchor_ways_utm)
    L = axis_utm.length; pks = np.linspace(0, L, n_pickets)
    axis_dem = gpd.GeoSeries([axis_utm], crs=UTM).to_crs(dem_crs).iloc[0]
    anchor_dem = ([gpd.GeoSeries([w], crs=UTM).to_crs(dem_crs).iloc[0] for w in anchor_ways_utm] if anchor_ways_utm else None)
    rows = []
    for pk in pks:
        pt = axis_dem.interpolate(float(pk))
        try: ground, meta = clg_v3.corridor_ground(src, (pt.x, pt.y), axis_dem, other_ways_dem=anchor_dem)
        except: ground, meta = np.nan, {'qflag':'error','n_blocks':0,'n_anchors':0,'L_used_m':0}
        rows.append({'pk':float(pk),'ground_ell':ground,'qflag':meta.get('qflag','unknown')})
    return pd.DataFrame(rows)

def _profile_coarse(src, axis_utm, dem_crs, n_pickets, res_m, anchor_ways_utm):
    from rasterio.windows import from_bounds; from collections import defaultdict
    L = axis_utm.length; pks = np.linspace(0, L, n_pickets)
    axis_dem = gpd.GeoSeries([axis_utm], crs=UTM).to_crs(dem_crs).iloc[0]
    HALF_W=30.0; L_HALF=50.0; BLOCK_M=max(res_m*1.5,30.0)
    rows = []
    for pk in pks:
        a0=max(0.0,float(pk)-L_HALF); a1=min(L,float(pk)+L_HALF)
        try: seg = shapely.ops.substring(axis_dem, a0, a1)
        except: rows.append({'pk':float(pk),'ground_ell':np.nan,'qflag':'error'}); continue
        corridor = seg.buffer(HALF_W, cap_style=2)
        try:
            win = from_bounds(*corridor.bounds, src.transform).round_offsets(op='floor').round_lengths(op='ceil')
            arr = src.read(1, window=win, boundless=True, fill_value=np.nan).astype('float64')
        except: rows.append({'pk':float(pk),'ground_ell':np.nan,'qflag':'read_error'}); continue
        if src.nodata is not None: arr[arr==src.nodata] = np.nan
        t = src.window_transform(win); rr,cc = np.mgrid[0:arr.shape[0],0:arr.shape[1]]
        xs = t.c+t.a*(cc+0.5)+t.b*(rr+0.5); ys = t.f+t.d*(cc+0.5)+t.e*(rr+0.5)
        fin = np.isfinite(arr); pts = shapely.points(xs[fin], ys[fin])
        off = shapely.distance(pts, seg); inside = off <= HALF_W
        vals = arr[fin][inside]; along = np.asarray(shapely.line_locate_point(seg, pts[inside]))
        if len(vals) < 5: rows.append({'pk':float(pk),'ground_ell':np.nan,'qflag':'too_few_px'}); continue
        ba = np.floor(along/BLOCK_M).astype(int); bo = np.floor(np.asarray(off[inside])/BLOCK_M).astype(int)
        groups = defaultdict(list)
        for v,a,o in zip(vals,ba,bo): groups[(a,o)].append(v)
        med = sorted(np.median(g) for g in groups.values() if len(g)>=1)
        med = np.array(med)
        if len(med) < 3: rows.append({'pk':float(pk),'ground_ell':np.nan,'qflag':'too_few_blocks'}); continue
        gaps = np.diff(med); cut = 0
        for i,gp in enumerate(gaps):
            if gp > 1.5 and (i+1) <= 0.30*len(med): cut = i+1
        med = med[cut:]
        if len(med) < 2: rows.append({'pk':float(pk),'ground_ell':np.nan,'qflag':'too_few_after_cut'}); continue
        floor = med[0]; sel = med[med <= floor+0.6]
        rows.append({'pk':float(pk),'ground_ell':float(np.median(sel)),'qflag':'ok' if len(sel)>=2 else 'too_few_ground'})
    return pd.DataFrame(rows)

def _prune_bins(b_arr, pk_arr, h_arr):
    from scipy.ndimage import binary_closing
    n = len(h_arr); keep = np.ones(n, dtype=bool); reason = np.array(['']*n, dtype=object)
    edge_m = 7.5 * 5.0
    for _ in range(30):
        idx = np.where(keep)[0]
        if len(idx) < 4: break
        hv, pkv = h_arr[idx], pk_arr[idx]
        base = pd.Series(hv).rolling(15, center=True, min_periods=5).median().values
        up = hv - base
        near_edge = ((pkv - pk_arr.min()) < edge_m) | ((pk_arr.max() - pkv) < edge_m)
        bad_jump = np.where(near_edge, up > 3.0, up > 1.0)
        bad_jump = np.nan_to_num(bad_jump.astype(float)).astype(bool)
        bad_slope = np.zeros(len(idx), dtype=bool)
        s = np.diff(hv) / np.maximum(np.diff(pkv), 1e-6)
        for i in np.where(np.abs(s) > 0.05)[0]:
            j = i if hv[i] > hv[i+1] else i+1; bad_slope[j] = True
        bad = bad_jump | bad_slope
        if not bad.any(): break
        keep[idx[bad]] = False; reason[idx[bad_jump]] = 'shadow_jump'; reason[idx[bad_slope & ~bad_jump]] = 'slope'
    if (~keep).any():
        bmin = int(b_arr.min()); full = np.zeros(int(b_arr.max())-bmin+1, dtype=bool)
        full[(b_arr[~keep]-bmin).astype(int)] = True
        closed = binary_closing(full, structure=np.ones(5))
        newly = closed & ~full; pos = {int(bb): i for i,bb in enumerate(b_arr)}
        for bb in np.nonzero(newly)[0]+bmin:
            i = pos.get(int(bb))
            if i is not None and keep[i]: keep[i] = False; reason[i] = 'zone_fill'
    return keep, reason

def main():
    log('='*60); log('multi_dem_profile'); log('='*60)
    osm = m01.fetch_osm(m01.MVP_BBOX)
    cw, axis, ways_gdf, _ = m01.build_object_geometry(osm, m01.MVP_BBOX, m01.MVP_NAME)
    anchor_ways = [g for g,h in zip(ways_gdf.geometry, ways_gdf['highway']) if h in clg_v3.ANCHOR_CLASSES]
    PROF_DIR = OUT / 'profiles'; PROF_DIR.mkdir(exist_ok=True)
    for old in PROF_DIR.glob('profile_*.csv'): old.unlink()
    profiles = {}
    for src_name, cfg in DEM_SOURCES.items():
        log(f'\n=== {src_name} ===')
        if not cfg['path'].exists(): continue
        with rasterio.open(str(cfg['path'])) as src:
            df = profile_along_axis(src, axis, src.crs, n_pickets=200, anchor_ways_utm=anchor_ways)
        ok = df[df.qflag=='ok']
        if len(ok) == 0: continue
        b = np.floor(ok.pk / 5.0).astype(int); rows = []
        for bi, g in ok.groupby(b):
            if len(g) < 1: continue
            anchor = np.percentile(g.ground_ell, 20); kept = g.ground_ell[g.ground_ell <= anchor+0.4]
            if len(kept) < 1: continue
            rows.append({'b':bi,'pk':g.pk.median(),'h':float(np.median(kept))})
        prof = pd.DataFrame(rows)
        if len(prof) < 4: continue
        keep, reason = _prune_bins(prof.b.values.astype(float), prof.pk.values, prof.h.values)
        prof['kept'] = keep
        # slug
        import re
        m_slug = re.search(r'strip_(\d{8})_(\w+)_(\w+)_vo_clip', cfg['path'].name)
        slug = f'strip_{m_slug.group(1)}_{m_slug.group(2)}_{m_slug.group(3)}' if m_slug else ('mosaic' if 'mosaic' in src_name.lower() else src_name.replace(' ','_').lower())
        prof[['b','pk','h','kept']].to_csv(PROF_DIR/f'profile_{slug}.csv', index=False)
        log(f'  ✓ profile_{slug}.csv ({len(prof)} бинов, kept={keep.sum()})')
    log(f'\n✓ Профили выгружены в {PROF_DIR}')

if __name__ == '__main__': main()
