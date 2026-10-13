#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.92 — quality-патч 5 дегенеративных северных prior-only зон (баг can30 5.84).

Баг: synth_quality 5.84 (унаследован в 5.90 для северных prior-only зон)
читал bareearth (int16, СМ) как метры -> can30 = clip((DSM-FABDEM_см)*100)=0
на всей зоне. 5.90 починил только юг (write_south_quality, /100); северные
prior-only N1/N13/STREL/AVTOV/LIGO остались b3==0 -> гейт 5.91 дал им rest
(не ok/gold), и они были исключены из prior-калибровки.

5.92 (пороги гейта 5.91 заморожены — калибровка не пересматривается):
  patch    : can30 5 зон по рецепту write_south_quality 5.90 (FABDEM/100 +
             GLO30 DSM, родная 2м-сетка зоны); патч spb_quality_5.90.tif
             band 3 (только px b1>=ND — strip-перекрытия не трогаются);
             перегейт окон -> mclf_v3_mask_5.90.tif + mclf_v3_dem_5.90.tif
             (prior-ветвь: gold can30<50; ok-полоса вырождена в 5.91).
  validate : канон v2 север — кросс-чек порога can30<50 на train, holdout
             по классам до/после; канон v3 rev 5.92 (can30/base/dsm
             пересчитаны); zone_gate_v3_5.92.csv (5 строк пересчитаны +
             фикс region STREL — в 5.91 startswith("S") ложно поймал STREL);
             отчёт results/mclf_v3_patch_5.92.{md,json,png}.

Запуск: python3 mclf_v3_patch_5.92.py [patch|validate|all]
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows as W
from rasterio.enums import Resampling
from rasterio.warp import reproject

R = Path("/tmp/my-project/gh-clone")
MOSD = R / "download/mosaic_5.90"
QUAL = MOSD / "spb_quality_5.90.tif"
DEMF = MOSD / "spb_hybrid_2m_mosaic_5.90.tif"
MASKF = R / "download/mclf_v3/mclf_v3_mask_5.90.tif"
CDEMF = R / "download/mclf_v3/mclf_v3_dem_5.90.tif"
NORTH = R / "scripts/data/icesat2_clean_v2_5.88.csv.gz"
CAN91 = R / "scripts/data/icesat2_canon_v3_5.91.csv.gz"
CAN92 = R / "scripts/data/icesat2_canon_v3_5.92.csv.gz"
ZONESF = R / "results/zones_summary_5.83.csv"
ZCSV91 = R / "results/zone_gate_v3_5.91.csv"
ZCSV92 = R / "results/zone_gate_v3_5.92.csv"
STATE = R / "results/mclf_v3_patch_5.92_state.json"
OUTJ = R / "results/mclf_v3_patch_5.92.json"
OUTMD = R / "results/mclf_v3_patch_5.92.md"
OUTPNG = R / "results/mclf_v3_patch_5.92.png"
FAB = R / "scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif"
GLO = R / "scripts/dem_cache/_glo30_tmp"
PROD = R / "download/products_5.83"

G91 = json.loads((R / "results/gate_v3_5.91.json").read_text())
MJ = json.loads((R / "results/mosaic_5.90.json").read_text())
W0, S0, E0, N0 = MJ["grid"]["bbox"]
PX = MJ["grid"]["px"]
ND = 65000
QNOD = 65535
NDEM = -32768

g = G91["gate"]
G_N = int(g["north_gold"]["n_cov_min"])
G_S = g["north_gold"]["sigma_max"]
G_C = g["north_gold"]["canopy_max"]
O_N = int(g["north_ok"]["n_cov_min"])
O_S = g["north_ok"]["sigma_max"]
O_C = g["north_ok"]["canopy_max"]
G_CS = float(g["south_gold"]["canopy_max"])
O_CS = float(g["south_ok"]["canopy_max"])
G_Si = 10**9 if G_S is None else float(G_S)
O_Si = 10**9 if O_S is None else float(O_S)
G_Ci = 10**9 if G_C is None else float(G_C)
O_Ci = 10**9 if O_C is None else float(O_C)

DEG_EXPECT = ["N1_5.83", "N13_5.83", "STREL", "AVTOV", "LIGO"]


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def ptag(t):
    return t if (not t.startswith("N") or t.endswith("_5.83")) else f"{t}_5.83"


def recompute_can30(b, Wz, Hz, tr):
    """Рецепт write_south_quality 5.90 один-в-один (FABDEM/100 + GLO30 DSM)."""
    with rasterio.open(FAB) as fsrc:
        fb_w = W.from_bounds(b[0] - 0.02, b[1] - 0.02, b[2] + 0.02,
                             b[3] + 0.02, fsrc.transform).round_lengths()
        fb = fsrc.read(1, window=fb_w).astype("float32")
        fb[fb == fsrc.nodata] = np.nan
        fb = fb / 100.0          # bareearth хранит СМ (int16) -> метры (ФИКС)
        ftr = rasterio.windows.transform(fb_w, fsrc.transform)
        fab = np.full((Hz, Wz), np.nan, dtype="float32")
        reproject(fb, fab, src_transform=ftr, src_crs=fsrc.crs,
                  dst_transform=tr, dst_crs="EPSG:4326",
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
    P = 40 * 0.0002777777778
    dsm = np.full((Hz, Wz), np.nan, dtype="float32")
    for n in (int(np.floor(b[1] - P)), int(np.floor(b[3] + P))):
        for e in (int(np.floor(b[0] - P)), int(np.floor(b[2] + P))):
            f = GLO / f"N{n:02d}_00_E{e:03d}_00.tif"
            if not f.exists():
                continue
            with rasterio.open(f) as sr:
                a = sr.read(1).astype("float32")
                buf = np.full((Hz, Wz), np.nan, dtype="float32")
                reproject(a, buf, src_transform=sr.transform, src_crs=sr.crs,
                          dst_transform=tr, dst_crs="EPSG:4326",
                          resampling=Resampling.bilinear, dst_nodata=np.nan)
                dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
    can = dsm - fab
    ok = np.isfinite(can)
    return np.where(ok, np.clip(np.rint(np.where(ok, can, 0) * 100), 0,
                                QNOD - 1), QNOD).astype("uint16")


def detect_degenerate(dsq):
    """Детекция 5.91: центр 200x200 prior (b1>=ND >=95%) + max(b3)==0."""
    H, W_ = dsq.height, dsq.width
    zs = pd.read_csv(ZONESF)
    out = []
    for _, z in zs.iterrows():
        try:
            Wz, Sz, Ez, Nz = [float(v) for v in z["bbox"].split()]
        except Exception:
            continue
        c0 = max(int((Wz - dsq.transform.c) / dsq.transform.a), 0)
        c1 = min(int((Ez - dsq.transform.c) / dsq.transform.a), W_)
        r0 = max(int((Nz - dsq.transform.f) / dsq.transform.e), 0)
        r1 = min(int((Sz - dsq.transform.f) / dsq.transform.e), H)
        if c1 <= c0 or r1 <= r0:
            continue
        rc, cc = r0 + (r1 - r0) // 2, c0 + (c1 - c0) // 2
        b1c = dsq.read(1, window=W.Window(max(cc - 100, 0), max(rc - 100, 0),
                                          200, 200))
        if (b1c >= ND).mean() < 0.95:
            continue                    # strip-зона: n_cov валиден
        b3z = dsq.read(3, window=W.Window(c0, r0, c1 - c0, r1 - r0))[::3, ::3]
        v3 = b3z[b3z < ND]
        if v3.size and v3.max() == 0:
            out.append(dict(tag=str(z["tag"]), bbox=[Wz, Sz, Ez, Nz]))
    return out


# ================================================== stage: patch
# NOTE(GDAL): сброс deflate-блоков происходит только при close() (урок 5.90),
# поэтому патч выполняется ПО ОДНОЙ ЗОНЕ на процесс ('zone TAG'); каждый
# вызов открывает растры, патчит окно и корректно закрывает.
def patch_one(tag, b, dsq, dsd, dsmk, dsdc):
    """Патч одной зоны. Возвращает словарь зоны. Idempotent: b3 prior-px
    перезаписывается целиком, strip-перекрытия (b1<ND) не трогаются."""
    with rasterio.open(PROD / f"hybrid_2m_5.83_{ptag(tag)}.tif") as s:
        Wz, Hz, tr = s.width, s.height, s.transform
        assert abs(s.bounds.left - b[0]) < 1e-6 and \
            abs(s.bounds.top - b[3]) < 1e-6, f"{tag}: сетка != bbox"
    G = int(round((N0 - b[3]) / PX))
    J = int(round((b[0] - W0) / PX))
    win = W.Window(J, G, Wz, Hz)
    can30 = recompute_can30(b, Wz, Hz, tr)
    b1 = dsq.read(1, window=win)
    pm = b1 >= ND                       # prior-only px (strip не трогаем)
    b3old = dsq.read(3, window=win)
    patched = can30[pm]
    oldv = b3old[pm]
    dsq.write(np.where(pm, can30, b3old), 3, window=win)
    # --- перегейт окна (пороги 5.91) ---
    qq = dsq.read((1, 2, 3), window=win)
    b1, b2, b3 = qq[0].astype(np.int32), qq[1].astype(np.int32), \
        qq[2].astype(np.int32)
    dem = dsd.read(1, window=win)
    dem_ok = dem != NDEM
    canok = b3 < ND
    north_br = (b1 >= 1) & (b1 < ND) & canok & (b2 < ND)
    prior_all = (b1 >= ND) & canok & dem_ok
    valid_n = north_br & dem_ok
    gold_n = valid_n & (b1 >= G_N) & (b2 <= G_Si) & (b3 < G_Ci)
    ok_n = valid_n & (b1 >= O_N) & (b2 <= O_Si) & (b3 < O_Ci)
    gold_s_px = prior_all & (b3 < G_CS)
    ok_s_px = prior_all & (b3 >= G_CS) & (b3 < O_CS)
    cls = np.zeros(b1.shape, np.uint8)
    cls[valid_n | prior_all] = 1
    cls[ok_n | ok_s_px] = 2
    cls[gold_n | gold_s_px] = 3
    dsmk.write(cls, 1, window=win)
    dsdc.write(np.where(cls >= 2, dem, NDEM).astype("int16"), 1, window=win)
    # cls "до" = состояние 5.91: prior-px дегенеративных зон были исключены
    # из prior-ветви (deg_boxes) -> rest; strip-px пересчитываются как
    # обычно. b3_before=0 только для учёта (баг), в тир-логику не идёт.
    b3_before = np.where(pm, 0, b3old).astype(np.int32)
    canok_b = b3_before < ND
    north_br_b = (b1 >= 1) & (b1 < ND) & canok_b & (b2 < ND)
    valid_n_b = north_br_b & dem_ok
    gold_n_b = valid_n_b & (b1 >= G_N) & (b2 <= G_Si) & (b3_before < G_Ci)
    ok_n_b = valid_n_b & (b1 >= O_N) & (b2 <= O_Si) & (b3_before < O_Ci)
    cls_b = np.zeros(b1.shape, np.uint8)
    cls_b[valid_n_b | (pm & dem_ok)] = 1     # prior deg-px -> rest (5.91)
    cls_b[ok_n_b] = 2
    cls_b[gold_n_b] = 3
    pv = patched[patched < QNOD]
    zn = dict(
        tag=tag, bbox=[float(x) for x in b], G=G, J=J, Wz=Wz, Hz=Hz,
        px_total=int(Wz * Hz), prior_px=int(pm.sum()),
        patch_share=round(float(pm.sum()) / (Wz * Hz), 4),
        old_b3_max=int(oldv.max()) if len(oldv) else None,
        can30_med_cm=round(float(np.median(pv)), 1) if pv.size else None,
        can30_ge100=round(float((pv >= 100).sum()) / max(pv.size, 1), 4),
        can30_nodata=round(float((patched >= QNOD).sum())
                           / max(len(patched), 1), 4),
        cls_before=dict(zip(["excl", "rest", "ok", "gold"],
                            np.bincount(cls_b.ravel(), minlength=4)
                            .tolist())),
        cls_after=dict(zip(["excl", "rest", "ok", "gold"],
                           np.bincount(cls.ravel(), minlength=4).tolist())))
    log(f"{tag}: prior px {zn['prior_px']} ({zn['patch_share']:.1%}), "
        f"can30 med {zn['can30_med_cm']} см, >=100 {zn['can30_ge100']:.1%}, "
        f"nodata {zn['can30_nodata']:.1%}")
    return zn


def merge_state(zn):
    names = ["excl", "rest", "ok", "gold"]
    state = dict(date=datetime.now().isoformat(), zones=[],
                 cls_before={k: 0 for k in names},
                 cls_after={k: 0 for k in names}, minutes=0.0)
    if STATE.exists():
        old = json.loads(STATE.read_text())
        state["zones"] = [z for z in old.get("zones", [])
                          if z["tag"] != zn["tag"]]
    state["zones"].append(zn)
    for z in state["zones"]:
        for k in names:
            state["cls_before"][k] += z["cls_before"][k]
            state["cls_after"][k] += z["cls_after"][k]
    state["deg_tags"] = [z["tag"] for z in state["zones"]]
    state["thresholds"] = dict(gold_n=[G_N, G_S, G_C], ok_n=[O_N, O_S, O_C],
                               gold_s_can=G_CS, ok_s_can=O_CS)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def zones_to_patch():
    dsq = rasterio.open(QUAL)
    deg = detect_degenerate(dsq)
    dsq.close()
    tags = [d["tag"] for d in deg]
    log(f"дегенеративные зоны: {tags}")
    if set(tags) != set(DEG_EXPECT):
        log(f"ВНИМАНИЕ: ожидаемый список {DEG_EXPECT} != найденный {tags}")
    return deg


def run_zone(tag, b):
    t0 = datetime.now()
    dsq = rasterio.open(QUAL, "r+")
    dsd = rasterio.open(DEMF)
    dsmk = rasterio.open(MASKF, "r+")
    dsdc = rasterio.open(CDEMF, "r+")
    zn = patch_one(tag, b, dsq, dsd, dsmk, dsdc)
    dsq.close(); dsd.close(); dsmk.close(); dsdc.close()
    zn["minutes"] = round((datetime.now() - t0).total_seconds() / 60, 1)
    merge_state(zn)
    log(f"{tag} закрыт и сброшен на диск ({zn['minutes']} мин)")


def stage_patch():
    t0 = datetime.now()
    for d in zones_to_patch():
        run_zone(d["tag"], d["bbox"])
    state = json.loads(STATE.read_text())
    names = ["excl", "rest", "ok", "gold"]
    log(f"патч готов ({(datetime.now() - t0).total_seconds() / 60:.1f} мин), "
        f"дельта классов: " +
        ", ".join(f"{k} {state['cls_after'][k] - state['cls_before'][k]:+d}"
                  for k in names))


# ================================================== stage: validate
def sample_tif(path, lons, lats, band=1, chunk=40000):
    """Сэмпл растра по (lon,lat), round = ближайший центр пикселя (5.91)."""
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
            inb = ((rows >= 0) & (rows < s.height) &
                   (cols >= 0) & (cols < s.width))
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


def stage_validate():
    state = json.loads(STATE.read_text())
    tags = [z["tag"] for z in state["zones"]]
    zb = {z["tag"]: z["bbox"] for z in state["zones"]}
    zwin = {z["tag"]: z for z in state["zones"]}

    # ---------- точки канона v2 в 5 зонах ----------
    n = pd.read_csv(NORTH)
    zid = np.full(len(n), "", dtype=object)
    for t, (w_, s_, e_, n_) in zb.items():
        mm = ((n.lat >= s_) & (n.lat <= n_) &
              (n.lon >= w_) & (n.lon <= e_)).values
        zid[mm] = t
    n = n[zid != ""].copy()
    n["zid"] = zid[zid != ""]
    log(f"точки канона v2 в 5 зонах: {len(n)}")
    n["can92"] = sample_tif(QUAL, n.lon.values, n.lat.values, band=3)
    n["cls92"] = sample_tif(MASKF, n.lon.values, n.lat.values)
    n["blk_half"] = ((np.floor(n.lat / 0.02).astype(int) +
                      np.floor(n.lon / 0.0127).astype(int)) % 2).values
    dh = n.dH2m
    n["base_old"] = dh.abs() <= 3.0     # can30==0 (баг) -> base = все |dH|<=3
    n["base_new"] = (dh.abs() <= 3.0) & (n.can92 < 100)
    n["dsm_new"] = (dh.abs() <= 3.0) & (n.can92 >= 100)

    # ---------- таблица зон: до/после ----------
    ztab = []
    for t in tags:
        z = n[n.zid == t]
        ho = z[z.blk_half == 1]
        ztab.append(dict(
            tag=t,
            n_old=int(z.base_old.sum()),
            mad_old=mstats(z.dH2m[z.base_old]).get("mad"),
            med_old=mstats(z.dH2m[z.base_old]).get("med"),
            n_new=int(z.base_new.sum()),
            mad_new=mstats(z.dH2m[z.base_new]).get("mad"),
            gold_all=mstats(z.dH2m[z.base_new & (z.cls92 == 3)]),
            rest_all=mstats(z.dH2m[z.base_new & (z.cls92 == 1)]),
            gold_ho=mstats(ho.dH2m[ho.base_new & (ho.cls92 == 3)]),
            rest_ho=mstats(ho.dH2m[ho.base_new & (ho.cls92 == 1)]),
            dsm_new=int(z.dsm_new.sum()),
            drop_nodata=int((z.can92.isna() & (z.dH2m.abs() <= 3)).sum()),
            share_gold_px=zwin[t]["cls_after"]["gold"] / zwin[t]["px_total"]))
    for r in ztab:
        log(f"{r['tag']}: MAD {r['mad_old']} -> {r['mad_new']} "
            f"(gold {r['gold_all'].get('mad')}, n {r['n_old']}->{r['n_new']},"
            f" dsm +{r['dsm_new']})")

    # ---------- кросс-чек порога can30<50 (train, pooled 5 зон) ----------
    tr5 = n[n.blk_half == 0]
    ho5 = n[n.blk_half == 1]
    bands = [("can<50", (tr5.can92 < 50)),
             ("can 50-100", (tr5.can92 >= 50) & (tr5.can92 < 100)),
             ("can 100-200", (tr5.can92 >= 100) & (tr5.can92 < 200)),
             ("can>=200", (tr5.can92 >= 200))]
    tr_bands = {nm: dict(band=nm,
                         **mstats(tr5.dH2m[mm & (tr5.dH2m.abs() <= 3)]))
                for nm, mm in bands}
    ho_gold = mstats(ho5.dH2m[(ho5.can92 < 50) & (ho5.dH2m.abs() <= 3)])
    ho_rest = mstats(ho5.dH2m[(ho5.can92 >= 50) & (ho5.can92 < 100) &
                              (ho5.dH2m.abs() <= 3)])
    log(f"train pooled can<50: {tr_bands['can<50']} (юг train gold "
        f"{G91['gate']['south_gold']['train_raw'].get('mad')})")
    log(f"holdout pooled gold: {ho_gold} (юг holdout gold "
        f"{G91['holdout']['south']['gold']['all'].get('mad')})")

    # ---------- канон v3 rev 5.92 ----------
    c = pd.read_csv(CAN91)
    inm = np.zeros(len(c), dtype=bool)
    for t, (w_, s_, e_, n_) in zb.items():
        inm |= (((c.region == "N") & (c.lat >= s_) & (c.lat <= n_) &
                 (c.lon >= w_) & (c.lon <= e_)).values)
    idx = c.index[inm]
    c.loc[idx, "can30"] = sample_tif(QUAL, c.loc[idx, "lon"].values,
                                     c.loc[idx, "lat"].values, band=3)
    keep = c.can30.notna() & (c.can30 < 100)
    drop_ge100 = int((c.loc[~keep, "can30"] >= 100).sum())
    drop_nan = int(c.loc[~keep, "can30"].isna().sum())
    c2 = c[keep].copy()
    zm = np.zeros(len(c2), dtype=bool)
    for t, (w_, s_, e_, n_) in zb.items():
        zm |= (((c2.region == "N").values & (c2.lat >= s_) &
                (c2.lat <= n_) & (c2.lon >= w_) & (c2.lon <= e_)))
    rec = ((c2.sigma90 <= 50) & (c2.trust >= 1)).values
    c2["core_v3"] = c2["core_v3"].astype(bool).values
    c2.loc[zm, "core_v3"] = rec[zm]
    c2.to_csv(CAN92, index=False, compression="gzip")
    canon = dict(v3_91_base=int(len(c)), v3_92_base=int(len(c2)),
                 drop_ge100=drop_ge100, drop_nodata=drop_nan,
                 core_91=int(c.core_v3.sum()), core_92=int(c2.core_v3.sum()),
                 dsm_91=int(G91["canon"]["v3_dsm"]),
                 dsm_92=int(G91["canon"]["v3_dsm"]) + drop_ge100)
    log(f"канон v3 rev 5.92: base {canon['v3_91_base']} -> "
        f"{canon['v3_92_base']} (dsm +{drop_ge100}, выбыло nodata {drop_nan})")

    # ---------- zone CSV 5.92 ----------
    zc = pd.read_csv(ZCSV91)
    dsmk = rasterio.open(MASKF)
    for t in tags:
        zw = zwin[t]
        win = W.Window(zw["J"], zw["G"], zw["Wz"], zw["Hz"])
        cnt = np.bincount(dsmk.read(1, window=win).ravel(), minlength=4)
        tot = int(cnt.sum())
        z = n[n.zid == t]
        mv2 = mstats(z.dH2m[z.base_old]).get("mad")
        mv3 = mstats(z.dH2m[z.base_new]).get("mad")
        i = zc.index[zc.tag == t][0]
        zc.loc[i, "region"] = "N"
        zc.loc[i, "share_gold"] = round(float(cnt[3]) / tot, 3)
        zc.loc[i, "share_ok"] = round(float(cnt[2]) / tot, 3)
        zc.loc[i, "share_rest"] = round(float(cnt[1]) / tot, 3)
        zc.loc[i, "share_excl"] = round(float(cnt[0]) / tot, 3)
        zc.loc[i, "n_pts_v3"] = int(z.base_new.sum())
        zc.loc[i, "mad_v3"] = mv3
        zc.loc[i, "n_pts_v2"] = int(z.base_old.sum())
        zc.loc[i, "mad_v2"] = mv2
    dsmk.close()
    zc["region"] = ["S" if re.fullmatch(r"S\d+", str(t)) else "N"
                    for t in zc.tag]
    zc.to_csv(ZCSV92, index=False)
    log(f"zone CSV: {ZCSV92.name} (region STREL -> N)")

    # ---------- PNG ----------
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
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True)
    ax = axes[0]
    y = np.arange(len(ztab))
    mo = [r["mad_old"] if r["mad_old"] is not None else np.nan
          for r in ztab]
    mg = [r["gold_all"].get("mad", np.nan) or np.nan for r in ztab]
    mr = [r["rest_all"].get("mad", np.nan) or np.nan for r in ztab]
    ax.barh(y - 0.25, mo, height=0.25, color="#c44e52", alpha=0.9,
            label="до (base с багом can30==0)")
    ax.barh(y, mg, height=0.25, color="#55a868", alpha=0.9,
            label=f"после, GOLD can30<{G_CS:.0f}")
    ax.barh(y + 0.25, mr, height=0.25, color="#8172b2", alpha=0.9,
            label="после, rest can30 50-100")
    ax.set_yticks(y)
    ax.set_yticklabels([r["tag"] for r in ztab], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("MAD, м (|dH|<=3)")
    ax.set_title("5 зон: MAD до/после quality-патча")
    ax.legend(fontsize=9)
    ax.grid(axis="x", alpha=0.3)
    ax = axes[1]
    sh_g = [r["share_gold_px"] for r in ztab]
    sh_r = [zwin[r["tag"]]["cls_after"]["rest"] / zwin[r["tag"]]["px_total"]
            for r in ztab]
    sh_e = [zwin[r["tag"]]["cls_after"]["excl"] / zwin[r["tag"]]["px_total"]
            for r in ztab]
    ax.barh(y, sh_g, color="#55a868", alpha=0.9, label="gold")
    ax.barh(y, sh_r, left=sh_g, color="#8172b2", alpha=0.9, label="rest")
    ax.barh(y, sh_e, left=[a + b for a, b in zip(sh_g, sh_r)],
            color="#bbbbbb", alpha=0.9, label="excl")
    ax.set_yticks(y)
    ax.set_yticklabels([r["tag"] for r in ztab], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("доля пикселей зоны")
    ax.set_title("классы гейта после патча")
    ax.legend(fontsize=9, loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    fig.suptitle("5.92: quality-патч can30 (баг 5.84) 5 северных prior-only "
                 "зон + перегейт", fontsize=13)
    fig.savefig(OUTPNG, dpi=130)
    plt.close(fig)

    # ---------- JSON + MD ----------
    names = ["excl", "rest", "ok", "gold"]
    g91cls = G91["raster"]["classes"]
    delta = {k: state["cls_after"][k] - state["cls_before"][k]
             for k in names}
    glob92 = {k: g91cls[k] + delta[k] for k in names}
    res = dict(
        date=datetime.now().isoformat(),
        method="5.92: quality-патч can30 (баг 5.84: bareearth cm как метры) "
               "5 северных prior-only зон + перегейт окон; пороги гейта 5.91 "
               "заморожены; валидация канон v2 (train кросс-чек + holdout)",
        patch=state,
        transfer_check=dict(
            south_train_gold_mad=G91["gate"]["south_gold"]["train_raw"]
            .get("mad"),
            south_holdout_gold_mad=G91["holdout"]["south"]["gold"]["all"]
            .get("mad"),
            north5_train_bands=tr_bands,
            north5_holdout_gold=ho_gold,
            north5_holdout_rest=ho_rest),
        zones=ztab,
        canon=canon,
        zone_csv=str(ZCSV92.name),
        cls_delta=delta,
        cls_global_91=g91cls,
        cls_global_92=glob92)
    json.dump(res, open(OUTJ, "w"), ensure_ascii=False, indent=1)

    def fmt(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return round(v, 3) if isinstance(v, float) else v

    rows_z = "\n".join(
        f"| {r['tag']} | {r['n_old']} | {fmt(r['mad_old'])} | "
        f"{r['gold_all'].get('n','—')} | {fmt(r['gold_all'].get('mad'))} | "
        f"{r['rest_all'].get('n','—')} | {fmt(r['rest_all'].get('mad'))} | "
        f"{r['n_new']} | {fmt(r['mad_new'])} | {r['share_gold_px']:.0%} |"
        for r in ztab)
    rows_b = "\n".join(
        f"| {v['band']} | {v.get('n','—')} | {fmt(v.get('med'))} | "
        f"{fmt(v.get('mad'))} | {fmt(v.get('p95'))} |"
        for v in tr_bands.values())
    rows_p = "\n".join(
        f"| {z['tag']} | {z['prior_px']} ({z['patch_share']:.0%}) | "
        f"{z['can30_med_cm']} | {z['can30_ge100']:.1%} | "
        f"{z['can30_nodata']:.1%} |" for z in state["zones"])
    sgn = delta
    md = f"""# 5.92 — Quality-патч can30: 5 северных prior-only зон (баг 5.84)

Дата: {datetime.now().date()}. Пункт «Открыто» блока 5.91. Скрипт:
`scripts/mclf_v3_patch_5.92.py`. Пороги гейта 5.91 **заморожены**
(GOLD(S) can30<{G_CS:.0f}; OK(S) вырожден), калибровка не пересматривалась.

## 1. Контекст

synth_quality 5.84 читал bareearth (int16, **см**) как метры: can30 =
clip((GLO30 DSM − FABDEM_см)·100, 0) = 0 на всей зоне. 5.90 починил рецепт
для юга (write_south_quality, /100), но северные prior-only зоны получили
quality из synth 5.83-era / синтеза без /100 — b3==0. Гейт 5.91: зоны
{', '.join(tags)} → rest (не ok/gold), исключены из prior-калибровки.

## 2. Патч quality (spb_quality_5.90.tif, band 3)

Рецепт write_south_quality 5.90 один-в-один (FABDEM/100 + GLO30 DSM, родная
2м-сетка зоны). Запись только в px b1>=ND (prior-only) — strip-перекрытия
не тронуты. Sanity: старый b3 max = {state['zones'][0]['old_b3_max']}
(подтверждение бага).

| Зона | prior px | can30 мед., см | can30>=100 | nodata |
|---|---|---|---|---|
{rows_p}

## 3. Перегейт (mclf_v3_mask/dem_5.90, окна 5 зон)

Дельта классов по 5 окнам (excl/rest/ok/gold): {sgn['excl']:+,} / {sgn['rest']:+,} /
{sgn['ok']:+,} / {sgn['gold']:+,}. Глобально (5.91 → 5.92): excl
{g91cls['excl']:,} → {glob92['excl']:,}; rest {g91cls['rest']:,} →
{glob92['rest']:,}; ok {g91cls['ok']:,} → {glob92['ok']:,}; gold
{g91cls['gold']:,} → {glob92['gold']:,}.

## 4. Валидация (канон v2 север, 5 зон; порог can30<50 — трансфер с юга)

Кросс-чек на train-половине (блок-паритет 5.83), pooled 5 зон:

| Полоса can30 | n | med | MAD | p95 |
|---|---|---|---|---|
{rows_b}

Южный референс: GOLD(S) train MAD {G91['gate']['south_gold']['train_raw'].get('mad')},
holdout gold {G91['holdout']['south']['gold']['all'].get('mad')}.
Holdout pooled 5 зон: gold (can<50) MAD {fmt(ho_gold.get('mad'))}
(n={ho_gold.get('n')}), rest [50,100) MAD {fmt(ho_rest.get('mad'))}
(n={ho_rest.get('n')}).

| Зона | n до | MAD до | n gold | MAD gold | n rest | MAD rest | n после | MAD после | gold px |
|---|---|---|---|---|---|---|---|---|---|
{rows_z}

## 5. Канон v3 (rev 5.92)

base {canon['v3_91_base']:,} → {canon['v3_92_base']:,}: −{canon['drop_ge100']}
(переведены в DSM-кроны: dsm {canon['dsm_91']:,} → {canon['dsm_92']:,}),
−{canon['drop_nodata']} (can30 nodata — вне канона). Файл:
`scripts/data/icesat2_canon_v3_5.92.csv.gz`.

## 6. Ограничения

- Порог can30<50 перенесён с юга (южная калибровка 5.91); северный
  кросс-чек на train — контроль трансфера, не новая калибровка.
- can30 = GLO30 DSM − FABDEM DTM — грубая 30м-оценка канопи; внутри
  GOLD-полосы точность продукта остаётся prior-класса (юг: holdout 0.309).
- dH точек — по продукту 5.84/5.90 (север бит-в-бит), конвенции дампа
  5.86 (floor) против 5.91 (round) — расхождения в 3-м знаке MAD.
- Фикс учёта зоны: STREL в zone_gate CSV 5.91 была помечена S
  (startswith("S")) — в 5.92 region = S<цифры> else N.

## 7. Файлы

- `download/mosaic_5.90/spb_quality_5.90.tif` — band 3 запатчен (5 окон)
- `download/mclf_v3/mclf_v3_mask_5.90.tif`, `mclf_v3_dem_5.90.tif` — перегейт
- `results/mclf_v3_patch_5.92.json` / `.png`, `results/zone_gate_v3_5.92.csv`
- `scripts/data/icesat2_canon_v3_5.92.csv.gz` — канон v3 rev 5.92
"""
    open(OUTMD, "w").write(md)
    log("MD записан")
    print("\n=== ИТОГ 5.92 ===")
    print(f"деген-зоны пропатчены: {tags}")
    print("дельта классов окон:", delta, "| глобально 5.92:", glob92)
    for r in ztab:
        print(f"  {r['tag']}: MAD {r['mad_old']} -> gold "
              f"{r['gold_all'].get('mad')} / rest "
              f"{r['rest_all'].get('mad')}")
    print(f"канон v3: base {canon['v3_91_base']} -> {canon['v3_92_base']}, "
          f"dsm {canon['dsm_91']} -> {canon['dsm_92']}")
    print(f"holdout pooled gold: {ho_gold}")


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "all"
    if st == "zone":
        tag = sys.argv[2]
        _zs = pd.read_csv(ZONESF)
        _b = [float(x) for x in
              _zs.loc[_zs.tag == tag, "bbox"].iloc[0].split()]
        _deg = {d["tag"] for d in zones_to_patch()}
        if tag not in _deg:
            log(f"ВНИМАНИЕ: {tag} не детектирована как дегенеративная "
                f"(возможно, частично пропатчена при прерванном запуске) — "
                f"патчим принудительно (идемпотентно)")
        run_zone(tag, _b)
    else:
        if st in ("patch", "all"):
            stage_patch()
        if st in ("validate", "all"):
            stage_validate()
