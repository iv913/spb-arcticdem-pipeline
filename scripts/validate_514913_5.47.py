#!/usr/bin/env python3
"""5.47 A5: валидация georef-марок 514913.

1. Внутренняя согласованность: |h1-h2| для пар ближе 30 м
   (5.46-критерий: 0.18 м по 71 марке — извлечение верное)
2. Санити dH vs Copernicus GLO-30 (грубо: город, деревья/крыши в пикселе;
   ожидаемо хуже 2m-грида, но ловит грубые ошибки >5 м)
3. Сравнение dH старых 77 марок с peer's h_model (воспроизводимость)

Выход: results/archi_514913_validation_5.47.json
"""
import csv
import json
import math

import numpy as np
import rasterio
import rasterio.warp as rw

REPO = "/home/z/my-project/spb45"
GLO30 = ("https://copernicus-dem-30m.s3.amazonaws.com/"
         "Copernicus_DSM_COG_10_N59_00_E030_00_DEM/"
         "Copernicus_DSM_COG_10_N59_00_E030_00_DEM.tif")


def glo30_sampler():
    ds = rasterio.open(GLO30)
    cache = {}

    def get(lat, lon):
        key = (round(lat, 5), round(lon, 5))
        if key in cache:
            return cache[key]
        xs, ys = rw.transform("EPSG:4326", ds.crs, [lon], [lat])
        v = float(next(ds.sample([(xs[0], ys[0])]))[0])
        cache[key] = v
        return v
    return get


def main():
    marks = list(csv.DictReader(open(f"{REPO}/results/archi_514913_marks_georef_5.47.csv")))
    for m in marks:
        m["val"] = float(m["val"])
        m["lat"] = float(m["lat"])
        m["lon"] = float(m["lon"])

    # 1. внутренняя согласованность
    pairs = []
    n = len(marks)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = marks[i], marks[j]
            dlat = (a["lat"] - b["lat"]) * 111320.0
            dlon = (a["lon"] - b["lon"]) * 111320.0 * math.cos(math.radians(59.83))
            dist = math.hypot(dlat, dlon)
            if dist < 30.0:
                pairs.append(abs(a["val"] - b["val"]))
    out = {"n_marks": n, "n_pairs_30m": len(pairs)}
    if pairs:
        out["consistency_median_m"] = round(float(np.median(pairs)), 3)
        out["consistency_p90_m"] = round(float(np.percentile(pairs, 90)), 3)

    # 2. dH vs GLO-30
    get = glo30_sampler()
    dh = []
    for m in marks:
        g = get(m["lat"], m["lon"])
        if g == g:
            dh.append(g - m["val"])
    out["glo30_n"] = len(dh)
    out["glo30_median_dH"] = round(float(np.median(dh)), 3)
    out["glo30_MAD_m"] = round(float(np.median(np.abs(dh - np.median(dh)))), 3)

    # 3. воспроизводимость по peer's h_model (только старые 77)
    comp = list(csv.DictReader(open(f"{REPO}/scripts/data/archi_ru_514913_comparison.csv")))
    dh_peer = [float(r["h_model"]) - float(r["elevation_m"]) for r in comp
               if r.get("h_model") not in ("", None)]
    out["peer_model_n"] = len(dh_peer)
    out["peer_model_median_dH"] = round(float(np.median(dh_peer)), 3)
    out["peer_model_MAD_m"] = round(float(np.median(np.abs(dh_peer - np.median(dh_peer)))), 3)

    json.dump(out, open(f"{REPO}/results/archi_514913_validation_5.47.json", "w"),
              indent=1, ensure_ascii=False)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
