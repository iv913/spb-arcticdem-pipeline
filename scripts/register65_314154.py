#!/usr/bin/env python3
"""5.65f: Plan 314154 wall extraction + brute-force chamfer registration to OSM buildings.

World frame: meters, origin at REF = (59.8723521, 30.4691565) (86 k2 address point, 5.53).
Plan frame: pixels of 2500x1666 scan (x right, y down). Similarity transform:
  world = s * R(theta) @ [px, -py]  (y flip) + t
Brute force over theta (full circle), s, t-grid; cost = mean of world-DT at plan wall pts.
"""
import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF = (59.8723521, 30.4691565)
KX = 111320 * math.cos(math.radians(REF[0]))
KY = 111320.0

# ---------------- world: OSM building mask -> meters ----------------
meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
mpp = meta["m_per_px"]
mask = np.asarray(Image.open(f"{RES}/osm_bld_mask_z18_5.65.png")) > 127
H, W = mask.shape
# world coords of mask pixels
lat_top, lon_left = meta["lat_top"], meta["lon_left"]
wy0 = (lat_top - REF[0]) * KY          # world y of top row (positive north)
wx0 = (lon_left - REF[1]) * KX
ys, xs = np.where(mask)
wx = wx0 + xs * mpp
wy = wy0 - ys * mpp
print(f"world building pts: {len(wx)}, wx range {wx.min():.0f}..{wx.max():.0f}, "
      f"wy {wy.min():.0f}..{wy.max():.0f}")

# distance transform of world building field (1 m grid over window)
gx0, gx1 = -350, 350
gy0, gy1 = -350, 350
g = 1.0  # m per grid cell
GW, GH = int((gx1 - gx0) / g) + 1, int((gy1 - gy0) / g) + 1
grid = np.zeros((GH, GW), dtype=np.uint8)
ix = np.clip(((wx - gx0) / g).astype(int), 0, GW - 1)
iy = np.clip(((gy1 - wy) / g).astype(int), 0, GH - 1)
grid[iy, ix] = 1
grid = ndimage.binary_closing(grid, np.ones((3, 3)))
dt = ndimage.distance_transform_edt(~grid) * g
print(f"DT grid {GH}x{GW}, max dist {dt.max():.1f} m")
np.save(f"{RES}/reg65_world_dt.npy", dt)

# ---------------- plan: wall extraction ----------------
plan = np.asarray(Image.open(f"{RES}/img_314154.jpg").convert("RGB")).astype(int)
PH, PW = plan.shape[:2]
r, gg, b = plan[..., 0], plan[..., 1], plan[..., 2]
# black/dark walls (existing context incl. dormitory)
black = (r < 100) & (gg < 100) & (b < 100)
# red проектируемые walls
red = (r > 120) & (r - gg > 60) & (r - b > 60)
# thin-line filter: walls are 1-3 px lines; labels are blobs. Keep all dark thin:
black_thin = black & ~ndimage.binary_erosion(black, np.ones((5, 5)))
red_thin = red & ~ndimage.binary_erosion(red, np.ones((5, 5)))
print(f"black px {black.sum()}, thin {black_thin.sum()}; red px {red.sum()}, thin {red_thin.sum()}")

for name, m in (("black", black_thin), ("red", red_thin)):
    ys2, xs2 = np.where(m)
    np.save(f"{RES}/reg65_plan_{name}_pts.npy", np.stack([xs2, ys2], 1))
print("saved plan wall point sets")

# plan drawing area only (exclude legend right third + margins)
def in_drawing(pts):
    return pts[(pts[:, 0] > 60) & (pts[:, 0] < 1560) & (pts[:, 1] > 40) & (pts[:, 1] < 1630)]

pb = in_drawing(np.load(f"{RES}/reg65_plan_black_pts.npy")).astype(float)
pr = in_drawing(np.load(f"{RES}/reg65_plan_red_pts.npy")).astype(float)
print(f"drawing-area black {len(pb)}, red {len(pr)}")

# subsample for speed
rng = np.random.default_rng(42)
pb_s = pb[rng.choice(len(pb), min(1200, len(pb)), replace=False)]
pr_s = pr[rng.choice(len(pr), min(1200, len(pr)), replace=False)]


def dt_lookup(pts_w):
    ix = np.clip(((pts_w[..., 0] - gx0) / g).astype(int), 0, GW - 1)
    iy = np.clip(((gy1 - pts_w[..., 1]) / g).astype(int), 0, GH - 1)
    return dt[iy, ix]


# ---------------- brute force ----------------
# dorm seed correspondence: plan dorm interior centroid ~ (870, 1470)? compute from black
# walls near the dorm label area (bottom): use bbox window
win = pb[(pb[:, 0] > 520) & (pb[:, 0] < 1200) & (pb[:, 1] > 1380) & (pb[:, 1] < 1620)]
dorm_c = win.mean(0) if len(win) else np.array([870.0, 1470.0])
print(f"dorm window pts {len(win)}, centroid px {dorm_c.round(1)}")
# world seed: 88 k2 centroid (user anchor)
k2 = (59.8718931, 30.4698941)
k2_w = np.array([(k2[1] - REF[1]) * KX, (k2[0] - REF[0]) * KY])
print(f"88k2 world offset {k2_w.round(1)}")

# plan -> world: world = s*R @ [px - cx, -(py - cy)] + t  with t near k2_w
results = []
thetas = np.arange(0, 360, 4)
scales = np.arange(0.06, 0.32, 0.01)
ts_grid = np.arange(-80, 81, 10)  # translation offsets around seed, m
for th in thetas:
    a = math.radians(th)
    ca, sa = math.cos(a), math.sin(a)
    for s in scales:
        # transform plan points (relative to dorm centroid)
        rel = np.stack([pb_s[:, 0] - dorm_c[0], -(pb_s[:, 1] - dorm_c[1])], 1)
        R = np.array([[ca, -sa], [sa, ca]])
        base = (R @ rel.T).T * s
        for tx in ts_grid:
            for ty in ts_grid:
                t = k2_w + np.array([tx, ty])
                pts_w = base + t
                ix = np.clip(((pts_w[:, 0] - gx0) / g).astype(int), 0, GW - 1)
                iy = np.clip(((gy1 - pts_w[:, 1]) / g).astype(int), 0, GH - 1)
                c = dt[iy, ix].mean()
                results.append((c, th, s, tx, ty))
results.sort(key=lambda x: x[0])
print("TOP-10 coarse:")
for c, th, s, tx, ty in results[:10]:
    print(f"  cost={c:6.2f} th={th:5.1f} s={s:.2f} t=({tx:+d},{ty:+d})")
json.dump({"top": [(float(c), float(th), float(s), float(tx), float(ty))
                   for c, th, s, tx, ty in results[:50]],
           "dorm_c": dorm_c.tolist(), "k2_w": k2_w.tolist()},
          open(f"{RES}/reg65_coarse_5.65.json", "w"), indent=1)
print("saved reg65_coarse_5.65.json")
