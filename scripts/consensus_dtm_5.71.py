#!/usr/bin/env python3
"""5.71: Консенсусный bare-earth DTM для СПб (FABDEM V1.2 + GEDTM30).

Сетка: 59.55-60.30N, 28.35-31.05E (весь СПб: Кронштадт 28.7E, Зеленогорск 60.2N),
30 м (0.0002777..°), WGS84. GLO-30 (DSM) не член консенсуса — только baseline
для сравнения эффекта снятия канопи. DeltaDTM отложен (4TU в maintenance).
Этапы: --download, --build, --calibrate. Артефакты: scripts/data/consensus_dtm_5.71/
"""
import json
import os
import subprocess
import sys
import urllib.request

WORK = "/home/z/my-project/repo/scripts/data/consensus_dtm_5.71"
REPO = "/home/z/my-project/repo"
os.makedirs(WORK, exist_ok=True)

BBOX = [28.35, 59.55, 31.05, 60.30]          # W, S, E, N
TR = 0.0002777777778                          # 30 м
W = round((BBOX[2] - BBOX[0]) / TR)           # 9720
H = round((BBOX[3] - BBOX[1]) / TR)           # 2700
TE_STR = f"{BBOX[0]} {BBOX[1]} {BBOX[2]} {BBOX[3]}"

FAB = [  # (tile, group) — группы проверены HEAD 200
    ("N59E028", "N50E020-N60E030"), ("N60E028", "N60E020-N70E030"),
    ("N59E029", "N50E020-N60E030"), ("N60E029", "N60E020-N70E030"),
    ("N59E030", "N50E030-N60E040"), ("N60E030", "N60E030-N70E040"),
]
GLO30 = [f"N{n}_00_E{e}_00" for n in (59, 60) for e in (28, 29, 30)]
GEDTM_URL = ("https://s3.ecodatacube.eu/arco/gedtm_rf_m_30m_s_"
             "20060101_20151231_eu_epsg.3035_v20250318.tif")


def fabdem_url(tile, group):
    return (f"https://huggingface.co/buckets/links-ads/fabdem/resolve/tiles/"
            f"{group}_FABDEM_V1-2/{tile}_FABDEM_V1-2.tif")


def glo30_url(t):
    return (f"https://copernicus-dem-30m.s3.amazonaws.com/"
            f"Copernicus_DSM_COG_10_{t}_DEM/Copernicus_DSM_COG_10_{t}_DEM.tif")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}...: {r.stderr[-400:]}")
    return r


def download(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 10000:
        print(f"  skip {os.path.basename(dst)}")
        return
    print(f"  GET {os.path.basename(dst)}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dst, "wb") as f:
        while True:
            c = r.read(1 << 20)
            if not c:
                break
            f.write(c)


def stage_download():
    print("== 1. FABDEM V1.2 tiles ==")
    for tile, group in FAB:
        download(fabdem_url(tile, group), f"{WORK}/fab_{tile}.tif")
    print("== 2. GLO-30 tiles (baseline) ==")
    for t in GLO30:
        download(glo30_url(t), f"{WORK}/glo_{t}.tif")
    print("done")


def stage_build():
    # 3. GEDTM30: окно из общеевропейского COG (EPSG:3035) -> наша сетка 4326
    print("== 3. GEDTM30 window ==")
    if not os.path.exists(f"{WORK}/gedtm_4326.tif"):
        run(["gdalwarp", "-overwrite", "-q", "-ot", "Float32", "-r", "bilinear",
             "-t_srs", "EPSG:4326", "-te", *TE_STR.split(), "-tr", str(TR), str(TR),
             "-tap", "-multi", f"/vsicurl/{GEDTM_URL}", f"{WORK}/gedtm_4326.tif"])
    # 4. FABDEM: vrt из 6 тайлов -> та же сетка
    print("== 4. FABDEM mosaic ==")
    if not os.path.exists(f"{WORK}/fabdem_4326.tif"):
        run(["gdalbuildvrt", "-overwrite", f"{WORK}/fabdem.vrt"] +
            [f"{WORK}/fab_{t}.tif" for t, _ in FAB])
        run(["gdalwarp", "-overwrite", "-q", "-ot", "Float32", "-r", "bilinear",
             "-te", *TE_STR.split(), "-tr", str(TR), str(TR), "-tap",
             f"{WORK}/fabdem.vrt", f"{WORK}/fabdem_4326.tif"])
    # 5. GLO-30: vrt -> та же сетка
    print("== 5. GLO-30 mosaic ==")
    if not os.path.exists(f"{WORK}/glo30_4326.tif"):
        run(["gdalbuildvrt", "-overwrite", f"{WORK}/glo30.vrt"] +
            [f"{WORK}/glo_{t}.tif" for t in GLO30])
        run(["gdalwarp", "-overwrite", "-q", "-ot", "Float32", "-r", "bilinear",
             "-te", *TE_STR.split(), "-tr", str(TR), str(TR), "-tap",
             f"{WORK}/glo30.vrt", f"{WORK}/glo30_4326.tif"])
    # 6. Консенсус: осмысленное среднее FABDEM+GEDTM30 (nodata-aware, через numpy)
    print("== 6. consensus ==")
    import numpy as np
    import rasterio
    arrs = {}
    for name in ("fabdem_4326", "gedtm_4326", "glo30_4326"):
        with rasterio.open(f"{WORK}/{name}.tif") as s:
            a = s.read(1).astype("float64")
            nd = s.nodata
            if nd is not None:
                a[a == nd] = np.nan
            a[a < -1000] = np.nan
            if name == "gedtm_4326":
                a *= 0.1          # дециметры -> метры (expression (data)*0.1)
            arrs[name] = a
            print(f"  {name}: min={np.nanmin(a):.2f} max={np.nanmax(a):.2f} "
                  f"nan={np.isnan(a).mean()*100:.1f}%")
    cons = np.nanmean(np.stack([arrs["fabdem_4326"], arrs["gedtm_4326"]]), axis=0)
    prof = dict(driver="GTiff", width=W, height=H, count=1, dtype="float32",
                crs="EPSG:4326", transform=rasterio.transform.from_origin(
                    BBOX[0], BBOX[3], TR, TR), compress="deflate", nodata=-9999.0)
    with rasterio.open(f"{WORK}/consensus_fab_gedtm_4326.tif", "w", **prof) as d:
        d.write(cons.astype("float32"), 1)
    np.save(f"{WORK}/consensus.npy", cons.astype("float32"))
    print(f"  consensus: min={np.nanmin(cons):.2f} max={np.nanmax(cons):.2f}")
    print("done")


def stage_calibrate():
    """Эмпирическая привязка датума + зонная валидация на icesat2_clean."""
    print("== 7. calibration vs icesat2_clean ==")
    import numpy as np
    import pandas as pd
    import rasterio
    df = pd.read_csv(f"{REPO}/scripts/data/icesat2_clean.csv")
    print(f"  icesat2_clean: {len(df)} pts, cols: {list(df.columns)[:8]}")
    lats = df["lat"].values
    lons = df["lon"].values
    hs = df["h_icesat_baltic"].values
    srcs = {n: rasterio.open(f"{WORK}/{n}.tif")
            for n in ("fabdem_4326", "gedtm_4326", "glo30_4326")}
    cons = np.load(f"{WORK}/consensus.npy")
    with rasterio.open(f"{WORK}/consensus_fab_gedtm_4326.tif") as s:
        tr0 = s.transform
    rows, cols = rasterio.transform.rowcol(tr0, lons, lats)
    rows = np.clip(rows, 0, H - 1)
    cols = np.clip(cols, 0, W - 1)
    ok_bbox = ((lons >= BBOX[0]) & (lons <= BBOX[2]) &
               (lats >= BBOX[1]) & (lats <= BBOX[3]) & ~np.isnan(hs))
    print(f"  in-bbox: {ok_bbox.sum()}")
    res = {"n_bbox": int(ok_bbox.sum()), "dem_offsets_bs77": {}, "zone_stats": {}}
    samples = {}
    for name, s in srcs.items():
        v = s.read(1).astype("float64")[rows[ok_bbox], cols[ok_bbox]]
        v[(v < -1000)] = np.nan
        samples[name] = v
    v = cons[rows[ok_bbox], cols[ok_bbox]].astype("float64")
    v[v < -1000] = np.nan
    samples["consensus"] = v
    for name, v in samples.items():
        dH = hs[ok_bbox] - v
        off = np.nanmedian(dH)
        res["dem_offsets_bs77"][name] = round(float(off), 3)
        resid = dH - off
        res["dem_offsets_bs77"][name + "_MAD_after"] = round(
            float(np.nanmedian(np.abs(resid))), 3)
        res["dem_offsets_bs77"][name + "_corr"] = round(float(
            np.corrcoef(hs[ok_bbox][~np.isnan(v)], v[~np.isnan(v)])[0, 1]), 4)
        dfx = pd.DataFrame({"dH": dH, "resid": resid, "lat": lats[ok_bbox],
                            "lon": lons[ok_bbox]}).dropna()
        dfx.to_csv(f"{WORK}/calib_{name}.csv", index=False)
    # GLO-30 в 5.67 считался с моделью M-CLF; здесь прямое сравнение с DEM
    json.dump(res, open(f"{WORK}/calib_summary_5.71.json", "w"), indent=1)
    print(json.dumps(res, indent=1))
    print("done")


if __name__ == "__main__":
    stages = sys.argv[1:] or ["download", "build", "calibrate"]
    if "download" in stages:
        stage_download()
    if "build" in stages:
        stage_build()
    if "calibrate" in stages:
        stage_calibrate()
