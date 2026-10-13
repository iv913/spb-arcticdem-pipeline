#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.93a — Пилот уплотнения съёмки ATL06 (SlideRule), чанк-исполнение.

Единственное изменение протокола 5.86e/5.89b: cnf HIGH(4) -> MEDIUM(3) / LOW(2)
(ATL03 signal_conf_ph: 4=high, 3=medium, 2=low; фильтр ">= порога").
Остальное заморожено: len=40, res=20, ats=10, cnt=5, maxi=1, srt=LAND,
2019-2026. Окна сегментов детерминированы len/res -> дедуп по ключу
(rgt, cycle, gt, date, round(lat,4), round(lon,4)) — северный дамп 5.86
без time_ns, поэтому ключ координатный (шаг окон 20 м > ячейки 11 м,
коллизий нет); на южном тайле контроль true-ключа time_ns.

Пилотные тайлы (1 город-стрип, 1 лес-стрип, 1 юг-prior):
  city : 30.20-30.32 / 59.8905-60.0045  (центр + N1 prior)
  forest: 30.44-30.56 / 60.0045-60.1183 (RZHEV/OZER леса)
  south:  30.36-30.49 / 59.70-59.80     (Пушкинский, prior-only)

Запуск: DENS_VARIANT=medium|low python3 ...  (по варианту за прогон,
возобновляемо: метрики в OUTJ, новые точки в dem_cache/densify_pilot_5.93/)

Метрики на НОВЫХ окнах (vs базовые дампы 5.86+5.89, ключ-дедуп):
  n_new, новые ячейки 0.001°, MAD base (can30<100), MAD gold
  (N: n_cov>=2 & sigma<=50 & can<200; prior: can<50).

GO-критерий: pooled n_new >= +15% к HIGH-окнам в тех же bbox И
MAD(new gold) <= 0.45 (n_gold >= 300).

Выход: results/densify_pilot_5.93.json + pooled-блок; GO-вердикт.
"""
import os
import json
import time
import glob

import numpy as np
import pandas as pd

R = "/tmp/my-project/gh-clone"
DEM = f"{R}/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif"
QUAL = f"{R}/download/mosaic_5.90/spb_quality_5.90.tif"
ND = 65000
DELTA = 16.04

NORTH_DUMP = f"{R}/scripts/data/icesat2_atl06_dump_5.86.csv.gz"
SOUTH_DUMP = f"{R}/scripts/data/icesat2_atl06_dump_south_5.89.csv.gz"
OUTJ = f"{R}/results/densify_pilot_5.93.json"
PTDIR = f"{R}/scripts/dem_cache/densify_pilot_5.93"
os.makedirs(PTDIR, exist_ok=True)

TILES = {
    "city":   (30.20, 59.8905, 30.32, 60.0045),
    "forest": (30.44, 60.0045, 30.56, 60.1183),
    "south":  (30.36, 59.70, 30.49, 59.80),
}
VARIANTS = {"medium": 3, "low": 2}
RUN = os.environ.get("DENS_VARIANT", "medium,low").split(",")

# ---------------- сэмпл растра (round, конвенция 5.91) ----------------
import rasterio
import rasterio.windows as W


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


def gold_of(df):
    fin = df.dH.notna()
    prior = df.n_cov.isna()                     # prior-only: b1 = nodata
    strip = (~prior) & fin
    b = fin & (df.dH.abs() <= 3.0) & (df.can30 < 100)
    return b, (b & strip & (df.n_cov >= 2) & (df.sigma <= 50) &
               (df.can30 < 200)) | (b & prior & (df.can30 < 50))


# ---------------- состояние ----------------
res = {"protocol": "atl06p len=40 res=20 ats=10 cnt=5 maxi=1 srt=LAND "
                   "2019-2026; варианты cnf (4=HIGH база, 3=MEDIUM, 2=LOW); "
                   "дедуп (rgt,cycle,gt,date,round(lat,4),round(lon,4)); "
                   "растр 5.90, round, ND=65000",
       "tiles": {}, "pooled": {}}
if os.path.exists(OUTJ):
    try:
        res = json.load(open(OUTJ))
    except Exception:
        pass
res["tiles"] = res.get("tiles", {})
res["pooled"] = res.get("pooled", {})

# ---------------- база HIGH: дампы 5.86 + 5.89 ----------------
print("=== загрузка базовых дампов HIGH ===", flush=True)
bn = pd.read_csv(NORTH_DUMP, usecols=["lat", "lon", "rgt", "cycle", "gt",
                                      "date"])
bs = pd.read_csv(SOUTH_DUMP, usecols=["lat", "lon", "rgt", "cycle", "gt",
                                      "date", "time_ns"])
bs["time_ns"] = bs.time_ns.astype("int64")
base = pd.concat([bn, bs], ignore_index=True)
base["date"] = base.date.astype(str).str.replace(".0", "", regex=False)
base["k"] = key(base)
base = base.drop_duplicates("k")
print(f"база HIGH (дедуп по окнам): {len(base)}", flush=True)

# ---------------- SlideRule ----------------
from sliderule import icesat2
icesat2.init("slideruleearth.io", verbose=False)

parm_base = dict(t0="2019-01-01", t1="2026-12-31", srt=icesat2.SRT_LAND,
                 ats=10.0, cnt=5, len=40.0, res=20.0, maxi=1)

for vname in RUN:
    cnf = VARIANTS[vname]
    for tname, (x0, y0, x1, y1) in TILES.items():
        pf = f"{PTDIR}/{tname}_{vname}.csv.gz"
        if (tname in res["tiles"] and vname in res["tiles"][tname] and
                os.path.exists(pf)):
            print(f"[{tname}/{vname}] уже готово — skip", flush=True)
            continue
        inb = ((base.lat >= y0) & (base.lat < y1) & (base.lon >= x0) &
               (base.lon < x1))
        b = base[inb]
        kb = set(b.k)
        res["tiles"].setdefault(tname, {})["bbox"] = [x0, y0, x1, y1]
        res["tiles"][tname]["high"] = dict(
            n=int(len(b)),
            cells=int(pd.Series(b.lat.round(3).astype(str) + "_" +
                                b.lon.round(3).astype(str)).nunique()))
        print(f"[{tname}] HIGH: {len(b)} окон", flush=True)

        parm = dict(parm_base)
        parm["cnf"] = cnf
        parm["poly"] = [{"lon": x0, "lat": y0}, {"lon": x1, "lat": y0},
                        {"lon": x1, "lat": y1}, {"lon": x0, "lat": y1}]
        t0 = time.time()
        gdf = None
        for att in range(3):
            try:
                gdf = icesat2.atl06p(parm)
                break
            except Exception as e:
                print(f"  [{tname}/{vname}] retry {att+1}: {str(e)[:70]}",
                      flush=True)
                time.sleep(5)
        if gdf is None or not len(gdf):
            res["tiles"][tname][vname] = dict(n=0)
            json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)
            continue
        g = gdf.geometry
        d = pd.DataFrame(dict(
            lat=g.y.values, lon=g.x.values, h_mean=gdf.h_mean.values,
            h_sigma=gdf.h_sigma.values, n_fit=gdf.n_fit_photons.values,
            time_ns=gdf.index.astype("int64").values,
            rgt=gdf.rgt.values, cycle=gdf.cycle.values,
            gt=gdf["gt"].values))
        d["h_baltic"] = d.h_mean - DELTA
        d["date"] = pd.to_datetime(d.time_ns).dt.strftime("%Y%m%d")
        d["k"] = key(d)
        d["is_new"] = ~d.k.isin(kb)
        new = d[d.is_new].copy()
        mon = pd.to_datetime(new.time_ns).dt.month.values
        new["season"] = np.where(np.isin(mon, [11, 12, 1, 2, 3, 4]),
                                 "leaf_off", "leaf_on")

        # -- контроль ключа на южном тайле (там у базы есть time_ns)
        if tname == "south":
            bt = set(zip(bs.rgt, bs.cycle, bs["gt"], bs.time_ns))
            sus = sum((r.rgt, r.cycle, r.gt, r.time_ns) in bt
                      for r in new.itertuples())
            res.setdefault("key_sanity", {})[vname] = dict(
                new=int(len(new)), false_new=int(sus),
                rate=round(sus / max(len(new), 1), 4))
            print(f"  key-sanity {vname}: false_new {sus}/{len(new)}",
                  flush=True)

        # --- сэмпл растра 5.90
        new["dem2m"] = sample_tif(DEM, new.lon.values, new.lat.values) / 100.0
        new["n_cov"] = sample_tif(QUAL, new.lon.values, new.lat.values, band=1)
        new["sigma"] = sample_tif(QUAL, new.lon.values, new.lat.values, band=2)
        new["can30"] = sample_tif(QUAL, new.lon.values, new.lat.values, band=3)
        new["dH"] = new.h_baltic - new.dem2m
        bmask, gmask = gold_of(new)
        cell = new.lat.round(3).astype(str) + "_" + new.lon.round(3).astype(str)
        m = dict(
            raw=int(len(d)), new=int(len(new)),
            new_cells=int(cell.nunique()),
            sampled=int(new.dH.notna().sum()),
            base=mstats(new.dH[bmask]),
            gold=mstats(new.dH[gmask]),
            base_leaf_off=mstats(new.dH[bmask & (new.season == "leaf_off")]),
            base_leaf_on=mstats(new.dH[bmask & (new.season == "leaf_on")]),
            prior_share=round(float((new.n_cov.isna() &
                                     new.dH.notna()).mean()), 3)
            if new.dH.notna().sum() else None,
            secs=round(time.time() - t0, 1))
        res["tiles"][tname][vname] = m
        new.to_csv(pf, index=False, compression="gzip")
        json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)
        print(f"[{tname}/{vname}] raw {m['raw']} -> new {m['new']} "
              f"(+{100.0*m['new']/max(len(b),1):.1f}% к HIGH), "
              f"base {m['base']}, gold {m['gold']} ({m['secs']}s)",
              flush=True)

    # ---------------- pooled по варианту ----------------
    files = sorted(glob.glob(f"{PTDIR}/*_{vname}.csv.gz"))
    if files:
        a = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
        bmask, gmask = gold_of(a)
        res["pooled"][vname] = dict(
            new=int(len(a)),
            base=mstats(a.dH[bmask]),
            gold=mstats(a.dH[gmask]),
            base_leaf_off=mstats(a.dH[bmask & (a.season == "leaf_off")]),
            base_leaf_on=mstats(a.dH[bmask & (a.season == "leaf_on")]),
            cells=int((a.lat.round(3).astype(str) + "_" +
                       a.lon.round(3).astype(str)).nunique()))
        json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)
        print(f"POOLED {vname}: new {len(a)}, base "
              f"{res['pooled'][vname]['base']}, gold "
              f"{res['pooled'][vname]['gold']}", flush=True)

# ---------------- GO/NO-GO ----------------
if "medium" in res["pooled"]:
    mb = res["pooled"]["medium"]
    hi_n = sum(res["tiles"][t]["high"]["n"] for t in TILES
               if t in res["tiles"] and "high" in res["tiles"][t])
    g_ = mb.get("gold", {})
    gold_ok = (g_.get("n", 0) and g_.get("n", 0) >= 300 and
               g_.get("mad") is not None and g_.get("mad") <= 0.45)
    go = dict(gain_new_base_vs_high_windows=round(mb["base"].get("n", 0) /
                                                  max(hi_n, 1), 3),
              gold_n=g_.get("n"), gold_mad=g_.get("mad"),
              gold_p95=g_.get("p95"),
              decision="GO" if (mb["base"].get("n", 0) / max(hi_n, 1) >= 0.15
                                and gold_ok) else "NO-GO")
    res["go"] = go
    json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)
    print("GO:", json.dumps(go, ensure_ascii=False), flush=True)
print("ГОТОВО", flush=True)
