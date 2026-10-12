#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.89 — M-CLF-v3: пер-пиксельный гейт доверия стеку 5.84.

M-CLF-v3 (по плану 5.83/5.84): заменить плоскую trust-маску (dens>=6)
на калиброванный по канону v2 пер-пиксельный гейт по трём quality-каналам
(n_cov, sigma_cm, canopy30_cm).

Метод:
  A) Калибровка на TRAIN-половине канона v2 (блок-паритет 5.83):
     перебор порогов n_cov_min x sigma_max x canopy_max; для каждого —
     MAD/p95/n на train (два вида: весь покрытый |dH| и canon-v2-подмнож.)
     и пиксельное покрытие по 3D-гистограмме (один чанковый проход растра).
     Выбор GOLD/OK по Парето (min MAD при max coverage) ТОЛЬКО на train.
  B) Растровое применение: uint8-классы {0 excl, 1 rest, 2 ok, 3 gold},
     чанково; продукт download/mclf_v3/mclf_v3_mask_5.84.tif + clean-DEM.
  C) Валидация на HOLDOUT: метрики по классам, сравнение с trust (dens>=6)
     при сопоставимом покрытии; доли классов по 21 зоне.

Выход:
  results/mclf_v3_5.89.json, results/mclf_v3_5.89.md
  download/mclf_v3/mclf_v3_mask_5.84.tif (uint8), mclf_v3_dem_5.84.tif
  results/zone_mclfv3_5.89.csv
"""
import numpy as np
import pandas as pd
import rasterio, json, os, sys
from rasterio import windows

R = "/tmp/my-project/gh-clone"
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"
TRUST = f"{R}/download/mosaic_5.84/trust_mask_2m_5.84.tif"
DUMP = f"{R}/scripts/data/icesat2_atl06_dump_5.86.csv.gz"
OUT_MASK = f"{R}/download/mclf_v3/mclf_v3_mask_5.84.tif"
OUT_DEM = f"{R}/download/mclf_v3/mclf_v3_dem_5.84.tif"
OUT_RES = f"{R}/results"

NC_BINS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]      # последний = 9+
SG_BINS = [-1, 30, 50, 80, 150, 10**9]            # 5 граней -> idx 0..4
CP_BINS = [-1, 50, 100, 200, 10**9]               # 4 грани -> idx 0..3
NN, NS, NC_ = len(NC_BINS), len(SG_BINS), len(CP_BINS)

# ------------------------------------------------------- биннинг
def code3(nc, sg, cp):
    """Код тройки бинов; nodata -> -1."""
    b_n = np.digitize(nc, NC_BINS) - 1          # 0..10
    b_s = np.digitize(sg, SG_BINS) - 1          # 0..4
    b_c = np.digitize(cp, CP_BINS) - 1          # 0..3
    bad = (nc <= 0) | (sg < 0) | (cp < 0) | (sg >= 65000) | (cp >= 65000) \
        | (b_n < 0) | (b_s < 0) | (b_c < 0)
    code = b_n * (NS * NC_) + np.clip(b_s, 0, None) * NC_ + np.clip(b_c, 0, None)
    code = np.where(bad, -1, code)
    return code, bad

# ------------------------------------------------------- A. точки (train)
print("загрузка демпа...", flush=True)
dump = pd.read_csv(DUMP)
cov = dump[dump.dH2m.notna()].copy()
print("покрыто:", len(cov), flush=True)
cov["is_v2"] = (cov.dH2m.abs() <= 3.0) & (cov.canopy30_cm < 100)
cov["is_core"] = cov.is_v2 & (cov.sigma_cm <= 50) & (cov.trust >= 1)
blk = (np.floor(cov.lat / 0.02).astype(int) +
       np.floor(cov.lon / 0.0127).astype(int))
cov["blk_half"] = (blk % 2).values
tr = cov[cov.blk_half == 0]
ho = cov[cov.blk_half == 1]
print(f"train {len(tr)} / holdout {len(ho)}", flush=True)

code_tr, _ = code3(tr.n_cov.values, tr.sigma_cm.values, tr.canopy30_cm.values)
tr["code"] = code_tr
dh_tr = tr.dH2m.values
v2_tr = tr.is_v2.values

NC_MIN_OPTS = [2, 3, 4, 6, 8]
SG_MAX_OPTS = [30, 50, 80, None]      # None = без ограничения
CP_MAX_OPTS = [50, 100, 200, None]

# кодовые границы бинов
def sg_code_of(maxval):
    return NS - 1 if maxval is None else max(
        i for i in range(NS) if SG_BINS[i + 1] >= maxval) - 0 if False else \
        (0 if maxval is None else int(np.searchsorted(SG_BINS, maxval, side="right") - 1))
def cp_code_of(maxval):
    return NC_ - 1 if maxval is None else int(np.searchsorted(CP_BINS, maxval, side="right") - 1)

# ------------------------------------------------------- A2. 3D-гистограмма растра (один проход)
print("растровый проход: 3D-гистограмма quality...", flush=True)
dsq = rasterio.open(QUAL)
H, W = dsq.height, dsq.width
CH = 250
hist3 = np.zeros(NN * NS * NC_, dtype=np.int64)
for r0 in range(0, H, CH):
    r1 = min(r0 + CH, H)
    win = windows.Window(0, r0, W, r1 - r0)
    b1 = dsq.read(1, window=win)
    b2 = dsq.read(2, window=win)
    b3 = dsq.read(3, window=win)
    b2 = b2.astype(np.int32); b3 = b3.astype(np.int32)
    b1f = b1.astype(np.int32)
    c, bad = code3(b1f.ravel(), b2.ravel(), b3.ravel())
    c = c[c >= 0]
    hist3 += np.bincount(c, minlength=NN * NS * NC_)
    if (r0 // CH) % 20 == 0:
        print(f"  строки {r0}/{H}", flush=True)
hist3 = hist3.reshape(NN, NS, NC_)
valid_px = int(hist3.sum())
print(f"валидных пикселей quality: {valid_px} ({100*valid_px/(H*W):.1f}%)", flush=True)

# ------------------------------------------------------- A3. перебор комбинаций
def combo_metrics(df, n_min, s_max, c_max):
    m = (df.n_cov >= n_min)
    if s_max is not None:
        m &= (df.sigma_cm <= s_max)
    if c_max is not None:
        m &= (df.canopy30_cm < c_max)
    return m

def mstats(d):
    d = np.asarray(d, float); d = d[np.isfinite(d)]
    if len(d) < 30:
        return dict(n=int(len(d)))
    return dict(n=int(len(d)), med=round(float(np.median(d)), 3),
                mad=round(float(np.median(np.abs(d - np.median(d)))), 3),
                p95=round(float(np.percentile(np.abs(d), 95)), 3))

rows = []
for n_min in NC_MIN_OPTS:
    for s_max in SG_MAX_OPTS:
        for c_max in CP_MAX_OPTS:
            mtr = combo_metrics(tr, n_min, s_max, c_max)
            if mtr.sum() < 500:
                continue
            # пиксельное покрытие из гистограммы
            s_idx = NS - 1 if s_max is None else sg_code_of(s_max)
            c_idx = NC_ - 1 if c_max is None else cp_code_of(c_max)
            n_idx = n_min
            sel = hist3[n_idx:, :s_idx + 1, :c_idx + 1]
            px = int(sel.sum())
            rows.append(dict(
                n_cov_min=n_min, sigma_max=s_max, canopy_max=c_max,
                px=px, px_share=round(px / valid_px, 4),
                train_n=int(mtr.sum()),
                raw=mstats(dh_tr[mtr.values]),
                v2=mstats(dh_tr[mtr.values & v2_tr]),
                out3_suppr=round(float(
                    (np.abs(dh_tr[~mtr.values]) > 3).mean() if (~mtr).sum() else 0), 3)))
cal = pd.DataFrame(rows)
cal["out3_share_in"] = cal.raw.apply(
    lambda d: d.get("p95") and round(float(
        np.mean(np.abs(dh_tr[combo_metrics(tr, 0, None, None).values]) <= 3)), 3))
print(f"комбинаций: {len(cal)}", flush=True)

# Парето-выбор ТОЛЬКО по train: GOLD = min MAD (raw) при coverage>=0.25;
# OK = min MAD при coverage>=0.45; среди равных — max coverage.
def pick(base_df, cov_min, mad_cap, p95_cap=None):
    """Max покрытие (px_share) при MAD<=mad_cap (и p95<=p95_cap) на train."""
    d = base_df[(base_df.px_share >= cov_min)].copy()
    if not len(d):
        return None
    d = d[d.raw.apply(lambda x: x.get("mad", 9) is not None)]
    d["mad"] = d.raw.apply(lambda x: x.get("mad", 9.0))
    d["p95"] = d.raw.apply(lambda x: x.get("p95", 99.0))
    d = d[d.mad <= mad_cap]
    if p95_cap is not None:
        d = d[d.p95 <= p95_cap]
    if not len(d):
        return None
    d = d.sort_values(["px_share", "mad"], ascending=[False, True])
    return d.iloc[0]

gold = pick(cal, 0.20, 0.25, 1.5)
ok = pick(cal, 0.40, 0.30, 2.0)
print("GOLD:", None if gold is None else gold.to_dict(), flush=True)
print("OK:", None if ok is None else ok.to_dict(), flush=True)

# если Парето пуст при таких кэпах — расширяем
if gold is None:
    gold = pick(cal, 0.15, 0.30, 2.0)
if ok is None:
    ok = pick(cal, 0.30, 0.40, 3.0)

# ------------------------------------------------------- B. растровое применение
print("растровое применение классов...", flush=True)
os.makedirs(f"{R}/download/mclf_v3", exist_ok=True)
tr_from = rasterio.transform.Affine(*dsq.transform[:6])
prof = dict(driver="GTiff", width=W, height=H, count=1, dtype="uint8",
            crs=dsq.crs, transform=dsq.transform, compress="deflate",
            nodata=255)
ds_m = rasterio.open(OUT_MASK, "w", **prof)
ds_d = rasterio.open(OUT_DEM, "w", **{**prof, "dtype": "int16", "nodata": -32768})
ds_dem = rasterio.open(DEM)

def _none(v):
    """NaN/None -> None (без ограничения), иначе float."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)

g_n, g_s, g_c = int(gold.n_cov_min), _none(gold.sigma_max), _none(gold.canopy_max)
o_n, o_s, o_c = int(ok.n_cov_min), _none(ok.sigma_max), _none(ok.canopy_max)
cls_counts = np.zeros(4, dtype=np.int64)

for r0 in range(0, H, CH):
    r1 = min(r0 + CH, H)
    win = windows.Window(0, r0, W, r1 - r0)
    b1 = dsq.read(1, window=win).astype(np.int32)
    b2 = dsq.read(2, window=win).astype(np.int32)
    b3 = dsq.read(3, window=win).astype(np.int32)
    dem = ds_dem.read(1, window=win)
    _, bad = code3(b1.ravel(), b2.ravel(), b3.ravel())
    bad = bad.reshape(b1.shape)
    goldm = (b1 >= g_n) & (b2 <= (g_s if g_s is not None else 10**9)) & \
            (b3 < (g_c if g_c is not None else 10**9)) & ~bad
    okm = (b1 >= o_n) & (b2 <= (o_s if o_s is not None else 10**9)) & \
          (b3 < (o_c if o_c is not None else 10**9)) & ~bad
    dem_ok = dem != -32768
    cls = np.zeros(b1.shape, np.uint8)          # 0 = excl (nodata/нет quality)
    cls[okm & dem_ok] = 2
    cls[goldm & dem_ok] = 3
    # rest = валидные пиксели (quality есть, DEM есть) вне гейта
    valid = (~bad) & dem_ok
    cls[valid & (cls == 0)] = 1
    cls_counts += np.bincount(cls.ravel(), minlength=4)
    ds_m.write(cls, 1, window=win)
    dem_out = np.where(cls >= 2, dem, -32768)
    ds_d.write(dem_out.astype("int16"), 1, window=win)
ds_m.close(); ds_d.close()
print("классы 0/1/2/3:", cls_counts.tolist(), flush=True)

# ------------------------------------------------------- C. валидация на holdout
print("валидация holdout...", flush=True)
dsm = rasterio.open(OUT_MASK)
def sample_cls(lats, lons):
    T = dsm.transform
    cols = np.floor((np.asarray(lons) - T.c) / T.a).astype(int)
    rows = np.floor((np.asarray(lats) - T.f) / T.e).astype(int)
    okp = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    out = np.full(len(lats), 255, np.uint8)
    if okp.any():
        win = windows.Window(int(cols[okp].min()), int(rows[okp].min()),
                             int(cols[okp].max() - cols[okp].min() + 1),
                             int(rows[okp].max() - rows[okp].min() + 1))
        arr = dsm.read(1, window=win)
        rr = rows[okp] - int(rows[okp].min()); cc = cols[okp] - int(cols[okp].min())
        out[okp] = arr[rr, cc]
    return out

ho_cls = sample_cls(ho.lat.values, ho.lon.values)
tr_cls = sample_cls(tr.lat.values, tr.lon.values)
ho = ho.assign(cls=ho_cls)
tr = tr.assign(cls=tr_cls)
dst = rasterio.open(TRUST)
Tt = dst.transform
cols = np.floor((ho.lon.values - Tt.c) / Tt.a).astype(int)
rows = np.floor((ho.lat.values - Tt.f) / Tt.e).astype(int)
okp = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
tm = np.full(len(ho), 0, np.uint8)
win = windows.Window(int(cols[okp].min()), int(rows[okp].min()),
                     int(cols[okp].max() - cols[okp].min() + 1),
                     int(rows[okp].max() - rows[okp].min() + 1))
ta = dst.read(1, window=win)
rr = rows[okp] - int(rows[okp].min()); cc = cols[okp] - int(cols[okp].min())
tm[okp] = ta[rr, cc]
ho["trust5"] = tm

res = dict(
    method="M-CLF-v3: пер-пиксельный гейт n_cov/sigma/canopy, калибровка на train-половине канона v2 (max покрытие при кэпах MAD/p95), применение растрово, валидация на holdout",
    gold=dict(n_cov_min=g_n, sigma_max=g_s, canopy_max=g_c,
              px_share=float(gold.px_share), train_mad=float(gold.mad)),
    ok=dict(n_cov_min=o_n, sigma_max=o_s, canopy_max=o_c,
            px_share=float(ok.px_share), train_mad=float(ok.mad)),
    px_classes=dict(excl=int(cls_counts[0]), rest=int(cls_counts[1]),
                    ok=int(cls_counts[2]), gold=int(cls_counts[3]),
                    valid_total=int(cls_counts.sum())),
    calib_table=cal.to_dict(orient="records"),
    holdout={},
    holdout_trust_dens6=mstats(ho[ho.trust5 == 1].dH2m),
)
for c, name in ((0, "excl"), (1, "rest"), (2, "ok"), (3, "gold")):
    m = ho.cls == c
    res["holdout"][name] = dict(
        all=mstats(ho[m].dH2m), v2=mstats(ho[m & ho.is_v2].dH2m),
        share_out3=round(float((ho[m].dH2m.abs() > 3).mean()) if m.sum() else 0, 4))
# доверительные интервалы не считаем — MAD устойчив на таких n

# сравнение trust (dens>=6) vs gate; доверительная площадь trust — из гистограммы
res["comparison"] = dict(
    trust_dens6=dict(px=int(hist3[6:, :, :].sum()),
                     holdout=mstats(ho[ho.trust5 == 1].dH2m),
                     holdout_v2=mstats(ho[(ho.trust5 == 1) & ho.is_v2].dH2m)),
    gate_ok_gold=dict(holdout=mstats(ho[ho.cls >= 2].dH2m),
                      holdout_v2=mstats(ho[(ho.cls >= 2) & ho.is_v2].dH2m)),
    trust_and_gate=dict(holdout=mstats(ho[(ho.trust5 == 1) & (ho.cls >= 2)].dH2m)),
    gate2_trust1=dict(holdout=mstats(ho[(ho.cls >= 2) & (ho.trust5 == 0)].dH2m)))

# ------------------------------------------------------- зоны: доли классов
zs = pd.read_csv(f"{R}/results/zones_summary_5.83.csv")
zrows = []
for _, z in zs.iterrows():
    try:
        W_, S_, E_, N_ = [float(v) for v in z["bbox"].split()]
    except Exception:
        continue
    c0 = int((W_ - dsq.transform.c) / dsq.transform.a)
    c1 = int((E_ - dsq.transform.c) / dsq.transform.a)
    r0 = int((N_ - dsq.transform.f) / dsq.transform.e)
    r1 = int((S_ - dsq.transform.f) / dsq.transform.e)
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, W), min(r1, H)
    if c1 <= c0 or r1 <= r0:
        continue
    win = windows.Window(c0, r0, c1 - c0, r1 - r0)
    a = dsm.read(1, window=win)
    cnt = np.bincount(a.ravel(), minlength=4)
    tot = cnt.sum()
    zrows.append(dict(tag=z["tag"],
                      share_gold=round(float(cnt[3]) / tot, 3),
                      share_ok=round(float(cnt[2]) / tot, 3),
                      share_excl=round(float(cnt[0]) / tot, 3),
                      n=int(tot)))
zdf = pd.DataFrame(zrows)
zdf.to_csv(f"{OUT_RES}/zone_mclfv3_5.89.csv", index=False)
res["zones_share_gold_gt50"] = zdf[zdf.share_gold > 0.5].tag.tolist()

json.dump(res, open(f"{OUT_RES}/mclf_v3_5.89.json", "w"),
          ensure_ascii=False, indent=1)

# ------------------------------------------------------- консоль
print("\n=== ИТОГ 5.89 ===")
print(f"GOLD: n_cov>={g_n} sigma<={g_s} canopy<{g_c} px={gold.px_share:.1%}")
print(f"OK:   n_cov>={o_n} sigma<={o_s} canopy<{o_c} px={ok.px_share:.1%}")
print("пиксели 0/1/2/3:", cls_counts.tolist())
for name in ("gold", "ok", "rest", "excl"):
    h = res["holdout"][name]
    a = h.get("all", {})
    print(f"holdout {name:5s} n={a.get('n','-'):>7} MAD={a.get('mad','-')} "
          f"p95={a.get('p95','-')} share|dH|>3={h.get('share_out3','-')}")
print("holdout trust(dens>=6):", res["holdout_trust_dens6"])
print("holdout gate(cls>=2):  ", res["comparison"]["gate_ok_gold"]["holdout"])
print("trust & gate:          ", res["comparison"]["trust_and_gate"]["holdout"])
print("gate2 & trust0:        ", res["comparison"]["gate2_trust1"]["holdout"])
print("\nзоны share_gold:")
print(zdf.sort_values("share_gold").to_string(index=False))
