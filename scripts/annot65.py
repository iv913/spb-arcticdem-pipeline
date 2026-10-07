#!/usr/bin/env python3
"""5.65m: annotated crops - korpus polygons + world grid drawn on OSM/Esri rasters."""
import json
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RES = "/home/z/my-project/repo/results"
REF_LON, REF_LAT = 30.4691565, 59.8723521
KY = 111320.0
KX = 111320 * math.cos(math.radians(REF_LAT))

nom = json.load(open(f"{RES}/nom_poly_korpusa_5.65.json"))
polys = {}
for key, h in nom.items():
    c = h.get("coords")
    if c and h["type"] == "Polygon":
        pts = [((p[0] - REF_LON) * KX, (p[1] - REF_LAT) * KY) for p in c[0]]
        polys[key] = pts

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
except Exception:
    font = font_s = ImageFont.load_default()

CX, CY = 41.2, -51.1  # crop center (88k2 addr)
HALF = 130.0          # m
ZOOM = 3

for src in ("osm", "esri"):
    meta = json.load(open(f"{RES}/{src}_sosnovka_z18_5.65.png.json"))
    m = meta.get("m_per_px_x") or meta.get("m_per_px")
    im = Image.open(f"{RES}/{src}_sosnovka_z18_5.65.png").convert("RGB")
    wx0 = (meta["lon_left"] - REF_LON) * KX
    wy0 = (meta["lat_top"] - REF_LAT) * KY

    def w2p(X, Y):
        return ((X - wx0) / m * ZOOM, (wy0 - Y) / m * ZOOM)

    # crop in display space
    px, py = w2p(CX, CY)
    box = (int(px - HALF / m * ZOOM), int(py - HALF / m * ZOOM),
           int(px + HALF / m * ZOOM), int(py + HALF / m * ZOOM))
    crop = im.crop(box)
    dr = ImageDraw.Draw(crop)
    # world grid every 20 m
    X0 = math.ceil((CX - HALF) / 20) * 20
    while X0 <= CX + HALF:
        p = w2p(X0, CY - HALF)
        p2 = w2p(X0, CY + HALF)
        dr.line([p, p2], fill=(0, 160, 255), width=1)
        dr.text((p[0] + 2, 4), str(X0), fill=(0, 90, 200), font=font_s)
        X0 += 20
    Y0 = math.ceil((CY - HALF) / 20) * 20
    while Y0 <= CY + HALF:
        p = w2p(CX - HALF, Y0)
        p2 = w2p(CX + HALF, Y0)
        dr.line([p, p2], fill=(0, 160, 255), width=1)
        dr.text((4, p[1] + 2), str(Y0), fill=(0, 90, 200), font=font_s)
        Y0 += 20
    # korpus polygons
    for key, pts in polys.items():
        pp = [w2p(*q) for q in pts]
        dr.line(pp + [pp[0]], fill=(255, 0, 255), width=4)
        cx = sum(q[0] for q in pp) / len(pp)
        cy = sum(q[1] for q in pp) / len(pp)
        dr.text((cx - 40, cy - 20), key, fill=(180, 0, 180), font=font)
    # addr star
    p = w2p(CX, CY)
    dr.ellipse([p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10], outline=(255, 0, 0), width=5)
    crop = crop.resize((crop.width // 2, crop.height // 2), Image.LANCZOS)
    crop.save(f"{RES}/diag65_annot_{src}.png")
    print(f"saved diag65_annot_{src}.png {crop.size}")
