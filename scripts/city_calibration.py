#!/usr/bin/env python3
"""
city_calibration.py (v3) — городская вертикальная привязка по опорным точкам
v3: corridor_ground вместо сырого пикселя; holdout ДО сдвига; versioned JSON.
"""
import json, re, sys
from pathlib import Path
from datetime import datetime
import numpy as np, pandas as pd, geopandas as gpd, rasterio, shapely
from shapely.geometry import LineString
import requests
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('owner_map_mod', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
sys.path.insert(0, str(Path(__file__).parent))
import city_local_ground_v3 as clg

HOME = Path('/home/z/my-project'); UPLOAD = HOME / 'upload'
CACHE = HOME / 'scripts' / 'dem_cache'; OUT = HOME / 'download'
DEM_PATH = CACHE / 'arctic' / 'spb_center_arcticdem.tif'; UTM = 'EPSG:32636'
CITY_BBOX = (29.70, 59.74, 30.55, 60.05); MATCH_DIST_MAX = 25.0
HOLDOUT_PCT = 25; RANDOM_STATE = 42; DEDUP_DIST_M = 300.0
VO_LON_MAX = 30.31; VO_LAT = (59.92, 59.965)
DRIVE = {'motorway','trunk','primary','secondary','tertiary','residential','unclassified','living_street','service'}
GEOID_FALLBACK = m01.GEOID_FALLBACK

def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

def load_control_points():
    pts = []; path = UPLOAD / 'Отметки.txt'
    if not path.exists(): log(f'ERROR: нет {path}'); sys.exit(1)
    for raw in open(path, encoding='utf-8'):
        line = raw.strip()
        if not line or ':' not in line: continue
        l, r = line.split(':', 1)
        mm = re.match(r'^(\d+[.,]\d+)\s*,\s*(\d+[.,]\d+)$', l.strip())
        if not mm: continue
        m2 = re.match(r'^(-?\d+[.,]?\d*)', r.strip())
        if not m2: continue
        pts.append({'lat': float(mm.group(1).replace(',','.')), 'lon': float(mm.group(2).replace(',','.')), 'h_pas': float(m2.group(1).replace(',','.'))})
    df = pd.DataFrame(pts); log(f'Опорных точек: {len(df)}'); return df

def fetch_city_ways():
    W, S, E, N = CITY_BBOX; tag = f'{W}_{S}_{E}_{N}'.replace('.','p')
    cache = CACHE / 'osm' / f'city_ways_{tag}.json'; cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists(): data = json.loads(cache.read_text())
    else:
        q = f'[out:json][timeout:300];way["highway"~"^({"|".join(sorted(DRIVE))})$"]({S},{W},{N},{E});out geom;'
        data = {'elements': []}
        for url in ('https://maps.mail.ru/osm/tools/overpass/api/interpreter','https://overpass-api.de/api/interpreter'):
            try:
                log(f'  Overpass: {url}'); r = requests.post(url, data={'data': q}, timeout=300)
                if r.status_code == 200 and r.text.strip(): data = r.json(); cache.write_text(json.dumps(data)); break
            except Exception as e: log(f'  ! {e}')
    ways = []
    for el in data.get('elements', []):
        if el.get('type') != 'way' or 'geometry' not in el: continue
        c = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(c) < 2: continue
        tags = el.get('tags', {})
        ways.append({'geometry': LineString(c), 'name': tags.get('name',''), 'highway': tags.get('highway',''), 'osm_id': el.get('id')})
    gdf = gpd.GeoDataFrame(ways, crs='EPSG:4326').to_crs(UTM); log(f'Проезжих way: {len(gdf)}'); return gdf

def match_points_to_ways(cp, ways_utm):
    pts = gpd.GeoDataFrame(cp, geometry=gpd.points_from_xy(cp['lon'], cp['lat']), crs='EPSG:4326').to_crs(UTM)
    tree = shapely.STRtree(ways_utm.geometry.values)
    idx = tree.nearest(pts.geometry.values)
    cp = cp.copy(); cp['way_i'] = idx
    cp['dist'] = shapely.distance(pts.geometry.values, ways_utm.geometry.values[idx])
    cp['street'] = ways_utm['name'].values[idx]; cp['highway'] = ways_utm['highway'].values[idx]
    cp['pt_x_utm'] = pts.geometry.x.values; cp['pt_y_utm'] = pts.geometry.y.values
    log(f'Матчинг: {int((cp["dist"] <= MATCH_DIST_MAX).sum())}/{len(cp)} в {MATCH_DIST_MAX}м'); return cp

def estimate_grounds(cp, ways_utm):
    with rasterio.open(str(DEM_PATH)) as src:
        dem_crs = src.crs; ways_dem = ways_utm.to_crs(dem_crs)
        anchor_dem = list(ways_dem[ways_dem['highway'].isin(clg.ANCHOR_CLASSES)].geometry.values)
        from rasterio.warp import transform as warp_transform
        xs, ys = warp_transform('EPSG:4326', dem_crs, cp['lon'].tolist(), cp['lat'].tolist())
        grounds, qflags, nblocks, lused, nanch = [], [], [], [], []
        for i, (x, y, wi, dist) in enumerate(zip(xs, ys, cp['way_i'], cp['dist'])):
            if dist > MATCH_DIST_MAX:
                grounds.append(np.nan); qflags.append('far_from_road'); nblocks.append(0); lused.append(0.0); nanch.append(0); continue
            way_dem = ways_dem.geometry.values[int(wi)]
            try: g, meta = clg.corridor_ground(src, (x, y), way_dem, other_ways_dem=anchor_dem)
            except Exception: g, meta = np.nan, {'qflag':'error','n_blocks':0,'L_used_m':0.0,'n_anchors':0}
            grounds.append(g); qflags.append(meta['qflag']); nblocks.append(meta['n_blocks']); lused.append(meta['L_used_m']); nanch.append(meta['n_anchors'])
            if (i+1) % 25 == 0: log(f'  ... {i+1}/{len(cp)}')
    cp = cp.copy(); cp['ground_ell'] = grounds; cp['qflag'] = qflags
    cp['n_blocks'] = nblocks; cp['L_used_m'] = lused; cp['n_anchors'] = nanch
    cp['dH_v3'] = cp['ground_ell'] - cp['h_pas']
    log(f'corridor_ground: qflags={cp["qflag"].value_counts().to_dict()}'); return cp

def regional_split(street_stats):
    def dedup(df):
        chosen = []
        for _, r in df.sort_values('count', ascending=False).iterrows():
            if all(np.hypot(r['cx']-c['cx'], r['cy']-c['cy']) > DEDUP_DIST_M for c in chosen): chosen.append(r)
        return pd.DataFrame(chosen)
    def stats(df, label):
        dd = dedup(df)
        if len(dd) < 3: return {'label':label,'n_streets_raw':int(len(df)),'n_streets_dedup':int(len(dd)),'shift':None,'mad':None}
        med = float(dd['median'].median()); mad = float(np.median(np.abs(dd['median']-med))*1.4826)
        return {'label':label,'n_streets_raw':int(len(df)),'n_streets_dedup':int(len(dd)),'shift':round(med,3),'mad':round(mad,3)}
    is_vo = (street_stats['lon'] < VO_LON_MAX) & street_stats['lat'].between(*VO_LAT)
    vo = stats(street_stats[is_vo], 'vo'); ml = stats(street_stats[~is_vo], 'mainland')
    delta = None if vo['shift'] is None or ml['shift'] is None else round(vo['shift']-ml['shift'], 3)
    sig = None
    if delta is not None and vo['mad'] is not None and ml['mad'] is not None:
        se = np.sqrt((1.253*vo['mad'])**2/max(1,vo['n_streets_dedup']) + (1.253*ml['mad'])**2/max(1,ml['n_streets_dedup']))
        sig = bool(abs(delta) > 2*max(se, 0.05))
    return {'vo':vo, 'mainland':ml, 'delta_m':delta, 'significant':sig, 'dedup_dist_m':DEDUP_DIST_M}

def main():
    log('='*60); log('city_calibration v3: corridor_ground + честный holdout'); log('='*60)
    cp = load_control_points(); ways = fetch_city_ways()
    cp = match_points_to_ways(cp, ways); cp = estimate_grounds(cp, ways)
    ok = cp[(cp['qflag']=='ok') & np.isfinite(cp['dH_v3'])].copy()
    if len(ok) < 8: log('ERROR: слишком мало ok-точек'); sys.exit(1)
    rng = np.random.RandomState(RANDOM_STATE); ok['holdout'] = False
    n_ho = max(1, int(len(ok)*HOLDOUT_PCT/100))
    ok.loc[rng.choice(ok.index, n_ho, replace=False), 'holdout'] = True
    cal, ho = ok[~ok['holdout']], ok[ok['holdout']]
    log(f'Calibration: {len(cal)}, holdout: {len(ho)} (выбран ДО сдвига)')
    shift, agg = clg.aggregate_city_shift(cal.assign(qflag='ok'), dh_col='dH_v3', name_col='street')
    rmse = float(np.sqrt(np.mean((ho['dH_v3']-shift)**2))) if len(ho) else None
    mae = float(np.mean(np.abs(ho['dH_v3']-shift))) if len(ho) else None
    log(f'shift={shift:.3f}м, MAD={agg["between_street_mad_m"]:.3f}м, улиц {agg["n_streets_inlier"]}/{agg["n_streets"]}')
    if rmse is not None: log(f'Holdout: RMSE={rmse:.3f}м, MAE={mae:.3f}м')
    st = cal.groupby('street').agg(median=('dH_v3','median'), count=('dH_v3','count'), cx=('pt_x_utm','mean'), cy=('pt_y_utm','mean'), lon=('lon','mean'), lat=('lat','mean')).reset_index()
    regional = regional_split(st); log(f'Регионы: {regional}')
    if regional.get('significant'): log('⚠ Δ(В.О.-материк) значима — для объектов В.О. брать regional.vo.shift!')
    cp.merge(ok[['holdout']], left_index=True, right_index=True, how='left').to_csv(OUT/'city_calibration_points.csv', index=False)
    qa = {'version':'v3','estimator':'corridor_ground (block-median lower envelope, adaptive corridor, anchor intersections)',
          'n_total':int(len(cp)),'n_matched':int((cp['dist']<=MATCH_DIST_MAX).sum()),'n_ok':int(len(ok)),
          'n_cal':int(len(cal)),'n_holdout':int(len(ho)),'shift_m':round(shift,4),'shift_source':'city_calibration_v3',
          'between_street_mad_m':round(agg['between_street_mad_m'],4),'n_streets':agg['n_streets'],'n_streets_inlier':agg['n_streets_inlier'],
          'rmse_holdout_m':round(rmse,4) if rmse is not None else None,'mae_holdout_m':round(mae,4) if mae is not None else None,
          'regional':regional}
    (OUT/'city_calibration.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    log(f'✓ {OUT/"city_calibration.json"} (version=v3)')

if __name__ == '__main__': main()
