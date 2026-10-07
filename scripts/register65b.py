#!/usr/bin/env python3
"""5.65g: Two-way chamfer registration, text-filtered walls, scale 0.08-0.30."""
import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF = (59.8723521, 30.4691565)
KX = 111320 * math.cos(math.radians(REF[0]))
KY = 111320.0

# ---------------- world DT (1 m grid) ----------------
meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
mpp = meta["m_per_px"]
mask = np.asarray(Image.open(f"{RES}/osm_bld_mask_z18_5.65.png")) > 127
H, W = mask.shape
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
# world building sample pts (for world->plan term), subsample grid cells that are 1
wys, wxs = np.where(grid > 0)
wpts = np.stack([wxs * g + gx0, gy1 - wys * g], 1)
rng = np.random.default_rng(7)
wpts_s = wpts[rng.choice(len(wpts), 600, replace=False)]

# ---------------- plan walls: text-filtered ----------------
plan = np.asarray(Image.open(f"{RES}/img_314154.jpg").convert("RGB")).astype(int)
r, gg, b = plan[..., 0], plan[..., 1], plan[..., 2]
black = (r < 100) & (gg < 100) & (b < 100)
lab, n = ndimage.label(black, structure=np.ones((3, 3)))
sl = ndimage.find_objects(lab)
keep_ids = []
for i, slc in enumerate(sl, start=1):
    if slc is None:
        continue
    h = slc[0].stop - slc[0].start
    w = slc[1].stop - slc[1].start
    area = (lab[slc] == i).sum()
    diag = math.hypot(h, w)
    # walls: long lines OR large structures; text digits: h,w < 40
    if diag > 55 or (w > 90 and area > 300) or (h > 90 and area > 300):
        keep_ids.append(i)
print(f"black comps: {n}, kept {len(keep_ids)}")
wmask = np.isin(lab, keep_ids)
ys2, xs2 = np.where(wmask)
ok = (xs2 > 60) & (xs2 < 1560) & (ys2 > 40) & (ys2 < 1630)
pb = np.stack([xs2[ok], ys2[ok]], 1).astype(float)
print(f"plan black wall pts: {len(pb)}")
np.save(f"{RES}/reg65_plan_walls.npy", pb)

# plan-frame DT of walls (for world->plan term)
pm = np.zeros(plan.shape[:2], dtype=bool)
pm[ys2[ok].astype(int), xs2[ok].astype(int)] = True
dt_plan = ndimage.distance_transform_edt(~pm)  # px units

pb_s = pb[rng.choice(len(pb), min(1500, len(pb)), replace=False)]

# ---------------- pose search ----------------
dorm_c = np.array([797.0, 1485.1])
k2_w = np.array([41.2, -51.1])


def cost_pose(th, s, tx, ty):
    a = math.radians(th)
    ca, sa = math.cos(a), math.sin(a)
    R = np.array([[ca, -sa], [sa, ca]])
    rel = np.stack([pb_s[:, 0] - dorm_c[0], -(pb_s[:, 1] - dorm_c[1])], 1)
    pw = (R @ rel.T).T * s + k2_w + np.array([tx, ty])
    ix = np.clip(((pw[:, 0] - gx0) / g).astype(int), 0, GW - 1)
    iy = np.clip(((gy1 - pw[:, 1]) / g).astype(int), 0, GH - 1)
    cA = dt_world[iy, ix].mean()
    # world -> plan: inverse transform world sample pts into plan px
    Rw = R.T
    rel_w = (wpts_s - k2_w - np.array([tx, ty])) / s
    pl = (Rw @ rel_w.T).T
    px = pl[:, 0] + dorm_c[0]
    py = -pl[:, 1] + dorm_c[1]
    ok = (px >= 0) & (px < 2500) & (py >= 0) & (py < 1666)
    if ok.sum() < 0.2 * len(px):
        return 99.0, cA
    cB = dt_plan[py[ok].astype(int), px[ok].astype(int)].mean() * s  # meters
    return cA + cB, cA


results = []
for th in range(0, 360, 4):
    for s in np.arange(0.08, 0.305, 0.01):
        for tx in range(-80, 81, 10):
            for ty in range(-80, 81, 10):
                c, cA = cost_pose(th, s, tx, ty)
                results.append((c, cA, th, float(s), tx, ty))
results.sort(key=lambda x: x[0])
print("TOP-12 coarse (two-way):")
for c, cA, th, s, tx, ty in results[:12]:
    print(f"  cost={c:6.2f} (A={cA:5.2f}) th={th:3d} s={s:.2f} t=({tx:+d},{ty:+d})")
json.dump({"top": [tuple(map(float, r)) for r in results[:60]]},
          open(f"{RES}/reg65_coarse2_5.65.json", "w"), indent=1)
print("saved reg65_coarse2_5.65.json")
