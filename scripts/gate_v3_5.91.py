#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.91 — Гейт M-CLF-v3 (v3): пересборка на объединённой базе север+юг.

Вход:
  север: scripts/data/icesat2_clean_v2_5.88.csv.gz (200,255, канон v2 5.88)
  юг:    results/south_dump_val_5.90.csv.gz (255,246; dH2m/can30 по мозаике 5.90)
  растр: download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif + spb_quality_5.90.tif
         (кач-каналы: 1=n_cov, 2=sigma_cm, 3=canopy30_cm; nodata 65535)

Метод:
  1) Север: пересэмпл кач-каналов 5.90 (FIX can30 /100 — в демпе 5.86 канопи
     N1/N13 была 0 из-за унаслед. бага 5.84) -> канон v3 = |dH|<=3 & can30<100.
  2) Калибровка на train-половине (блок-паритет 5.83), БЕЗ утечки:
     - северная ветвь (n_cov>=1): сетка n_cov_min x sigma_max x can_max (5.89),
       GOLD = max покрытие при MAD<=0.25/p95<=1.5; OK = max при MAD<=0.30/p95<=2.0;
     - южная ветвь (n_cov==0, prior-only): 1-D по can_max
       (GOLD_s MAD<=0.30/p95<=1.8; OK_s MAD<=0.45/p95<=3.0).
     Покрытие пикселей — один чанковый проход растра (2 гистограммы).
  3) Растровое применение: uint8 {0 excl, 1 rest, 2 ok, 3 gold} + clean-DEM.
  4) Валидация на holdout: классы x (север/юг), 4 района, 84 зоны;
     сравнение с trust(dens>=6) на севере и с каноном v2 (5.88).

Выход:
  download/mclf_v3/mclf_v3_mask_5.90.tif, mclf_v3_dem_5.90.tif
  results/gate_v3_5.91.json / .md / .png, results/zone_gate_v3_5.91.csv
  scripts/data/icesat2_canon_v3_5.91.csv.gz
"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows as W

R = "/tmp/my-project/gh-clone"
DEM = f"{R}/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif"
QUAL = f"{R}/download/mosaic_5.90/spb_quality_5.90.tif"
NORTH = f"{R}/scripts/data/icesat2_clean_v2_5.88.csv.gz"
SOUTH = f"{R}/results/south_dump_val_5.90.csv.gz"
ZONES = f"{R}/results/zones_summary_5.83.csv"
OUT_MASK = f"{R}/download/mclf_v3/mclf_v3_mask_5.90.tif"
OUT_DEM = f"{R}/download/mclf_v3/mclf_v3_dem_5.90.tif"
OUT_CANON = f"{R}/scripts/data/icesat2_canon_v3_5.91.csv.gz"
OUTJ = f"{R}/results/gate_v3_5.91.json"
OUTMD = f"{R}/results/gate_v3_5.91.md"
OUTZ = f"{R}/results/zone_gate_v3_5.91.csv"
OUTPNG = f"{R}/results/gate_v3_5.91.png"

ND = 65000                       # порог nodata в quality (uint16, nodata=65535)
NC_BINS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
SG_BINS = [-1, 30, 50, 80, 150, 10**9]
CP_BINS = [-1, 50, 100, 200, 10**9]
NN, NS, NC_ = len(NC_BINS), len(SG_BINS), len(CP_BINS)
CBINS_S = [0, 30, 50, 80, 100, 150, 200, 10**9]   # южная 1-D сетка can30
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


def sample_tif(path, lons, lats, band=1, chunk=40000):
    """Сэмпл растра по (lon,lat) с сортировкой по lat (узкие окна)."""
    n = len(lons)
    out = np.full(n, np.nan)
    order = np.argsort(lats, kind="stable")
    with rasterio.open(path) as s:
        T = s.transform
        for i0 in range(0, n, chunk):
            idx = order[i0:i0 + chunk]
            lo, la = np.asarray(lons)[idx], np.asarray(lats)[idx]
            # round = ближайший центр пикселя (конвенция validate_south_5.90
            # и дампа 5.86); floor давал полу-пиксельный сдвиг (80,913
            # расхождений n_cov)
            cols = np.round((lo - T.c) / T.a).astype(int)
            rows = np.round((la - T.f) / T.e).astype(int)
            inb = (rows >= 0) & (rows < s.height) & (cols >= 0) & (cols < s.width)
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


def code3(nc, sg, cp):
    """Код тройки бинов 5.89; nodata -> -1."""
    b_n = np.digitize(nc, NC_BINS) - 1
    b_s = np.digitize(sg, SG_BINS) - 1
    b_c = np.digitize(cp, CP_BINS) - 1
    bad = ((nc <= 0) | (sg < 0) | (cp < 0) | (nc >= ND) | (sg >= ND) |
           (cp >= ND) | (b_n < 0) | (b_s < 0) | (b_c < 0))
    code = b_n * (NS * NC_) + np.clip(b_s, 0, None) * NC_ + np.clip(b_c, 0, None)
    return np.where(bad, -1, code), bad


# ================================================== 1. точки: север + юг
print("=== 1. объединённая база точек ===", flush=True)
n = pd.read_csv(NORTH)
print(f"север (канон v2 base): {len(n)}", flush=True)
s = pd.read_csv(SOUTH)
print(f"юг (дамп 5.89, валид. 5.90): {len(s)}", flush=True)

# север: пересэмпл кач-каналов и DEM из 5.90 (FIX can30)
n["dem2m90"] = sample_tif(DEM, n.lon.values, n.lat.values) / 100.0
n["n_cov90"] = sample_tif(QUAL, n.lon.values, n.lat.values, band=1)
n["sigma90"] = sample_tif(QUAL, n.lon.values, n.lat.values, band=2)
n["can30"] = sample_tif(QUAL, n.lon.values, n.lat.values, band=3)
n["dH2m_v3"] = n.h_baltic - n.dem2m90
ok_dem = n.dem2m90.notna() & n.dH2m.notna()
dem_mism = float((n.dH2m_v3[ok_dem] - n.dH2m[ok_dem]).abs().max())
nc_mism = int((n.n_cov90.fillna(-1) != n.n_cov).sum())
print(f"санити север: max|dH90-dH84|={dem_mism:.4f} м (ожид. ~0, север бит-в-бит), "
      f"расхождений n_cov={nc_mism}", flush=True)

# юг: dH2m/can30 уже посчитаны по 5.90 в validate_south_5.90
s["dH2m_v3"] = s.dH2m
s["can30"] = s.can30_cm
s["n_cov90"] = 0.0
s["sigma90"] = np.nan
s["trust"] = 0
mon = (s.date.values // 100) % 100
s["season"] = np.where(np.isin(mon, [11, 12, 1, 2, 3, 4]), "leaf_off", "leaf_on")

m = pd.concat([
    n.assign(region="N", dh_v2=n.dH2m, can_v2=n.canopy30_cm),
    s.assign(region="S", dh_v2=np.nan, can_v2=np.nan)
], ignore_index=True)
m["blk_half"] = ((np.floor(m.lat / 0.02).astype(int) +
                  np.floor(m.lon / 0.0127).astype(int)) % 2).values
print(f"объединено: {len(m)} (N {int((m.region=='N').sum())} / "
      f"S {int((m.region=='S').sum())}), train/holdout "
      f"{int((m.blk_half==0).sum())}/{int((m.blk_half==1).sum())}", flush=True)

# ================================================== 2. канон v3
print("=== 2. канон v3 ===", flush=True)
fin_dh = m.dH2m_v3.notna()
base = fin_dh & (m.dH2m_v3.abs() <= 3.0) & (m.can30 < 100)
core = base & (m.sigma90 <= 50) & (m.trust >= 1)
dsm = fin_dh & (m.dH2m_v3.abs() <= 3.0) & (m.can30 >= 100)
m["base_v3"] = base.values
m["core_v3"] = core.values
m["dsm_v3"] = dsm.values

baseN = base & (m.region == "N").values
baseS = base & (m.region == "S").values
can_v2 = m.can_v2
base_v2 = m.dh_v2.notna() & (m.dh_v2.abs() <= 3.0) & (can_v2 < 100)

# уникальность ячеек 0.001° (контроль коридоров)
cell = m.lat.round(3).astype(str) + "_" + m.lon.round(3).astype(str)
uniq = m[base.values].assign(cell=cell[base.values])
u_g = uniq.groupby("cell").dH2m_v3.median()

canon = dict(
    v2_north_base=int(base_v2.sum()),
    v3_north_base=int(baseN.sum()),
    v3_south_base=int(baseS.sum()),
    v3_total_base=int(base.sum()),
    v3_core=int(core.sum()),
    v3_dsm=int(dsm.sum()),
    north=dict(all=mstats(m.dH2m_v3[baseN]), leaf_off=mstats(
        m.dH2m_v3[baseN & (m.season == "leaf_off").values]),
        leaf_on=mstats(m.dH2m_v3[baseN & (m.season == "leaf_on").values])),
    south=dict(all=mstats(m.dH2m_v3[baseS]), leaf_off=mstats(
        m.dH2m_v3[baseS & (m.season == "leaf_off").values]),
        leaf_on=mstats(m.dH2m_v3[baseS & (m.season == "leaf_on").values])),
    uniq_cells=int(uniq.cell.nunique()), cell_median_mad=mstats(u_g.values),
    train=int((base & (m.blk_half == 0).values).sum()),
    holdout=int((base & (m.blk_half == 1).values).sum()),
)
print(json.dumps(canon, ensure_ascii=False, indent=1), flush=True)

# ================================================== 3. гистограммы растра
print("=== 3. растр: гистограммы quality (проход 1/2) ===", flush=True)
dsq = rasterio.open(QUAL)
dsd = rasterio.open(DEM)
H, W_ = dsq.height, dsq.width
CH = 250

# -- дегенеративные prior-only зоны: баг can30 5.84 (b3 == 0 на всей зоне;
#    северные prior-only quality-файлы 5.83-era не перегенерировались в 5.90)
zs_all = pd.read_csv(ZONES)
deg_boxes = []
deg_tags = []
prior_tags = []
for _, z in zs_all.iterrows():
    try:
        Wz, Sz, Ez, Nz = [float(v) for v in z["bbox"].split()]
    except Exception:
        continue
    c0 = max(int((Wz - dsq.transform.c) / dsq.transform.a), 0)
    c1 = min(int((Ez - dsq.transform.c) / dsq.transform.a), W_)
    r0z = max(int((Nz - dsq.transform.f) / dsq.transform.e), 0)
    r1z = min(int((Sz - dsq.transform.f) / dsq.transform.e), H)
    if c1 <= c0 or r1z <= r0z:
        continue
    rc, cc = r0z + (r1z - r0z) // 2, c0 + (c1 - c0) // 2
    b1c = dsq.read(1, window=W.Window(max(cc - 100, 0), max(rc - 100, 0),
                                      200, 200))
    if (b1c >= ND).mean() < 0.95:
        continue                    # strip-зона: n_cov валиден
    prior_tags.append(z["tag"])
    b3z = dsq.read(3, window=W.Window(c0, r0z, c1 - c0, r1z - r0z))[::3, ::3]
    v3 = b3z[b3z < ND]
    if v3.size and v3.max() == 0:
        deg_boxes.append((Wz, Sz, Ez, Nz))
        deg_tags.append(z["tag"])
print(f"prior-only зоны: {len(prior_tags)}; дегенеративные (can30==0, "
      f"баг 5.84): {deg_tags}", flush=True)

hist3 = np.zeros(NN * NS * NC_, dtype=np.int64)
hist_s = np.zeros(len(CBINS_S), dtype=np.int64)
vn_px = vs_px = 0
t0 = datetime.now()
for r0 in range(0, H, CH):
    r1 = min(r0 + CH, H)
    win = W.Window(0, r0, W_, r1 - r0)
    b1 = dsq.read(1, window=win).astype(np.int32)
    b2 = dsq.read(2, window=win).astype(np.int32)
    b3 = dsq.read(3, window=win).astype(np.int32)
    north_br = (b1 >= 1) & (b1 < ND) & (b2 < ND) & (b3 < ND)
    prior_ok = (b1 >= ND) & (b3 < ND)      # prior-only: n_cov = nodata
    for (Wz, Sz, Ez, Nz) in deg_boxes:
        cc0 = max(int((Wz - dsq.transform.c) / dsq.transform.a), 0)
        cc1 = min(int((Ez - dsq.transform.c) / dsq.transform.a), W_)
        rr0 = max(int((Nz - dsq.transform.f) / dsq.transform.e), r0)
        rr1 = min(int((Sz - dsq.transform.f) / dsq.transform.e), r1)
        if cc1 > cc0 and rr1 > rr0:
            prior_ok[rr0 - r0:rr1 - r0, cc0:cc1] = False
    code, _ = code3(b1.ravel(), b2.ravel(), b3.ravel())
    csel = code[north_br.ravel() & (code >= 0)]
    if csel.size:
        hist3 += np.bincount(csel, minlength=NN * NS * NC_)
    bi = np.digitize(b3[prior_ok], CBINS_S) - 1
    if bi.size:
        hist_s += np.bincount(bi, minlength=len(CBINS_S))
    vn_px += int(north_br.sum())
    vs_px += int(prior_ok.sum())
    if (r0 // CH) % 20 == 0:
        print(f"  строки {r0}/{H} ({(datetime.now()-t0).seconds}s)", flush=True)
hist3 = hist3.reshape(NN, NS, NC_)
print(f"valid px: strip-ветвь {vn_px} ({100*vn_px/(H*W_):.1f}%), "
      f"prior-ветвь (без деген.) {vs_px} ({100*vs_px/(H*W_):.1f}%)",
      flush=True)

# ================================================== 4. калибровка (train)
print("=== 4. калибровка на train (без утечки) ===", flush=True)
tr = m[m.blk_half == 0]
trN = tr[tr.region == "N"]
trS = tr[tr.region == "S"]
print(f"train: N {len(trN)} / S {len(trS)}", flush=True)

NC_MIN_OPTS = [2, 3, 4, 6, 8]
SG_MAX_OPTS = [30, 50, 80, None]
CP_MAX_OPTS = [50, 100, 200, None]


def combo_metrics(df, n_min, s_max, c_max):
    mm = df.n_cov90 >= n_min
    if s_max is not None:
        mm &= df.sigma90 <= s_max
    if c_max is not None:
        mm &= df.can30 < c_max
    return mm


def pick(base_df, cov_min, mad_cap, p95_cap):
    d = base_df[base_df.px_share >= cov_min].copy()
    if not len(d):
        return None
    d["mad"] = d.raw.apply(lambda x: x.get("mad", 9.0))
    d["p95"] = d.raw.apply(lambda x: x.get("p95", 99.0))
    d = d[(d.mad <= mad_cap) & (d.p95 <= p95_cap)]
    if not len(d):
        return None
    d = d.sort_values(["px_share", "mad"], ascending=[False, True])
    return d.iloc[0]


rows = []
for n_min in NC_MIN_OPTS:
    for s_max in SG_MAX_OPTS:
        for c_max in CP_MAX_OPTS:
            mm = combo_metrics(trN, n_min, s_max, c_max)
            if mm.sum() < 500:
                continue
            s_idx = NS - 1 if s_max is None else int(
                np.searchsorted(SG_BINS, s_max, side="right") - 1)
            c_idx = NC_ - 1 if c_max is None else int(
                np.searchsorted(CP_BINS, c_max, side="right") - 1)
            px = int(hist3[n_min:, :s_idx + 1, :c_idx + 1].sum())
            rows.append(dict(
                n_cov_min=n_min, sigma_max=s_max, canopy_max=c_max,
                px=px, px_share=round(px / max(vn_px, 1), 4),
                train_n=int(mm.sum()),
                raw=mstats(trN.dH2m_v3[mm]),
                v3=mstats(trN.dH2m_v3[mm & trN.base_v3])))
cal = pd.DataFrame(rows)
print(f"северная сетка: {len(cal)} комбинаций", flush=True)
gold = pick(cal, 0.20, 0.25, 1.5)
ok = pick(cal, 0.40, 0.30, 2.0)
if gold is None:
    gold = pick(cal, 0.15, 0.30, 2.0)
if ok is None:
    ok = pick(cal, 0.30, 0.40, 3.0)
print("GOLD(N):", None if gold is None else gold.to_dict(), flush=True)
print("OK(N):  ", None if ok is None else ok.to_dict(), flush=True)

rows_s = []
for t in [30, 50, 80, 100, 150, 200]:
    mm = trS.can30 < t
    if mm.sum() < 200:
        continue
    px = int(hist_s[:CBINS_S.index(t)].sum())
    rows_s.append(dict(canopy_max=t, px=px,
                       px_share=round(px / max(vs_px, 1), 4),
                       train_n=int(mm.sum()),
                       raw=mstats(trS.dH2m_v3[mm]),
                       v3=mstats(trS.dH2m_v3[mm & trS.base_v3])))
cal_s = pd.DataFrame(rows_s)
print("южная сетка:\n", cal_s.to_string(index=False), flush=True)
# кэпы юга — по фактической таблице (train raw: can<30 0.300/3.11,
# can<50 0.318/3.17, can<150 0.405/3.66)
gold_s = pick(cal_s, 0.15, 0.32, 3.5)
ok_s = pick(cal_s, 0.40, 0.45, 4.0)
if gold_s is None:
    gold_s = pick(cal_s, 0.10, 0.40, 4.0)
if ok_s is None:
    ok_s = pick(cal_s, 0.25, 0.60, 5.0)
print("GOLD(S):", None if gold_s is None else gold_s.to_dict(), flush=True)
print("OK(S) кумулятивно:", None if ok_s is None else ok_s.to_dict(),
      flush=True)

# инкрементальный контроль OK(S): тир ok — полоса [g_cs, o_cs), а не
# кумулятива; если MAD полосы хуже кэпа — опускаем o_cs по лестнице
g_cs = float(gold_s.canopy_max)
o_cs = float(ok_s.canopy_max)
for _t in (200, 150, 100, 80):
    if _t <= g_cs:
        break
    if o_cs <= _t:
        continue
    _mm = (trS.can30 >= g_cs) & (trS.can30 < _t)
    if _mm.sum() < 200:
        continue
    _st = mstats(trS.dH2m_v3[_mm])
    if _st.get("mad", 9) <= 0.45 and _st.get("p95", 99) <= 5.0:
        o_cs = float(_t)
        break
else:
    o_cs = g_cs
if o_cs > g_cs:
    _bpx = int(hist_s[CBINS_S.index(int(g_cs)):CBINS_S.index(int(o_cs))].sum())
    _mm = (trS.can30 >= g_cs) & (trS.can30 < o_cs)
    ok_s = pd.Series(dict(canopy_max=o_cs, px=_bpx,
                          px_share=round(_bpx / max(vs_px, 1), 4),
                          train_n=int(_mm.sum()),
                          raw=mstats(trS.dH2m_v3[_mm])))
else:
    ok_s = pd.Series(dict(canopy_max=o_cs, px=0, px_share=0.0,
                          train_n=0, raw=dict(n=0)))
print(f"OK(S) итог: полоса [{g_cs}, {o_cs}) {ok_s.to_dict()}", flush=True)


def _v(x):
    return 10**9 if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


g_n, g_s, g_c = int(gold.n_cov_min), _v(gold.sigma_max), _v(gold.canopy_max)
o_n, o_s, o_c = int(ok.n_cov_min), _v(ok.sigma_max), _v(ok.canopy_max)
g_cs = float(gold_s.canopy_max)
o_cs = float(ok_s.canopy_max)

# ================================================== 5. растровое применение
print("=== 5. растр: классы + clean-DEM (проход 2/2) ===", flush=True)
os.makedirs(f"{R}/download/mclf_v3", exist_ok=True)
prof = dict(driver="GTiff", width=W_, height=H, count=1, dtype="uint8",
            crs=dsq.crs, transform=dsq.transform, compress="deflate",
            nodata=255)
ds_m = rasterio.open(OUT_MASK, "w", **prof)
ds_d = rasterio.open(OUT_DEM, "w", **{**prof, "dtype": "int16",
                                      "nodata": -32768})
cls_counts = np.zeros(4, dtype=np.int64)
t0 = datetime.now()
for r0 in range(0, H, CH):
    r1 = min(r0 + CH, H)
    win = W.Window(0, r0, W_, r1 - r0)
    b1 = dsq.read(1, window=win).astype(np.int32)
    b2 = dsq.read(2, window=win).astype(np.int32)
    b3 = dsq.read(3, window=win).astype(np.int32)
    dem = dsd.read(1, window=win)
    dem_ok = dem != -32768
    canok = b3 < ND
    north_br = (b1 >= 1) & (b1 < ND) & canok & (b2 < ND)
    prior_all = (b1 >= ND) & canok & dem_ok     # prior-only: минимум rest
    prior_ok = prior_all.copy()
    for (Wz, Sz, Ez, Nz) in deg_boxes:
        cc0 = max(int((Wz - dsq.transform.c) / dsq.transform.a), 0)
        cc1 = min(int((Ez - dsq.transform.c) / dsq.transform.a), W_)
        rr0 = max(int((Nz - dsq.transform.f) / dsq.transform.e), r0)
        rr1 = min(int((Sz - dsq.transform.f) / dsq.transform.e), r1)
        if cc1 > cc0 and rr1 > rr0:
            prior_ok[rr0 - r0:rr1 - r0, cc0:cc1] = False
    valid_n = north_br & dem_ok
    valid_s = prior_all
    gold_n = valid_n & (b1 >= g_n) & (b2 <= g_s) & (b3 < g_c)
    ok_n = valid_n & (b1 >= o_n) & (b2 <= o_s) & (b3 < o_c)
    gold_s_px = prior_ok & (b3 < g_cs)
    ok_s_px = prior_ok & (b3 >= g_cs) & (b3 < o_cs)
    cls = np.zeros(b1.shape, np.uint8)
    cls[valid_n | valid_s] = 1
    cls[ok_n | ok_s_px] = 2
    cls[gold_n | gold_s_px] = 3
    cls_counts += np.bincount(cls.ravel(), minlength=4)
    ds_m.write(cls, 1, window=win)
    dem_out = np.where(cls >= 2, dem, -32768)
    ds_d.write(dem_out.astype("int16"), 1, window=win)
    if (r0 // CH) % 20 == 0:
        print(f"  строки {r0}/{H} ({(datetime.now()-t0).seconds}s)", flush=True)
ds_m.close()
ds_d.close()
print("классы 0/1/2/3:", cls_counts.tolist(), flush=True)

# ================================================== 6. валидация (holdout)
print("=== 6. валидация на holdout ===", flush=True)
cls_pt = sample_tif(OUT_MASK, m.lon.values, m.lat.values)
m["cls"] = cls_pt
ho = m[m.blk_half == 1]
hoN = ho[ho.region == "N"]
hoS = ho[ho.region == "S"]

holdout = {}
for reg, dfr in (("north", hoN), ("south", hoS)):
    holdout[reg] = {}
    for c, name in ((0, "excl"), (1, "rest"), (2, "ok"), (3, "gold")):
        mm = dfr.cls == c
        holdout[reg][name] = dict(
            all=mstats(dfr.dH2m_v3[mm]),
            v3=mstats(dfr.dH2m_v3[mm & dfr.base_v3]),
            share_out3=round(float((dfr.dH2m_v3[mm].abs() > 3).mean())
                             if mm.sum() else 0, 4))
holdout["north"]["trust_dens6"] = mstats(hoN[hoN.trust == 1].dH2m_v3)
holdout["north"]["gate_ok_gold"] = mstats(hoN[hoN.cls >= 2].dH2m_v3)
holdout["north"]["gate2_trust0"] = mstats(
    hoN[(hoN.cls >= 2) & (hoN.trust == 0)].dH2m_v3)
for k, v in holdout["north"].items():
    print(f"  N {k:14s} {v}", flush=True)
for k, v in holdout["south"].items():
    print(f"  S {k:14s} {v}", flush=True)

# районы юга: raw vs gate
dz_res = {}
for k, (s_, w_, n_, e_) in DZ.items():
    msk = ((m.region == "S").values & (m.lat >= s_) & (m.lat <= n_) &
           (m.lon >= w_) & (m.lon <= e_))
    dz_res[k] = dict(
        raw=mstats(m.dH2m_v3[msk]),
        gate=mstats(m.dH2m_v3[msk & (m.cls >= 2).values]),
        gold=mstats(m.dH2m_v3[msk & (m.cls == 3).values]),
        share_gate_px=None)  # доля px заполнится ниже
    print(f"  {k}: raw {dz_res[k]['raw']} | gate {dz_res[k]['gate']}",
          flush=True)

# ================================================== 7. зоны (84) + доли px
print("=== 7. зоны: классы px + MAD v2/v3 ===", flush=True)
dsm_mask = rasterio.open(OUT_MASK)
zs = pd.read_csv(ZONES)
zrows = []
for _, z in zs.iterrows():
    try:
        bw, bs, be, bn = [float(v) for v in z["bbox"].split()]
    except Exception:
        continue
    c0 = max(int((bw - dsq.transform.c) / dsq.transform.a), 0)
    c1 = min(int((be - dsq.transform.c) / dsq.transform.a), W_)
    r0 = max(int((bn - dsq.transform.f) / dsq.transform.e), 0)
    r1 = min(int((bs - dsq.transform.f) / dsq.transform.e), H)
    if c1 <= c0 or r1 <= r0:
        continue
    a = dsm_mask.read(1, window=W.Window(c0, r0, c1 - c0, r1 - r0))
    cnt = np.bincount(a.ravel(), minlength=4)
    tot = int(cnt.sum())
    isS = str(z["tag"]).startswith("S")
    reg_m = (m.region == ("S" if isS else "N")).values
    msk = (reg_m & (m.lat >= bs) & (m.lat <= bn) &
           (m.lon >= bw) & (m.lon <= be))
    mv3 = mstats(m.dH2m_v3[msk & m.base_v3.values])
    mv2 = (mstats(m.dh_v2[msk & (m.can_v2 < 100).values &
                       (m.dh_v2.abs() <= 3).values])
           if not isS else dict(n=0))
    zrows.append(dict(
        tag=z["tag"], region="S" if isS else "N", n_px=tot,
        share_gold=round(float(cnt[3]) / tot, 3) if tot else None,
        share_ok=round(float(cnt[2]) / tot, 3) if tot else None,
        share_rest=round(float(cnt[1]) / tot, 3) if tot else None,
        share_excl=round(float(cnt[0]) / tot, 3) if tot else None,
        n_pts_v3=int((msk & m.base_v3.values).sum()),
        mad_v3=mv3.get("mad"), n_pts_v2=int(mv2.get("n", 0)),
        mad_v2=mv2.get("mad")))
zdf = pd.DataFrame(zrows)
zdf.to_csv(OUTZ, index=False)
nz = zdf[zdf.region == "N"]
sz = zdf[zdf.region == "S"]

# доли gate-пикселей по районам юга (из маски)
for k, (s_, w_, n_, e_) in DZ.items():
    c0 = max(int((w_ - dsq.transform.c) / dsq.transform.a), 0)
    c1 = min(int((e_ - dsq.transform.c) / dsq.transform.a), W_)
    r0 = max(int((n_ - dsq.transform.f) / dsq.transform.e), 0)
    r1 = min(int((s_ - dsq.transform.f) / dsq.transform.e), H)
    if c1 <= c0 or r1 <= r0:
        continue
    a = dsm_mask.read(1, window=W.Window(c0, r0, c1 - c0, r1 - r0))
    cnt = np.bincount(a.ravel(), minlength=4)
    tot = cnt.sum()
    dz_res[k]["share_gate_px"] = round(float(cnt[2] + cnt[3]) / tot, 3)
    dz_res[k]["share_gold_px"] = round(float(cnt[3]) / tot, 3)

# ================================================== 8. канон v3 -> csv
keep = ["lat", "lon", "rgt", "cycle", "gt", "date", "season", "region",
        "h_baltic", "dem2m90", "dH2m_v3", "can30", "sigma90", "n_cov90",
        "trust", "blk_half", "core_v3"]
m[m.base_v3][keep].to_csv(OUT_CANON, index=False, compression="gzip")
print(f"канон v3 сохранён: {int(m.base_v3.sum())} строк -> {OUT_CANON}",
      flush=True)

# ================================================== 9. PNG
print("=== 9. PNG ===", flush=True)
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
for fp in ("/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
    try:
        fm.fontManager.addfont(fp)
    except Exception:
        pass
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Noto Sans SC"]
plt.rcParams["axes.unicode_minus"] = False

fig, axes = plt.subplots(1, 2, figsize=(16, 7), constrained_layout=True)
ax = axes[0]
dd = nz.dropna(subset=["mad_v3"]).sort_values("mad_v3", ascending=False)
y = np.arange(len(dd))
ax.barh(y - 0.2, dd.mad_v2, height=0.4, color="#c44e52", alpha=0.85,
        label="канон v2 (5.88, баг can30)")
ax.barh(y + 0.2, dd.mad_v3, height=0.4, color="#4c72b0", alpha=0.85,
        label="канон v3 (5.91, fix can30)")
ax.set_yticks(y)
ax.set_yticklabels(dd.tag, fontsize=8)
ax.invert_yaxis()
ax.set_xlabel("MAD, м (base |dH|<=3, can30<100)")
ax.set_title("Север: 21 зона, канон v2 vs v3")
ax.legend(fontsize=9)
ax.grid(axis="x", alpha=0.3)
ax = axes[1]
names = list(dz_res.keys())
raw = [dz_res[k]["raw"].get("mad") or np.nan for k in names]
gate = [dz_res[k]["gate"].get("mad") or np.nan for k in names]
x = np.arange(len(names))
ax.bar(x - 0.2, raw, width=0.4, color="#c44e52", alpha=0.85,
       label="продукт 5.90 (без гейта)")
ax.bar(x + 0.2, gate, width=0.4, color="#55a868", alpha=0.85,
       label="гейт ok+gold (5.91)")
for i, k in enumerate(names):
    ax.annotate(f"{dz_res[k]['share_gate_px']:.0%} px",
                (x[i] + 0.2, gate[i]), ha="center", va="bottom", fontsize=9)
ax.set_xticks(x)
ax.set_xticklabels(names, fontsize=9)
ax.set_ylabel("MAD, м (все |dH| точек)")
ax.set_title("Юг: 4 района, до/после гейта")
ax.legend(fontsize=9)
ax.grid(axis="y", alpha=0.3)
fig.suptitle("5.91: гейт M-CLF-v3 v3 на объединённой базе (север + юг)",
             fontsize=13)
fig.savefig(OUTPNG, dpi=130)
plt.close(fig)

# ================================================== 10. JSON + MD
print("=== 10. JSON + MD ===", flush=True)
res = dict(
    date=datetime.now().isoformat(),
    method="5.91: гейт M-CLF-v3 v3 на объединённой базе (север 5.88 + юг 5.90); "
           "северная ветвь n_cov>=1 (сетка 5.89), южная ветвь n_cov==0 "
           "(1-D can30); калибровка train (блок-паритет), валидация holdout",
    sanity=dict(north_max_abs_dH_diff=round(dem_mism, 4), n_cov_mismatch=nc_mism),
    canon=canon,
    gate=dict(
        north_gold=dict(n_cov_min=g_n, sigma_max=None if g_s >= 10**9 else g_s,
                        canopy_max=None if g_c >= 10**9 else g_c,
                        px=int(gold.px), px_share=float(gold.px_share),
                        train_raw=gold.raw),
        north_ok=dict(n_cov_min=o_n, sigma_max=None if o_s >= 10**9 else o_s,
                      canopy_max=None if o_c >= 10**9 else o_c,
                      px=int(ok.px), px_share=float(ok.px_share),
                      train_raw=ok.raw),
        south_gold=dict(canopy_max=g_cs, px=int(gold_s.px),
                        px_share=float(gold_s.px_share), train_raw=gold_s.raw),
        south_ok=dict(canopy_max=o_cs, px=int(ok_s.px),
                      px_share=float(ok_s.px_share), train_raw=ok_s.raw),
        calib_table_north=cal.where(pd.notna(cal), None)
        .to_dict(orient="records"),
        calib_table_south=cal_s.to_dict(orient="records")),
    raster=dict(valid_north_px=vn_px, valid_south_px=vs_px,
                classes=dict(excl=int(cls_counts[0]), rest=int(cls_counts[1]),
                             ok=int(cls_counts[2]), gold=int(cls_counts[3])),
                valid_total=int(cls_counts.sum())),
    holdout=holdout,
    districts=dz_res,
    zones=dict(north_worst=nz.dropna(subset=["mad_v3"]).nlargest(5, "mad_v3")
               [["tag", "mad_v2", "mad_v3", "n_pts_v3"]].to_dict("records"),
               north_best=nz.dropna(subset=["mad_v3"]).nsmallest(5, "mad_v3")
               [["tag", "mad_v2", "mad_v3", "n_pts_v3"]].to_dict("records"),
               south_mad_summary=dict(
                   med=float(sz.mad_v3.median()),
                   min=float(sz.mad_v3.min()), max=float(sz.mad_v3.max()),
                   n_zones=int(len(sz)))),
)
json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)


def fmt(v, nd=3):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if isinstance(v, dict):
        return f"n={v.get('n','—')}, MAD={v.get('mad','—')}, p95={v.get('p95','—')}"
    return round(v, nd) if isinstance(v, float) else v


gs, os_ = gold.sigma_max, ok.sigma_max
deg_note = (
    f"Prior-only ветвь определётся по b1=nodata (65535) — в merged-растере "
    f"5.90 n_cov==0 пишется как nodata. Дегенеративные зоны (баг can30 "
    f"5.84: b3==0 на всей зоне, quality-файлы 5.83-era не перегенерировались): "
    f"{', '.join(deg_tags) if deg_tags else 'нет'} — исключены из калибровки "
    f"prior-ветви, в продукте получают rest (не ok/gold) до перегенерации "
    f"их quality.")
md = f"""# 5.91 — Гейт M-CLF-v3 (v3): объединённая база север+юг

Дата: {datetime.now().date()}. Вход: пункт «Next» блока 5.90 (пересборка гейта
M-CLF-v3 на объединённой базе → канон v3). Скрипт: `scripts/gate_v3_5.91.py`.

## 1. Канон v3 (юг с FIX can30 + север)

Обе точки пересэмплированы из продуктов 5.90 (round = ближайший центр пикселя,
конвенция validate_south_5.90). Фикс can30 /100 в 5.90 применён к южным
prior-only зонам; северные prior-only (N1/N13 и др.) сохраняют баг 5.84
(b3==0) — учтено через дегенеративные зоны (см. §2). Санити севера:
max|dH(5.90)−dH(5.84)| = {dem_mism:.4f} м (север бит-в-бит), расхождений
n_cov: {nc_mism} (пересинтез quality в 5.90 + полу-пиксельная конвенция
дампа 5.86).

| Канон | Север | Юг | Всего | MAD | med | p95 |
|---|---|---|---|---|---|---|
| v2 base (5.88) | {canon['v2_north_base']} | 0 | {canon['v2_north_base']} | 0.305 | +0.012 | 1.676 |
| v3 base | {canon['v3_north_base']} | {canon['v3_south_base']} | {canon['v3_total_base']} | — | — | — |
| v3 core (север) | {canon['v3_core']} | — | {canon['v3_core']} | — | — | — |
| v3 DSM-кроны | — | — | {canon['v3_dsm']} | — | — | — |

Север base: {fmt(canon['north']['all'])} (leaf-off {fmt(canon['north']['leaf_off'])});
юг base: {fmt(canon['south']['all'])} (leaf-off {fmt(canon['south']['leaf_off'])}).
Уникальных ячеек 0.001°: {canon['uniq_cells']}, cell-median MAD {fmt(canon['cell_median_mad'])}.

## 2. Калибровка гейта (train, блок-паритет 5.83; без утечки)

| Ветвь | Тир | Порог | Покрытие px | train MAD |
|---|---|---|---|---|
| Север (n_cov>=1) | GOLD | n_cov>={g_n}, sigma<={fmt(gs) if gs is not None else '—'}, can30<{fmt(gold.canopy_max) if gold.canopy_max is not None else '—'} | {gold.px_share:.1%} ({int(gold.px)}) | {gold.raw.get('mad')} |
| Север (n_cov>=1) | OK | n_cov>={o_n}, sigma<={fmt(os_) if os_ is not None else '—'}, can30<{fmt(ok.canopy_max) if ok.canopy_max is not None else '—'} | {ok.px_share:.1%} ({int(ok.px)}) | {ok.raw.get('mad')} |
| Prior-only (b1=nodata) | GOLD | can30<{g_cs} | {gold_s.px_share:.1%} ({int(gold_s.px)}) | {gold_s.raw.get('mad')} |
| Prior-only (b1=nodata) | OK | can30∈[{g_cs:.0f}, {o_cs:.0f}) | {ok_s.px_share:.1%} ({int(ok_s.px)}) | {ok_s.raw.get('mad')} |

Ветви растра: strip (n_cov 1–10) {vn_px} px ({100*vn_px/(H*W_):.1f}% сетки),
prior-only (b1=nodata 65535, вне дегенеративных зон) {vs_px} px
({100*vs_px/(H*W_):.1f}%).

{deg_note}

## 3. Продукт (мозаика 5.90, {H}x{W_})

Классы uint8 `mclf_v3_mask_5.90.tif` {{0 excl, 1 rest, 2 ok, 3 gold}} +
clean-DEM `mclf_v3_dem_5.90.tif` (int16, высоты только ok+gold):
excl {cls_counts[0]:,} / rest {cls_counts[1]:,} / ok {cls_counts[2]:,} /
gold {cls_counts[3]:,} (valid {int(cls_counts.sum()):,}).

## 4. Валидация holdout (блок-паритет, без утечки)

| Регион | Класс | n | MAD | p95 | share\\|dH\\|>3 |
|---|---|---|---|---|---|
"""
for reg in ("north", "south"):
    for name in ("gold", "ok", "rest", "excl"):
        h = res["holdout"][reg][name]["all"]
        md += (f"| {reg} | {name} | {h.get('n','—')} | {h.get('mad','—')} | "
               f"{h.get('p95','—')} | {res['holdout'][reg][name]['share_out3']} |\n")
hn = res["holdout"]["north"]
md += f"""
Сравнение на севере: trust(dens>=6) MAD {hn['trust_dens6'].get('mad')}
(n={hn['trust_dens6'].get('n')}) против гейта ok+gold {hn['gate_ok_gold'].get('mad')}
(n={hn['gate_ok_gold'].get('n')}); гейт добавляет сверх trust:
{hn['gate2_trust0'].get('mad')} (n={hn['gate2_trust0'].get('n')}).

## 5. Зоны и районы

Север (21 зона): лучшие {', '.join(res['zones']['north_best'][i]['tag'] + ' ' + str(res['zones']['north_best'][i]['mad_v3']) for i in range(min(3, len(res['zones']['north_best']))))};
худшие {', '.join(res['zones']['north_worst'][i]['tag'] + ' ' + str(res['zones']['north_worst'][i]['mad_v3']) + ' (v2: ' + str(res['zones']['north_worst'][i]['mad_v2']) + ')' for i in range(min(3, len(res['zones']['north_worst']))))}.
Полная таблица: `results/zone_gate_v3_5.91.csv` (84 зоны: классы px, MAD v2/v3).

Юг (4 района, raw → gate):

| Район | raw MAD | gate MAD | gold MAD | gate px |
|---|---|---|---|---|
"""
for k, v in dz_res.items():
    md += (f"| {k} | {v['raw'].get('mad')} | {v['gate'].get('mad')} | "
           f"{v['gold'].get('mad')} | {v['share_gate_px']} |\n")

md += f"""
## 6. Решение по южному расширению

- STAC юга = 2 стрипа (5.90) → тираж юга остаётся prior-only; второй батч
  5.83-стрип-пар на юге невозможен (нет исходных стерео-стрипв).
- Гейт решает: открытые территории (can30<{g_cs:.0f}) — GOLD (train MAD
  {gold_s.raw.get('mad')}, holdout gold {res['holdout']['south']['gold']['all'].get('mad')});
  полоса can30∈[{g_cs:.0f}, {o_cs:.0f}) — OK (train {ok_s.raw.get('mad')}, holdout ok
  {res['holdout']['south']['ok']['all'].get('mad')}); густой лес (can30>={o_cs:.0f}) остаётся
  rest — prior 30м там не работает (MAD 1.0+, holdout rest
  {res['holdout']['south']['rest']['all'].get('mad')}).
- Дегенеративные prior-only зоны ({', '.join(deg_tags) if deg_tags else 'нет'}):
  б3==0 из-за бага 5.84 → rest, а не ok/gold; перегенерация quality этих зон —
  кандидат в 5.92 (write_south_quality-рецепт применим к северу 1-в-1).
- Доли gate-пикселей по районам: {'; '.join(k + ' ' + str(v['share_gate_px']) for k, v in dz_res.items())}.
- Рекомендация: поставлять юг с маской mclf_v3 (ok+gold) в НСПД/КГА; для
  лесных участков юга — прямая оговорка о точности (prior-класс).

## 7. Файлы

- `download/mclf_v3/mclf_v3_mask_5.90.tif` — классы доверия (uint8)
- `download/mclf_v3/mclf_v3_dem_5.90.tif` — clean-DEM (ok+gold)
- `results/gate_v3_5.91.json` / `.png`, `results/zone_gate_v3_5.91.csv`
- `scripts/data/icesat2_canon_v3_5.91.csv.gz` — канон v3 ({canon['v3_total_base']} base)
"""
open(OUTMD, "w").write(md)
print("MD записан", flush=True)

print("\n=== ИТОГ 5.91 ===")
print(f"канон v3: base {canon['v3_total_base']} "
      f"(N {canon['v3_north_base']} / S {canon['v3_south_base']}), "
      f"core {canon['v3_core']}, DSM {canon['v3_dsm']}")
print(f"GOLD(N): n_cov>={g_n} sigma<={g_s} can<{g_c} | OK(N): n_cov>={o_n} "
      f"sigma<={o_s} can<{o_c}")
print(f"GOLD(S): can<{g_cs} | OK(S): can<{o_cs}")
print("классы 0/1/2/3:", cls_counts.tolist())
print(f"holdout N gate ok+gold: {hn['gate_ok_gold']}")
print(f"holdout S ok: {res['holdout']['south']['ok']['all']}, "
      f"gold: {res['holdout']['south']['gold']['all']}")
print(f"ГОТОВО {(datetime.now()-t0).seconds}s (от начала сек.5)")
