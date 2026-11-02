#!/usr/bin/env python3
"""Per-tile warp (from step 1) + re-placement and checks of the plan marks; NW-fragment marks keep the agent NW affine.
usage: georef_marks.py <sheet.jpg> <repo> <georef_plan.npz> <fusion.tif> <out_csv>"""
import sys, json, csv, numpy as np, cv2, rasterio
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import shape, Point, box
from shapely.affinity import affine_transform
from shapely.strtree import STRtree
IMG, REPO, NPZ, FUS, OUTCSV = sys.argv[1:6]
z = np.load(NPZ); Wg = z['Wg'].astype(np.float32); S = float(z['S']); W_I, H_I = int(z['W_I']), int(z['H_I'])
lon0, lat1, GS, MX, MY = (float(z[k]) for k in ('lon0', 'lat1', 'GS', 'MX', 'MY')); GW, GH = 1500, 1300
tiles = {(int(r[0]), int(r[1])): r[2:].reshape(2, 3).astype(np.float32) for r in z['tiles']}
im = np.asarray(Image.open(IMG).convert('RGB')).astype(int); r, g, b = im[..., 0], im[..., 1], im[..., 2]
plan = ((r > 170) & (r < 235) & (g > 120) & (g < 185) & (b > 100) & (b < 170) & (r - g > 25) & (r - b > 40)).astype(np.uint8)
plan = cv2.morphologyEx(plan, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
F = json.load(open(f'{REPO}/results/atayanc_6.30/plan_buildings_6.30.geojson'))['features']
bl = [shape(x['geometry']) for x in F if x['properties'].get('src') in ('osm_only', 'osm')]
to_grid = [MX / GS, 0, 0, -MY / GS, -lon0 * MX / GS, lat1 * MY / GS]
osm = rasterize([(affine_transform(x, to_grid), 1) for x in bl], out_shape=(GH, GW), transform=Affine.identity(), dtype='uint8')
dt = cv2.distanceTransform((cv2.Canny(osm * 255, 50, 150) == 0).astype(np.uint8), cv2.DIST_L2, 5) * GS
edges = np.argwhere(cv2.Canny(plan * 255, 50, 150) > 0)[:, ::-1].astype(np.float64)
def inv(W): return np.linalg.inv(np.vstack([W, [0, 0, 1]]))[:2]
def chamfer(W, sel):
    Wi = inv(W); gxy = (Wi[:, :2] @ edges[sel].T).T + Wi[:, 2]
    ok = (gxy[:, 0] >= 0) & (gxy[:, 0] < GW - 1) & (gxy[:, 1] >= 0) & (gxy[:, 1] < GH - 1)
    return np.median(dt[gxy[ok, 1].astype(int), gxy[ok, 0].astype(int)])
def ecc(W, gx0, gy0, gx1, gy1, src):
    tmpl = cv2.GaussianBlur(osm[gy0:gy1, gx0:gx1].astype(np.float32), (0, 0), 2.0)
    inp = cv2.GaussianBlur(src.astype(np.float32), (0, 0), 2.5)
    Wc = W.copy(); Wc[:, 2] = W[:, :2] @ np.array([gx0, gy0], np.float32) + W[:, 2]
    try: cc, Wc = cv2.findTransformECC(tmpl, inp, Wc, cv2.MOTION_AFFINE, (3, 300, 1e-6), None, 5)
    except cv2.error: return None, -1
    Wn = Wc.copy(); Wn[:, 2] = Wc[:, 2] - Wc[:, :2] @ np.array([gx0, gy0], np.float32); return Wn, cc
# ---------- choose a warp per sheet tile ----------
TX, TY = 4, 3
def tile_of(u, v): return min(int(u / (W_I / TX)), TX - 1), min(int(v / (H_I / TY)), TY - 1)
use = {}
for (i, j), Wt in tiles.items():
    sel = (edges[:, 0] >= i * W_I / TX) & (edges[:, 0] < (i + 1) * W_I / TX) & (edges[:, 1] >= j * H_I / TY) & (edges[:, 1] < (j + 1) * H_I / TY)
    use[(i, j)] = Wt if chamfer(Wt, sel) < chamfer(Wg, sel) - 0.5 else Wg
def to_lonlat(W, u, v):
    Wi = inv(W); gx, gy = Wi[:, :2] @ np.array([u, v]) + Wi[:, 2]; return lon0 + gx * GS / MX, lat1 - gy * GS / MY
# ---------- marks ----------
R = list(csv.DictReader(open(f'{REPO}/results/atayanc_6.50/marks_catalog_6.50.csv')))
water = [shape(x['geometry']).buffer(0) for x in json.load(open(f'{REPO}/results/fusion_6.21/inland_water_6.21.geojson'))['features']]
bb = box(30.25, 59.92, 30.36, 59.97); water = [w for w in water if w.intersects(bb)]
tw, tb = STRtree(water), STRtree(bl)
def inside(tree, geoms, lon, lat):
    p = Point(lon, lat); return any(geoms[k].contains(p) for k in tree.query(p))
fs = rasterio.open(FUS)
def samp(lo, la):
    v = next(fs.sample([(lo, la)]))[0]; return np.nan if v == -32768 else v * 0.01
out = []
for rr in R:
    u, v = float(rr['px_bullet']) / S, float(rr['py']) / S
    lo_o, la_o = float(rr['lon']), float(rr['lat'])
    if rr['nw_fragment'] == '1': lo_n, la_n = lo_o, la_o   # agent NW affine kept
    else: lo_n, la_n = to_lonlat(use.get(tile_of(u, v), Wg), u, v)
    h = float(rr['h_bs77_m'])
    out.append(dict(id=rr['id'], conf=rr['conf'], nw=int(rr['nw_fragment']), tile=str(tile_of(u, v)), h=h,
                    lon_old=lo_o, lat_old=la_o, lon_new=lo_n, lat_new=la_n,
                    shift_m=float(np.hypot((lo_n - lo_o) * MX, (la_n - la_o) * MY)),
                    water_old=inside(tw, water, lo_o, la_o), water_new=inside(tw, water, lo_n, la_n),
                    bld_old=inside(tb, bl, lo_o, la_o), bld_new=inside(tb, bl, lo_n, la_n),
                    dH_old=samp(lo_o, la_o) - h, dH_new=samp(lo_n, la_n) - h))
with open(OUTCSV, 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
A = {k: np.array([o[k] for o in out]) for k in out[0]}
def mad(x): x = x[np.isfinite(x)]; return np.median(np.abs(x - np.median(x)))
print(f"\nmarks {len(out)}; position change old->new: median {np.median(A['shift_m']):.1f} m, p90 {np.percentile(A['shift_m'],90):.1f} m")
for lbl, m in (('all', np.ones(len(out), bool)), ('NW fragment', A['nw'] == 1), ('rest of sheet', A['nw'] == 0)):
    print(f"  {lbl:14s} n={m.sum():3d}  shift median {np.median(A['shift_m'][m]):6.1f} m | in water {A['water_old'][m].sum():3d} -> {A['water_new'][m].sum():3d} | "
          f"inside buildings {A['bld_old'][m].sum():3d} -> {A['bld_new'][m].sum():3d}")
for lbl, k in (('old position', 'dH_old'), ('new position', 'dH_new')):
    x = A[k]; ok = np.isfinite(x)
    print(f"  dH (surface 6.00 − mark) at {lbl}: n={ok.sum()} median {np.nanmedian(x):+.2f}  MAD {mad(x):.2f}  |dH|>1 m {100*(np.abs(x[ok])>1).mean():.0f}%")
