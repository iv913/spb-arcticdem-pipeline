#!/usr/bin/env python3
"""5.86e — Полный ATL06-дамп (SlideRule) по bbox мозаики 5.84 = «снятие большего
количества точек» тем же протоколом: сегменты 40м/шаг 20м, cnf HIGH, land.

Протокол канона 5.68-C: |dH| <= 3.0 м -> clean.
Высота: h_baltic = h_mean(эллипсоид) − 16.04 (δ канона ICESat-2 5.68).
Выход: scripts/data/icesat2_atl06_dump_5.86.csv, results/dump_5.86.json
"""
import numpy as np
import pandas as pd
import rasterio, json, os, time, glob
from scipy.spatial import cKDTree

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
TRUST = f"{R}/download/mosaic_5.84/trust_mask_2m_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"
DELTA_BALTIC = 16.04

ds = rasterio.open(DEM); dstr = rasterio.open(TRUST); dsq = rasterio.open(QUAL)
T, Ts, Tq = ds.transform, dstr.transform, dsq.transform


def sample2(lats, lons, src, T, band=1, nd=None, scale=1.0):
    vals = np.full(len(lats), np.nan)
    lon = np.asarray(lons, float); lat = np.asarray(lats, float)
    cols = np.floor((lon - T.c) / T.a).astype(int)
    rows = np.floor((lat - T.f) / T.e).astype(int)
    H, W = src.height, src.width
    inside = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    if inside.any():
        rr, cc = rows[inside], cols[inside]
        win = rasterio.windows.Window(int(cc.min()), int(rr.min()),
                                      int(cc.max()-cc.min()+1), int(rr.max()-rr.min()+1))
        arr = src.read(band, window=win)
        v = arr[rr-rr.min(), cc-cc.min()]
        good = np.ones_like(v, bool) if nd is None else (v != nd)
        vals[inside] = np.where(good, v.astype(float) * scale, np.nan)
    return vals


# ---------- SlideRule dump по тайлам ----------
from sliderule import icesat2
icesat2.init("slideruleearth.io", verbose=False)

TILEDIR = f"{R}/scripts/dem_cache/atl06_tiles"
os.makedirs(TILEDIR, exist_ok=True)

lon0, lon1 = 29.36, 30.56
lat0, lat1 = 59.8905, 60.1183
dx, dy = 0.12, 0.114
tiles = []
x = lon0
while x < lon1 - 1e-9:
    y = lat0
    while y < lat1 - 1e-9:
        tiles.append((x, y, min(x + dx, lon1), min(y + dy, lat1)))
        y += dy
    x += dx
print(f"тайлов: {len(tiles)}")

parm_base = dict(t0="2019-01-01", t1="2026-12-31", srt=icesat2.SRT_LAND,
                 cnf=icesat2.CNF_SURFACE_HIGH, ats=10.0, cnt=5, len=40.0,
                 res=20.0, maxi=1)

# --- возобновляемо: чанк на тайл, существующие пропускаем ---
LIMIT = int(os.environ.get("TILE_LIMIT", "999"))
new_cnt = 0
t_start = time.time()
for i, (x0, y0, x1, y1) in enumerate(tiles):
    fp = f"{TILEDIR}/tile_{i:02d}.csv"
    if os.path.exists(fp):
        continue
    if new_cnt >= LIMIT:
        print(f"лимит {LIMIT} новых тайлов за прогон достигнут — перезапустить")
        break
    parm = dict(parm_base)
    parm["poly"] = [{"lon": x0, "lat": y0}, {"lon": x1, "lat": y0},
                    {"lon": x1, "lat": y1}, {"lon": x0, "lat": y1}]
    gdf = None
    for att in range(2):
        try:
            gdf = icesat2.atl06p(parm)
            break
        except Exception as e:
            print(f"  [{i+1}] retry {att+1}: {str(e)[:70]}")
            time.sleep(3)
    if gdf is not None and len(gdf):
        g = gdf.geometry
        pd.DataFrame(dict(
            lat=g.y.values, lon=g.x.values, h_mean=gdf.h_mean.values,
            h_sigma=gdf.h_sigma.values, n_fit=gdf.n_fit_photons.values,
            time_ns=gdf.index.astype("int64").values, rgt=gdf.rgt.values,
            cycle=gdf.cycle.values, gt=gdf["gt"].values)).to_csv(fp, index=False)
    else:
        pd.DataFrame(columns=["lat", "lon", "h_mean", "h_sigma", "n_fit", "time_ns", "rgt", "cycle", "gt"]).to_csv(fp, index=False)
    new_cnt += 1
    print(f"  [{i+1}/{len(tiles)}] {x0:.2f}/{y0:.2f}: {0 if gdf is None else len(gdf)} seg (t={time.time()-t_start:.0f}s)", flush=True)

files = sorted(glob.glob(f"{TILEDIR}/tile_*.csv"))
if len(files) < len(tiles):
    print(f"готово {len(files)}/{len(tiles)} тайлов — продолжение при следующем запуске")
    raise SystemExit(0)

dump = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
dump["h_baltic"] = dump.h_mean - DELTA_BALTIC
dump["dem2m"] = sample2(dump.lat.values, dump.lon.values, ds, T, nd=-32768, scale=0.01)
dump["trust"] = sample2(dump.lat.values, dump.lon.values, dstr, Ts)
dump["n_cov"] = sample2(dump.lat.values, dump.lon.values, dsq, Tq, band=1, nd=65535)
dump["sigma_cm"] = sample2(dump.lat.values, dump.lon.values, dsq, Tq, band=2, nd=65535)
dump["canopy30_cm"] = sample2(dump.lat.values, dump.lon.values, dsq, Tq, band=3, nd=65535)
dump["dH2m"] = dump.h_baltic - dump.dem2m
print("\ndump raw:", len(dump), "| covered by mosaic:", dump.dH2m.notna().sum())

# ---------- фильтр 5.68-C + флаги 5.86 ----------
dump["qa_flags"] = ""
cov = dump.dH2m.notna()
dump.loc[cov & (dump.dH2m.abs() > 3.0), "qa_flags"] = "OUT3"
dump.loc[cov & (dump.canopy30_cm > 50), "qa_flags"] += " CANOPY"
dump.loc[cov & (dump.sigma_cm > 50), "qa_flags"] += " SIGMA"
dump.loc[cov & (dump.dH2m.abs() > 2.0) & (dump.dH2m.abs() <= 3.0), "qa_flags"] += " LEAK2RISK"
dump["qa_flags"] = dump.qa_flags.str.strip()
clean = dump[cov & (dump.qa_flags == "")].copy()
print("clean после 5.68-C(|dH|<=3)+canopy+sigma:", len(clean))

# ---------- дедуп против существующего канона ----------
ic_old = pd.read_csv(f"{R}/scripts/data/icesat2_clean.csv")
tree_old = cKDTree(np.c_[ic_old.lat.values * 111320, ic_old.lon.values * 111320 * 0.55])
pts_new = np.c_[clean.lat.values * 111320, clean.lon.values * 111320 * 0.55]
dist, _ = tree_old.query(pts_new, k=1)
clean["dup_old_10m"] = dist < 10.0
print("пересекается с каноном (<10м):", clean.dup_old_10m.sum(),
      "| новых:", (~clean.dup_old_10m).sum())

dump.to_csv(f"{R}/scripts/data/icesat2_atl06_dump_5.86.csv", index=False)

# ---------- районы ----------
def metrics(dh):
    dh = dh.dropna()
    if not len(dh):
        return dict(n=0)
    return dict(n=int(len(dh)), med=round(float(dh.median()), 3),
                mad=round(float((dh - dh.median()).abs().median()), 3),
                p95=round(float(dh.abs().quantile(0.95)), 3))

# 7 нулевых районов (bbox по 5.85 §4.1)
dz = {
    "Кировский":        (59.83, 30.14, 59.90, 30.32),
    "Красносельский":   (59.79, 30.10, 59.88, 30.30),
    "Пушкинский":       (59.70, 30.22, 59.80, 30.45),
    "Колпинский":       (59.70, 30.42, 59.80, 30.62),
    "Петродворцовый":   (59.84, 29.88, 59.93, 30.13),
    "Кронштадтский":    (59.95, 29.60, 60.02, 29.80),
    "Курортный":        (60.09, 29.85, 60.20, 30.30),
}
per_d = {}
for name, (a, b, c, d) in dz.items():
    m = clean[(clean.lat >= a) & (clean.lat < c) & (clean.lon >= b) & (clean.lon < d)]
    per_d[name] = dict(new=int((~m.dup_old_10m).sum()), total=int(len(m)),
                       mad=round(float((m.dH2m - m.dH2m.median()).abs().median()), 3) if len(m) else None)

summary = dict(
    raw_segments=int(len(dump)),
    covered=int(cov.sum()),
    clean_568C=int(len(clean)),
    new_beyond_canon=int((~clean.dup_old_10m).sum()),
    old_canon_n=int(len(ic_old)),
    metrics_dump_clean=metrics(clean.dH2m),
    metrics_dump_new=metrics(clean[~clean.dup_old_10m].dH2m),
    metrics_dump_clean_trust=metrics(clean[clean.trust == 1].dH2m),
    zero_districts=per_d,
    delta_baltic=DELTA_BALTIC, protocol="atl06p len=40 res=20 cnf=HIGH srt=LAND, clean: |dH2m|<=3 & canopy<=50 & sigma<=50",
)
json.dump(summary, open(f"{R}/results/dump_5.86.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1)[:1500])
print("\nsaved dump_5.86.json")
