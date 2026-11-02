#!/usr/bin/env python3
"""6.61: Datum framework — implied BS-77 offset vs EGM2008 per independent mark set.

D_implied(mark) = (h_fusion + 16.04) - N(lon,lat) - h_mark = D_true + eps(model surface error)
The product constant 16.04 cancels; what remains is datum + surface bias at marks.
Sets chosen so that measured/open-area sets (eps ~ 0) pin D, biased sets show the bias.
"""
import csv, sys, numpy as np, rasterio
sys.path.insert(0, '/tmp/my-project/gh-clone/scripts/handoff_2026-11')
from geoid_egm2008 import Geoid

GEO_TIF = '/tmp/my-project/data/us_nga_egm08_25.tif'
FUSION  = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'   # Int16 cm, BS-77 via 16.04
CONF    = '/tmp/my-project/gh-clone/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
PASSPORT= '/tmp/my-project/gh-clone/scripts/data/spb_passport_validation_v11.csv'
POOL2   = '/tmp/my-project/gh-clone/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv'
ALLIND  = '/tmp/my-project/gh-clone/scripts/data/ALL_independent_points.csv'
OUT     = '/tmp/my-project/gh-clone/results/datum_6.61'
DELTA_CURRENT = 16.04

g = Geoid(GEO_TIF)
F = rasterio.open(FUSION); C = rasterio.open(CONF)

def samp_fusion(lo, la):
    v = np.array([x[0] for x in F.sample(list(zip(lo, la)))], float)
    cv = np.array([x[0] for x in C.sample(list(zip(lo, la)))], float)
    v[(v == -32768) | (cv == 0)] = np.nan
    return v * 0.01  # cm -> m

def mad(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    return float(np.median(np.abs(x - np.median(x)))) if x.size else float('nan')

def implied(lo, la, h):
    lo, la, h = map(np.array, (lo, la, h), (float, float, float))
    f = samp_fusion(lo, la)
    ok = np.isfinite(f) & np.isfinite(h)
    d = (f[ok] + DELTA_CURRENT) - g.n(lo[ok], la[ok]) - h[ok]
    return d, ok

sets = {}

# Passport (wall marks, BS-77) by detail class
P = list(csv.DictReader(open(PASSPORT)))
for det, lbl in (('21', 'passport detail=21 (measured DSM)'),
                 ('23', 'passport detail=23 (prior fill)'),
                 ('24', 'passport detail=24 (train zone)'),
                 ('*', 'passport all')):
    rows = [r for r in P if det == '*' or r['detail'] == det]
    if rows:
        sets[lbl] = ([float(r['lon']) for r in rows], [float(r['lat']) for r in rows],
                     [float(r['hp']) for r in rows], len(rows))

# Atayans pool v2 — confirmed by double read (ground truth + near)
T = list(csv.DictReader(open(POOL2)))
for use_f, lbl in ((lambda u: u in ('ground_truth', 'ground_truth_0.1'),
                    'atayans confirmed 188 (center)'),
                   (lambda u: u == 'ground_truth', 'atayans confirmed 160 strict')):
    rows = [r for r in T if use_f(r['use'])]
    sets[lbl] = ([float(r['lon']) for r in rows], [float(r['lat']) for r in rows],
                 [float(r['h_best']) for r in rows], len(rows))
# whole agent catalog for the audit cross-check
rows_all = [r for r in T if r['h_agent']]
sets['atayans all agent reads (audit x-check)'] = (
    [float(r['lon']) for r in rows_all], [float(r['lat']) for r in rows_all],
    [float(r['h_agent']) for r in rows_all], len(rows_all))

# Kupchino archi 514913 (georeferenced, independent)
A = [r for r in csv.DictReader(open(ALLIND)) if r['source'] == 'archi.ru 514913' and r['lon']]
sets['kupchino archi 514913 (open, south)'] = (
    [float(r['lon']) for r in A], [float(r['lat']) for r in A],
    [float(r['z_survey']) for r in A], len(A))

# ---- run ----
import os
os.makedirs(OUT, exist_ok=True)
detail_rows = []
print(f"{'set':44s} {'n_in':>5s} {'n_ok':>5s} {'median_dH':>9s} {'D_implied':>9s} {'MAD':>5s}")
for name, (lo, la, h, n) in sets.items():
    d, ok = implied(np.array(lo), np.array(la), np.array(h))
    # dH = fusion - mark (classic residual, no geoid)
    f = samp_fusion(np.array(lo), np.array(la))
    ok2 = np.isfinite(f) & np.isfinite(np.array(h, float))
    dh = f[ok2] - np.array(h, float)[ok2]
    print(f"{name:44s} {n:5d} {d.size:5d} {np.median(dh):+9.2f} {np.median(d):+9.3f} {mad(d):5.2f}")
    for i in np.where(ok)[0]:
        detail_rows.append({'set': name, 'lon': lo[i], 'lat': la[i], 'h_mark': h[i],
                            'fusion_m': f[ok2][i] if i < ok2.sum() else np.nan,
                            'D_implied_m': d[i if i < len(d) else -1]})
with open(f'{OUT}/implied_d_marks_6.61.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=['set', 'lon', 'lat', 'h_mark', 'fusion_m', 'D_implied_m'])
    w.writeheader(); w.writerows(detail_rows)

# N over the 7 reference points, for the doc
pts = {'Kronstadt': (29.776, 59.989), 'V.O. Bolshoy': (30.27, 59.941), 'Centre': (30.32, 59.935),
       'Lakhta': (30.20, 60.00), 'Pulkovo': (30.33, 59.77), 'Sestroretsk': (29.96, 60.09),
       'Kolpino': (30.60, 59.75), 'Kupchino': (30.375, 59.85)}
print('\nN EGM2008:')
for k, (lo, la) in pts.items():
    print(f'  {k:14s} {float(g.n(np.array([lo]), np.array([la]))[0]):7.3f}')
print('\nOK ->', f'{OUT}/implied_d_marks_6.61.csv')
