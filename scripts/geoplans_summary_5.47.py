#!/usr/bin/env python3
"""5.47: сводная валидация geoplans (datum-check vs Copernicus GLO-30).

Для каждого плана: отметки (VLM, kind=ground) -> медиана; GLO-30 в точке
проекта -> медиана высоты; dH = GLO30 - marks (GLO-30 грубая: лес/крыши
в пикселе, ожидаемо +0..+2 м). Плюс внутренняя согласованность |h1-h2|
для rel-близких пар.

Выход: results/archi_geoplans_summary_5.47.json
"""
import json
import math
import os

import numpy as np
import rasterio
import rasterio.warp as rw

REPO = "/home/z/my-project/spb45"
EXT = "/home/z/my-project/wr/archi3/extract"
GLO30_TILES = {
    (59, 30): "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N59_00_E030_00_DEM/Copernicus_DSM_COG_10_N59_00_E030_00_DEM.tif",
    (60, 29): "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N60_00_E029_00_DEM/Copernicus_DSM_COG_10_N60_00_E029_00_DEM.tif",
    (60, 30): "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N60_00_E030_00_DEM/Copernicus_DSM_COG_10_N60_00_E030_00_DEM.tif",
}

# проектные точки (из archi_catalog_gallery_5.47.json / пополнено вручную)
PROJ = {
    "314154.jpg": (59.872340, 30.469802),
    "448114.jpg": (59.864641, 30.502834),
    "456577.jpg": (59.921389, 30.287699),
    "422695.jpg": (59.930571, 30.234707),
    "257941.jpg": (59.977440, 30.228290),
    "493731.jpg": (59.850605, 30.304640),
    "456638.jpg": (59.931591, 30.378868),
    "317703.jpg": (60.030858, 30.320734),
    "434384.jpg": (59.924408, 30.275709),
    "465048.jpg": (60.149544, 29.932849),
    "465070.jpg": (60.149544, 29.932849),
    "483622.jpg": (None, None),
    "428282.jpg": (59.947769, 30.384216),
    "424159.jpg": (59.931591, 30.378868),
    "456598.jpg": (59.921389, 30.287699),
    "515977.jpg": (59.855813, 30.320885),
}

_ds_cache = {}


def glo30(lat, lon):
    key = (int(math.floor(lat)), int(math.floor(lon)))
    if key not in GLO30_TILES:
        return None
    if key not in _ds_cache:
        _ds_cache[key] = rasterio.open(GLO30_TILES[key])
    ds = _ds_cache[key]
    xs, ys = rw.transform("EPSG:4326", ds.crs, [lon], [lat])
    return float(next(ds.sample([(xs[0], ys[0])]))[0])


def main():
    summ = json.load(open(f"{REPO}/results/archi_vlm_extract_5.47.json"))
    out = []
    for img, meta in summ.items():
        lat, lon = PROJ.get(img, (None, None))
        rec = {"img": img, "n_marks": meta.get("n_marks", 0),
               "n_ground": meta.get("n_ground", 0),
               "streets": meta.get("streets", []), "proj_lat": lat, "proj_lon": lon}
        cache = os.path.join(EXT, img.rsplit(".", 1)[0] + ".json")
        marks = []
        if os.path.exists(cache):
            p = json.load(open(cache))
            for m in p.get("marks", []):
                try:
                    v = float(m["value"])
                except (TypeError, ValueError):
                    continue
                if 0.3 <= v <= 80 and m.get("kind") == "ground":
                    marks.append({"v": v, "x": m.get("x"), "y": m.get("y")})
        rec["marks_ground"] = len(marks)
        if marks:
            vals = [m["v"] for m in marks]
            rec["median_mark"] = round(float(np.median(vals)), 2)
            rec["min"], rec["max"] = min(vals), max(vals)
            # внутренняя согласованность в rel-пространстве (пары ближе 0.05)
            pairs = []
            for i in range(len(marks)):
                for j in range(i + 1, len(marks)):
                    xi, yi = marks[i]["x"], marks[i]["y"]
                    xj, yj = marks[j]["x"], marks[j]["y"]
                    if xi is None or xj is None:
                        continue
                    if math.hypot(xi - xj, yi - yj) < 0.05:
                        pairs.append(abs(marks[i]["v"] - marks[j]["v"]))
            if pairs:
                rec["consistency_median_m"] = round(float(np.median(pairs)), 2)
                rec["n_pairs"] = len(pairs)
        if lat:
            g = glo30(lat, lon)
            rec["glo30_at_site"] = round(g, 2) if g else None
            if marks and g:
                rec["datum_dH_glo30"] = round(g - rec["median_mark"], 2)
        out.append(rec)
    json.dump(out, open(f"{REPO}/results/archi_geoplans_summary_5.47.json", "w"),
              ensure_ascii=False, indent=1)
    for r in out:
        line = f"{r['img']:14s} ground={r['marks_ground']:3d}"
        if "median_mark" in r:
            line += f" median={r['median_mark']:6.2f} [{r['min']}-{r['max']}]"
        if "consistency_median_m" in r:
            line += f" cons={r['consistency_median_m']:.2f}м(n={r['n_pairs']})"
        if r.get("datum_dH_glo30") is not None:
            line += f" dH_GLO30={r['datum_dH_glo30']:+.2f}"
        if r.get("streets"):
            line += f"  | {', '.join(r['streets'][:3])}"
        print(line)


if __name__ == "__main__":
    main()
