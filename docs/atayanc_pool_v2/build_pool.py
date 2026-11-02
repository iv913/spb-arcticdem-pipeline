"""Merge my blind reads of the original sheet with the agent catalogue 6.50 -> extended mark pool.
usage: build_pool.py <reads.csv> <repo> <fusion.tif> <out.csv>"""
import sys, csv, json, numpy as np, rasterio
from scipy.spatial import cKDTree
RD, REPO, FUS, OUT = sys.argv[1:5]
R = list(csv.DictReader(open(RD)))
C = list(csv.DictReader(open(f'{REPO}/results/atayanc_6.50/marks_catalog_6.50.csv')))
a, b, c, d, e, f = json.load(open(f'{REPO}/results/atayanc_6.30/georef_6.30.json'))['affine_deg']
# NW-fragment affine fitted on the agent's own NW marks (px_bullet,py -> lon,lat)
nw = [k for k in C if k['nw_fragment'] == '1']
A = np.c_[[float(k['px_bullet']) for k in nw], [float(k['py']) for k in nw], np.ones(len(nw))]
cl = np.linalg.lstsq(A, np.array([float(k['lon']) for k in nw]), rcond=None)[0]
ca = np.linalg.lstsq(A, np.array([float(k['lat']) for k in nw]), rcond=None)[0]
MX, MY = 111320 * np.cos(np.radians(59.945)), 111133
res = np.hypot((A @ cl - [float(k['lon']) for k in nw]) * MX, (A @ ca - [float(k['lat']) for k in nw]) * MY)
nwx = (min(float(k['px_bullet']) for k in nw) - 30, max(float(k['px_bullet']) for k in nw) + 30)
nwy = (min(float(k['py']) for k in nw) - 30, max(float(k['py']) for k in nw) + 30)
print(f'NW affine from {len(nw)} agent marks: residual median {np.median(res):.2f} m; NW box px {nwx} py {nwy}')
def to_ll(x, y):
    if nwx[0] <= x <= nwx[1] and nwy[0] <= y <= nwy[1]: return float(cl @ [x, y, 1]), float(ca @ [x, y, 1]), 1
    return a * x + b * y + c, d * x + e * y + f, 0
# dedupe my reads from overlapping crops: same value within 8 px -> one
pts = []
for r in R:
    x, y, v = float(r['x']), float(r['y']), float(r['val'])
    if any(abs(p['x'] - x) < 8 and abs(p['y'] - y) < 8 and abs(p['v'] - v) < 0.015 for p in pts): continue
    pts.append(dict(x=x, y=y, v=v, conf=r['conf']))
print(f'my reads: {len(R)} raw -> {len(pts)} after overlap dedupe')
cx = np.array([float(k['px_bullet']) for k in C]); cy = np.array([float(k['py']) for k in C]); lx = np.array([float(k['px']) for k in C])
tb, tl = cKDTree(np.c_[cx, cy]), cKDTree(np.c_[lx, cy])
fs = rasterio.open(FUS)
def surf(lo, la):
    v = next(fs.sample([(lo, la)]))[0]; return np.nan if v == -32768 else v * 0.01
used = set(); rows = []
for p in pts:
    d1, i1 = tb.query([p['x'], p['y']]); d2, i2 = tl.query([p['x'], p['y']])
    dd, ii = (d1, i1) if d1 <= d2 else (d2, i2)
    if dd <= 15 and ii not in used:
        k = C[ii]; used.add(ii); ha = float(k['h_bs77_m'])
        st = 'confirmed' if abs(p['v'] - ha) <= 0.02 else ('near' if abs(p['v'] - ha) <= 0.10 else 'conflict')
        lo, la = float(k['lon']), float(k['lat'])
        rows.append(dict(id=k['id'], px=float(k['px_bullet']), py=float(k['py']), lon=lo, lat=la, nw=int(k['nw_fragment']),
                         h_claude=p['v'], conf_claude=p['conf'], h_agent=ha, conf_agent=k['conf'], status=st))
    else:
        lo, la, isnw = to_ll(p['x'], p['y'])
        rows.append(dict(id='', px=p['x'], py=p['y'], lon=lo, lat=la, nw=isnw, h_claude=p['v'], conf_claude=p['conf'],
                         h_agent='', conf_agent='', status='new_single_read'))
for i, k in enumerate(C):
    if i in used: continue
    rows.append(dict(id=k['id'], px=float(k['px_bullet']), py=float(k['py']), lon=float(k['lon']), lat=float(k['lat']), nw=int(k['nw_fragment']),
                     h_claude='', conf_claude='', h_agent=float(k['h_bs77_m']), conf_agent=k['conf'], status='agent_only'))
n = 0
for r in rows:
    if not r['id']: n += 1; r['id'] = f'c{n:03d}'
    s = surf(r['lon'], r['lat']); r['surface_6_00'] = round(s, 2) if np.isfinite(s) else ''
    h = r['h_claude'] if r['h_claude'] != '' else r['h_agent']
    r['h_best'] = h if r['status'] in ('confirmed', 'near', 'new_single_read', 'agent_only') else ''
    r['dH'] = round(s - h, 2) if (np.isfinite(s) and r['h_best'] != '') else ''
    r['use'] = {'confirmed': 'ground_truth', 'near': 'ground_truth_0.1', 'conflict': 'third_read',
                'new_single_read': 'candidate_second_read', 'agent_only': 'candidate_second_read'}[r['status']]
with open(OUT, 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
import collections
cnt = collections.Counter(r['status'] for r in rows); print('pool:', len(rows), dict(cnt))
def st(sel, tag):
    x = np.array([r['dH'] for r in rows if r['status'] in sel and r['dH'] != ''], float)
    if x.size: print(f'  {tag:34s} n={x.size:3d}  dH (surface − mark) median {np.median(x):+.2f}  MAD {np.median(np.abs(x-np.median(x))):.2f}  |dH|>1 m {100*(np.abs(x)>1).mean():3.0f}%')
st(('confirmed',), 'confirmed (two reads agree)'); st(('confirmed', 'near'), 'confirmed + within 0.10')
st(('new_single_read',), 'new, single read'); st(('agent_only',), 'agent only'); 
cf = [r for r in rows if r['status'] == 'conflict']
dd = np.array([r['h_claude'] - r['h_agent'] for r in cf])
print(f'  conflicts: {len(cf)}; first-digit (|diff| 0.8-1.2): {int(((abs(dd)>=0.8)&(abs(dd)<=1.2)).sum())}, '
      f'2nd digit (0.1-0.8): {int(((abs(dd)>0.1)&(abs(dd)<0.8)).sum())}, other: {int((abs(dd)>1.2).sum())}')
# in conflicts, which reading agrees better with neighbours? (median of confirmed marks within 60 m)
conf_xy = np.array([[r['lon'] * MX, r['lat'] * MY] for r in rows if r['status'] == 'confirmed']); conf_h = np.array([r['h_claude'] for r in rows if r['status'] == 'confirmed'])
tc = cKDTree(conf_xy); win = collections.Counter()
for r in cf:
    idx = tc.query_ball_point([r['lon'] * MX, r['lat'] * MY], 60)
    if len(idx) < 2: win['no_neighbours'] += 1; continue
    m = np.median(conf_h[idx]); win['claude' if abs(r['h_claude'] - m) < abs(r['h_agent'] - m) else 'agent'] += 1
print('  conflicts — reading closer to confirmed neighbours (≤60 m):', dict(win))
