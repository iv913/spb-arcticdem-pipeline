#!/usr/bin/env python3
"""5.65o: render 3 candidate poses as visual overlays for human judgment."""
import json
import math

import numpy as np
from PIL import Image, ImageDraw

RES = "/home/z/my-project/repo/results"
REF_LON, REF_LAT = 30.4691565, 59.8723521
KY = 111320.0
KX = 111320 * math.cos(math.radians(REF_LAT))
DORM_C = np.array([810.0, 1495.0])   # plan dorm centroid (walls), plan px (x right, y down)
K2_C = np.array([22.0, -65.0])       # real 88k2 building centroid (world m), user anchor

meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
m = meta["m_per_px"]
im = Image.open(f"{RES}/osm_sosnovka_z18_5.65.png").convert("RGB")
wx0 = (meta["lon_left"] - REF_LON) * KX
wy0 = (meta["lat_top"] - REF_LAT) * KY

pb = np.load(f"{RES}/reg65_plan_walls.npy")
pb = pb[(pb[:, 0] > 80) & (pb[:, 0] < 1560) & (pb[:, 1] > 40) & (pb[:, 1] < 1630)]

CANDS = [
    ("A_north_s009", 0.0, 0.090, K2_C - np.array([0, 0])),
    ("B_north_s013", 0.0, 0.130, K2_C + np.array([0, 35])),
    ("C_rot272_s135", 272.0, 0.135, np.array([-10.0, -30.0])),
]

for name, th, s, t in CANDS:
    a = math.radians(th)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    rel = np.stack([pb[:, 0] - DORM_C[0], -(pb[:, 1] - DORM_C[1])], 1)
    pw = (R @ rel.T).T * s + DORM_C * 0 + t
    # world -> osm source px
    sx = (pw[:, 0] - wx0) / m
    sy = (wy0 - pw[:, 1]) / m
    crop_half = 260 / m  # +-260 m window
    cx_px, cy_px = (K2_C[0] - wx0) / m, (wy0 - K2_C[1]) / m
    box = (int(cx_px - crop_half), int(cy_px - crop_half),
           int(cx_px + crop_half), int(cy_px + crop_half))
    crop = im.crop(box).resize((1734, 1734), Image.LANCZOS)
    dr = ImageDraw.Draw(crop, "RGBA")
    Z = 1734 / (2 * crop_half)
    for X, Y in zip(sx, sy):
        dx = (X - box[0]) * Z
        dy = (Y - box[1]) * Z
        if -3 < dx < 1737 and -3 < dy < 1737:
            dr.line([dx - 1.2, dy, dx + 1.2, dy], fill=(255, 0, 0, 255), width=3)
    # addr marker
    dr.ellipse([1734 / 2 - 6, 1734 / 2 - 6, 1734 / 2 + 6, 1734 / 2 + 6],
               outline=(0, 200, 0, 255), width=4)
    crop.save(f"{RES}/ov65_{name}.png")
    print(f"saved ov65_{name}.png")
