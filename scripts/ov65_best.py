#!/usr/bin/env python3
"""5.65q: render best scan pose - plan walls red over OSM and over Esri."""
import json
import math

import numpy as np
from PIL import Image, ImageDraw

RES = "/home/z/my-project/repo/results"
REF_LON, REF_LAT = 30.4691565, 59.8723521
KY = 111320.0
KX = 111320 * math.cos(math.radians(REF_LAT))

top = json.load(open(f"{RES}/reg65_scan_5.65.json"))["top"]
# median pose of top-20
ths = sorted(t[3] for t in top[:20])
ss = sorted(t[4] for t in top[:20])
txs = sorted(t[5] for t in top[:20])
tys = sorted(t[6] for t in top[:20])
th, s = ths[len(ths) // 2], ss[len(ss) // 2]
t = np.array([22.0 + txs[len(txs) // 2], -65.0 + tys[len(tys) // 2]])
print(f"median pose: th={th} s={s} t={t.round(1)}")

DORM_C = np.array([810.0, 1495.0])
pb = np.load(f"{RES}/reg65_plan_walls.npy")
pb = pb[(pb[:, 0] > 80) & (pb[:, 0] < 1560) & (pb[:, 1] > 40) & (pb[:, 1] < 1630)]
a = math.radians(th)
R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
rel = np.stack([pb[:, 0] - DORM_C[0], -(pb[:, 1] - DORM_C[1])], 1)
pw = (R @ rel.T).T * s + t

for src, fname in (("osm", "osm_sosnovka_z18_5.65.png"),
                   ("esri", "esri_sosnovka_z18_5.65.png")):
    meta = json.load(open(f"{RES}/{src}_sosnovka_z18_5.65.png.json"))
    m = meta.get("m_per_px_x") or meta.get("m_per_px")
    im = Image.open(f"{RES}/{fname}").convert("RGB")
    wx0 = (meta["lon_left"] - REF_LON) * KX
    wy0 = (meta["lat_top"] - REF_LAT) * KY
    cxw, cyw = 10.0, -20.0
    cx_px, cy_px = (cxw - wx0) / m, (wy0 - cyw) / m
    half = 280 / m
    box = (int(cx_px - half), int(cy_px - half), int(cx_px + half), int(cy_px + half))
    crop = im.crop(box).resize((1872, 1872), Image.LANCZOS)
    Z = 1872 / (2 * half)
    dr = ImageDraw.Draw(crop, "RGBA")
    sx = (pw[:, 0] - wx0) / m
    sy = (wy0 - pw[:, 1]) / m
    for X, Y in zip(sx, sy):
        dx, dy = (X - box[0]) * Z, (Y - box[1]) * Z
        if -3 < dx < 1875 and -3 < dy < 1875:
            dr.line([dx - 1.3, dy, dx + 1.3, dy], fill=(255, 0, 0, 255), width=3)
    crop = crop.resize((936, 936), Image.LANCZOS)
    crop.save(f"{RES}/ov65_best_{src}.png")
    print(f"saved ov65_best_{src}.png")
json.dump({"th": float(th), "s": float(s), "t": t.tolist(),
           "dorm_c": DORM_C.tolist(), "k2_c": [22.0, -65.0]},
          open(f"{RES}/reg65_bestpose_5.65.json", "w"), indent=1)
