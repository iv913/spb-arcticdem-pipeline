#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.93c — Канон v4: HIGH-дампы (5.86/5.89) + MEDIUM-дамп (5.93b) -> уплотнённая
валидационная база на мозаике 5.90.

Протокол канона заморожен (5.91/5.92): base |dH|<=3 & can30<100; core base &
sigma<=50 & trust>=1; dsm |dH|<=3 & can30>=100. Гейты GOLD(N) n_cov>=2 &
sigma<=50 & can<200 (strip), GOLD(S) can<50 (prior) — без перекалибровки.
Новые medium-точки несут флаг cnf_src="med" — качество учитывается отдельно.

Этапы (возобновляемо):
  1) унификация 3 дампов + дедуп по ключу (rgt,cycle,gt,date,round(lat,4),
     round(lon,4)), HIGH-first -> scripts/dem_cache/densify_canon_5.93/unified.csv.gz
  2) сэмпл мозаики 5.90 (round, ND=65000) чанками по lat ->
     chunk_XX.csv.gz (skip готовых)
  3) канон v4 + метрики + зоны 84 + PNG + отчётные файлы.

Выход:
  scripts/data/icesat2_canon_v4_5.93.csv.gz
  results/densify_5.93.json / .md, results/densify_zones_5.93.csv,
  results/densify_5.93.png
"""
import os
import json
import glob
import time

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows as W

R = "/tmp/my-project/gh-clone"
DEM = f"{R}/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif"
QUAL = f"{R}/download/mosaic_5.90/spb_quality_5.90.tif"
ND = 65000

D_NORTH = f"{R}/scripts/data/icesat2_atl06_dump_5.86.csv.gz"
D_SOUTH = f"{R}/scripts/data/icesat2_atl06_dump_south_5.89.csv.gz"
D_MED = f"{R}/scripts/data/icesat2_atl06_dump_med_5.93.csv.gz"
CANON3 = f"{R}/scripts/data/icesat2_canon_v3_5.92.csv.gz"
ZONES = f"{R}/results/zones_summary_5.83.csv"
CDIR = f"{R}/scripts/dem_cache/densify_canon_5.93"
os.makedirs(CDIR, exist_ok=True)

OUT_CANON = f"{R}/scripts/data/icesat2_canon_v4_5.93.csv.gz"
OUTJ = f"{R}/results/densify_5.93.json"
OUTMD = f"{R}/results/densify_5.93.md"
OUTZ = f"{R}/results/densify_zones_5.93.csv"
OUTPNG = f"{R}/results/densify_5.93.png"

N_CHUNK = 6
DZ = {"Кировский":      (59.83, 30.14, 59.90, 30.32),
      "Красносельский": (59.79, 30.10, 59.88, 30.30),
      "Пушкинский":     (59.70, 30.22, 59.80, 30.45),
      "Колпинский":     (59.70, 30.42, 59.80, 30.62)}


def mstats(d):
    d = np.asarray(d, float)
    d = d[np.isfinite(d)]
    if len(d) < 30:
        return dict(n=int(len(d)))
    off = float(np.median(d))
    return dict(n=int(len(d)), med=round(off, 3),
                mad=round(float(np.median(np.abs(d - off))), 3),
                p95=round(float(np.percentile(np.abs(d - off), 95)), 3))


def key(df):
    return df.rgt.astype(int).astype(str) + "_" + \
        df.cycle.astype(int).astype(str) + "_" + df["gt"].astype(str) + "_" + \
        df.date.astype(str) + "_" + df.lat.round(4).astype(str) + "_" + \
        df.lon.round(4).astype(str)


def sample_tif(path, lons, lats, band=1, chunk=40000):
    n = len(lons)
    out = np.full(n, np.nan)
    order = np.argsort(lats, kind="stable")
    with rasterio.open(path) as s:
        T = s.transform
        for i0 in range(0, n, chunk):
            idx = order[i0:i0 + chunk]
            lo, la = np.asarray(lons)[idx], np.asarray(lats)[idx]
            cols = np.round((lo - T.c) / T.a).astype(int)
            rows = np.round((la - T.f) / T.e).astype(int)
            inb = (rows >= 0) & (rows < s.height) & \
                  (cols >= 0) & (cols < s.width)
            if not inb.any():
                continue
            r = np.clip(rows, 0, s.height - 1)
            c = np.clip(cols, 0, s.width - 1)
            win = W.Window(int(c[inb].min()), int(r[inb].min()),
                           int(c[inb].max() - c[inb].min() + 1),
                           int(r[inb].max() - r[inb].min() + 1))
            a = s.read(band, window=win)
            rr = r[inb] - int(r[inb].min())
            cc = c[inb] - int(c[inb].min())
            v = a[rr, cc].astype("float64")
            if s.nodata is not None:
                v[v == s.nodata] = np.nan
            out[idx[inb]] = v
    return out


# ============================ 1. унификация ============================
UNI = f"{CDIR}/unified.csv.gz"
if not os.path.exists(UNI):
    t0 = time.time()
    print("=== 1. унификация + дедуп ===", flush=True)
    cols = ["lat", "lon", "rgt", "cycle", "gt", "h_baltic", "date"]
    hn = pd.read_csv(D_NORTH, usecols=cols + ["trust"])
    hn["cnf_src"] = "high"
    hs = pd.read_csv(D_SOUTH, usecols=cols)
    hs["cnf_src"] = "high"
    dm = pd.read_csv(D_MED, usecols=cols + ["region"])
    dm["cnf_src"] = "med"
    # region: N-дамп -> N, S-дамп -> S, medium -> из файла
    hn["region"] = "N"
    hs["region"] = "S"
    u = pd.concat([hn, hs, dm], ignore_index=True)
    u["date"] = u.date.astype(str).str.replace(".0", "", regex=False)
    u["k"] = key(u)
    n_raw = len(u)
    u = u.drop_duplicates("k", keep="first").drop(columns=["k"])
    print(f"унифицировано: {n_raw} -> {len(u)} (дедуп -{n_raw-len(u)}) "
          f"({time.time()-t0:.0f}s)", flush=True)
    u = u.sort_values(["lat", "lon"]).reset_index(drop=True)
    u.to_csv(UNI, index=False, compression="gzip")
else:
    u = pd.read_csv(UNI)
    print(f"=== 1. unified готов: {len(u)} ===", flush=True)

# границы чанков по lat (равные количества)
edges = np.quantile(u.lat.values, np.linspace(0, 1, N_CHUNK + 1))
edges[0] -= 1e-9
edges[-1] += 1e-9

# ============================ 2. сэмпл чанками ============================
done = 0
for ic in range(N_CHUNK):
    fp = f"{CDIR}/chunk_{ic:02d}.csv.gz"
    if os.path.exists(fp):
        done += 1
        continue
    t0 = time.time()
    m = (u.lat.values >= edges[ic]) & (u.lat.values < edges[ic + 1])
    d = u[m].copy()
    d["dem2m"] = sample_tif(DEM, d.lon.values, d.lat.values) / 100.0
    d["n_cov"] = sample_tif(QUAL, d.lon.values, d.lat.values, band=1)
    d["sigma_cm"] = sample_tif(QUAL, d.lon.values, d.lat.values, band=2)
    d["can30"] = sample_tif(QUAL, d.lon.values, d.lat.values, band=3)
    d["dH"] = d.h_baltic - d.dem2m
    d.to_csv(fp, index=False, compression="gzip")
    done += 1
    print(f"=== 2. chunk {ic+1}/{N_CHUNK}: {len(d)} строк "
          f"({time.time()-t0:.0f}s) ===", flush=True)

if done < N_CHUNK:
    print(f"сэмпл готов {done}/{N_CHUNK} — продолжить запуск", flush=True)
    raise SystemExit(0)

# ============================ 3. канон v4 + метрики ============================
print("=== 3. канон v4 ===", flush=True)
t0 = time.time()
m = pd.concat([pd.read_csv(f) for f in
               sorted(glob.glob(f"{CDIR}/chunk_*.csv.gz"))],
              ignore_index=True)
mon = (m.date.astype(int) // 100) % 100
m["season"] = np.where(np.isin(mon, [11, 12, 1, 2, 3, 4]),
                       "leaf_off", "leaf_on")
m["blk_half"] = (np.floor(m.lat / 0.02).astype(int) +
                 np.floor(m.lon / 0.0127).astype(int)) % 2
m["trust"] = m.trust.fillna(0)
m["cell"] = m.lat.round(3).astype(str) + "_" + m.lon.round(3).astype(str)

fin = m.dH.notna()
base = fin & (m.dH.abs() <= 3.0) & (m.can30 < 100)
core = base & (m.sigma_cm <= 50) & (m.trust >= 1)
dsm = fin & (m.dH.abs() <= 3.0) & (m.can30 >= 100)
m["base_v4"] = base.values
m["core_v4"] = core.values
m["dsm_v4"] = dsm.values
m["prior"] = m.n_cov.isna()
m["strip"] = (~m.prior) & m.n_cov.notna()

is_med = (m.cnf_src == "med").values
gold = (base & m.strip & (m.n_cov >= 2) & (m.sigma_cm <= 50) &
        (m.can30 < 200)) | (base & m.prior & (m.can30 < 50))
m["gold_v4"] = gold.values

# канон v3 для сравнения
c3 = pd.read_csv(CANON3, usecols=["lat", "lon", "dH2m_v3", "region",
                                  "blk_half"])
c3["cell"] = c3.lat.round(3).astype(str) + "_" + c3.lon.round(3).astype(str)
c3_cells = set(c3.cell)
c3_mad = dict(N=mstats(c3.dH2m_v3[c3.region == "N"]),
              S=mstats(c3.dH2m_v3[c3.region == "S"]))

new_med = m[is_med]
med_base = m[is_med & base.values]
med_new_cells = (~med_base.cell.isin(c3_cells)).values

res = dict(
    protocol="канон v4 = HIGH(5.86+5.89) + MEDIUM(5.93b cnf=3), растр 5.90 "
             "(round, ND=65000); правило канона заморожено (5.91/5.92)",
    windows=dict(raw=int(len(pd.read_csv(D_MED, usecols=["lat"])) +
                         691818 + 255246),
                 unified=int(len(m)),
                 med=int(is_med.sum()), high=int((~is_med).sum())),
    canon=dict(
        v3_base=int(len(c3)), v4_base=int(base.sum()),
        v4_core=int(core.sum()), v4_dsm=int(dsm.sum()),
        v3_cells=len(c3_cells), v4_cells=int(m.cell[base.values].nunique()),
        med_base=int(med_base.shape[0]),
        med_base_new_cells=int(med_new_cells.sum()),
        med_dsm=int((is_med & dsm.values).sum()),
        cells_added=int(m.cell[base.values].nunique() - len(
            c3_cells & set(m.cell[base.values])))),
    mad=dict(
        high_base=mstats(m.dH[base.values & ~is_med]),
        med_base=mstats(m.dH[base.values & is_med]),
        med_base_newcells=mstats(m.dH[base.values & is_med][med_new_cells]),
        high_gold=mstats(m.dH[gold.values & ~is_med]),
        med_gold=mstats(m.dH[gold.values & is_med]),
        v3=c3_mad,
        v4_N=mstats(m.dH[base.values & (m.region == "N").values]),
        v4_S=mstats(m.dH[base.values & (m.region == "S").values]),
        high_leaf_off=mstats(m.dH[base.values & ~is_med &
                                  (m.season == "leaf_off").values]),
        med_leaf_off=mstats(m.dH[base.values & is_med &
                                 (m.season == "leaf_off").values])),
    train_holdout=dict(
        high=[int((base.values & ~is_med & (m.blk_half == 0).values).sum()),
              int((base.values & ~is_med & (m.blk_half == 1).values).sum())],
        med=[int((base.values & is_med & (m.blk_half == 0).values).sum()),
             int((base.values & is_med & (m.blk_half == 1).values).sum())]),
    dedup_control=dict(
        uniq_cells=int(m.cell[base.values].nunique()),
        cell_median_mad=mstats(m[base.values].groupby("cell").dH.median()
                               .values)),
)
print(json.dumps({k: res[k] for k in ("canon", "mad", "train_holdout")},
                 ensure_ascii=False, indent=1), flush=True)

# ---- holdout по тирам x source
ho = m[m.blk_half == 1]
tiers = dict(
    gold_N_strip=(ho.gold_v4 & (ho.region == "N") & ho.strip),
    gold_prior=(ho.gold_v4 & ho.prior),
    ok_N=(ho.base_v4 & ~ho.gold_v4 & (ho.region == "N") & ho.strip &
          (ho.n_cov >= 2) & (ho.can30 < 200)),
    rest=(ho.base_v4 & ~ho.gold_v4))
holdout = {}
for t, msk in tiers.items():
    holdout[t] = dict(high=mstats(ho.dH[msk & (ho.cnf_src == "high")]),
                      med=mstats(ho.dH[msk & (ho.cnf_src == "med")]))
res["holdout_tiers"] = holdout
print("holdout tiers:", json.dumps(holdout, ensure_ascii=False), flush=True)

# ---- районы юга
dz_res = {}
for k, (s_, w_, n_, e_) in DZ.items():
    msk = ((m.region == "S").values & (m.lat >= s_) & (m.lat <= n_) &
           (m.lon >= w_) & (m.lon <= e_))
    dz_res[k] = dict(
        all=mstats(m.dH[msk]),
        high=mstats(m.dH[msk & ~is_med]),
        med=mstats(m.dH[msk & is_med]),
        gold=mstats(m.dH[msk & gold.values]))
res["districts_south"] = dz_res

# ---- зоны 84
zs = pd.read_csv(ZONES)
zrows = []
for _, z in zs.iterrows():
    try:
        bw, bs_, be, bn = [float(v) for v in z["bbox"].split()]
    except Exception:
        try:
            bw, bs_, be, bn = z["bbox"]
        except Exception:
            continue
    msk = ((m.lat >= bs_) & (m.lat <= bn) & (m.lon >= bw) & (m.lon <= be))
    m3 = ((c3.lat >= bs_) & (c3.lat <= bn) & (c3.lon >= bw) & (c3.lon <= be))
    mb4 = msk & base.values
    m3m = c3.dH2m_v3[m3]
    mad3 = round(float(np.median(np.abs(m3m - m3m.median()))), 3) \
        if m3.sum() >= 30 else None
    zrows.append(dict(
        tag=z["tag"], region="S" if str(z["tag"]).startswith("S") else "N",
        n_v3=int(m3.sum()), mad_v3=mad3,
        n_v4=int(mb4.sum()),
        mad_v4=mstats(m.dH[mb4]).get("mad"),
        n_med=int((mb4 & is_med).sum()),
        mad_med=mstats(m.dH[mb4 & is_med]).get("mad"),
        n_med_newcells=int((mb4 & is_med &
                            (~m.cell.isin(c3_cells)).values).sum())))
zdf = pd.DataFrame(zrows)
zdf.to_csv(OUTZ, index=False)
res["zones"] = dict(n=int(len(zdf)),
                    zones_with_med=int((zdf.n_med > 0).sum()),
                    top_med=zdf.sort_values("n_med", ascending=False)
                    .head(12)[["tag", "n_v3", "n_v4", "n_med", "mad_med"]]
                    .to_dict(orient="records"))

# ---- канон v4 -> csv
keep = ["lat", "lon", "rgt", "cycle", "gt", "date", "season", "region",
        "h_baltic", "dem2m", "dH", "can30", "sigma_cm", "n_cov", "trust",
        "cnf_src", "blk_half", "core_v4"]
m[m.base_v4][keep].to_csv(OUT_CANON, index=False, compression="gzip")
print(f"канон v4 сохранён: {int(base.sum())} строк", flush=True)

json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)
print(f"json: {OUTJ} ({time.time()-t0:.0f}s)", flush=True)

# ---- PNG (2 панели)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(1, 2, figsize=(15, 7), constrained_layout=True)
sub = m[m.base_v4].sample(min(60000, int(m.base_v4.sum())), random_state=1)
old = sub.cnf_src == "high"
ax[0].scatter(sub.lon[old], sub.lat[old], s=1.2, c="#9aa5b1", alpha=0.35,
              label=f"HIGH base ({int((~is_med & base.values).sum())})")
newc = sub.cell.isin(c3_cells)
ax[0].scatter(sub.lon[~old & newc], sub.lat[~old & newc], s=1.6,
              c="#f2a541", alpha=0.5, label="med: ячейка v3")
ax[0].scatter(sub.lon[~old & ~newc], sub.lat[~old & ~newc], s=2.6,
              c="#c0392b", alpha=0.8,
              label=f"med: НОВАЯ ячейка ({int(med_new_cells.sum())})")
ax[0].set_title("Канон v4: новые medium-точки (base)")
ax[0].legend(loc="lower left", fontsize=8, markerscale=4)
ax[0].set_xlabel("lon"); ax[0].set_ylabel("lat")

labels = ["N base", "S base", "gold all", "med base", "med new-cells"]
vals = [res["mad"]["v4_N"].get("mad"), res["mad"]["v4_S"].get("mad"),
        res["mad"]["med_gold"].get("mad"),
        res["mad"]["med_base"].get("mad"),
        res["mad"]["med_base_newcells"].get("mad")]
cols_ = ["#4c72b0", "#4c72b0", "#55a868", "#f2a541", "#c0392b"]
b = ax[1].bar(labels, vals, color=cols_)
for r, v in zip(b, vals):
    ax[1].text(r.get_x() + r.get_width()/2, v + 0.005, str(v), ha="center",
               fontsize=9)
ax[1].axhline(res["mad"]["high_base"].get("mad") or 0, ls="--", c="grey",
              lw=1, label=f"HIGH base {res['mad']['high_base'].get('mad')}")
ax[1].set_title("MAD (м) канона v4")
ax[1].legend(fontsize=8)
ax[1].set_ylim(0, max(v for v in vals if v) * 1.25)
fig.suptitle("5.93 уплотнение съёмки ATL06: HIGH + MEDIUM (cnf=3)",
             fontsize=12)
fig.savefig(OUTPNG, dpi=140)
plt.close(fig)
print(f"png: {OUTPNG}", flush=True)
print("ГОТОВО", flush=True)
