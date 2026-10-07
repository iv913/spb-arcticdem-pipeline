#!/usr/bin/env python3
"""5.65n: annotated crops v2 - single verified world->display transform."""
import json
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RES = "/home/z/my-project/repo/results"
REF_LON, REF_LAT = 30.4691565, 59.8723521
KY = 111320.0
KX = 111320 * math.cos(math.radians(REF_LAT))
CX, CY = 41.2, -51.1
HALF = 130.0
ZOOM = 3

nom = json.load(open(f"{RES}/nom_poly_korpusa_5.65.json"))
polys = {}
for key, h in nom.items():
    c = h.get("coords")
    if c and h["type"] == "Polygon":
        polys[key] = [((p[0] - REF_LON) * KX, (p[1] - REF_LAT) * KY) for p in c[0]]

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)

for src in ("osm", "esri"):
    meta = json.load(open(f"{RES}/{src}_sosnovka_z18_5.65.png.json"))
    m = meta.get("m_per_px_x") or meta.get("m_per_px")
    im = Image.open(f"{RES}/{src}_sosnovka_z18_5.65.png").convert("RGB")
    wx0 = (meta["lon_left"] - REF_LON) * KX
    wy0 = (meta["lat_top"] - REF_LAT) * KY

    # source-px of crop center
    pxs, pys = (CX - wx0) / m, (wy0 - CY) / m
    half_src = HALF / m
    ox, oy = pxs - half_src, pys - half_src
    box = (int(ox), int(oy), int(ox + 2 * half_src), int(oy + 2 * half_src))
    crop = im.crop(box).resize((int(2 * half_src * ZOOM), int(2 * half_src * ZOOM)),
                               Image.LANCZOS)
    W, H = crop.size

    def w2d(X, Y):
        return ((X - wx0) / m - ox) * ZOOM, ((wy0 - Y) / m - oy) * ZOOM

    # self-check: center must map to center
    dx, dy = w2d(CX, CY)
    assert abs(dx - W / 2) < 2 and abs(dy - H / 2) < 2, (dx, dy, W, H)

    dr = ImageDraw.Draw(crop)
    X0 = math.ceil((CX - HALF) / 20) * 20
    while X0 <= CX + HALF:
        p1, p2 = w2d(X0, CY - HALF), w2d(X0, CY + HALF)
        dr.line([p1, p2], fill=(0, 150, 255), width=2)
        dr.text((p1[0] + 3, 4), str(X0), fill=(0, 80, 180), font=font_s)
        X0 += 20
    Y0 = math.ceil((CY - HALF) / 20) * 20
    while Y0 <= CY + HALF:
        p1, p2 = w2d(CX - HALF, Y0), w2d(CX + HALF, Y0)
        dr.line([p1, p2], fill=(0, 150, 255), width=2)
        dr.text((4, p1[1] + 3), str(Y0), fill=(0, 80, 180), font=font_s)
        Y0 += 20
    for key, pts in polys.items():
        pp = [w2d(*q) for q in pts]
        dr.line(pp + [pp[0]], fill=(255, 0, 255), width=4)
        cx = sum(q[0] for q in pp) / len(pp)
        cy = sum(q[1] for q in pp) / len(pp)
        dr.text((cx - 45, cy - 18), key, fill=(160, 0, 160), font=font)
    p = w2d(CX, CY)
    dr.ellipse([p[0] - 9, p[1] - 9, p[0] + 9, p[1] + 9], outline=(255, 0, 0), width=5)
    crop = crop.resize((W // 2, H // 2), Image.LANCZOS)
    crop.save(f"{RES}/diag65_annot2_{src}.png")
    print(f"saved diag65_annot2_{src}.png, self-check dx,dy=({dx:.1f},{dy:.1f}) W/2,H/2=({W/2},{H/2})")
