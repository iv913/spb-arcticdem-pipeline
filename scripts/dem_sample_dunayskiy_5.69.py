#!/usr/bin/env python3
"""
5.69-A: ArcticDEM v4.1 (2m) тайл 32_73_2_1 — высота на якоре Дунайского
(59.832, 30.362) и статистика окрестности (site-level h_model).
"""
import os, json
import numpy as np
import rasterio
from rasterio.warp import transform

os.environ.setdefault('AWS_NO_SIGN_REQUEST', 'YES')
TILE = "https://pgc-opendata-dems.s3.us-west-2.amazonaws.com/arcticdem/mosaics/v4.1/2m/32/32_73_2_1.tif"

# якорь 5.64
lat0, lon0 = 59.832, 30.362

with rasterio.open(f"/vsicurl/{TILE}") as src:
    print("CRS:", src.crs, "res:", src.res, "bounds:", src.bounds)
    xs, ys = transform("EPSG:4326", src.crs, [lon0], [lat0])
    x, y = xs[0], ys[0]
    print(f"anchor в {src.crs}: {x:.1f}, {y:.1f}")
    # точечный сэмпл 3x3
    r, c = src.index(x, y)
    win = rasterio.windows.Window(c - 2, r - 2, 5, 5)
    patch = src.read(1, window=win).astype(float)
    patch[patch < -1000] = np.nan
    h_pt = float(np.nanmedian(patch))
    print(f"h(anchor) 3x3 med: {h_pt:.3f} м")
    # окрестности 100/300/500 м (консервативные квантили)
    stats = {}
    for R in (100, 300, 500):
        rp = R / 2.0  # 2 м пиксель
        win = rasterio.windows.Window(c - rp, r - rp, 2 * rp, 2 * rp)
        a = src.read(1, window=win).astype(float)
        a[a < -1000] = np.nan
        q = np.nanpercentile(a, [10, 25, 50, 75, 90])
        stats[R] = dict(min=round(float(np.nanmin(a)), 2),
                        p10=round(float(q[0]), 2), p25=round(float(q[1]), 2),
                        med=round(float(q[2]), 2), p75=round(float(q[3]), 2),
                        p90=round(float(q[4]), 2),
                        max=round(float(np.nanmax(a)), 2))
        print(f"R={R}м: {stats[R]}")
    out = dict(tile="32_73_2_1", version="v4.1-2m",
               anchor=dict(lat=lat0, lon=lon0, h_point=round(h_pt, 3)),
               stats=stats)
    json.dump(out, open("results/dem_sample_dunayskiy_5.69.json", "w"), indent=1)
    print("OK: results/dem_sample_dunayskiy_5.69.json")
