#!/usr/bin/env python3
"""Re-georeference the Atayants-2013 sheet against OSM buildings: agent affine -> global ECC -> per-tile ECC.
usage: georef_plan.py <sheet.jpg> <repo> <out.npz>"""
import sys, json, numpy as np, cv2
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import shape
from shapely.affinity import affine_transform
IMG, REPO, OUT = sys.argv[1:4]
im = np.asarray(Image.open(IMG).convert('RGB')).astype(int); H_I, W_I = im.shape[:2]
r, g, b = im[..., 0], im[..., 1], im[..., 2]
plan = ((r > 170) & (r < 235) & (g > 120) & (g < 185) & (b > 100) & (b < 170) & (r - g > 25) & (r - b > 40)).astype(np.uint8)
plan = cv2.morphologyEx(plan, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
S = 3000.0 / W_I                                     # image px -> agent sheet px
geo = json.load(open(f'{REPO}/results/atayanc_6.30/georef_6.30.json')); a, b_, c, d, e, f = geo['affine_deg']
LAT0 = 59.945; MX = 111320.0 * np.cos(np.radians(LAT0)); MY = 111133.0
lon0, lat1 = 30.2700, 59.9600                         # grid origin (NW), 2 m cells
GS = 2.0; GW, GH = 1500, 1300
def grid_to_lonlat(gx, gy): return lon0 + gx * GS / MX, lat1 - gy * GS / MY
# OSM buildings -> grid raster
F = json.load(open(f'{REPO}/results/atayanc_6.30/plan_buildings_6.30.geojson'))['features']
geoms = [shape(x['geometry']) for x in F if x['properties'].get('src') in ('osm_only', 'osm')]
to_grid = [MX / GS, 0, 0, -MY / GS, -lon0 * MX / GS, lat1 * MY / GS]
gg = [affine_transform(gm, to_grid) for gm in geoms]
osm = rasterize([(x, 1) for x in gg if not x.is_empty], out_shape=(GH, GW), transform=Affine.identity(), dtype='uint8')
print(f'OSM buildings {len(geoms)}, grid {GW}x{GH} @2 m, building share {osm.mean()*100:.1f}%')
# initial warp W: grid px -> image px, from the agent affine (lon,lat) = A [P,Q,1], P,Q sheet px
A = np.array([[a, b_], [d, e]]); t = np.array([c, f]); Ai = np.linalg.inv(A)
def W_from_agent():
    pts = np.array([[0, 0], [GW, 0], [0, GH]], float)
    ll = np.array([grid_to_lonlat(*p) for p in pts])
    PQ = (Ai @ (ll - t).T).T / S
    M = cv2.getAffineTransform(pts.astype(np.float32), PQ.astype(np.float32)); return M.astype(np.float32)
W0 = W_from_agent()
edges_osm = cv2.Canny(osm * 255, 50, 150)
dt_osm = cv2.distanceTransform((edges_osm == 0).astype(np.uint8), cv2.DIST_L2, 5) * GS      # metres to nearest OSM edge
plan_edges = np.argwhere(cv2.Canny(plan * 255, 50, 150) > 0)[:, ::-1].astype(np.float64)    # (u,v) image px
def chamfer(W, sel=None):
    Wf = np.vstack([W, [0, 0, 1]]); Wi = np.linalg.inv(Wf)[:2]
    uv = plan_edges if sel is None else plan_edges[sel]
    gxy = (Wi[:, :2] @ uv.T).T + Wi[:, 2]
    ok = (gxy[:, 0] >= 0) & (gxy[:, 0] < GW - 1) & (gxy[:, 1] >= 0) & (gxy[:, 1] < GH - 1)
    dd = dt_osm[gxy[ok, 1].astype(int), gxy[ok, 0].astype(int)]
    return dd, ok
def ecc(W, gx0=0, gy0=0, gx1=GW, gy1=GH, iters=300):
    tmpl = cv2.GaussianBlur(osm[gy0:gy1, gx0:gx1].astype(np.float32), (0, 0), 2.0)
    inp = cv2.GaussianBlur(plan.astype(np.float32), (0, 0), 2.0 / 0.78 * GS / 2)
    Wc = W.copy(); Wc[:, 2] = W[:, :2] @ np.array([gx0, gy0], np.float32) + W[:, 2]
    try:
        cc, Wc = cv2.findTransformECC(tmpl, inp, Wc, cv2.MOTION_AFFINE, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-6), None, 5)
    except cv2.error as ex:
        return None, None
    Wn = Wc.copy(); Wn[:, 2] = Wc[:, 2] - Wc[:, :2] @ np.array([gx0, gy0], np.float32)
    return Wn, cc
def rep(tag, W, sel=None):
    dd, ok = chamfer(W, sel)
    return f'{tag:30s} plan edges on map {ok.mean()*100:5.1f}%  edge->OSM median {np.median(dd):5.1f} m  p75 {np.percentile(dd,75):5.1f}  p90 {np.percentile(dd,90):5.1f}'
print(rep('agent affine (5 GCP)', W0))
Wg, cc = ecc(W0); print(rep(f'global ECC (cc {cc:.3f})', Wg))
# per-tile ECC in IMAGE space: 4x3 tiles, template crop = tile footprint on the grid
TX, TY = 4, 3; tiles = {}
Wgf = np.vstack([Wg, [0, 0, 1]]); Wgi = np.linalg.inv(Wgf)[:2]
for j in range(TY):
    for i in range(TX):
        u0, u1 = i * W_I / TX, (i + 1) * W_I / TX; v0, v1 = j * H_I / TY, (j + 1) * H_I / TY
        corners = np.array([[u0, v0], [u1, v0], [u0, v1], [u1, v1]])
        gxy = (Wgi[:, :2] @ corners.T).T + Wgi[:, 2]
        gx0, gy0 = np.clip(gxy.min(0).astype(int) - 40, 0, [GW, GH]); gx1, gy1 = np.clip(gxy.max(0).astype(int) + 40, 0, [GW, GH])
        sel = (plan_edges[:, 0] >= u0) & (plan_edges[:, 0] < u1) & (plan_edges[:, 1] >= v0) & (plan_edges[:, 1] < v1)
        if sel.sum() < 500 or gx1 - gx0 < 100 or gy1 - gy0 < 100: continue
        Wt, cct = ecc(Wg, gx0, gy0, gx1, gy1)
        if Wt is None: continue
        dg, _ = chamfer(Wg, sel); dtl, _ = chamfer(Wt, sel)
        # displacement of the tile centre between global and tile warp, metres
        uc = np.array([(u0 + u1) / 2, (v0 + v1) / 2])
        def to_m(W):
            Wi = np.linalg.inv(np.vstack([W, [0, 0, 1]]))[:2]; gp = Wi[:, :2] @ uc + Wi[:, 2]; return gp * GS
        disp = np.linalg.norm(to_m(Wt) - to_m(Wg))
        rot = np.degrees(np.arctan2(Wt[1, 0], Wt[0, 0]) - np.arctan2(Wg[1, 0], Wg[0, 0]))
        tiles[(i, j)] = Wt
        print(f'  tile ({i},{j}) edges {sel.sum():6d}  median edge->OSM: global {np.median(dg):5.1f} m -> tile {np.median(dtl):5.1f} m   '
              f'tile shift vs global {disp:6.1f} m, rot {rot:+5.2f} deg, cc {cct:.3f}')
np.savez(OUT, W0=W0, Wg=Wg, S=S, W_I=W_I, H_I=H_I, TX=TX, TY=TY, lon0=lon0, lat1=lat1, GS=GS, MX=MX, MY=MY,
         tiles=np.array([[i, j] + list(Wt.ravel()) for (i, j), Wt in tiles.items()]))
