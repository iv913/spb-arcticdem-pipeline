#!/usr/bin/env python3
"""5.86b — Рабочий канон «снятых точек» (archi+atayanc+passport+514913) vs 2м-мозаика.
Сверка медиан против якорей 5.66. Выход: scripts/data/canon2m_5.86.csv,
пополнение results/worst_points_5.86.json."""
import numpy as np
import pandas as pd
import rasterio, json, os

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
TRUST = f"{R}/download/mosaic_5.84/trust_mask_2m_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"

ds = rasterio.open(DEM); dstr = rasterio.open(TRUST)
dsq = rasterio.open(QUAL) if os.path.exists(QUAL) else None
T, Ts = ds.transform, dstr.transform


def sample2(lats, lons, src, T, band=1, nd=None):
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
        vals[inside] = np.where(good, v.astype(float), np.nan)
    return vals


parts = []
a = pd.read_csv(f"{R}/scripts/data/archi_final_expanded_v2.csv")
parts.append(pd.DataFrame(dict(lat=a.lat, lon=a.lon, z_survey=a.value,
                               source="archi_" + a.project_name.astype(str).str.slice(0, 40))))
b = pd.read_csv(f"{R}/scripts/data/atayanc_all_marks_raw.csv")
parts.append(pd.DataFrame(dict(lat=b.lat, lon=b.lon, z_survey=b.z_survey, source="atayanc_vo")))
p = pd.read_csv(f"{R}/scripts/data/passport_split.csv")
parts.append(pd.DataFrame(dict(lat=p.lat, lon=p.lon, z_survey=p.hp, source="passport_" + p.role)))
f = pd.read_csv(f"{R}/scripts/data/FINAL_CONSOLIDATED_v3.csv")
v = f[f.source == "archi_ru_514913_VLM"]
parts.append(pd.DataFrame(dict(lat=v.lat, lon=v.lon, z_survey=v.z_survey, source="archi_514913_VLM")))
canon = pd.concat(parts, ignore_index=True)
canon = canon.dropna(subset=["lat", "lon", "z_survey"])
print("canon working set:", len(canon))

canon["h_dem2m"] = sample2(canon.lat.values, canon.lon.values, ds, T, nd=-32768) / 100.0
canon["trust"] = sample2(canon.lat.values, canon.lon.values, dstr, Ts)
if dsq is not None:
    Tq = dsq.transform
    n = sample2(canon.lat.values, canon.lon.values, dsq, Tq, band=1, nd=65535)
    sg = sample2(canon.lat.values, canon.lon.values, dsq, Tq, band=2, nd=65535)
    cn = sample2(canon.lat.values, canon.lon.values, dsq, Tq, band=3, nd=65535)
    canon["n_cov"], canon["sigma_cm"], canon["canopy30_cm"] = n, sg, cn
canon["dH2m"] = canon.z_survey - canon.h_dem2m
ok = canon.dH2m.notna()
print(f"covered: {ok.sum()}/{len(canon)} ({ok.mean()*100:.0f}%)")
s = canon[ok]
print(f"canon vs 2м: n={len(s)} med={s.dH2m.median():+.2f} MAD={(s.dH2m-s.dH2m.median()).abs().median():.2f}")

canon.to_csv(f"{R}/scripts/data/canon2m_5.86.csv", index=False)

# Сверка против якорей 5.66
anchors = {
    "Коломяги АНО ФК": (60.0181, 30.3023), "ЖК Тайм Сквер": (60.0343, 30.1947),
    "Дом на Кубинской": (59.8391, 30.3023), "МФК Электростанция № 2": (59.9347, 30.3883),
    "Деловой центр": (59.8525, 30.3044), "Дом на Двинской": (59.9087, 30.2524),
    "Дом на Заневском пр.": (59.9295, 30.4143), "ЖК iLona": (59.9678, 30.3520),
    "БЦ на Поклонной горе": (59.9387, 30.3162), "Гербарий": (59.9706, 30.2892),
}
print("\nСверка медиан против 5.66 (радиус ~300м):")
res = {}
for name, (alat, alon) in anchors.items():
    near = s[((s.lat-alat).abs() < 0.0027) & ((s.lon-alon).abs() < 0.0054)]
    if len(near):
        res[name] = dict(n=int(len(near)), med=round(float(near.dH2m.median()), 2),
                         mad=round(float((near.dH2m-near.dH2m.median()).abs().median()), 2))
        print(f"  {name:28s} n={len(near):3d} med={near.dH2m.median():+6.2f} MAD={(near.dH2m-near.dH2m.median()).abs().median():.2f}")

# Худшие кластеры канона
s2 = s.copy()
cell = 0.0015
s2["gx"] = (s2.lon/cell).astype(int); s2["gy"] = (s2.lat/cell).astype(int)
grp = s2.groupby(["gx","gy"]).agg(n=("dH2m","size"), med=("dH2m","median"), lat=("lat","mean"), lon=("lon","mean")).reset_index()
grp = grp[grp.n >= 4].sort_values("med", key=lambda x: x.abs(), ascending=False).head(25)
print("\nTop canon clusters (n>=4):")
for _, r in grp.iterrows():
    proj = s2[(s2.gx==r.gx)&(s2.gy==r.gy)].source.mode().iloc[0]
    print(f"  ({r.lat:.4f},{r.lon:.4f}) n={int(r.n):3d} med={r.med:+6.2f}  {proj[:45]}")

json.dump(res, open(f"{R}/results/canon_anchor_check_5.86.json","w"), ensure_ascii=False, indent=1)
