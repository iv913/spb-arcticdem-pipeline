#!/usr/bin/env python3
"""5.65p: scale scan anchored dorm->k2, north-up +-10deg, global two-way cost."""
import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF_LON, REF_LAT = 30.4691565, 59.8723521
KY = 111320.0
KX = 111320 * math.cos(math.radians(REF_LAT))
DORM_C = np.array([810.0, 1495.0])
K2_C = np.array([22.0, -65.0])

# world building mask + DT (1 m grid)
meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
mpp = meta["m_per_px"]
mask = np.asarray(Image.open(f"{RES}/osm_bld_mask_z18_5.65.png")) > 127
wy0 = (meta["lat_top"] - REF_LAT) * KY
wx0 = (meta["lon_left"] - REF_LON) * KX
ys, xs = np.where(mask)
wx = wx0 + xs * mpp
wy = wy0 - ys * mpp
g = 1.0
GX0, GY1 = -320, 260
GW, GH = 641, 581
grid = np.zeros((GH, GW), dtype=bool)
grid[np.clip(((wy - GY1) / -g).astype(int) * 0 + np.clip(((GY1 - wy) / g).astype(int), 0, GH - 1),
             0, GH - 1),
     np.clip(((wx - GX0) / g).astype(int), 0, GW - 1)] = True
grid = ndimage.binary_closing(grid, np.ones((3, 3)))
dt_w = ndimage.distance_transform_edt(~grid) * g
wys2, wxs2 = np.where(grid)
wpts = np.stack([wxs2 * g + GX0, GY1 - wys2 * g], 1)
rng = np.random.default_rng(9)
wpts_s = wpts[rng.choice(len(wpts), 700, replace=False)]

pb = np.load(f"{RES}/reg65_plan_walls.npy")
pb = pb[(pb[:, 0] > 80) & (pb[:, 0] < 1560) & (pb[:, 1] > 40) & (pb[:, 1] < 1630)]
pm = np.zeros((1666, 2500), dtype=bool)
pm[pb[:, 1].astype(int), pb[:, 0].astype(int)] = True
dt_plan = ndimage.distance_transform_edt(~pm)
pb_s = pb[rng.choice(len(pb), min(1400, len(pb)), replace=False)]

# plan-DT in wall-window mean: to be scale-fair, world->plan uses same wall set
best = []
for s in np.arange(0.08, 0.325, 0.005):
    for th in np.arange(-10, 10.1, 1.0):
        a = math.radians(th)
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        rel = np.stack([pb_s[:, 0] - DORM_C[0], -(pb_s[:, 1] - DORM_C[1])], 1)
        base = (R @ rel.T).T * s
        for dtx in np.arange(-25, 25.1, 2.5):
            for dty in np.arange(-25, 25.1, 2.5):
                t = K2_C + np.array([dtx, dty])
                pw = base + t
                ix = np.clip(((pw[:, 0] - GX0) / g).astype(int), 0, GW - 1)
                iy = np.clip(((GY1 - pw[:, 1]) / g).astype(int), 0, GH - 1)
                cA = dt_w[iy, ix].mean()
                # world->plan: inverse
                pl = (R.T @ ((wpts_s - t) / s).T).T
                px = pl[:, 0] + DORM_C[0]
                py = -pl[:, 1] + DORM_C[1]
                ok = (px >= 60) & (px < 1600) & (py >= 40) & (py < 1640)
                if ok.sum() < 0.25 * len(px):
                    continue
                cB = np.sort(dt_plan[py[ok].astype(int), px[ok].astype(int)])[:max(50, int(ok.sum() * 0.45))].mean() * s
                best.append((cA + cB, cA, cB, float(th), float(s), float(dtx), float(dty)))

best.sort(key=lambda x: x[0])
print("TOP-14 (north-up anchored):")
for tot, cA, cB, th, s, dx, dy in best[:14]:
    print(f"  tot={tot:6.2f} (A={cA:5.2f} B={cB:5.2f}) th={th:6.1f} s={s:.3f} "
          f"t_off=({dx:+5.1f},{dy:+5.1f})")
json.dump({"top": best[:60]}, open(f"{RES}/reg65_scan_5.65.json", "w"), indent=1)
print("saved reg65_scan_5.65.json")
