#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5.96e — Диагностика «вылета» эталонного Большого проспекта В.О. и о. Декабристов.
Сэмплирование мозаики 5.90 (DEM + trust-маска + quality n_cov), покрытие зонами
тиража и футпринтами стрипов ArcticDEM, инвентаризация точек источников в окне.

Выход: results/sources_diag_5.96e.json + консольный свод.
"""
import json, gzip, csv, glob, math, sys
import numpy as np
import rasterio
from rasterio.windows import from_bounds

GH = '/tmp/my-project/gh-clone'
MOSAIC = f'{GH}/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
TRUST  = f'{GH}/download/mosaic_5.90/trust_mask_2m_5.90.tif'
QUAL   = f'{GH}/download/mosaic_5.90/spb_quality_5.90.tif'
CARR   = f'{GH}/download/carriageway.geojson'
OUT    = f'{GH}/results/sources_diag_5.96e.json'

# --- области анализа -------------------------------------------------------
# коридор Большого проспекта (эталон владельца)
carr = json.load(open(CARR))
g = carr['features'][0]['geometry']
parts = [g['coordinates'][0]] if g['type'] == 'Polygon' else [p[0] for p in g['coordinates']]
xs = [p[0] for pp in parts for p in pp]; ys = [p[1] for pp in parts for p in pp]
CARR_BBOX = (min(xs), min(ys), max(xs), max(ys))  # W,S,E,N

BOXES = {
    'bolshoy_corridor': dict(name='Большой проспект В.О. (эталон, carriageway)',
                             bbox=CARR_BBOX),
    'decabristov':      dict(name='Остров Декабристов (ядро)',
                             bbox=(30.205, 59.900, 30.280, 59.9415)),
    'gavan':            dict(name='Гавань В.О.',
                             bbox=(30.200, 59.931, 30.260, 59.956)),
    'vo_north':         dict(name='Север В.О. (Наличная — Морской фасад)',
                             bbox=(30.208, 59.955, 30.300, 59.966)),
    'vo_all':           dict(name='В.О. целиком (30.20–30.31, 59.90–59.97)',
                             bbox=(30.200, 59.900, 30.310, 59.970)),
}

def sample_raster(path, bbox, step_px=8, bands=None):
    """Сэмплирование окна регулярной сеткой; возвращает dict массивов."""
    with rasterio.open(path) as src:
        W, S, E, N = bbox
        win = from_bounds(W, S, E, N, transform=src.transform).round_offsets().round_lengths()
        idx = [b if isinstance(b, int) else src.indexes[0] for b in (bands or [1])]
        arrs = src.read(idx, window=win)
        # шаг по пикселям
        a0 = arrs[0]
        h, w = a0.shape
        sy = max(1, h // 400); sx = max(1, w // 400)   # <=400x400 сэмплов
        out = []
        for a in arrs:
            out.append(a[::sy, ::sx].astype('float64'))
        return out, (sy, sx)

def stats_for_box(tag, meta):
    W, S, E, N = meta['bbox']
    res = {'tag': tag, 'name': meta['name'],
           'bbox': [round(v, 5) for v in meta['bbox']],
           'area_km2': round((E - W) * 111.32 * math.cos(math.radians((S + N) / 2)) *
                             (N - S) * 111.13, 2)}
    dem, step = sample_raster(MOSAIC, (W, S, E, N))
    dem = dem[0]
    nod = np.isnan(dem) | (dem < -9000) | (dem > 90000)
    valid = ~nod
    res['nodata_pct'] = round(100 * nod.mean(), 1)
    if valid.any():
        res['dem_med_m'] = round(float(np.median(dem[valid])) / 100.0, 2)  # Int16 см -> м
        res['dem_p05_m'] = round(float(np.percentile(dem[valid], 5)) / 100.0, 2)
        res['dem_p95_m'] = round(float(np.percentile(dem[valid], 95)) / 100.0, 2)
    # trust-маска (классы 0 excl / 1 rest / 2 ok / 3 gold)
    tr, _ = sample_raster(TRUST, (W, S, E, N))
    tr = tr[0]
    res['trust_hist'] = {int(k): round(100 * float((tr == k).mean()), 1)
                         for k in (0, 1, 2, 3) if (tr == k).any()}
    # quality: band1=n_cov
    try:
        q, _ = sample_raster(QUAL, (W, S, E, N), bands=[1])
        q = q[0]
        res['ncov_ge1_pct'] = round(100 * float((q >= 1).mean()), 1)
        res['ncov_ge2_pct'] = round(100 * float((q >= 2).mean()), 1)
        res['ncov_med'] = float(np.median(q[(q > 0) & (q < 60000)])) if ((q > 0) & (q < 60000)).any() else 0.0
    except Exception as e:
        res['quality_err'] = str(e)[:120]
    return res

# --- зоны тиража, накрывающие окно ----------------------------------------
def zone_footprints():
    out = []
    for f in sorted(glob.glob(f'{GH}/download/products_5.83/*.tif')):
        tag = f.split('/')[-1].replace('hybrid_2m_5.83_', '').replace('.tif', '')
        try:
            with rasterio.open(f) as src:
                b = src.bounds
                out.append((tag, b.left, b.bottom, b.right, b.top))
        except Exception:
            pass
    return out

def zones_over(bbox, zones):
    W, S, E, N = bbox
    hits = []
    for tag, w, s, e, n in zones:
        ox = max(0, min(E, e) - max(W, w)); oy = max(0, min(N, n) - max(S, s))
        if ox > 0 and oy > 0:
            hits.append((tag, round(ox * oy / max(1e-9, (E - W) * (N - S)) * 100, 1)))
    hits.sort(key=lambda t: -t[1])
    return hits

# --- футпринты стрипов ------------------------------------------------------
STRIPS = json.load(open(f'{GH}/results/density_footprints_5.84.json'))  # [w,s,e,n]

def strips_over(bbox):
    W, S, E, N = bbox
    hits = 0; dts = set()
    for w, s, e, n in STRIPS:
        ox = max(0, min(E, e) - max(W, w)); oy = max(0, min(N, n) - max(S, s))
        if ox > 0 and oy > 0:
            hits += 1
    return hits

# --- точки источников в окне ------------------------------------------------
def points_over(path_gz, bbox, latc='lat', lonc='lon'):
    W, S, E, N = bbox
    n = 0; dates = set()
    with gzip.open(path_gz, 'rt') as fh:
        rd = csv.DictReader(fh, delimiter=',')
        cols = rd.fieldnames
        for row in rd:
            try:
                la = float(row[latc]); lo = float(row[lonc])
            except Exception:
                continue
            if W <= lo <= E and S <= la <= N:
                n += 1
                if 'date' in row and row['date']: dates.add(row['date'][:6])
    return n, sorted(dates)[:6], cols

def marks_over(bbox):
    W, S, E, N = bbox
    n = 0; srcs = {}
    with open(f'{GH}/results/marks_val_5.95.csv') as fh:
        for row in csv.DictReader(fh):
            try:
                la = float(row['lat']); lo = float(row['lon'])
            except Exception:
                continue
            if W <= lo <= E and S <= la <= N:
                n += 1; srcs[row['src']] = srcs.get(row['src'], 0) + 1
    return n, srcs

# === РАЗГОН ================================================================
report = {'date': '2026-10-14', 'carr_bbox': [round(v, 5) for v in CARR_BBOX]}

zones = zone_footprints()
report['n_zone_products'] = len(zones)

print('=== СЭМПЛ МОЗАИКИ 5.90 ===')
report['boxes'] = []
for tag, meta in BOXES.items():
    r = stats_for_box(tag, meta)
    zs = zones_over(meta['bbox'], zones)
    r['zones_cover'] = zs[:6]
    r['n_strips_over'] = strips_over(meta['bbox'])
    n_canon, dts, cols = points_over(f'{GH}/scripts/dem_cache/densify_canon_5.93/unified.csv.gz',
                                     meta['bbox'])
    r['n_atl06_canon'] = n_canon
    r['atl06_dates'] = dts
    n_south, _d2, _ = points_over(f'{GH}/results/south_dump_val_5.90.csv.gz', meta['bbox'])
    r['n_atl06_south'] = n_south
    n_marks, srcs = marks_over(meta['bbox'])
    r['n_marks_nspd'] = n_marks; r['mark_srcs'] = srcs
    report['boxes'].append(r)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:1400])

# jsonl-вердикты зон, накрывших коридор
verdicts = {}
for line in open(f'{GH}/results/batch_progress_5.83.jsonl'):
    try: j = json.loads(line)
    except Exception: continue
    for tag, ov in report['boxes'][0].get('zones_cover', []):
        if j.get('tag', '').startswith(tag.replace('_5.83', '')):
            verdicts.setdefault(tag, j)
report['zone_verdicts_corridor'] = {k: {kk: vv.get(kk) for kk in ('bbox', 'product', 'rule', 'prior_holdout')}
                                    for k, vv in verdicts.items()}

json.dump(report, open(OUT, 'w'), ensure_ascii=False, indent=1)
print('\nSaved:', OUT)
