#!/usr/bin/env python3
"""5.65e: OSM raster tiles z17/z18 of the Sosnovka block + building mask extraction."""
import io
import json
import math
import time
import urllib.request

from PIL import Image

UA = {"User-Agent": "spb-arcticdem-pipeline/5.65 (research; one-off fetch)"}
OUT = "/home/z/my-project/repo/results"


def ll2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(math.radians(lat)) +
         1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def fetch_osm(lat0, lat1, lon0, lon1, z, fname):
    xt0, yt0 = ll2tile(lat1, lon0, z)
    xt1, yt1 = ll2tile(lat0, lon1, z)
    x0, y0, x1, y1 = int(xt0), int(yt0), int(xt1), int(yt1)
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    mosaic = Image.new("RGB", (W, H), (240, 240, 240))
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            turl = f"https://tile.openstreetmap.org/{z}/{tx}/{ty}.png"
            for attempt in range(3):
                try:
                    rq = urllib.request.Request(turl, headers=UA)
                    with urllib.request.urlopen(rq, timeout=30) as rr:
                        tile = Image.open(io.BytesIO(rr.read())).convert("RGB")
                    mosaic.paste(tile, ((tx - x0) * 256, (ty - y0) * 256))
                    break
                except Exception:
                    if attempt == 2:
                        print(f"  tile fail {z}/{tx}/{ty}")
            time.sleep(0.12)
    mosaic.save(f"{OUT}/{fname}")
    n = 2 ** z
    lat_c = (lat0 + lat1) / 2
    mpp = 156543.03392 * math.cos(math.radians(lat_c)) / n / 1.0  # at equator-scale zoom
    mpp = 156543.03392 * math.cos(math.radians(lat_c)) / (2 ** z)
    meta = {"z": z, "x0": x0, "y0": y0, "W": W, "H": H,
            "lat_top": lat1, "lat_bot": lat0, "lon_left": lon0, "lon_right": lon1,
            "m_per_px": mpp}
    json.dump(meta, open(f"{OUT}/{fname}.json", "w"))
    print(f"osm {fname}: {W}x{H}, {mpp:.3f} m/px")
    return meta


m17 = fetch_osm(59.8680, 59.8765, 30.4620, 30.4830, 17, "osm_sosnovka_z17_5.65.png")
m18 = fetch_osm(59.8700, 59.8750, 30.4655, 30.4770, 18, "osm_sosnovka_z18_5.65.png")

# Building mask from OSM style: building fill ~ (217,208,201) / (227,219,212), outline darker
im = Image.open(f"{OUT}/osm_sosnovka_z18_5.65.png").convert("RGB")
a = __import__("numpy").asarray(im).astype(int)
r, g, b = a[..., 0], a[..., 1], a[..., 2]
bld = (abs(r - 217) < 22) & (abs(g - 208) < 22) & (abs(b - 201) < 22) & (r >= g) & (g >= b - 4)
print("building px:", int(bld.sum()))
Image.fromarray((bld * 255).astype("uint8")).save(f"{OUT}/osm_bld_mask_z18_5.65.png")
print("saved osm_bld_mask_z18_5.65.png")
