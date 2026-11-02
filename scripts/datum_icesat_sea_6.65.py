#!/usr/bin/env python3
"""6.65 B3-check2: Kronstadt sea surface from ATL06 anchors vs EGM2008.

h_ell = h_baltic + 16.04 recovers the raw ATL06 ellipsoidal height exactly
(16.04 was applied linearly). Filter: high confidence, ice-free months (May-Nov,
the gulf freezes -> ATL06 over ice sits high), open water (inside sea polygons
of osm_water_6.20), per-point N from the EGM2008 grid.
Estimate: median(h_ell - N) = D + L, where L = modern mean sea level minus the
BS-77 zero (Kronstadt datum, 1977). Literature D = 0.00 +/- 0.06 (B3).
"""
import gzip, csv, json, numpy as np, rasterio, warnings
warnings.filterwarnings('ignore')
from shapely.geometry import shape
from shapely.prepared import prep
from shapely.vectorized import contains as vcontains
import sys
sys.path.insert(0, '/tmp/my-project/gh-clone/scripts/handoff_2026-11')
from geoid_egm2008 import Geoid

UNI = '/tmp/my-project/gh-clone/scripts/dem_cache/densify_canon_5.93/unified.csv.gz'
SEA = '/tmp/my-project/results/fusion_6.20/osm_water_6.20.geojson'
GEO = '/tmp/my-project/data/us_nga_egm08_25.tif'
DELTA = 16.04

# --- sea polygons (big; prepared) ---
feats = json.load(open(SEA))['features']
polys = []
for f in feats:
    g = shape(f['geometry'])
    if not g.is_valid: g = g.buffer(0)
    polys.append(g)
from shapely.ops import unary_union
sea_u = unary_union(polys)
sea_prep = prep(sea_u)
print(f'sea features: {len(feats)}')

g = Geoid(GEO)

rows = []
with gzip.open(UNI, 'rt') as fh:
    for r in csv.DictReader(fh):
        lo = float(r['lon'])
        if not (29.20 <= lo <= 30.15): continue
        la = float(r['lat'])
        if not (59.70 <= la <= 60.15): continue
        if r['cnf_src'] != 'high': continue
        m = int(r['date'][4:6])
        if m < 5 or m > 11: continue          # ice-free months only
        hb = float(r['h_baltic'])
        if not (-0.8 <= hb <= 0.8): continue   # water-level window
        rows.append((lo, la, hb, int(r['date'][:4]), m))
print(f'gulf high-cnf ice-free anchors in level window: {len(rows)}')

lo = np.array([r[0] for r in rows]); la = np.array([r[1] for r in rows])
hb = np.array([r[2] for r in rows])
insea = vcontains(sea_u, lo, la)
print(f'inside sea polygons: {insea.sum()}')
lo, la, hb = lo[insea], la[insea], hb[insea]
yr = np.array([r[3] for r in rows])[insea]

N = g.n(lo, la)
d = (hb + DELTA) - N           # h_ell - N = D + L
med = float(np.median(d)); mad = float(1.4826 * np.median(np.abs(d - med)))
se = 1.2533 * mad / np.sqrt(d.size)
print(f'\nh_ell - N: median {med:+.3f}  MAD {mad:.3f}  SE {se:.3f}  n={d.size}')

# per-year stability (multi-year mean level shifts, ice effect remnant)
for y in sorted(set(yr.tolist())):
    m_ = d[yr == y]
    print(f'  {y}: n={m_.size:6d}  median {np.median(m_):+.3f}')

# spatial gradient check: east (toward Kronstadt 29.7-30.0) vs west
for lbl, m_ in (('west of 29.7', lo < 29.7), ('29.7-29.9', (lo >= 29.7) & (lo < 29.9)),
                ('east of 29.9', lo >= 29.9)):
    if m_.sum() > 100:
        print(f'  {lbl}: n={int(m_.sum()):6d}  median {np.median(d[m_]):+.3f}')

out = {
    'method': 'tide_gauge_icesat',
    'statistic': 'median(h_ell_ATL06 - N_EGM2008) over open gulf water, ice-free months',
    'median_m': round(med, 3), 'MAD_m': round(mad, 3), 'SE_m': round(se, 3), 'n': int(d.size),
    'interpretation': 'median = D + L, L = modern mean sea level minus BS-77 zero (uplift/eustasy since 1977, order -0.1..+0.1 m); ATL06 over water carries wave/segment bias',
    'literature_D_m': 0.00, 'literature_source': 'Ellmann et al. 2009, Newtons Bulletin 4, Baltic GNSS-levelling (BS-77)',
}
import os
os.makedirs('/tmp/my-project/gh-clone/results/datum_6.61', exist_ok=True)
json.dump(out, open('/tmp/my-project/gh-clone/results/datum_6.61/icesat_sea_check_6.65.json', 'w'),
          indent=1, ensure_ascii=False)
print('saved icesat_sea_check_6.65.json')
