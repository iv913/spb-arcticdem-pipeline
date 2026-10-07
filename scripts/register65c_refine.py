#!/usr/bin/env python3
"""5.65h: refine pose + render overlays (world->plan walls over Esri; plan walls over OSM)."""
import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF = (59.8723521, 30.4691565)
KX = 111320 * math.cos(math.radians(REF[0]))
KY = 111320.0

# world setup (same as 5.65g)
meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
mpp = meta["m_per_px"]
mask = np.asarray(Image.open(f"{RES}/osm_bld_mask_z18_5.65.png")) > 127
wy0 = (meta["lat_top"] - REF[0]) * KY
wx0 = (meta["lon_left"] - REF[1]) * KX
ys, xs = np.where(mask)
wx = wx0 + xs * mpp
wy = wy0 - ys * mpp
gx0, gy1, g = -350, 350, 1.0
GW = GH = 701
grid = np.zeros((GH, GW), dtype=np.uint8)
ix = np.clip(((wx - gx0) / g).astype(int), 0, GW - 1)
iy = np.clip(((gy1 - wy) / g).astype(int), 0, GH - 1)
grid[iy, ix] = 1
grid = ndimage.binary_closing(grid, np.ones((3, 3)))
dt_world = ndimage.distance_transform_edt(~grid) * g
wys, wxs = np.where(grid > 0)
wpts = np.stack([wxs * g + gx0, gy1 - wys * g], 1)

pb = np.load(f"{RES}/reg65_plan_walls.npy")
plan = np.asarray(Image.open(f"{RES}/img_314154.jpg").convert("RGB"))
pm = np.zeros(plan.shape[:2], dtype=bool)
pm[pb[:, 1].astype(int), pb[:, 0].astype(int)] = True
dt_plan = ndimage.distance_transform_edt(~pm)

dorm_c = np.array([797.0, 1485.1])
k2_w = np.array([41.2, -51.1])
rng = np.random.default_rng(7)
pb_s = pb[rng.choice(len(pb), min(1500, len(pb)), replace=False)]
wpts_s = wpts[rng.choice(len(wpts), 600, replace=False)]


def cost_pose(th, s, tx, ty, ret=False):
    a = math.radians(th)
    ca, sa = math.cos(a), math.sin(a)
    R = np.array([[ca, -sa], [sa, ca]])
    rel = np.stack([pb_s[:, 0] - dorm_c[0], -(pb_s[:, 1] - dorm_c[1])], 1)
    pw = (R @ rel.T).T * s + k2_w + np.array([tx, ty])
    ix = np.clip(((pw[:, 0] - gx0) / g).astype(int), 0, GW - 1)
    iy = np.clip(((gy1 - pw[:, 1]) / g).astype(int), 0, GH - 1)
    cA = dt_world[iy, ix].mean()
    rel_w = (wpts_s - k2_w - np.array([tx, ty])) / s
    pl = (R.T @ rel_w.T).T
    px = pl[:, 0] + dorm_c[0]
    py = -pl[:, 1] + dorm_c[1]
    ok = (px >= 0) & (px < 2500) & (py >= 0) & (py < 1666)
    cB = dt_plan[py[ok].astype(int), px[ok].astype(int)].mean() * s if ok.sum() > 120 else 99.0
    return (cA + cB, cA) if not ret else (cA + cB, cA, R)


best = None
for th in np.arange(256, 292, 1.0):
    for s in np.arange(0.115, 0.165, 0.0025):
        for tx in np.arange(-20, 21, 2):
            for ty in np.arange(-45, 16, 2):
                c, cA = cost_pose(th, s, tx, ty)
                if best is None or c < best[0]:
                    best = (c, cA, th, float(s), float(tx), float(ty))
print("refined best:", [round(v, 3) for v in best])
c, cA, th, s, tx, ty = best
json.dump({"cost": c, "costA": cA, "theta_deg": th, "scale_m_per_px": s,
           "tx": tx, "ty": ty, "dorm_c": dorm_c.tolist(), "k2_w": k2_w.tolist(),
           "ref": REF},
          open(f"{RES}/reg65_refined_5.65.json", "w"), indent=1)

# full-resolution transform for rendering
a = math.radians(th)
ca, sa = math.cos(a), math.sin(a)
R = np.array([[ca, -sa], [sa, ca]])
rel = np.stack([pb[:, 0] - dorm_c[0], -(pb[:, 1] - dorm_c[1])], 1)
pw = (R @ rel.T).T * s + k2_w + np.array([tx, ty])

# --- overlay 1: plan walls (red) over Esri z18 imagery ---
esri = json.load(open(f"{RES}/esri_sosnovka_z18_5.65.png.json"))
empp = esri["m_per_px"]
eimg = np.asarray(Image.open(f"{RES}/esri_sosnovka_z18_5.65.png").convert("RGB")).copy()
ewy0 = (esri["lat_top"] - REF[0]) * KY
ewx0 = (esri["lon_left"] - REF[1]) * KX
ex = ((pw[:, 0] - ewx0) / empp).astype(int)
ey = ((ewy0 - pw[:, 1]) / empp).astype(int)
ok = (ex >= 0) & (ex < eimg.shape[1]) & (ey >= 0) & (ey < eimg.shape[0])
for dx in (-1, 0, 1):
    for dy in (-1, 0, 1):
        eimg[np.clip(ey[ok] + dy, 0, eimg.shape[0] - 1),
             np.clip(ex[ok] + dx, 0, eimg.shape[1] - 1)] = [255, 40, 40]
Image.fromarray(eimg).save(f"{RES}/reg65_overlay_plan_on_esri.png")
print("overlay saved: plan walls (red) on Esri")

# --- overlay 2: OSM buildings (blue) into plan frame ---
pimg = plan.copy()
rel_w = (wpts - k2_w - np.array([tx, ty])) / s
pl = (R.T @ rel_w.T).T
px = (pl[:, 0] + dorm_c[0]).astype(int)
py = (-pl[:, 1] + dorm_c[1]).astype(int)
ok = (px >= 0) & (px < 2500) & (py >= 0) & (py < 1666)
for dx in (-1, 0, 1):
    for dy in (-1, 0, 1):
        pimg[np.clip(py[ok] + dy, 0, 1665), np.clip(px[ok] + dx, 0, 2499)] = [0, 90, 255]
Image.fromarray(pimg).save(f"{RES}/reg65_overlay_osm_on_plan.png")
print("overlay saved: OSM buildings (blue) on plan")
