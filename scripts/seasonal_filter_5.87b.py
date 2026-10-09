#!/usr/bin/env python3
"""5.87b — Сезонный разрез демпа ATL06 5.86e: leaf-off vs leaf-on метрики
против мозаики 5.84. Обоснование фильтра дат для «снятия большего
количества точек». Выход: results/seasonal_filter_5.87.json"""
import numpy as np
import pandas as pd
import rasterio, json, os, glob

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"
TILES = f"{R}/scripts/dem_cache/atl06_tiles"

ds = rasterio.open(DEM); dsq = rasterio.open(QUAL)
T, Tq = ds.transform, dsq.transform


def sample(lats, lons, src, T_, band=1, nodata=None, scale=1.0):
    vals = np.full(len(lats), np.nan)
    lon = np.asarray(lons, float); lat = np.asarray(lats, float)
    cols = np.floor((lon - T_.c) / T_.a).astype(int)
    rows = np.floor((lat - T_.f) / T_.e).astype(int)
    H, W = src.height, src.width
    inside = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    if inside.any():
        rr, cc = rows[inside], cols[inside]
        win = rasterio.windows.Window(int(cc.min()), int(rr.min()),
                                      int(cc.max() - cc.min() + 1),
                                      int(rr.max() - rr.min() + 1))
        arr = src.read(band, window=win)
        v = arr[rr - rr.min(), cc - cc.min()]
        good = np.ones_like(v, bool) if nodata is None else (v != nodata)
        vals[inside] = np.where(good, v.astype(float) * scale, np.nan)
    return vals


df = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{TILES}/tile_*.csv"))],
               ignore_index=True)
df["date"] = pd.to_datetime(df["time_ns"])
df["month"] = df.date.dt.month
df["h_baltic"] = df.h_mean - 16.04
df["h_model"] = sample(df.lat.values, df.lon.values, ds, T, 1, nodata=-32768, scale=0.01)
df["canopy"] = sample(df.lat.values, df.lon.values, dsq, Tq, 3, nodata=65535)
df["sigma"] = sample(df.lat.values, df.lon.values, dsq, Tq, 2, nodata=65535)
df["dH"] = df.h_baltic - df.h_model
cov = df[df.dH.notna()].copy()
print("покрыто:", len(cov), "из", len(df))

cov["season"] = np.where(cov.month.isin([11, 12, 1, 2, 3, 4]), "leaf_off", "leaf_on")


def metrics(d):
    d = d.dropna()
    return dict(n=int(len(d)), med=round(float(d.median()), 3),
                mad=round(float((d - d.median()).abs().median()), 3),
                p95=round(float(d.abs().quantile(0.95)), 3),
                share_le_05=round(float((d.abs() <= 0.5).mean()), 3))


res = {"covered_all": metrics(cov.dH),
       "leaf_off": metrics(cov[cov.season == "leaf_off"].dH),
       "leaf_on": metrics(cov[cov.season == "leaf_on"].dH)}
# канопи-срез: открытые (canopy<100см) vs кроны (>=100см)
for cap, lo, hi in (("open_canopy_lt100", 0, 100), ("canopy_ge100", 100, 1e9)):
    m = cov[(cov.canopy >= lo) & (cov.canopy < hi)]
    for ses in ("leaf_off", "leaf_on"):
        res[f"{cap}_{ses}"] = metrics(m[m.season == ses].dH)
# чистый лист: |dH|<=3 (протокол 5.68-C)
clean = cov[cov.dH.abs() <= 3]
res["clean3_all"] = metrics(clean.dH)
res["clean3_leaf_off"] = metrics(clean[clean.season == "leaf_off"].dH)
res["clean3_leaf_on"] = metrics(clean[clean.season == "leaf_on"].dH)
res["clean3_leaf_off_open"] = metrics(clean[(clean.season == "leaf_off") & (clean.canopy < 100)].dH)

json.dump(res, open(f"{R}/results/seasonal_filter_5.87.json", "w"),
          ensure_ascii=False, indent=1)
for k, v in res.items():
    print(f"{k:28s} n={v['n']:7d} med={v['med']:+.3f} MAD={v['mad']:.3f} "
          f"p95={v['p95']:.3f} share<=0.5={v['share_le_05']:.3f}")
