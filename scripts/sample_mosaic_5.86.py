#!/usr/bin/env python3
"""5.86a — Сэмпл 2м-мозаики 5.84 в точках валидации + инвентарь худших расхождений.

Вход:
  scripts/data/icesat2_clean.csv           (33 908 тчк ICESat-2, h_icesat_baltic)
  scripts/data/FINAL_CONSOLIDATED_v3.csv   (канон города 2 019, z_survey, source)
  download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif (Int16 см БС-77)
  download/mosaic_5.84/trust_mask_2m_5.84.tif        (0/1 dens>=6)

Выход:
  scripts/data/icesat2_vs_mosaic_5.86.csv
  scripts/data/canon_vs_mosaic_5.86.csv
  results/worst_points_5.86.json   (топ-кластеры + обязательные аномалии 5.66)
"""
import numpy as np
import pandas as pd
import rasterio, json, os, sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
TRUST = f"{R}/download/mosaic_5.84/trust_mask_2m_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"

ds = rasterio.open(DEM)
dstr = rasterio.open(TRUST)
dsq = rasterio.open(QUAL) if os.path.exists(QUAL) else None


def sample(lats, lons, src, band=1):
    """Сэмпл пикселя в (lat,lon); nan если вне растра/nodata. Векторно через affine."""
    vals = np.full(len(lats), np.nan)
    T = src.transform
    lon = np.asarray(lons, dtype=float); lat = np.asarray(lats, dtype=float)
    cols = np.floor((lon - T.c) / T.a).astype(int)
    rows = np.floor((lat - T.f) / T.e).astype(int)
    H, W = src.height, src.width
    inside = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    nd = src.nodata
    if inside.any():
        rr = rows[inside]; cc = cols[inside]
        win = rasterio.windows.Window(int(cc.min()), int(rr.min()),
                                      int(cc.max() - cc.min() + 1), int(rr.max() - rr.min() + 1))
        arr = src.read(band, window=win)
        rr_off = rr - rr.min(); cc_off = cc - cc.min()
        v = arr[rr_off, cc_off]
        good = np.ones_like(v, dtype=bool)
        if nd is not None:
            good = v != nd
        vals[inside] = np.where(good, v.astype(float), np.nan)
    return vals


def quality_sample(lats, lons):
    n = np.full(len(lats), np.nan); sg = n.copy(); cn = n.copy()
    if dsq is None:
        return n, sg, cn
    T = dsq.transform
    lon = np.asarray(lons, dtype=float); lat = np.asarray(lats, dtype=float)
    cols = np.floor((lon - T.c) / T.a).astype(int)
    rows = np.floor((lat - T.f) / T.e).astype(int)
    H, W = dsq.height, dsq.width
    inside = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    for i in np.where(inside)[0]:
        win = rasterio.windows.Window(int(cols[i]), int(rows[i]), 1, 1)
        b = dsq.read(window=win)[:, 0, 0]
        if b[0] != 65535:
            n[i], sg[i], cn[i] = float(b[0]), float(b[1]), float(b[2])
    return n, sg, cn


# ---------- ICESat-2 ----------
ic = pd.read_csv(f"{R}/scripts/data/icesat2_clean.csv")
print("icesat2_clean:", len(ic))
dem = sample(ic.lat.values, ic.lon.values, ds) / 100.0
tr = sample(ic.lat.values, ic.lon.values, dstr)
nc, sg, cn = quality_sample(ic.lat.values, ic.lon.values)
ic["h_dem2m"] = dem
ic["dH2m"] = ic.h_icesat_baltic - dem
ic["trust"] = tr
ic["n_cov"] = nc; ic["sigma_cm"] = sg; ic["canopy30_cm"] = cn
ok = ic.dH2m.notna()
print(f"covered by mosaic: {ok.sum()}/{len(ic)} ({ok.mean()*100:.1f}%)")
sub = ic[ok]
mad = (sub.dH2m - sub.dH2m.median()).abs().median()
print(f"ICESat-2 vs mosaic 2м: n={len(sub)} med={sub.dH2m.median():+.3f} MAD={mad:.3f} p95={sub.dH2m.abs().quantile(0.95):.3f}")
for t in [1, 0]:
    s = sub[sub.trust == t]
    if len(s):
        m2 = (s.dH2m - s.dH2m.median()).abs().median()
        print(f"  trust={t}: n={len(s)} med={s.dH2m.median():+.3f} MAD={m2:.3f}")
ic.to_csv(f"{R}/scripts/data/icesat2_vs_mosaic_5.86.csv", index=False)

# ---------- Канон города (проектные точки): берём готовый canon2m_5.86.csv ----------
cn_pts = pd.read_csv(f"{R}/scripts/data/canon2m_5.86.csv")
print("\ncanon (working):", len(cn_pts), "| sources:", cn_pts.source.nunique())
okc = cn_pts.dH2m.notna()
print(f"covered: {okc.sum()}/{len(cn_pts)}")
subc = cn_pts[okc]
print(f"canon vs mosaic 2м: n={len(subc)} med={subc.dH2m.median():+.3f} "
      f"MAD={(subc.dH2m-subc.dH2m.median()).abs().median():.3f}")

# ---------- Кластеризация худших ----------
def cluster_worst(df, cell=0.0015, min_n=5, top=30, dedup_m=0.45):
    """Клетки ~165м с n>=min_n, сортировка по mean|dH|; жадный дедуп ближе dedup_m."""
    g = df.copy()
    g["absdH"] = g["dH2m"].abs()
    g["gx"] = (g["lon"] / cell).astype(int)
    g["gy"] = (g["lat"] / cell).astype(int)
    grp = g.groupby(["gx", "gy"]).agg(
        n=("dH2m", "size"), med=("dH2m", "median"), sev=("absdH", "mean"),
        mad=("dH2m", lambda x: (x - x.median()).abs().median()),
        lat=("lat", "mean"), lon=("lon", "mean")).reset_index()
    grp = grp[grp.n >= min_n].sort_values("sev", ascending=False).head(top)
    mlat = dedup_m / 111320.0
    picked, out = [], []
    for _, r in grp.iterrows():
        if all(((r.lat - p[0]) ** 2 + ((r.lon - p[1]) * 0.55) ** 2) ** 0.5 > mlat for p in picked):
            picked.append((r.lat, r.lon))
            pts = g[(g.gx == r.gx) & (g.gy == r.gy)]
            out.append(dict(lat=round(r.lat, 5), lon=round(r.lon, 5), n=int(r.n),
                            sev=round(float(r.sev), 2), med_dH=round(r.med, 2), mad=round(r.mad, 2),
                            bbox=[round(pts.lon.min() - 0.001, 5), round(pts.lat.min() - 0.001, 5),
                                  round(pts.lon.max() + 0.001, 5), round(pts.lat.max() + 0.001, 5)],
                            point_sources=pts.source.unique().tolist() if "source" in pts else ["ICESat-2"]))
    return out


worst_ic = cluster_worst(sub, min_n=5, top=40)
worst_cn = cluster_worst(subc, min_n=4, top=40)
print(f"\nworst ICESat clusters: {len(worst_ic)}; worst canon clusters: {len(worst_cn)}")

# Обязательные аномалии 5.66 (якоря из validation_coverage_5.66.md §2/§4)
anomalies_566 = [
    dict(name="Коломяги АНО ФК", lat=60.0181, lon=30.3023, med_566=-13.90, n=14),
    dict(name="Электростанция №2", lat=59.9347, lon=30.3883, med_566=+9.83, n=11),
    dict(name="Деловой центр (Моск.)", lat=59.8525, lon=30.3044, med_566=+7.48, n=10),
    dict(name="Поклонная гора", lat=59.9387, lon=30.3162, med_566=+5.99, n=2),
    dict(name="Кубинская", lat=59.8391, lon=30.3023, med_566=+2.88, n=11),
    dict(name="ЖК iLona Полюстрово", lat=59.9678, lon=30.3520, med_566=-3.78, n=4),
]
# добавить текущую med по мозаике в радиусе 150м
for a in anomalies_566:
    near = subc[((subc.lat - a["lat"]).abs() < 0.00135) & ((subc.lon - a["lon"]).abs() < 0.0027)]
    a["n_near"] = int(len(near))
    a["med_now"] = round(float(near.dH2m.median()), 2) if len(near) else None

json.dump(dict(icesat_worst=worst_ic, canon_worst=worst_cn, anomalies_566=anomalies_566,
               stats=dict(icesat=dict(n=int(ok.sum()), med=round(float(sub.dH2m.median()), 3), mad=round(float(mad), 3)),
                          canon=dict(n=int(okc.sum()), med=round(float(subc.dH2m.median()), 3)))),
          open(f"{R}/results/worst_points_5.86.json", "w"), ensure_ascii=False, indent=1)
print("\nsaved results/worst_points_5.86.json")
print("anomalies now:", [(a["name"], a["med_now"]) for a in anomalies_566])
