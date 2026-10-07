#!/usr/bin/env python3
"""5.65r: extract red site ring, test ring==real-block hypothesis vs scan pose."""
import json
import math

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF_LON, REF_LAT = 30.4691565, 59.8723521
KY = 111320.0
KX = 111320 * math.cos(math.radians(REF_LAT))
DORM_C = np.array([810.0, 1495.0])
K2_C = np.array([22.0, -65.0])

plan = np.asarray(Image.open(f"{RES}/img_314154.jpg").convert("RGB")).astype(int)
r, gg, b = plan[..., 0], plan[..., 1], plan[..., 2]
red = (r > 110) & (r - gg > 55) & (r - b > 55)
ys, xs = np.where(red)
ok = (xs > 80) & (xs < 1560) & (ys > 40) & (ys < 1630)
redpts = np.stack([xs[ok], ys[ok]], 1)
print("red pts:", len(redpts))
# ring = outermost red structure; estimate bbox of red points in central zone
# exclude the проектируемые buildings (red fill) by taking per-row min/max envelope
xr = redpts[:, 0]
yr = redpts[:, 1]
print("red x range", xr.min(), xr.max(), "y range", yr.min(), yr.max())
# The ring should be the outer envelope. Robust bbox: 1st/99th percentiles
xb = np.percentile(xr, [0.5, 99.5])
yb = np.percentile(yr, [0.5, 99.5])
print("red bbox p0.5-99.5:", xb, yb)
ring_w = xb[1] - xb[0]
ring_h = yb[1] - yb[0]
print(f"ring approx {ring_w:.0f} x {ring_h:.0f} px, center=({(xb[0]+xb[1])/2:.0f},{(yb[0]+yb[1])/2:.0f})")
np.save(f"{RES}/reg65_red_pts.npy", redpts)

# real block bounds (from annotated OSM, measured): x -55..88, y -50..55
BLOCK = np.array([(-55.0, 88.0), (-50.0, 55.0)])
bc = np.array([(BLOCK[0][0] + BLOCK[0][1]) / 2, (BLOCK[1][0] + BLOCK[1][1]) / 2])

RINGS = []
# Hypothesis 1: ring == real block -> scale from both dims
s1 = min((BLOCK[0][1] - BLOCK[0][0]) / ring_w, (BLOCK[1][1] - BLOCK[1][0]) / ring_h)
RINGS.append(("H1_ring_block", 0.0, s1, bc - np.array([(xb[0] + xb[1]) / 2, (yb[0] + yb[1]) / 2]) * s1))
# Hypothesis 2: scan pose
RINGS.append(("H2_scan", -8.0, 0.175, np.array([27.0, -77.5])))
# Hypothesis 3: dorm->k2 at s = 45m/470px
s3 = 45.0 / 470.0
RINGS.append(("H3_dormk2", 0.0, s3, K2_C - np.array([DORM_C[0], -DORM_C[1]]) * s3))

meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
m = meta["m_per_px"]
im = Image.open(f"{RES}/osm_sosnovka_z18_5.65.png").convert("RGB")
wx0 = (meta["lon_left"] - REF_LON) * KX
wy0 = (meta["lat_top"] - REF_LAT) * KY
cx_px, cy_px = (10.0 - wx0) / m, (wy0 + 20.0) / m
half = 280 / m
box = (int(cx_px - half), int(cy_px - half), int(cx_px + half), int(cy_px + half))
base = im.crop(box).resize((1872, 1872), Image.LANCZOS)
Z = 1872 / (2 * half)

for name, th, s, t in RINGS:
    a = math.radians(th)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    rel = np.stack([redpts[:, 0] - 0, -(redpts[:, 1] - 0)], 1)  # global transform (no dorm anchor)
    pw = (R @ rel.T).T * s + t
    crop = base.copy()
    dr = ImageDraw.Draw(crop, "RGBA")
    sx = (pw[:, 0] - wx0) / m
    sy = (wy0 - pw[:, 1]) / m
    for X, Y in zip(sx, sy):
        dx, dy = (X - box[0]) * Z, (Y - box[1]) * Z
        if -3 < dx < 1875 and -3 < dy < 1875:
            dr.line([dx - 1, dy, dx + 1, dy], fill=(255, 0, 0, 220), width=2)
    crop = crop.resize((936, 936), Image.LANCZOS)
    crop.save(f"{RES}/ov65_{name}.png")
    print(f"saved ov65_{name}.png  th={th} s={s:.4f} t={t.round(1)}")
