#!/usr/bin/env python3
"""5.83-prep: пересборка bareearth_bs77_cm_5.71.tif в новой среде.

Канон 5.71 (results/consensus_dtm_5.71.md):
  - сетка 59.55-60.30N, 28.35-31.05E, 30 м, -tap -> 9721x2701, EPSG:4326;
  - консенсус = FABDEM над РФ (GEDTM30 покрывает 0.82% -> член не вносит
    вклад; шаблон консенсуса сохранён в consensus_dtm_5.71.py);
  - датум: EGM2008 ~= БС-77, offset consensus -> БС-77 = +0.008 м;
  - артефакт: Int16 СМ, nodata -32768, ~1.86% акватория/пустые.
Валидация пересборки: offset/MAD/corr против icesat2_clean должны
воспроизвести строку FABDEM таблицы 5.71 (+0.010 / 0.344 / 0.9991).
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

# автодетект корня репо (портация 5.78); средонезависимо
REPO = str(Path(__file__).resolve().parents[1])
WORK = f"{REPO}/scripts/data/consensus_dtm_5.71"
os.makedirs(WORK, exist_ok=True)

BBOX = [28.35, 59.55, 31.05, 60.30]
TE_STR = f"{BBOX[0]} {BBOX[1]} {BBOX[2]} {BBOX[3]}"
TR = "0.0002777777778"
OFFSET_BS77 = 0.008          # consensus -> БС-77 (канон 5.71, §3)

FAB = [
    ("N59E028", "N50E020-N60E030"), ("N60E028", "N60E020-N70E030"),
    ("N59E029", "N50E020-N60E030"), ("N60E029", "N60E020-N70E030"),
    ("N59E030", "N50E030-N60E040"), ("N60E030", "N60E030-N70E040"),
]
GLO30 = [f"N{n}_00_E{e:03d}_00" for n in (59, 60) for e in (28, 29, 30)]


def fabdem_url(tile, group):
    return (f"https://huggingface.co/buckets/links-ads/fabdem/resolve/tiles/"
            f"{group}_FABDEM_V1-2/{tile}_FABDEM_V1-2.tif")


def glo30_url(t):
    return (f"https://copernicus-dem-30m.s3.amazonaws.com/"
            f"Copernicus_DSM_COG_10_{t}_DEM/Copernicus_DSM_COG_10_{t}_DEM.tif")


def download(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 10000:
        print(f"  skip {os.path.basename(dst)}", flush=True)
        return
    print(f"  GET {os.path.basename(dst)}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dst, "wb") as f:
        while True:
            c = r.read(1 << 20)
            if not c:
                break
            f.write(c)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}...: {r.stderr[-400:]}")
    return r


def main():
    print("== 1. FABDEM V1.2 tiles ==", flush=True)
    for tile, group in FAB:
        download(fabdem_url(tile, group), f"{WORK}/fab_{tile}.tif")
    print("== 2. GLO-30 tiles ==", flush=True)
    for t in GLO30:
        download(glo30_url(t), f"{WORK}/glo_{t}.tif")

    print("== 3. FABDEM mosaic -> grid ==", flush=True)
    if not os.path.exists(f"{WORK}/fabdem_4326.tif"):
        run(["gdalbuildvrt", "-overwrite", f"{WORK}/fabdem.vrt"] +
            [f"{WORK}/fab_{t}.tif" for t, _ in FAB])
        run(["gdalwarp", "-overwrite", "-q", "-ot", "Float32", "-r", "bilinear",
             "-te", *TE_STR.split(), "-tr", TR, TR, "-tap",
             f"{WORK}/fabdem.vrt", f"{WORK}/fabdem_4326.tif"])

    print("== 4. GLO-30 mosaic -> grid ==", flush=True)
    if not os.path.exists(f"{WORK}/glo30_4326.tif"):
        run(["gdalbuildvrt", "-overwrite", f"{WORK}/glo30.vrt"] +
            [f"{WORK}/glo_{t}.tif" for t in GLO30])
        run(["gdalwarp", "-overwrite", "-q", "-ot", "Float32", "-r", "bilinear",
             "-te", *TE_STR.split(), "-tr", TR, TR, "-tap",
             f"{WORK}/glo30.vrt", f"{WORK}/glo30_4326.tif"])

    print("== 5. bareearth_bs77_cm_5.71 ==", flush=True)
    with rasterio.open(f"{WORK}/fabdem_4326.tif") as s:
        fab = s.read(1).astype("float64")
        nd = s.nodata
        tr0, crs0 = s.transform, s.crs
        GH, GW = s.shape
    fab[fab == nd] = np.nan
    fab[fab < -1000] = np.nan
    print(f"  fabdem_4326: {GH}x{GW}, nan={np.isnan(fab).mean()*100:.2f}%")

    # консенсус над РФ = FABDEM (GEDTM 0.82%); датум +0.008 -> БС-77
    bare = fab + OFFSET_BS77
    cm = np.where(np.isfinite(bare), np.round(bare * 100.0), -32768).astype("int16")
    prof = dict(driver="GTiff", width=GW, height=GH, count=1, dtype="int16",
                crs=crs0, transform=tr0, compress="deflate", nodata=-32768)
    with rasterio.open(f"{WORK}/bareearth_bs77_cm_5.71.tif", "w", **prof) as d:
        d.write(cm, 1)
    print(f"  bareearth: nodata {np.mean(cm == -32768)*100:.2f}%")

    print("== 6. валидация vs icesat2_clean ==", flush=True)
    df = pd.read_csv(f"{REPO}/scripts/data/icesat2_clean.csv")
    rows, cols = rasterio.transform.rowcol(tr0, df["lon"].values, df["lat"].values)
    rows = np.clip(rows, 0, GH - 1)
    cols = np.clip(cols, 0, GW - 1)
    v = fab[rows, cols]                       # без сдвига, как в каноне таблицы
    hs = df["h_icesat_baltic"].values
    ok = np.isfinite(v) & np.isfinite(hs)
    dH = hs[ok] - v[ok]
    off = float(np.median(dH))
    resid = dH - off
    res = {
        "n": int(ok.sum()),
        "offset_to_bs77": round(off, 3),
        "mad_after": round(float(np.median(np.abs(resid))), 3),
        "corr": round(float(np.corrcoef(hs[ok], v[ok])[0, 1]), 4),
        "nodata_pct": round(float(np.mean(cm == -32768) * 100), 2),
        "grid": [GH, GW],
        "canonical_571": {"fab_offset": 0.010, "fab_mad": 0.344, "fab_corr": 0.9991,
                          "consensus_offset": 0.008},
    }
    json.dump(res, open(f"{WORK}/rebuild_validation_5.83.json", "w"), indent=1)
    print(json.dumps(res, indent=1))
    ok_canon = (abs(res["offset_to_bs77"] - 0.010) < 0.03 and
                abs(res["mad_after"] - 0.344) < 0.02 and
                res["corr"] > 0.998)
    print("REBUILD", "OK (воспроизводит канон 5.71)" if ok_canon else "РАСХОЖДЕНИЕ!")
    sys.exit(0 if ok_canon else 2)


if __name__ == "__main__":
    main()
